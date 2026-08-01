"""Tests unitaires pour le regroupement des elements en blocs vectorisables."""

from __future__ import annotations

from src.docling_service.blocks import Block, build_blocks, has_content


def element(text, label="text", reference_id="sec1", element_id=None):
    """Fabrique un element minimal."""
    return {
        "id": element_id or f"id{abs(hash((text, label, reference_id))) % 10**9:09d}"[:10],
        "label": label,
        "text": text,
        "reference_id": reference_id,
    }


class TestHasContent:
    def test_texte_ordinaire(self):
        assert has_content("bonjour") is True

    def test_chiffre_seul(self):
        assert has_content("2") is True

    def test_ponctuation_seule(self):
        assert has_content("...") is False

    def test_filet_de_tableau(self):
        assert has_content("-" * 70) is False

    def test_chaine_vide(self):
        assert has_content("") is False

    def test_espaces_et_puces(self):
        assert has_content("  •  |  ") is False


class TestFiltrageDuBruit:
    def test_bruit_pur_ecarte(self):
        blocks = build_blocks([element("-------"), element(".")], target_chars=900, min_chars=24)
        assert blocks == []

    def test_element_trop_court_ecarte(self):
        blocks = build_blocks([element("x")], target_chars=900, min_chars=24)
        assert blocks == []

    def test_element_suffisant_conserve(self):
        texte = "Un paragraphe assez long pour passer le plancher."
        blocks = build_blocks([element(texte)], target_chars=900, min_chars=24)
        assert [b.text for b in blocks] == [texte]

    def test_bruit_ne_coupe_pas_la_fusion(self):
        # Un filet de tableau entre deux paragraphes ne doit pas les separer.
        elements = [element("Premiere phrase."), element("|---|"), element("Seconde phrase.")]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 1
        assert "Premiere phrase." in blocks[0].text
        assert "Seconde phrase." in blocks[0].text


class TestFusion:
    def test_fragments_fusionnes(self):
        # Le cas reel : des fragments isoles issus de l'analyse de layout.
        elements = [element(t) for t in ("x", "and", "Note", "n", "y", "les valeurs observees")]
        blocks = build_blocks(elements, target_chars=900, min_chars=24)
        assert len(blocks) == 1
        for fragment in ("x", "and", "Note", "les valeurs observees"):
            assert fragment in blocks[0].text

    def test_fusion_bornee_par_la_taille_cible(self):
        elements = [element("a" * 100) for _ in range(10)]
        blocks = build_blocks(elements, target_chars=300, min_chars=10)
        assert len(blocks) > 1
        assert all(len(b.text) <= 300 for b in blocks)

    def test_pas_de_fusion_entre_sections(self):
        elements = [
            element("Premier paragraphe.", reference_id="sec1"),
            element("Second paragraphe.", reference_id="sec2"),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 2

    def test_bloc_porte_les_identifiants_fusionnes(self):
        elements = [
            element("Premiere phrase.", element_id="aaaaaaaaaa"),
            element("Seconde phrase.", element_id="bbbbbbbbbb"),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert blocks[0].element_ids == ["aaaaaaaaaa", "bbbbbbbbbb"]
        assert blocks[0].size == 2

    def test_ancre_est_le_premier_element(self):
        elements = [
            element("Premiere phrase.", element_id="aaaaaaaaaa"),
            element("Seconde phrase.", element_id="bbbbbbbbbb"),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert blocks[0].anchor["id"] == "aaaaaaaaaa"

    def test_listes_fusionnees_entre_elles(self):
        elements = [element(f"item {i}", label="list_item") for i in range(6)]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 1


class TestElementsAutonomes:
    def test_table_reste_seule(self):
        elements = [
            element("Un paragraphe introductif."),
            element("| a | b |\n|---|---|\n| 1 | 2 |", label="table"),
            element("Un paragraphe de conclusion."),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 3
        assert blocks[1].anchor["label"] == "table"

    def test_titre_reste_seul(self):
        elements = [
            element("Mesures de dispersion et intervalles", label="section_header"),
            element("Un paragraphe qui suit le titre."),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 2

    def test_code_ne_fusionne_pas_avec_la_prose(self):
        elements = [
            element("Voici comment faire le calcul."),
            element("import statistics as stats", label="code"),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 2

    def test_code_fusionne_avec_du_code(self):
        elements = [element(f"ligne_{i} = {i}", label="code") for i in range(5)]
        blocks = build_blocks(elements, target_chars=900, min_chars=10)
        assert len(blocks) == 1


class TestCasLimites:
    def test_liste_vide(self):
        assert build_blocks([], target_chars=900, min_chars=24) == []

    def test_tout_est_du_bruit(self):
        elements = [element("-"), element("."), element(",")]
        assert build_blocks(elements, target_chars=900, min_chars=1) == []

    def test_element_plus_long_que_la_cible_conserve(self):
        # Le decoupage en fenetres intervient ensuite ; ici on ne perd rien.
        long_texte = "mot " * 500
        blocks = build_blocks([element(long_texte)], target_chars=900, min_chars=24)
        assert len(blocks) == 1
        assert blocks[0].text.strip() == long_texte.strip()

    def test_ordre_de_lecture_preserve(self):
        elements = [
            element("Alpha.", reference_id="s1"),
            element("Beta.", reference_id="s2"),
            element("Gamma.", reference_id="s3"),
        ]
        blocks = build_blocks(elements, target_chars=900, min_chars=1)
        assert [b.text for b in blocks] == ["Alpha.", "Beta.", "Gamma."]

    def test_aucun_texte_perdu_parmi_les_elements_retenus(self):
        elements = [element(f"phrase numero {i} du document.") for i in range(20)]
        blocks = build_blocks(elements, target_chars=200, min_chars=10)
        rendu = " ".join(b.text for b in blocks)
        for i in range(20):
            assert f"phrase numero {i}" in rendu


class TestBlockDataclass:
    def test_size_par_defaut(self):
        assert Block(text="x", anchor={}).size == 0
