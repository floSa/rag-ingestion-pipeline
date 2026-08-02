"""Tests unitaires pour la normalisation du Markdown."""

from __future__ import annotations

from src.docling_service.markdown import (
    IMAGE_MARKER,
    extract_image_references,
    normalize_markdown,
)


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


class TestExtractionDesImages:
    def test_wikilink_obsidian(self):
        rendu, refs = extract_image_references("![[schema.jpg|1000]]")
        assert len(refs) == 1
        assert refs[0].target == "schema.jpg"
        assert refs[0].caption == ""
        assert rendu == "⟦IMG:0000⟧ schema"

    def test_syntaxe_standard(self):
        rendu, refs = extract_image_references("![Le schema](images/fig1.png)")
        assert refs[0].target == "images/fig1.png"
        assert refs[0].caption == "Le schema"
        assert rendu == "⟦IMG:0000⟧ Le schema"

    def test_chemin_encode_est_decode(self):
        _, refs = extract_image_references("![](Pi%C3%A8ces%20jointes/f.png)")
        assert refs[0].target == "Pièces jointes/f.png"

    def test_position_preservee(self):
        # L'ancrage est tout l'enjeu : l'image doit rester entre les deux
        # paragraphes, pour que sa legende lui reste adjacente.
        source = "Avant.\n\n![[f.jpg]]\n\nLegende de la figure."
        rendu, _ = extract_image_references(source)
        lignes = [l for l in rendu.splitlines() if l.strip()]
        assert lignes[0] == "Avant."
        assert lignes[1].startswith("⟦IMG:0000⟧")
        assert lignes[2] == "Legende de la figure."

    def test_plusieurs_images_numerotees_dans_l_ordre(self):
        _, refs = extract_image_references("![[a.jpg]]\n\n![[b.jpg]]\n\n![[c.jpg]]")
        assert [r.index for r in refs] == [0, 1, 2]
        assert [r.target for r in refs] == ["a.jpg", "b.jpg", "c.jpg"]
        assert [r.marker for r in refs] == ["⟦IMG:0000⟧", "⟦IMG:0001⟧", "⟦IMG:0002⟧"]

    def test_image_en_ligne_sortie_du_paragraphe(self):
        rendu, refs = extract_image_references("Voir ![[f.jpg]] ci-dessus.")
        assert len(refs) == 1
        lignes = rendu.splitlines()
        assert "Voir" in lignes[0] and "ci-dessus." in lignes[0]
        assert lignes[1].startswith("⟦IMG:0000⟧")

    def test_bloc_de_code_intact(self):
        source = "```markdown\n![[doc.png]]\n```"
        rendu, refs = extract_image_references(source)
        assert refs == []
        assert rendu == source

    def test_document_sans_image_inchange(self):
        source = "# Titre\n\nUn paragraphe.\n"
        rendu, refs = extract_image_references(source)
        assert refs == []
        assert rendu == source

    def test_lien_non_image_ignore(self):
        # [[Note]] est un lien interne Obsidian, pas une image.
        rendu, refs = extract_image_references("Voir [[Une autre note]] pour la suite.")
        assert refs == []
        assert rendu == "Voir [[Une autre note]] pour la suite."

    def test_balise_reconnue_apres_coup(self):
        rendu, refs = extract_image_references("![[schema.jpg]]")
        m = IMAGE_MARKER.match(rendu)
        assert m is not None
        assert int(m.group(1)) == refs[0].index
        assert m.group(2) == "schema"

    def test_balise_non_recollee_par_la_normalisation(self):
        # Sans ce garde-fou, la balise serait absorbee dans le paragraphe
        # voisin et l'image perdrait son element propre.
        source = "Un texte\ncoupe en deux.\n\n![[f.jpg]]\n\nSuite du propos."
        rendu = normalize_markdown(extract_image_references(source)[0])
        assert "⟦IMG:0000⟧ f" in rendu.splitlines()

    def test_legende_repliee_sur_le_nom_de_fichier(self):
        rendu, _ = extract_image_references("![[kimik3_slide_00m01s.jpg|1000]]")
        assert rendu == "⟦IMG:0000⟧ kimik3_slide_00m01s"
