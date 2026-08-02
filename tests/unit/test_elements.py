"""Tests unitaires pour la construction des elements de document.

Ces tests importent les fonctions reelles du service. La version precedente en
recopiait une replique dans le fichier de test — le code de production n'etait
donc pas couvert, et une divergence serait passee inapercue.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from src.docling_service.elements import (
    ROOT_REFERENCE,
    SECTION_LABELS,
    TAG_MAP,
    DocumentAccumulator,
    DocumentFacts,
    compute_id,
    document_identity,
    extract_bbox,
    item_label,
    item_text,
    tag_for_label,
)
from src.pipeline.schemas import DocumentElement


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
        # Le defaut de fond : sans le chemin, deux « Preface » de deux
        # ouvrages produisaient les memes identifiants d'elements.
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
