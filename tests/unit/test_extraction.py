"""Gardes de l'orchestration d'extraction.

Ce fichier n'existait pas, et pour la raison MECANIQUE que ce chantier connait :
``extraction.py`` importait ``docling`` au niveau du module, or ``docling`` n'est
pas dans le venv du depot — les deps lourdes vivent dans ``Dockerfile.docling``.
Aucun test ne pouvait donc importer le module. C'etait le sixieme et dernier
module dans ce cas, apres ``index_report`` (registre 3.4), ``verify_contract``
(4.4), ``verify_data`` (4.5), ``vectors`` (4.4) et ``nebula`` (4.28.d).
*Ce qu'un test n'importe pas, il ne teste pas.*

Ce qu'il garde, et que rien ne gardait :

- **registre 4.2** — un document est OUBLIE des deux stores avant d'etre
  reecrit. Sans cela, un texte modifie produit de nouveaux identifiants et les
  anciens survivent en orphelins. Le capteur declenchant sur ``mtime``, c'est le
  chemin NOMINAL qui cassait ;
- **registre 4.1** — un lot PDF en echec fait RETIRER le document partiel. Sans
  cela, la partition est rouge ET l'ouvrage est dans l'index, tronque, et
  ``verify_contract`` ne peut pas le voir : les ``element_id`` ecrits sont
  valides. C'est le pire des deux etats, parce qu'il ressemble a des stores
  vides.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from src.docling_service import extraction
from src.docling_service.elements import DocumentIdentity

# PyMuPDF n'est pas dans le venv du depot : le PDF est bouchonne. Seule sa
# PAGINATION compte ici — la boucle de lots et la decision de retrait sont
# celles du code livre.
PAGES_DU_PDF_BOUCHONNE = 10


class _DocumentFitz:
    """Document PyMuPDF bouchonne : une pagination, rien d'autre."""

    def __len__(self) -> int:
        return PAGES_DU_PDF_BOUCHONNE

    def __enter__(self) -> _DocumentFitz:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _FitzBouchonne:
    """Module `fitz` bouchonne."""

    @staticmethod
    def open(chemin: Any) -> _DocumentFitz:
        return _DocumentFitz()


@pytest.fixture
def fichier_html(tmp_path: Path) -> Path:
    """Un HTML de corpus, dans un arbre qui porte l'ouvrage comme parent."""
    chemin = tmp_path / "htms" / "MLOps with Databricks" / "Preface.html"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("<html><body><h1>Preface</h1><p>Du texte.</p></body></html>", "utf-8")
    return chemin


class TestUnDocumentEstOublieAvantDEtreReecrit:
    """Registre 4.2 : ``delete_document`` existait et n'avait AUCUN appelant."""

    def test_l_extraction_oublie_le_document_avant_de_convertir(
        self, tmp_path: Path, fichier_html: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oublies: list[DocumentIdentity] = []
        ordre: list[str] = []

        monkeypatch.setattr(extraction, "file_digest", lambda path: "empreinte")
        monkeypatch.setattr(extraction, "_already_ingested", lambda h, i: "")

        def oublier(identity: DocumentIdentity) -> None:
            oublies.append(identity)
            ordre.append("oubli")

        def convertir(*a: Any, **k: Any) -> dict[str, Any]:
            ordre.append("conversion")
            return {"elements": 1}

        monkeypatch.setattr(extraction.storage, "forget_document", oublier)
        monkeypatch.setattr(extraction, "_extract_flat", convertir)

        extraction.extract(fichier_html, source_path="htms/MLOps with Databricks/Preface.html")

        assert [identity.key for identity in oublies] == ["htms/MLOps with Databricks/Preface"], (
            oublies
        )
        assert ordre == ["oubli", "conversion"], (
            "l'oubli doit PRECEDER la conversion : purger apres avoir ecrit "
            "detruirait ce qu'on vient d'ecrire"
        )

    def test_un_doublon_exact_n_oublie_rien(
        self, tmp_path: Path, fichier_html: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN, et il porte l'ordre des deux controles.

        Un doublon exact sort avant la purge : reingerer un fichier INCHANGE ne
        doit rien detruire pour le reecrire a l'identique. Une purge posee avant
        le controle de doublon rendrait ce test rouge, et le chemin du doublon
        deviendrait une destruction suivie d'une non-reecriture — la perte
        silencieuse d'un document, par le geste qui existe pour l'eviter.
        """
        oublies: list[DocumentIdentity] = []

        monkeypatch.setattr(extraction, "file_digest", lambda path: "empreinte")
        monkeypatch.setattr(extraction, "_already_ingested", lambda h, i: "htms/autre/Preface.html")
        monkeypatch.setattr(
            extraction.storage, "forget_document", lambda identity: oublies.append(identity)
        )

        bilan = extraction.extract(
            fichier_html, source_path="htms/MLOps with Databricks/Preface.html"
        )

        assert oublies == [], "un doublon exact ne doit rien purger"
        assert bilan["duplicate_of"] == "htms/autre/Preface.html"

    def test_une_purge_impossible_empeche_la_reecriture(
        self, tmp_path: Path, fichier_html: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reecrire par-dessus une purge a moitie faite est exactement 4.2."""
        converti: list[str] = []

        monkeypatch.setattr(extraction, "file_digest", lambda path: "empreinte")
        monkeypatch.setattr(extraction, "_already_ingested", lambda h, i: "")

        def refuse(identity: DocumentIdentity) -> None:
            raise extraction.storage.PurgeIncompleteError("graphd injoignable")

        monkeypatch.setattr(extraction.storage, "forget_document", refuse)
        monkeypatch.setattr(extraction, "_extract_flat", lambda *a, **k: converti.append("x") or {})

        with pytest.raises(extraction.storage.PurgeIncompleteError):
            extraction.extract(fichier_html, source_path="htms/MLOps with Databricks/Preface.html")

        assert converti == [], "rien ne doit etre reecrit par-dessus une purge ratee"


class TestUnLotPdfEnEchecRetireLeDocumentPartiel:
    """Registre 4.1 : la partition rougissait, et l'ouvrage restait dans l'index.

    Le chemin est reconstitue au niveau de ``_extract_pdf`` : la conversion
    Docling et les stores sont bouchonnes, mais la BOUCLE DE LOTS et la decision
    de retrait sont celles du code livre.
    """

    @staticmethod
    def _monter(monkeypatch: pytest.MonkeyPatch, lots_qui_echouent: set[int]) -> dict[str, Any]:
        """Bouchonne autour de ``_extract_pdf`` et retient ce qui est demande."""
        trace: dict[str, Any] = {"persistes": [], "oublies": [], "convertis": []}

        # `fitz` est pose par `monkeypatch.setitem`, donc REVOQUE a la fin du
        # test : contrairement a un bouchon pose a la main dans `sys.modules`,
        # il ne survit pas et l'ordre des tests ne devient pas significatif.
        monkeypatch.setitem(sys.modules, "fitz", _FitzBouchonne())

        monkeypatch.setattr(extraction, "get_converter", lambda ocr=False: object())
        monkeypatch.setattr(extraction, "_pdf_font_profile", lambda *a, **k: (15.0, {}))
        monkeypatch.setattr(extraction, "_front_back_matter_pages", lambda *a, **k: set())
        monkeypatch.setattr(extraction, "_has_text_layer", lambda *a, **k: True)
        monkeypatch.setattr(extraction, "_detect_document_language", lambda *a, **k: "en")

        def convertir(
            converter: Any,
            pdf_path: Any,
            stem: str,
            document: Any,
            accumulator: Any,
            start_page: int,
            end_page: int,
            body_size: Any,
            size_ranks: Any,
        ) -> Any:
            trace["convertis"].append((start_page, end_page))
            if start_page in lots_qui_echouent:
                raise RuntimeError(f"page {start_page} illisible")
            # Un element par page du lot, avec ses deux pages : le bouchon doit
            # rendre la forme que la production rend, sans quoi le compteur de
            # pages perdues serait teste sur une forme inventee.
            elements = [
                {
                    "id": f"e{page:04d}",
                    "label": "text",
                    "page_no": page,
                    "page_no_end": page,
                }
                for page in range(start_page, end_page + 1)
            ]
            return elements, object(), (0, 0)

        monkeypatch.setattr(extraction, "_convert_batch", convertir)
        monkeypatch.setattr(
            extraction.storage,
            "persist",
            lambda elements, identity, facts, doc: trace["persistes"].append(identity.key) or 3,
        )
        monkeypatch.setattr(
            extraction.storage,
            "forget_document",
            lambda identity: trace["oublies"].append(identity.key),
        )
        return trace

    IDENTITE = DocumentIdentity(
        source_path="pdfs/livre.pdf", key="pdfs/livre", filename="livre", collection=""
    )

    def test_un_lot_en_echec_fait_retirer_le_document(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = self._monter(monkeypatch, lots_qui_echouent={6})
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        with pytest.raises(extraction.BatchExtractionError) as leve:
            extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

        assert trace["oublies"] == ["pdfs/livre"], (
            "le document partiel doit etre retire des stores : sinon la "
            "partition est rouge ET l'ouvrage est dans l'index, tronque"
        )
        assert trace["persistes"], "le cas voulu n'est pas atteint : aucun lot n'a ete ecrit"
        assert "retire" in str(leve.value)

    def test_les_lots_suivants_sont_quand_meme_tentes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN de la conception d'origine, qui reste juste.

        Une page illisible ne doit pas condamner les autres : on note l'echec, on
        continue, et le job echoue a la FIN avec la liste des pages manquantes.
        Sans ce temoin, un correctif qui leve au premier echec passerait le test
        precedent en changeant le comportement voulu.
        """
        trace = self._monter(monkeypatch, lots_qui_echouent={1})
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        with pytest.raises(extraction.BatchExtractionError):
            extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

        assert len(trace["convertis"]) > 1, trace["convertis"]

    def test_un_pdf_entierement_converti_n_est_pas_retire(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE SECOND TEMOIN, et c'est le plus important des trois.

        Sans lui, un retrait pose inconditionnellement — hors du `if
        failed_batches` — detruirait CHAQUE document juste apres l'avoir ecrit,
        et le test principal resterait vert. Ce serait la perte silencieuse de
        tout le corpus, par le geste qui existe pour l'empecher.
        """
        trace = self._monter(monkeypatch, lots_qui_echouent=set())
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        bilan = extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

        assert trace["oublies"] == [], "un document complet ne doit jamais etre retire"
        assert bilan["chunks"] > 0

    def test_une_purge_impossible_est_dite_dans_l_erreur_d_extraction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L'echec d'extraction reste la cause levee, et le second est chaine.

        Un `raise` depuis le bloc de purge masquerait les pages manquantes
        derriere une panne de store — et c'est la panne de store qu'on
        chercherait a corriger.
        """
        self._monter(monkeypatch, lots_qui_echouent={6})

        def refuse(identity: DocumentIdentity) -> None:
            raise extraction.storage.PurgeIncompleteError("graphd injoignable")

        monkeypatch.setattr(extraction.storage, "forget_document", refuse)
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        with pytest.raises(extraction.BatchExtractionError) as leve:
            extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

        message = str(leve.value)
        assert "page 6 illisible" in message, message
        assert "N'A PAS PU" in message and "graphd injoignable" in message, message
