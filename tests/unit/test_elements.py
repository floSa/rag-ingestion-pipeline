"""Tests unitaires pour la construction des elements de document.

Ces tests importent les fonctions reelles du service, et non une copie : une
divergence avec le code de production ne peut pas passer inapercue.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from src.docling_service.elements import (
    CLEANED_SUBDIR,
    ROOT_REFERENCE,
    SECTION_LABELS,
    TAG_MAP,
    DocumentAccumulator,
    DocumentFacts,
    cleaned_path,
    cleaned_root,
    compute_id,
    document_identity,
    extract_bbox,
    item_label,
    item_text,
    pages_sans_element,
    tag_for_label,
)
from src.pipeline.schemas import DocumentElement

# L'un des deux Preface.html du corpus.
IDENTITY = document_identity("htms/MLOps with Databricks/Preface.html")


class FakeBbox:
    def __init__(self, left=10.0, top=200.0, right=100.0, bottom=150.0):
        self.l = left
        self.t = top
        self.r = right
        self.b = bottom


class FakeProv:
    def __init__(self, page_no=1, bbox=None):
        self.page_no = page_no
        self.bbox = bbox


class FakeItem:
    """Reproduit la surface d'un item Docling utilisee par le service."""

    def __init__(self, label="text", text="", page_no=1, bbox=None):
        self.label = label
        self.text = text
        self.prov = [FakeProv(page_no, bbox)] if page_no else []


class TestComputeId:
    def test_deterministic(self):
        assert compute_id("doc", 1, 0, "hello") == compute_id("doc", 1, 0, "hello")

    def test_length_10(self):
        assert len(compute_id("test", 1, 0, "some text")) == 10

    def test_hex_chars_only(self):
        # rag-agent-chat valide /context/{element_id} sur ^[a-f0-9]{10}$.
        assert all(c in "0123456789abcdef" for c in compute_id("test", 1, 0, "text"))

    def test_different_documents_differ(self):
        assert compute_id("doc1", 1, 0, "t") != compute_id("doc2", 1, 0, "t")

    def test_different_pages_differ(self):
        assert compute_id("doc", 1, 0, "t") != compute_id("doc", 2, 0, "t")

    def test_different_positions_differ(self):
        assert compute_id("doc", 1, 0, "t") != compute_id("doc", 1, 1, "t")

    def test_long_text_truncated_to_50_chars(self):
        long_text = "x" * 200
        expected = hashlib.sha256(f"doc|1|0|{long_text[:50]}".encode()).hexdigest()[:10]
        assert compute_id("doc", 1, 0, long_text) == expected

    def test_empty_text(self):
        assert len(compute_id("doc", 1, 0, "")) == 10


class TestExtractBbox:
    def test_none_returns_none(self):
        assert extract_bbox(None) is None

    def test_falsy_returns_none(self):
        assert extract_bbox(0) is None

    def test_valid_bbox_rounded(self):
        assert extract_bbox(FakeBbox(10.123, 20.456, 30.789, 40.012)) == {
            "l": 10.12,
            "t": 20.46,
            "r": 30.79,
            "b": 40.01,
        }


class TestLabels:
    def test_known_label_mapped(self):
        assert tag_for_label("formula") == "Formula"

    def test_unknown_label_defaults_to_paragraph(self):
        assert tag_for_label("inconnu") == "Paragraph"

    def test_section_labels_derived_from_tag_map(self):
        expected = {lbl for lbl, tag in TAG_MAP.items() if tag == "SectionHeader"}
        assert expected == SECTION_LABELS
        assert "title" in SECTION_LABELS

    def test_item_label_lowercased(self):
        assert item_label(FakeItem(label="Section_Header")) == "section_header"

    def test_item_text_stripped(self):
        assert item_text(FakeItem(text="  bonjour  ")) == "bonjour"

    def test_item_without_text(self):
        item = FakeItem()
        del item.text
        assert item_text(item) == ""


class FakeTableItem:
    """Item de type table : `text` vaut None, le contenu s'exporte."""

    label = "table"
    text = None
    prov = []

    def export_to_markdown(self, document):
        assert document is not None
        return "| a | b |\n|---|---|\n| 1 | 2 |"


class TestTableText:
    def test_table_exported_when_text_is_none(self):
        # Sans cet export, les tables ressortaient vides de l'extraction :
        # presentes dans le graphe, introuvables par la recherche.
        assert item_text(FakeTableItem(), document=object()) == "| a | b |\n|---|---|\n| 1 | 2 |"

    def test_table_without_document_yields_empty(self):
        assert item_text(FakeTableItem()) == ""

    def test_export_failure_is_not_fatal(self):
        class Broken(FakeTableItem):
            def export_to_markdown(self, document):
                raise RuntimeError("indisponible")

        assert item_text(Broken(), document=object()) == ""

    def test_plain_text_wins_over_export(self):
        class WithBoth(FakeTableItem):
            text = "texte direct"

        assert item_text(WithBoth(), document=object()) == "texte direct"

    def test_table_element_carries_its_content(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        element = acc.add_item(FakeTableItem(), document=object())
        assert element["label"] == "table"
        assert "| a | b |" in element["text"]


class TestDocumentAccumulator:
    def test_order_increments(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        orders = [acc.add_item(FakeItem(text=f"t{i}"))["order"] for i in range(3)]
        assert orders == [0, 1, 2]

    def test_count_tracks_elements(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        for i in range(4):
            acc.add_item(FakeItem(text=f"t{i}"))
        assert acc.count == 4

    def test_page_position_resets_per_page(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        first = acc.add_item(FakeItem(text="a", page_no=1))
        second = acc.add_item(FakeItem(text="b", page_no=1))
        third = acc.add_item(FakeItem(text="c", page_no=2))
        assert (first["page_position"], second["page_position"]) == (0, 1)
        assert third["page_position"] == 0

    def test_header_attaches_to_document(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        header = acc.add_item(FakeItem(label="section_header", text="Chapitre 1"))
        assert header["reference_id"] == ROOT_REFERENCE

    def test_element_attaches_to_last_header(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        header = acc.add_item(FakeItem(label="section_header", text="Chapitre 1"))
        body = acc.add_item(FakeItem(label="text", text="corps"))
        assert body["reference_id"] == header["id"]

    def test_orphan_before_any_header_attaches_to_document(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        assert acc.add_item(FakeItem(text="avant tout titre"))["reference_id"] == ROOT_REFERENCE

    def test_ref_position_counts_within_parent(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        acc.add_item(FakeItem(label="section_header", text="Chapitre 1"))
        first = acc.add_item(FakeItem(text="a"))
        second = acc.add_item(FakeItem(text="b"))
        acc.add_item(FakeItem(label="section_header", text="Chapitre 2"))
        third = acc.add_item(FakeItem(text="c"))
        assert (first["ref_position"], second["ref_position"]) == (0, 1)
        assert third["ref_position"] == 0

    def test_titre_de_section_porte_par_les_elements(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        acc.add_item(FakeItem(label="section_header", text="Mesures de dispersion"))
        body = acc.add_item(FakeItem(text="corps de la section"))
        assert body["section_title"] == "Mesures de dispersion"

    def test_titre_de_section_survit_au_changement_de_page(self):
        # Les lots de pages d'un PDF ne doivent pas perdre le titre courant.
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        acc.add_item(FakeItem(label="section_header", text="Chapitre 3", page_no=10))
        body = acc.add_item(FakeItem(text="suite", page_no=11))
        assert body["section_title"] == "Chapitre 3"

    def test_titre_remplace_par_la_section_suivante(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        acc.add_item(FakeItem(label="section_header", text="Premiere"))
        acc.add_item(FakeItem(label="section_header", text="Seconde"))
        body = acc.add_item(FakeItem(text="corps"))
        assert body["section_title"] == "Seconde"

    def test_sans_titre_avant_le_premier_en_tete(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        assert acc.add_item(FakeItem(text="avant tout titre"))["section_title"] == ""

    def test_section_context_survives_page_change(self):
        # Les batchs de pages ne doivent pas casser la hierarchie du document.
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        header = acc.add_item(FakeItem(label="section_header", text="Chapitre", page_no=1))
        body = acc.add_item(FakeItem(text="suite", page_no=2))
        assert body["reference_id"] == header["id"]

    def test_ids_stable_across_reingestion(self):
        # Meme document reconverti : memes ids, donc upsert et non doublons.
        def build() -> list[str]:
            acc = DocumentAccumulator(document_identity("mds/doc.md"))
            items = [
                FakeItem(label="section_header", text="Titre", page_no=1),
                FakeItem(text="paragraphe", page_no=1),
                FakeItem(text="autre", page_no=2),
            ]
            return [acc.add_item(item)["id"] for item in items]

        assert build() == build()

    def test_element_matches_shared_schema(self):
        # Garde-fou de contrat : le dict produit doit valider contre le modele
        # partage avec rag-agent-chat.
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        element = acc.add_item(FakeItem(label="picture", text="", bbox=FakeBbox()))
        validated = DocumentElement.model_validate(element)
        assert validated.id == element["id"]
        assert validated.bbox is not None
        assert validated.bbox.left == 10.0

    def test_element_without_bbox_validates(self):
        acc = DocumentAccumulator(document_identity("mds/doc.md"))
        element = acc.add_item(FakeItem(text="sans bbox"))
        assert DocumentElement.model_validate(element).bbox is None


class TestIdentiteDuDocument:
    def test_chapitre_dans_un_livre(self):
        d = document_identity("htms/Practical MLOps/1. Introduction to MLOps.html")
        assert d.filename == "1. Introduction to MLOps"
        assert d.collection == "Practical MLOps"
        assert d.key == "htms/Practical MLOps/1. Introduction to MLOps"

    def test_fichier_a_plat_na_pas_de_livre(self):
        d = document_identity("pdfs/statisticsfordatascience.pdf")
        assert d.collection == ""
        assert d.filename == "statisticsfordatascience"

    def test_le_dossier_des_html_nettoyes_est_ignore(self):
        # Le chemin nettoye double l'arborescence : l'identite doit rester
        # celle du fichier d'origine.
        propre = document_identity(".cleaned/htms/Practical MLOps/Preface.html")
        origine = document_identity("htms/Practical MLOps/Preface.html")
        assert propre.key == origine.key
        assert propre.collection == origine.collection

    def test_deux_chapitres_homonymes_ont_des_cles_distinctes(self):
        # Sans le chemin, deux « Preface » de deux ouvrages produiraient les
        # memes identifiants d'elements.
        a = document_identity("htms/Livre A/Preface.html")
        b = document_identity("htms/Livre B/Preface.html")
        assert a.filename == b.filename
        assert a.key != b.key
        assert a.collection != b.collection

    def test_identifiants_delements_distincts_entre_homonymes(self):
        a = DocumentAccumulator(document_identity("htms/Livre A/Preface.html"))
        b = DocumentAccumulator(document_identity("htms/Livre B/Preface.html"))
        item = FakeItem(text="Un avant-propos identique dans les deux ouvrages.")
        assert a.add_item(item)["id"] != b.add_item(item)["id"]

    def test_note_obsidian_dans_son_dossier(self):
        d = document_identity("mds/Architectures de LLM/Kimi K3.md")
        assert d.collection == "Architectures de LLM"
        assert d.source_path == "mds/Architectures de LLM/Kimi K3.md"

    def test_sous_dossiers_profonds_gardent_l_ouvrage(self):
        d = document_identity("htms/Mon Livre/Partie 1/Chapitre 3.html")
        assert d.collection == "Mon Livre"

    def test_separateurs_windows_normalises(self):
        d = document_identity("htms\\Mon Livre\\Chapitre.html")
        assert d.collection == "Mon Livre"
        assert d.filename == "Chapitre"

    def test_nom_sans_extension(self):
        d = document_identity("mds/note")
        assert d.filename == "note"
        assert d.key == "mds/note"


class TestDocumentFacts:
    def test_defaults_are_neutral(self):
        """Un appel manuel a l'API ne connait ni langue ni empreinte."""
        facts = DocumentFacts(type_file="pdf")
        assert facts.total_pages == 0
        assert facts.language == ""
        assert facts.content_hash == ""

    def test_is_immutable(self):
        facts = DocumentFacts(type_file="html", language="fr")
        with pytest.raises(FrozenInstanceError):
            facts.language = "en"  # type: ignore[misc]


class TestAccumulatorHierarchy:
    """La hierarchie des titres, telle qu'elle sort de l'accumulateur."""

    def _accumulateur(self):
        return DocumentAccumulator(document_identity("htms/Livre/Chapitre.html"))

    def test_a_subtitle_is_attached_to_its_title(self):
        acc = self._accumulateur()
        titre = acc.add_item(FakeItem("title", "Chapitre 1"), heading_rank=0)
        sous = acc.add_item(FakeItem("section_header", "Une section"), heading_rank=1)
        assert sous["reference_id"] == titre["id"]
        assert titre["depth"] == 0
        assert sous["depth"] == 1

    def test_two_titles_of_same_rank_stay_siblings(self):
        acc = self._accumulateur()
        acc.add_item(FakeItem("title", "Chapitre 1"), heading_rank=0)
        second = acc.add_item(FakeItem("title", "Chapitre 2"), heading_rank=0)
        assert second["reference_id"] == ROOT_REFERENCE

    def test_a_paragraph_is_attached_to_the_deepest_open_title(self):
        acc = self._accumulateur()
        acc.add_item(FakeItem("title", "Chapitre 1"), heading_rank=0)
        sous = acc.add_item(FakeItem("section_header", "Une section"), heading_rank=1)
        para = acc.add_item(FakeItem("text", "Du texte."))
        assert para["reference_id"] == sous["id"]
        assert para["depth"] == 2

    def test_a_higher_rank_closes_the_previous_branch(self):
        acc = self._accumulateur()
        acc.add_item(FakeItem("title", "Chapitre 1"), heading_rank=0)
        acc.add_item(FakeItem("section_header", "Une section"), heading_rank=1)
        suivant = acc.add_item(FakeItem("title", "Chapitre 2"), heading_rank=0)
        assert suivant["reference_id"] == ROOT_REFERENCE
        assert suivant["depth"] == 0

    def test_without_rank_everything_stays_flat(self):
        """Comportement anterieur preserve quand la source ne donne aucun niveau."""
        acc = self._accumulateur()
        premier = acc.add_item(FakeItem("section_header", "A"))
        second = acc.add_item(FakeItem("section_header", "B"))
        assert premier["reference_id"] == ROOT_REFERENCE
        assert second["reference_id"] == ROOT_REFERENCE

    def test_an_element_before_any_title_is_attached_to_the_document(self):
        acc = self._accumulateur()
        para = acc.add_item(FakeItem("text", "Avant tout titre."))
        assert para["reference_id"] == ROOT_REFERENCE
        assert para["depth"] == 0


class TestUnElementQuiEnjambeUnePageLeDit:
    """Registre 4.22 : un element qui enjambe une page le dit par `page_no_end`.

    Mesure sur le PDF du corpus : les pages 8, 18, 19, 25, 68 et 69 sur 71 ne
    sont la page de debut d'aucun element, alors que PyMuPDF y lit 1 181 a
    1 472 caracteres. Le texte n'est pas perdu (72 316 caracteres ecrits sur
    72 326) : Docling fusionne un paragraphe qui enjambe une page et l'attribue
    a la page precedente. Une citation « page 7 » peut donc couvrir 7 et 8.

    L'information existe : mesure le 1er septembre 2026, conversion reelle du
    PDF du corpus en `page_range=(7, 8)`, l'item `#/texts/3` porte deux
    provenances, pages [7, 8].

    `page_no` garde son sens (la premiere page, ou la lecture commence) et
    `page_no_end` dit ou l'element finit. `compute_id` derive de
    `(cle, page_no, position_in_page, texte[:50])` : ne pas toucher a `page_no`
    laisse les `element_id` inchanges, donc le jeu de questions de l'agent
    reste valide (registre 4.28.e).
    """

    @staticmethod
    def _item(pages: list[int], texte: str = "Un paragraphe qui enjambe.") -> Any:
        """Un item Docling dont les provenances couvrent les pages donnees."""

        class Prov:
            def __init__(self, page: int) -> None:
                self.page_no = page
                self.bbox = None

        class Item:
            def __init__(self) -> None:
                self.prov = [Prov(page) for page in pages]
                self.text = texte
                self.self_ref = "#/texts/3"
                self.label = "text"

        return Item()

    def test_un_element_sur_une_seule_page_finit_sur_cette_page(self):
        accumulateur = DocumentAccumulator(IDENTITY)
        element = accumulateur.add_item(self._item([7]), None)

        assert element["page_no"] == 7
        assert element["page_no_end"] == 7, (
            "un element d'une seule page doit finir sur elle : sans cela, tout "
            "element paraitrait enjamber quelque chose"
        )

    def test_un_element_qui_enjambe_porte_sa_page_de_fin(self):
        accumulateur = DocumentAccumulator(IDENTITY)
        element = accumulateur.add_item(self._item([7, 8]), None)

        assert element["page_no"] == 7
        assert element["page_no_end"] == 8, (
            "la page 8 est dans les provenances de l'item et etait jetee : une "
            "citation « page 7 » couvre en realite 7 et 8"
        )

    def test_page_no_reste_la_premiere_page(self):
        """`page_no` reste la premiere page.

        La deplacer vers la derniere page, ou vers une moyenne, changerait
        `compute_id`, donc tous les `element_id` du corpus et le jeu de
        questions de l'agent. `page_no_end` s'ajoute sans y toucher.
        """
        accumulateur = DocumentAccumulator(IDENTITY)
        element = accumulateur.add_item(self._item([7, 8, 9]), None)

        assert element["page_no"] == 7, "page_no doit rester la page d'ENTREE"
        assert element["page_no_end"] == 9

    def test_l_identifiant_ne_bouge_pas_quand_un_element_enjambe(self):
        """L'identifiant d'un element qui enjambe est le meme que celui du meme
        element vu sur sa seule premiere page.

        C'est ce qui garde le jeu de questions de l'agent valide.
        """
        seul = DocumentAccumulator(IDENTITY).add_item(self._item([7]), None)
        enjambe = DocumentAccumulator(IDENTITY).add_item(self._item([7, 8]), None)

        assert seul["id"] == enjambe["id"]

    def test_un_element_sans_provenance_reste_en_page_un(self):
        """Le cas des formats non pagines, inchange."""

        class SansProv:
            prov: list[Any] = []
            text = "Du texte."
            self_ref = "#/texts/0"
            label = "text"

        element = DocumentAccumulator(IDENTITY).add_item(SansProv(), None)

        assert element["page_no"] == 1
        assert element["page_no_end"] == 1


class TestLesPagesCouvertesParAucunElement:
    """Le compteur de pages perdues : quelles pages n'ont aucun element ?

    Une page enjambee est couverte par un element qui commence avant elle.
    La perte reelle est une page que personne ne couvre, ni comme page
    d'entree ni comme page de fin : c'est elle qui est comptee.
    """

    # Le cas mesure du corpus, reduit : la page 8 n'a aucun element propre parce
    # qu'un element de la page 7 l'enjambe.
    ENJAMBE = [
        {"page_no": 6, "page_no_end": 6},
        {"page_no": 7, "page_no_end": 8},
        {"page_no": 9, "page_no_end": 9},
    ]

    def test_les_pages_enjambees_ne_comptent_plus_comme_perdues(self):
        assert pages_sans_element(self.ENJAMBE, total_pages=9, ecartees={1, 2, 3, 4, 5}) == []

    def test_une_page_que_personne_ne_couvre_est_signalee(self):
        """Contre-epreuve du precedent.

        Sans ce test, un compteur qui rend toujours la liste vide passerait le
        test ci-dessus. Ici la page 8 est reellement perdue : aucun element ne
        commence dessus, et aucun ne l'enjambe.
        """
        sans_enjambement = [
            {"page_no": 6, "page_no_end": 6},
            {"page_no": 7, "page_no_end": 7},
            {"page_no": 9, "page_no_end": 9},
        ]

        assert pages_sans_element(sans_enjambement, total_pages=9, ecartees={1, 2, 3, 4, 5}) == [8]

    def test_les_pages_ecartees_ne_sont_pas_comptees_comme_perdues(self):
        """Le front/back matter est ecarte volontairement : le compter comme une
        perte rendrait le compteur bruyant sur chaque PDF."""
        elements = [{"page_no": 3, "page_no_end": 3}]

        assert pages_sans_element(elements, total_pages=5, ecartees={1, 2, 4, 5}) == []

    def test_un_document_sans_element_signale_toutes_ses_pages(self):
        assert pages_sans_element([], total_pages=3) == [1, 2, 3]

    def test_un_element_dont_la_fin_precede_le_debut_ne_boucle_pas(self):
        """Le cas que le code naturel fait mal tourner : des provenances en
        desordre. `range(7, 6)` est vide, donc la page 7 elle-meme serait perdue.
        """
        elements = [{"page_no": 7, "page_no_end": 6}]

        assert 7 not in pages_sans_element(elements, total_pages=7)


class TestLAllerRetourDuSousRepertoireNettoye:
    """L'aller-retour entre copie nettoyee et identite du document (registre 4.29.a).

    Deux sites dependent du nom du sous-repertoire : l'asset `cleaned_html`
    y ecrit, et `document_identity` retire ce segment pour retrouver le chemin
    source. S'ils divergent, l'identite change sans erreur. Mesure le
    1er septembre 2026 avec un sous-repertoire `.propre`, sur le chemin
    nettoye de `htms/MLOps with Databricks/Preface.html` :

    ======================= ================================== =================
    ce qui est lu           avec le reglage a la constante      avec `.propre`
    ======================= ================================== =================
    `identity.key`          `htms/MLOps.../Preface`            `.propre/htms/...`
    `identity.collection`   `MLOps with Databricks`            `htms`
    `element_id`            `fab608f4eb`                       `9d6460cded`
    ======================= ================================== =================

    Les exigences 2 et 3 du contrat sont rompues sans erreur. Ces tests
    verifient l'aller-retour des deux cotes : le chemin que la production
    ecrit, et l'identite qu'elle en deduit.
    """

    RACINE = "/opt/dagster/app/Datas"
    SOURCE = "htms/MLOps with Databricks/Preface.html"

    def test_la_copie_nettoyee_rend_l_identite_du_document(self) -> None:
        """Une seconde derivation divergente fait echouer ce test.

        `_deduce_source_path` est la voie que suit un appel manuel a l'API, ou le
        pipeline ne passe pas `source_path` : le service part alors du chemin du
        fichier, qui est celui de la copie nettoyee.
        """
        from src.docling_service.extraction import _deduce_source_path

        ecrit = cleaned_path(self.RACINE, self.SOURCE)
        identite = document_identity(_deduce_source_path(ecrit))

        assert identite.key == "htms/MLOps with Databricks/Preface", (
            f"la copie nettoyee {ecrit} ne rend plus l'identite du document : "
            "ses element_id ont change, en silence (contrat, exigence 2)"
        )
        assert identite.collection == "MLOps with Databricks", (
            "l'OUVRAGE est perdu : une citation ne peut plus dire de quel livre "
            "elle vient (contrat, exigence 3)"
        )

    def test_l_element_id_est_le_meme_par_les_deux_chemins(self) -> None:
        """La consequence : le meme `element_id` par les deux chemins.

        Le test precedent compare des chaines ; celui-ci compare ce que le
        contrat designe. Un jeu de questions de l'agent nomme des `element_id` :
        deux chemins qui rendent deux identifiants pour le meme element rendent
        toute mesure historique incomparable.
        """
        from src.docling_service.extraction import _deduce_source_path

        par_la_source = compute_id(document_identity(self.SOURCE).key, 1, 0, "un texte")
        par_la_copie = compute_id(
            document_identity(_deduce_source_path(cleaned_path(self.RACINE, self.SOURCE))).key,
            1,
            0,
            "un texte",
        )

        assert par_la_source == par_la_copie, (
            f"le meme element rend {par_la_source} depuis la source et "
            f"{par_la_copie} depuis sa copie nettoyee"
        )

    def test_les_deux_derivations_partent_de_la_meme_constante(self) -> None:
        """`cleaned_path` passe bien par `cleaned_root`.

        Sans ce test, les deux fonctions pourraient porter deux litteraux, et
        les tests ci-dessus passeraient encore si `document_identity` lisait
        celui de `cleaned_path`.
        """
        assert cleaned_path(self.RACINE, self.SOURCE).is_relative_to(cleaned_root(self.RACINE))
        assert cleaned_root(self.RACINE).name == CLEANED_SUBDIR
