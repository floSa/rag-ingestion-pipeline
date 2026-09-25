"""Tests de l'orchestration d'extraction.

``extraction.py`` importe ``docling`` localement (``get_converter``) : ``docling``
n'est pas dans le venv du depot, et un import de module rendrait le module
intestable cote hote (meme cas que ``index_report``, registre 3.4,
``verify_contract`` 4.4, ``verify_data`` 4.5, ``vectors`` 4.4 et ``nebula``
4.28.d).

Proprietes verifiees :

- **registre 4.2** — un document est purge des deux stores avant d'etre
  reecrit. Sans cela, un texte modifie produit de nouveaux identifiants et les
  anciens survivent en orphelins. Le capteur declenchant sur ``mtime``, c'est le
  cas nominal ;
- **registre 4.1** — un lot PDF en echec fait retirer le document partiel. Sans
  cela, la partition est en echec et l'ouvrage reste tronque dans l'index, ce
  que ``verify_contract`` ne peut pas voir : les ``element_id`` ecrits sont
  valides.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from src.docling_service import extraction, images
from src.docling_service.elements import DocumentIdentity

# PyMuPDF n'est pas dans le venv du depot : le PDF est bouchonne. Seule sa
# pagination compte ici — la boucle de lots et la decision de retrait sont
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
        """L'ordre des deux controles : le doublon avant la purge.

        Un doublon exact sort avant la purge : reingerer un fichier inchange ne
        doit rien detruire. Une purge placee avant le controle de doublon ferait
        echouer ce test : le doublon serait detruit sans etre reecrit.
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
    """Registre 4.1 : un lot en echec ne laisse pas l'ouvrage tronque dans l'index.

    Le chemin est reconstitue au niveau de ``_extract_pdf`` : la conversion
    Docling et les stores sont bouchonnes, mais la boucle de lots et la decision
    de retrait sont celles du code livre.
    """

    @staticmethod
    def _monter(
        monkeypatch: pytest.MonkeyPatch,
        lots_qui_echouent: set[int],
        pages_sans_element: set[int] | None = None,
        pages_ecartees: set[int] | None = None,
    ) -> dict[str, Any]:
        """Bouchonne autour de ``_extract_pdf`` et retient ce qui est demande.

        Args:
            monkeypatch: Le patcheur de pytest.
            lots_qui_echouent: Premieres pages des lots qui doivent lever.
            pages_sans_element: Pages pour lesquelles le bouchon ne rend aucun
                element, ni comme page d'entree ni comme page de fin. C'est la
                seule facon de fabriquer une perte reelle, celle que le compteur
                du registre 4.22 signale.
            pages_ecartees: Pages que `_front_back_matter_pages` declare sautees.
        """
        trace: dict[str, Any] = {"persistes": [], "oublies": [], "convertis": []}

        # `fitz` est pose par `monkeypatch.setitem`, donc revoque a la fin du
        # test : contrairement a un bouchon pose a la main dans `sys.modules`,
        # il ne survit pas et l'ordre des tests ne devient pas significatif.
        monkeypatch.setitem(sys.modules, "fitz", _FitzBouchonne())

        monkeypatch.setattr(extraction, "get_converter", lambda ocr=False: object())
        monkeypatch.setattr(extraction, "_pdf_font_profile", lambda *a, **k: (15.0, {}))
        monkeypatch.setattr(
            extraction, "_front_back_matter_pages", lambda *a, **k: set(pages_ecartees or set())
        )
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
            muettes = pages_sans_element or set()
            elements = [
                {
                    "id": f"e{page:04d}",
                    "label": "text",
                    "page_no": page,
                    "page_no_end": page,
                }
                for page in range(start_page, end_page + 1)
                if page not in muettes
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
        """Un lot en echec n'arrete pas la conversion des suivants.

        Une page illisible ne doit pas condamner les autres : l'echec est note,
        la conversion continue, et le job echoue a la fin avec la liste des
        pages manquantes. Sans ce test, une version qui leverait au premier echec
        passerait le test precedent.
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
        """Contre-epreuve principale : un PDF sans echec n'est pas retire.

        Sans ce test, un retrait inconditionnel (hors du `if failed_batches`)
        detruirait chaque document juste apres l'avoir ecrit, et le test
        principal passerait encore.
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
        derriere une panne de store, qui deviendrait la seule piste.
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


class TestLeCompteurDePagesPerduesEstGardeASonSiteDAppel:
    """Registre 4.22 : le compteur de pages perdues, a son site d'appel.

    `test_elements.py` couvre `pages_sans_element` seule (calcul, enjambement,
    pages ecartees, plage vide). Ces tests couvrent `_extract_pdf`, qui
    accumule la couverture lot par lot, appelle le compteur, journalise et rend
    `pages_without_element` dans son bilan, donc dans les metadonnees Dagster.
    C'est cette boucle qui produit le chiffre, pas la fonction pure.

    Le harnais pilote `_extract_pdf` avec des bouchons, et sait fabriquer un
    trou : des pages pour lesquelles la conversion ne rend aucun element. Sans
    trou, le compteur vaudrait zero que le code soit juste ou non.
    """

    IDENTITE = TestUnLotPdfEnEchecRetireLeDocumentPartiel.IDENTITE

    def _bilan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        pages_sans_element: set[int] | None = None,
        pages_ecartees: set[int] | None = None,
    ) -> dict[str, Any]:
        TestUnLotPdfEnEchecRetireLeDocumentPartiel._monter(
            monkeypatch,
            lots_qui_echouent=set(),
            pages_sans_element=pages_sans_element,
            pages_ecartees=pages_ecartees,
        )
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        return extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

    def test_les_pages_sans_aucun_element_sont_comptees_dans_le_bilan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le bilan est ce que Dagster publie : il doit compter les pages perdues.

        Trois pages muettes sur les dix du PDF bouchonne : le compte annonce doit
        etre 3, et pas 0.
        """
        bilan = self._bilan(tmp_path, monkeypatch, pages_sans_element={2, 5, 9})

        assert bilan["pages_without_element"] == 3, (
            f"trois pages n'ont aucun element et le bilan en annonce "
            f"{bilan['pages_without_element']} : la perte ne sort pas du job"
        )

    def test_la_couverture_est_accumulee_sur_tout_le_document_et_pas_par_lot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La couverture est accumulee lot par lot (`pages_couvertes.extend`).

        Les elements ne survivent pas a leur lot (ils sont persistes puis jetes),
        donc la couverture doit etre retenue lot par lot. Sans l'`extend`, le
        compteur ne verrait rien de couvert et annoncerait toutes les pages
        perdues sur un document sain.

        Le PDF bouchonne fait 10 pages et les lots en font moins : le document
        traverse donc plusieurs lots, et c'est ce que ce test exige d'abord.
        """
        bilan = self._bilan(tmp_path, monkeypatch, pages_sans_element={4})

        assert bilan["pages"] == PAGES_DU_PDF_BOUCHONNE
        assert bilan["pages_without_element"] == 1, (
            f"une seule page est muette, le bilan en annonce "
            f"{bilan['pages_without_element']} : la couverture des lots "
            f"precedents est perdue a chaque lot"
        )

    def test_un_document_entierement_couvert_n_annonce_aucune_perte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contre-epreuve : un compteur qui signalerait toujours passerait les deux precedents.

        C'est aussi le cas du corpus reel : les six pages du PDF sans element de
        debut (8, 18, 19, 25, 68, 69) sont enjambees, donc couvertes, et ne sont
        pas signalees.
        """
        bilan = self._bilan(tmp_path, monkeypatch)

        assert bilan["pages_without_element"] == 0, (
            "aucune page n'est muette : le compteur ne doit rien annoncer"
        )

    def test_une_page_enjambee_n_est_pas_une_page_perdue_au_site_d_appel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une page enjambee n'est pas perdue, verifie sur la boucle d'accumulation.

        Une page qu'aucun element ne prend pour page d'entree mais qu'un element
        voisin couvre par sa page de fin n'est pas perdue (registre 4.22). Le test
        unitaire le verifie sur la fonction ; celui-ci sur la boucle qui
        accumule, ou l'enjambement doit traverser la frontiere des lots.
        """
        TestUnLotPdfEnEchecRetireLeDocumentPartiel._monter(monkeypatch, lots_qui_echouent=set())

        def enjambe(
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
            # Chaque element couvre sa page et la suivante, et aucun element ne
            # prend les pages paires pour page d'entree.
            elements = [
                {
                    "id": f"e{page:04d}",
                    "label": "text",
                    "page_no": page,
                    "page_no_end": page + 1,
                }
                for page in range(start_page, end_page + 1)
                if page % 2 == 1
            ]
            return elements, object(), (0, 0)

        monkeypatch.setattr(extraction, "_convert_batch", enjambe)
        pdf = tmp_path / "livre.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        bilan = extraction._extract_pdf(pdf, self.IDENTITE, "empreinte", lambda **k: None)

        assert bilan["pages_without_element"] == 0, (
            "les pages paires sont couvertes par le `page_no_end` de leur "
            "voisine : les compter perdues rendrait le compteur bavard sur "
            "chaque PDF"
        )
        assert bilan["pages_spanned"] > 0, (
            "le cas voulu n'est pas atteint : aucun element n'enjambe"
        )

    def test_une_page_ecartee_volontairement_n_est_pas_comptee_perdue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le front/back matter, saute volontairement, n'est pas compte comme perdu.

        Le compter rendrait le compteur bruyant sur chaque PDF. Ce test verifie
        que les pages ecartees parviennent au compteur depuis le site d'appel,
        `skipped` etant calcule la et nulle part ailleurs.
        """
        bilan = self._bilan(tmp_path, monkeypatch, pages_sans_element={2, 3}, pages_ecartees={2, 3})

        assert bilan["pages_skipped"] == 2
        assert bilan["pages_without_element"] == 0, (
            "les pages ecartees sont sautees volontairement : les compter "
            "perdues rend le compteur bavard"
        )


class TestLesUrlDImagesHtmlAtteignentLeGraphe:
    """Registre 3.5 : les URL des images de capture HTML atteignent le graphe.

    `cleaning.py` reecrit `img src` avec l'adresse de l'objet, mais Docling ne
    la rend pas. Mesure le 1er septembre 2026, conversion reelle de 4 chapitres
    nettoyes dans l'image d'extraction : `item.image` vaut `None` sur les 24
    `picture`, et `item.source`, `item.references` et `item.meta` sont vides.

    Le HTML nettoye porte les URL dans l'ordre du document, et Docling rend ses
    `picture` dans le meme ordre. Mesure sur 4 chapitres : `img` en `http` =
    `picture` rendus, 4 fois sur 4 (4/4, 1/1, 9/9, 10/10). La correspondance
    est donc positionnelle.

    Elle est fragile : si les deux comptes divergent, aucune URL n'est posee.
    Une URL fausse est pire qu'une URL absente : l'agent servirait
    l'illustration d'un autre passage sans erreur.
    """

    HTML_NETTOYE = (
        "<html><body><h1>Chapitre</h1>"
        '<p>Avant.</p><img src="http://stockage-de-test:8333/documents/images/html/livre/img_0000.png"/>'
        '<p>Milieu.</p><img src="http://stockage-de-test:8333/documents/images/html/livre/img_0001.png"/>'
        "<p>Apres.</p></body></html>"
    )

    def test_les_url_sont_lues_dans_l_ordre_du_document(self, tmp_path: Path) -> None:
        chemin = tmp_path / "chapitre.html"
        chemin.write_text(self.HTML_NETTOYE, encoding="utf-8")

        assert extraction.html_image_urls(chemin) == [
            "http://stockage-de-test:8333/documents/images/html/livre/img_0000.png",
            "http://stockage-de-test:8333/documents/images/html/livre/img_0001.png",
        ]

    def test_les_src_qui_ne_sont_pas_des_url_sont_ignores(self, tmp_path: Path) -> None:
        """Une image en `data:` ou en chemin relatif n'a aucun objet dans le bucket.

        La compter fausserait la correspondance positionnelle et decalerait
        toutes les URL suivantes d'un rang.
        """
        chemin = tmp_path / "chapitre.html"
        chemin.write_text(
            '<html><body><img src="data:image/png;base64,AAAA"/>'
            '<img src="http://stockage-de-test:8333/documents/images/html/livre/img_0000.png"/>'
            '<img src="../images/local.png"/></body></html>',
            encoding="utf-8",
        )

        assert extraction.html_image_urls(chemin) == [
            "http://stockage-de-test:8333/documents/images/html/livre/img_0000.png"
        ]

    def test_un_html_sans_image_rend_une_liste_vide(self, tmp_path: Path) -> None:
        chemin = tmp_path / "chapitre.html"
        chemin.write_text("<html><body><p>Rien.</p></body></html>", encoding="utf-8")

        assert extraction.html_image_urls(chemin) == []

    def test_un_fichier_illisible_rend_une_liste_vide_et_ne_leve_pas(self, tmp_path: Path) -> None:
        """La lecture des URL est un confort : elle ne doit jamais empecher une
        ingestion. Une image sans URL est un defaut connu et compte ; un document
        non ingere est une perte."""
        assert extraction.html_image_urls(tmp_path / "absent.html") == []


class TestLAdresseEtLaCleSontPoseesEnsemble:
    """Le contrat publie deux champs de media, et ils vont par paire.

    Un element dont seule l'adresse serait renseignee est a demi ecrit, et rien
    ne le rattrape : le graphe et ChromaDB sont ecrits une fois par ingestion.
    Les trois chemins d'image (crop PDF, balise Markdown, correspondance
    positionnelle du HTML) passent par `poser_le_media`, seul site de la
    paire.
    """

    def test_l_adresse_et_la_cle_sont_posees_du_meme_geste(self) -> None:
        element: dict[str, Any] = {"label": "picture"}

        extraction.poser_le_media(element, "http://stockage-de-test:8333/documents/images/l/a.png")

        assert element["media_url"] == "http://stockage-de-test:8333/documents/images/l/a.png"
        assert element["object_key"] == "images/l/a.png"

    def test_la_cle_est_celle_qui_a_ete_passee_a_put_object(self) -> None:
        """Elle est derivee de l'adresse, et `images.object_key` en est l'inverse exact.

        Elle ne peut donc pas diverger de ce que le televersement a ecrit : les
        deux valeurs viennent de la meme chaine.
        """
        cle = "images/htms/Un_livre/Preface/img_0000.png"

        element: dict[str, Any] = {"label": "picture"}
        extraction.poser_le_media(element, images.object_url(cle))

        assert element["object_key"] == cle

    def test_un_televersement_en_echec_ne_pose_ni_adresse_ni_cle(self) -> None:
        """Un televersement en echec ne laisse pas de cle vers un objet inexistant.

        `crop_and_upload` rend None sur une zone vide ou un envoi refuse. La
        cle doit suivre l'adresse dans son absence, sans quoi le graphe
        porterait l'identite d'un objet qui n'existe pas.
        """
        element: dict[str, Any] = {"label": "picture"}

        extraction.poser_le_media(element, None)

        assert element["media_url"] is None
        assert element["object_key"] == ""


class TestLaCorrespondancePositionnelleEstGardeeParUnRefus:
    """Le garde qui rend la correspondance positionnelle defendable."""

    URLS = [
        "http://stockage-de-test:8333/documents/a.png",
        "http://stockage-de-test:8333/documents/b.png",
    ]

    def test_les_url_sont_posees_dans_l_ordre_quand_les_comptes_concordent(self) -> None:
        elements = [
            {"label": "text", "media_url": ""},
            {"label": "picture", "media_url": ""},
            {"label": "text", "media_url": ""},
            {"label": "picture", "media_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 2
        assert [e["media_url"] for e in elements] == ["", self.URLS[0], "", self.URLS[1]]

    def test_aucune_url_n_est_posee_quand_les_comptes_divergent(self) -> None:
        """Des comptes divergents ne posent aucune URL.

        Une URL fausse est pire qu'une URL absente : l'agent servirait
        l'illustration d'un autre passage sans que rien ne le dise. En cas de
        desaccord, la fonction refuse plutot que de deviner.
        """
        elements = [
            {"label": "picture", "media_url": ""},
            {"label": "picture", "media_url": ""},
            {"label": "picture", "media_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 0
        assert all(e["media_url"] == "" for e in elements), (
            "trois images pour deux URL : poser les deux premieres attribuerait "
            "une illustration au mauvais passage"
        )

    def test_un_desaccord_est_journalise_avec_ses_deux_comptes(self, caplog) -> None:
        elements = [{"label": "picture", "media_url": ""} for _ in range(3)]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        messages = [e.getMessage() for e in caplog.records]
        assert any("3" in m and "2" in m for m in messages), messages

    def test_le_chemin_nominal_ne_journalise_rien(self, caplog) -> None:
        """Contre-epreuve : une alerte a chaque chapitre rendrait la vraie invisible."""
        elements = [{"label": "picture", "media_url": ""} for _ in range(2)]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert [e.getMessage() for e in caplog.records] == []

    def test_les_tables_ne_recoivent_pas_les_url_des_images(self) -> None:
        """Un `table` est un element visuel mais n'est pas une `<img>` du HTML.

        Le compter parmi les cibles decalerait toutes les URL, et la premiere
        image recevrait l'URL destinee a la table.
        """
        elements = [
            {"label": "table", "media_url": ""},
            {"label": "picture", "media_url": ""},
            {"label": "picture", "media_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 2
        assert elements[0]["media_url"] == ""
        assert elements[1]["media_url"] == self.URLS[0]

    def test_aucune_url_du_tout_ne_journalise_pas_et_ne_pose_rien(self, caplog) -> None:
        """Un chapitre sans image : le cas nominal de la moitie du corpus."""
        elements = [{"label": "text", "media_url": ""}]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            assert extraction.propager_les_url_dimages(elements, [], "chapitre") == 0

        assert [e.getMessage() for e in caplog.records] == []


class TestLaCompositionEstGardee:
    """Les deux fonctions ci-dessus sont appelees par `_extract_flat`.

    Tester les fonctions isolement ne suffit pas : ces tests verifient la
    composition, c'est-a-dire qu'elles tournent sur le chemin du document
    (registre 4.4).
    """

    HTML_NETTOYE = (
        "<html><body><h1>Chapitre</h1>"
        '<img src="http://stockage-de-test:8333/documents/images/html/livre/img_0000.png"/>'
        "</body></html>"
    )

    def _convertir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Fait tourner `_extract_flat` sur un HTML nettoye, stores bouchonnes."""
        chemin = tmp_path / "chapitre.html"
        chemin.write_text(self.HTML_NETTOYE, encoding="utf-8")

        class Item:
            def __init__(self, label: str) -> None:
                self.label = label
                self.text = f"contenu {label}"
                self.self_ref = f"#/texts/{label}"
                self.prov: list[Any] = []

        class Document:
            def iterate_items(self, **_: Any) -> Any:
                return [(Item(label), 0) for label in labels]

        monkeypatch.setattr(
            extraction,
            "get_converter",
            lambda ocr=False: type(
                "C", (), {"convert": lambda s, p: type("R", (), {"document": Document()})()}
            )(),
        )
        monkeypatch.setattr(extraction.ranking, "flat_rank", lambda item, doc: None)
        monkeypatch.setattr(extraction, "_detect_document_language", lambda *a, **k: "en")

        ecrits: list[list[dict[str, Any]]] = []
        monkeypatch.setattr(
            extraction.storage,
            "persist",
            lambda elements, identity, facts, doc: ecrits.append(list(elements)) or 1,
        )

        identity = extraction.document_identity("htms/livre/chapitre.html")
        extraction._extract_flat(chemin, identity, "html", "empreinte", lambda **k: None)
        assert ecrits, "le cas voulu n'est pas atteint : rien n'a ete persiste"
        return ecrits[0]

    def test_l_url_atteint_l_element_persiste(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elements = self._convertir(tmp_path, monkeypatch, ["text", "picture"])

        images = [e for e in elements if e["label"] == "picture"]
        assert images, elements
        assert images[0]["media_url"] == (
            "http://stockage-de-test:8333/documents/images/html/livre/img_0000.png"
        ), (
            "l'URL du HTML nettoye n'atteint pas le sommet Picture : c'est le "
            "registre 3.5, et les 199 images du corpus etaient dans ce cas"
        )

    def test_la_cle_de_l_objet_atteint_elle_aussi_l_element_persiste(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le contrat publie la cle, et c'est sur ce chemin-ci qu'elle est derivee.

        L'adresse du HTML nettoye est lue, pas construite : c'est le seul des
        trois chemins ou la cle ne vient pas du televersement lui-meme. Si elle
        devait manquer quelque part, ce serait ici.
        """
        elements = self._convertir(tmp_path, monkeypatch, ["text", "picture"])

        images_persistees = [e for e in elements if e["label"] == "picture"]
        assert images_persistees, elements
        assert images_persistees[0]["object_key"] == "images/html/livre/img_0000.png"

    def test_un_element_qui_n_est_pas_une_image_ne_recoit_pas_d_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contre-epreuve : une URL posee partout passerait le premier test."""
        elements = self._convertir(tmp_path, monkeypatch, ["text", "picture"])

        textes = [e for e in elements if e["label"] == "text"]
        assert textes and all(not e.get("media_url") for e in textes), elements

    def test_un_desaccord_de_comptes_laisse_les_images_sans_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le refus est atteint lui aussi : deux images pour une seule URL."""
        elements = self._convertir(tmp_path, monkeypatch, ["picture", "picture"])

        images = [e for e in elements if e["label"] == "picture"]
        assert len(images) == 2
        assert all(not e.get("media_url") for e in images), (
            "devant un desaccord, aucune URL ne doit etre posee : une URL fausse "
            "servirait l'illustration d'un autre passage"
        )
