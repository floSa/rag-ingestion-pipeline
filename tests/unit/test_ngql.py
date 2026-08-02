"""Tests unitaires pour la construction des requetes nGQL.

L'echappement est le point ou une regression se paie le plus cher : une chaine
mal echappee produit un INSERT rejete par le graphd, donc un trou invisible
dans le graphe.
"""

from __future__ import annotations

import pytest

from src.docling_service.ngql import (
    MAX_VALUES_PER_STATEMENT,
    VID_MAX_BYTES,
    batch_values,
    document_vid,
    edge_value,
    escape_ngql,
    insert_edge_statements,
    insert_vertex_statements,
    quote,
    render,
    vertex_value,
)


class TestEscapeNgql:
    def test_plain_text_unchanged(self):
        assert escape_ngql("Hello world") == "Hello world"

    def test_double_quote_escaped(self):
        assert escape_ngql('dit "bonjour"') == 'dit \\"bonjour\\"'

    def test_backslash_escaped(self):
        assert escape_ngql("a\\b") == "a\\\\b"

    def test_latex_formula_survives(self):
        # Regression : \frac produisait "\f" (form feed) puis un nGQL invalide,
        # et tous les noeuds Formula d'un livre de maths etaient perdus.
        assert escape_ngql(r"\frac{1}{2}") == r"\\frac{1}{2}"

    def test_latex_alpha_survives(self):
        assert escape_ngql(r"\alpha + \beta") == r"\\alpha + \\beta"

    def test_backslash_escaped_before_quote(self):
        # L'ordre compte : echapper le guillemet d'abord produirait \\" au lieu
        # de \\\", soit une litterale fermee trop tot.
        assert escape_ngql('\\"') == '\\\\\\"'

    def test_single_quote_left_alone(self):
        # Une apostrophe n'a aucun sens special entre guillemets doubles ;
        # l'echapper injectait un antislash parasite dans le texte stocke.
        assert escape_ngql("l'ecart-type") == "l'ecart-type"

    def test_newline_and_tab_escaped(self):
        assert escape_ngql("a\nb\tc\rd") == "a\\nb\\tc\\rd"

    def test_empty_string(self):
        assert escape_ngql("") == ""


class TestQuoteAndRender:
    def test_quote_wraps(self):
        assert quote("abc") == '"abc"'

    def test_render_int(self):
        assert render(42) == "42"

    def test_render_float(self):
        assert render(1.5) == "1.5"

    def test_render_bool_before_int(self):
        # bool est une sous-classe de int : sans test dedie, True donnerait "1".
        assert render(True) == "true"
        assert render(False) == "false"

    def test_render_none_is_empty_string(self):
        assert render(None) == '""'

    def test_render_str_quoted_and_escaped(self):
        assert render('a"b') == '"a\\"b"'


class TestValueExpressions:
    def test_vertex_value(self):
        assert vertex_value("abc", ("text", 3)) == '"abc":("text", 3)'

    def test_edge_value(self):
        assert edge_value("a", "b", (7,)) == '"a" -> "b":(7)'

    def test_vertex_value_escapes_vid(self):
        assert vertex_value('a"b', ()) == '"a\\"b":()'


class TestBatchValues:
    def test_empty_yields_nothing(self):
        assert list(batch_values([])) == []

    def test_small_input_single_batch(self):
        assert list(batch_values(["a", "b", "c"])) == [["a", "b", "c"]]

    def test_splits_on_count(self):
        values = [f"v{i}" for i in range(MAX_VALUES_PER_STATEMENT * 2 + 1)]
        batches = list(batch_values(values))
        assert len(batches) == 3
        assert [len(b) for b in batches] == [
            MAX_VALUES_PER_STATEMENT,
            MAX_VALUES_PER_STATEMENT,
            1,
        ]

    def test_splits_on_bytes(self):
        # Deux valeurs de 200 Ko depassent le plafond de 256 Ko : deux paquets.
        heavy = "x" * 200_000
        assert len(list(batch_values([heavy, heavy]))) == 2

    def test_oversized_value_kept_alone(self):
        # Une valeur seule au-dela du plafond ne doit pas etre perdue.
        huge = "x" * 300_000
        batches = list(batch_values([huge, "small"]))
        assert batches == [[huge], ["small"]]

    def test_preserves_order_and_content(self):
        values = [f"v{i}" for i in range(500)]
        assert [v for batch in batch_values(values) for v in batch] == values


class TestStatements:
    def test_vertex_statement_shape(self):
        statements = list(insert_vertex_statements("Paragraph", ("label", "page_no"), ['"a":(1)']))
        assert statements == ['INSERT VERTEX Paragraph(label, page_no) VALUES "a":(1);']

    def test_edge_statement_shape(self):
        statements = list(insert_edge_statements("PARENT_OF", ("sequence",), ['"a" -> "b":(1)']))
        assert statements == ['INSERT EDGE PARENT_OF(sequence) VALUES "a" -> "b":(1);']

    def test_multiple_values_grouped_in_one_statement(self):
        statements = list(insert_vertex_statements("Paragraph", ("label",), ['"a":(1)', '"b":(2)']))
        assert len(statements) == 1
        assert statements[0].count("VALUES") == 1
        assert '"a":(1), "b":(2)' in statements[0]

    def test_no_values_no_statement(self):
        assert list(insert_vertex_statements("Paragraph", ("label",), [])) == []


@pytest.mark.parametrize(
    "text",
    [
        r"\frac{a}{b}",
        'guillemet " au milieu',
        "antislash final \\",
        "ligne1\nligne2",
        "melange \\ et \" et '",
    ],
)
def test_escaped_literal_is_balanced(text: str):
    """La litterale produite ne se referme jamais avant sa fin."""
    literal = quote(text)
    assert literal.startswith('"') and literal.endswith('"')
    # Compte les guillemets non precedes d'un nombre impair d'antislashs.
    body = literal[1:-1]
    index = 0
    while index < len(body):
        if body[index] == "\\":
            index += 2
            continue
        assert body[index] != '"', f"guillemet non echappe dans {literal!r}"
        index += 1


class TestDocumentVid:
    def test_nom_court_reste_lisible(self):
        assert document_vid("mon_livre") == "doc_mon_livre"

    def test_titre_francais_long_tient_dans_la_limite(self):
        # Regression : « Kimi K3 — l'architecture d'un modele pense pour
        # l'efficacite » depassait les 64 octets de l'ancien space, les accents
        # comptant double et le tiret cadratin triple. Le graphd rejetait alors
        # l'insertion du document entier.
        titre = "Kimi K3 — l'architecture d'un modèle pensé pour l'efficacité"
        vid = document_vid(titre)
        assert len(vid.encode()) <= VID_MAX_BYTES
        assert vid == f"doc_{titre}"

    def test_titre_demesure_est_tronque(self):
        vid = document_vid("é" * 400)
        assert len(vid.encode()) <= VID_MAX_BYTES

    def test_troncature_sans_caractere_casse(self):
        # Le decoupage tombe au milieu d'un caractere multi-octets : il ne doit
        # pas produire de sequence invalide.
        vid = document_vid("é" * 300)
        assert vid.encode().decode() == vid

    def test_deux_titres_de_meme_debut_ne_se_confondent_pas(self):
        base = "a" * 300
        assert document_vid(base + "premier") != document_vid(base + "second")
