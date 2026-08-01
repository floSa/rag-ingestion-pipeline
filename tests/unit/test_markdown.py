"""Tests unitaires pour la normalisation du Markdown."""

from __future__ import annotations

from src.docling_service.markdown import normalize_markdown


class TestParagraphes:
    def test_lignes_dun_paragraphe_recollees(self):
        source = "Une phrase coupee\nsur deux lignes."
        assert normalize_markdown(source) == "Une phrase coupee sur deux lignes."

    def test_paragraphes_separes_restent_separes(self):
        source = "Premier paragraphe.\n\nSecond paragraphe."
        assert normalize_markdown(source) == "Premier paragraphe.\n\nSecond paragraphe."

    def test_trois_lignes_recollees(self):
        source = "une\ndeux\ntrois"
        assert normalize_markdown(source) == "une deux trois"

    def test_document_deja_normalise_inchange(self):
        source = "# Titre\n\nUn paragraphe deja sur une ligne.\n\n- item\n- autre item\n"
        assert normalize_markdown(source) == source

    def test_saut_de_ligne_final_preserve(self):
        assert normalize_markdown("une\ndeux\n").endswith("\n")

    def test_absence_de_saut_final_preservee(self):
        assert not normalize_markdown("une\ndeux").endswith("\n")

    def test_chaine_vide(self):
        assert normalize_markdown("") == ""


class TestBlocsPreserves:
    def test_titre_non_recolle(self):
        source = "# Titre\nLe paragraphe qui suit."
        assert normalize_markdown(source) == "# Titre\nLe paragraphe qui suit."

    def test_lignes_de_liste_non_recollees(self):
        source = "- premier item\n- second item\n- troisieme"
        assert normalize_markdown(source) == source

    def test_lignes_de_tableau_non_recollees(self):
        source = "| a | b |\n|---|---|\n| 1 | 2 |"
        assert normalize_markdown(source) == source

    def test_citation_non_recollee(self):
        source = "> une citation\n> sur deux lignes"
        assert normalize_markdown(source) == source

    def test_filet_horizontal_non_recolle(self):
        source = "du texte\n\n---\n\nd'autre texte"
        assert normalize_markdown(source) == source

    def test_code_indente_non_recolle(self):
        source = "    ligne de code\n    autre ligne"
        assert normalize_markdown(source) == source

    def test_html_inline_non_recolle(self):
        source = "<div>\n<span>x</span>\n</div>"
        assert normalize_markdown(source) == source


class TestBlocsDeCode:
    def test_bloc_cloture_intact(self):
        source = "```python\nx = 1\n\ny = 2\n```"
        assert normalize_markdown(source) == source

    def test_prose_avant_et_apres_un_bloc(self):
        source = "avant coupe\nici\n\n```\ncode\nsuite\n```\n\napres coupe\nla"
        expected = "avant coupe ici\n\n```\ncode\nsuite\n```\n\napres coupe la"
        assert normalize_markdown(source) == expected

    def test_tildes_acceptes(self):
        source = "~~~\ncode\nsuite\n~~~"
        assert normalize_markdown(source) == source

    def test_backticks_dans_un_bloc_tilde_ne_ferment_pas(self):
        source = "~~~\n```\nencore du code\n~~~"
        assert normalize_markdown(source) == source

    def test_prose_apres_bloc_non_ferme_reste_intacte(self):
        # Bloc non ferme : tout ce qui suit est considere comme du code.
        source = "```\ncode\nligne"
        assert normalize_markdown(source) == source


class TestRetoursExplicites:
    def test_deux_espaces_finaux_font_une_coupure(self):
        source = "premiere ligne  \nseconde ligne"
        assert normalize_markdown(source) == source

    def test_antislash_final_fait_une_coupure(self):
        source = "premiere ligne\\\nseconde ligne"
        assert normalize_markdown(source) == source


class TestTitresSetext:
    def test_titre_souligne_non_absorbe(self):
        # Sans garde-fou, « Titre » serait recolle au paragraphe precedent et
        # le soulignement transformerait le tout en titre.
        source = "un paragraphe\nTitre\n====="
        assert normalize_markdown(source) == source

    def test_soulignement_lui_meme_intact(self):
        source = "Titre\n-----\n\ndu texte"
        assert normalize_markdown(source) == source


class TestCasReel:
    def test_paragraphe_coupe_a_80_colonnes(self):
        source = (
            "## Tendance centrale\n"
            "\n"
            "La mediane est la valeur qui separe l'echantillon ordonne en deux\n"
            "moities de meme effectif. Contrairement a la moyenne, elle ne se\n"
            "deplace pas quand une valeur extreme s'eloigne.\n"
            "\n"
            "| Mesure | Formule |\n"
            "|---|---|\n"
            "| Etendue | max - min |\n"
        )
        result = normalize_markdown(source)
        lines = result.splitlines()
        # Le paragraphe tient desormais sur une seule ligne...
        assert "La mediane est la valeur" in lines[2]
        assert "s'eloigne." in lines[2]
        # ...et le tableau est intact.
        assert lines[4:] == ["| Mesure | Formule |", "|---|---|", "| Etendue | max - min |"]
