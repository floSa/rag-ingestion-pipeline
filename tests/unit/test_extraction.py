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

import logging
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
            pages_sans_element: Pages pour lesquelles le bouchon ne rend AUCUN
                element, ni comme page d'entree ni comme page de fin. C'est la
                seule facon de fabriquer une perte reelle, celle que le compteur
                du registre 4.22 existe pour crier.
            pages_ecartees: Pages que `_front_back_matter_pages` declare sautees.
        """
        trace: dict[str, Any] = {"persistes": [], "oublies": [], "convertis": []}

        # `fitz` est pose par `monkeypatch.setitem`, donc REVOQUE a la fin du
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


class TestLeCompteurDePagesPerduesEstGardeASonSiteDAppel:
    """Registre 4.22, ET LE MOTIF EXACT QUE J'AVAIS DEJA FERME AILLEURS.

    `pages_sans_element` est gardee en unitaire — `test_elements.py` couvre le
    calcul, l'enjambement, les pages ecartees, la plage vide. **Son SITE D'APPEL
    ne l'etait pas**, et c'est la que la perte se voit ou ne se voit pas : c'est
    `_extract_pdf` qui accumule la couverture lot par lot, qui appelle le
    compteur, qui crie, et qui rend `pages_without_element` dans son bilan — donc
    dans les metadonnees Dagster.

    C'est le motif que j'ai trouve et ferme pour la chaine d'images
    (`TestLaCorrespondancePositionnelleEstGardeeParUnRefus`) et laisse ouvert sur
    mon propre fil conducteur. *Mute le producteur, pas le consommateur* : le
    producteur du chiffre est cette boucle, pas la fonction pure.

    Le harnais existant pilote `_extract_pdf` avec des bouchons ; il gagne ici de
    quoi fabriquer un TROU — des pages pour lesquelles la conversion ne rend
    aucun element. Sans trou, le compteur est vrai a zero des deux cotes du
    defaut.
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
        """LE GARDE. Le bilan est ce que Dagster publie : c'est le chiffre qui sort.

        Trois pages muettes sur les dix du PDF bouchonne. Le compte annonce doit
        etre 3, et pas 0 — un run vert sur un corpus troue est exactement ce que
        ce lot ferme.
        """
        bilan = self._bilan(tmp_path, monkeypatch, pages_sans_element={2, 5, 9})

        assert bilan["pages_without_element"] == 3, (
            f"trois pages n'ont aucun element et le bilan en annonce "
            f"{bilan['pages_without_element']} : la perte ne sort pas du job"
        )

    def test_la_couverture_est_accumulee_sur_tout_le_document_et_pas_par_lot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La seconde moitie du garde, et elle porte sur `pages_couvertes.extend`.

        Les elements ne survivent pas a leur lot — ils sont persistes puis jetes —
        donc la couverture doit etre retenue lot par lot. Sans l'`extend`, le
        compteur ne voit RIEN de couvert et annonce toutes les pages perdues :
        un compteur qui crie sur un document sain, qu'on cesse d'ecouter.

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
        """LE TEMOIN, et sans lui un compteur toujours bavard passerait les deux.

        C'est aussi le cas du corpus reel : les six pages du PDF qui paraissaient
        vides (8, 18, 19, 25, 68, 69) sont ENJAMBEES, donc couvertes, donc ce
        compteur doit se taire dessus. Il ne parle que d'une perte reelle.
        """
        bilan = self._bilan(tmp_path, monkeypatch)

        assert bilan["pages_without_element"] == 0, (
            "aucune page n'est muette : le compteur ne doit rien annoncer"
        )

    def test_une_page_enjambee_n_est_pas_une_page_perdue_au_site_d_appel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le second temoin, sur le mecanisme meme du registre 4.22.

        Une page qu'aucun element ne prend pour page d'ENTREE mais qu'un element
        voisin couvre par sa page de FIN n'est pas perdue. C'est le cas des six
        pages du corpus, et c'est ce que `page_no_end` a rendu distinguable. Le
        garde unitaire le dit sur la fonction ; celui-ci le dit sur la boucle qui
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
            # Chaque element couvre sa page ET la suivante, et AUCUN element ne
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
        """Le troisieme temoin : le front/back matter est saute VOLONTAIREMENT.

        Le compter comme une perte rendrait le compteur bavard sur chaque PDF —
        et un compteur qu'on n'ecoute plus ne compte rien. Ce test verrouille que
        les pages ecartees traversent bien jusqu'au compteur depuis le site
        d'appel, `skipped` etant calcule la et nulle part ailleurs.
        """
        bilan = self._bilan(tmp_path, monkeypatch, pages_sans_element={2, 3}, pages_ecartees={2, 3})

        assert bilan["pages_skipped"] == 2
        assert bilan["pages_without_element"] == 0, (
            "les pages ecartees sont sautees volontairement : les compter "
            "perdues rend le compteur bavard"
        )


class TestLesUrlDImagesHtmlAtteignentLeGraphe:
    """Registre 3.5 : 199 images de capture HTML sans `minio_url` dans le graphe.

    `cleaning.py` reecrit `img src` avec l'URL MinIO ; `extraction.py` ne
    propageait cette URL que si `item.image.uri` commence par `http`. Cette
    description du code est exacte et TROMPEUSE comme cause : le test du prefixe
    n'est JAMAIS atteint, parce que `item.image` vaut `None`.

    Remesure de mes mains le 1er septembre 2026, conversion reelle de 4 chapitres
    nettoyes dans l'image d'extraction : `item.image` non `None` sur **0 / 24**.
    `item.source`, `item.references` et `item.meta` sont vides aussi — l'URL
    n'atterrit nulle part d'exploitable. Le registre est reproduit.

    **LA MESURE QUI DECIDE DE LA FORME DU CORRECTIF.** Le HTML nettoye porte les
    URL, dans l'ordre du document ; Docling rend ses `picture` dans le meme
    ordre. Mesure sur 4 chapitres : `img` en `http` = `picture` rendus, **4
    chapitres sur 4** — 4/4, 1/1, 9/9, 10/10. La correspondance est donc
    POSITIONNELLE, et c'est la seule voie qui reste.

    Une correspondance positionnelle est fragile par nature : elle est donc
    GARDEE par un refus. Si les deux comptes divergent, aucune URL n'est posee —
    une URL fausse sur une image est pire qu'une URL absente, parce que l'agent
    servirait l'illustration d'un autre passage sans qu'aucune erreur ne le dise.
    """

    HTML_NETTOYE = (
        "<html><body><h1>Chapitre</h1>"
        '<p>Avant.</p><img src="http://minio:9000/documents/images/html/livre/img_0000.png"/>'
        '<p>Milieu.</p><img src="http://minio:9000/documents/images/html/livre/img_0001.png"/>'
        "<p>Apres.</p></body></html>"
    )

    def test_les_url_sont_lues_dans_l_ordre_du_document(self, tmp_path: Path) -> None:
        chemin = tmp_path / "chapitre.html"
        chemin.write_text(self.HTML_NETTOYE, encoding="utf-8")

        assert extraction.html_image_urls(chemin) == [
            "http://minio:9000/documents/images/html/livre/img_0000.png",
            "http://minio:9000/documents/images/html/livre/img_0001.png",
        ]

    def test_les_src_qui_ne_sont_pas_des_url_sont_ignores(self, tmp_path: Path) -> None:
        """Une image restee en `data:` ou en chemin relatif n'a pas d'objet MinIO.

        La compter fausserait la correspondance positionnelle et decalerait
        toutes les URL suivantes d'un rang.
        """
        chemin = tmp_path / "chapitre.html"
        chemin.write_text(
            '<html><body><img src="data:image/png;base64,AAAA"/>'
            '<img src="http://minio:9000/documents/images/html/livre/img_0000.png"/>'
            '<img src="../images/local.png"/></body></html>',
            encoding="utf-8",
        )

        assert extraction.html_image_urls(chemin) == [
            "http://minio:9000/documents/images/html/livre/img_0000.png"
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


class TestLaCorrespondancePositionnelleEstGardeeParUnRefus:
    """Le garde qui rend la correspondance positionnelle defendable."""

    URLS = ["http://minio:9000/a.png", "http://minio:9000/b.png"]

    def test_les_url_sont_posees_dans_l_ordre_quand_les_comptes_concordent(self) -> None:
        elements = [
            {"label": "text", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
            {"label": "text", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 2
        assert [e["minio_url"] for e in elements] == ["", self.URLS[0], "", self.URLS[1]]

    def test_aucune_url_n_est_posee_quand_les_comptes_divergent(self) -> None:
        """LE GARDE, et c'est lui qui rend la methode defendable.

        Une URL fausse sur une image est PIRE qu'une URL absente : l'agent
        servirait l'illustration d'un autre passage, et rien ne le dirait. Devant
        un desaccord, on refuse plutot que de deviner.
        """
        elements = [
            {"label": "picture", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 0
        assert all(e["minio_url"] == "" for e in elements), (
            "trois images pour deux URL : poser les deux premieres attribuerait "
            "une illustration au mauvais passage"
        )

    def test_un_desaccord_est_journalise_avec_ses_deux_comptes(self, caplog) -> None:
        elements = [{"label": "picture", "minio_url": ""} for _ in range(3)]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        messages = [e.getMessage() for e in caplog.records]
        assert any("3" in m and "2" in m for m in messages), messages

    def test_le_chemin_nominal_ne_journalise_rien(self, caplog) -> None:
        """LE TEMOIN : une alerte a chaque chapitre rendrait la vraie invisible."""
        elements = [{"label": "picture", "minio_url": ""} for _ in range(2)]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert [e.getMessage() for e in caplog.records] == []

    def test_les_tables_ne_recoivent_pas_les_url_des_images(self) -> None:
        """Un `table` est un element VISUEL mais n'est pas une `<img>` du HTML.

        Le compter parmi les cibles decalerait toutes les URL, et la premiere
        image recevrait l'URL destinee a la table.
        """
        elements = [
            {"label": "table", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
            {"label": "picture", "minio_url": ""},
        ]
        posees = extraction.propager_les_url_dimages(elements, self.URLS, "chapitre")

        assert posees == 2
        assert elements[0]["minio_url"] == ""
        assert elements[1]["minio_url"] == self.URLS[0]

    def test_aucune_url_du_tout_ne_journalise_pas_et_ne_pose_rien(self, caplog) -> None:
        """Un chapitre sans image : le cas nominal de la moitie du corpus."""
        elements = [{"label": "text", "minio_url": ""}]

        with caplog.at_level(logging.WARNING, logger="src.docling_service.extraction"):
            assert extraction.propager_les_url_dimages(elements, [], "chapitre") == 0

        assert [e.getMessage() for e in caplog.records] == []


class TestLaCompositionEstGardee:
    """Les deux fonctions ci-dessus sont ATTEINTES par `_extract_flat`.

    **`mesure` : sans cette classe, retirer l'appel de `_extract_flat` laissait
    la suite ENTIEREMENT VERTE.** Les deux fonctions etaient gardees prises
    isolement, et la composition ne l'etait pas — c'est mot pour mot le defaut
    que l'audit du lot 3 a trouve sur `verify_contract.racine_de_chaque_element`,
    et le registre 4.4 le dit : « le garde asserte la COMPOSITION, et pas la
    fonction seule ».

    Une fonction pure qui propage des URL a l'air d'une commodite. Ce qui compte
    est qu'elle TOURNE sur le chemin du document.
    """

    HTML_NETTOYE = (
        "<html><body><h1>Chapitre</h1>"
        '<img src="http://minio:9000/documents/images/html/livre/img_0000.png"/>'
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
        assert images[0]["minio_url"] == (
            "http://minio:9000/documents/images/html/livre/img_0000.png"
        ), (
            "l'URL du HTML nettoye n'atteint pas le sommet Picture : c'est le "
            "registre 3.5, et les 199 images du corpus etaient dans ce cas"
        )

    def test_un_element_qui_n_est_pas_une_image_ne_recoit_pas_d_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN : une URL posee partout serait vraie du premier test."""
        elements = self._convertir(tmp_path, monkeypatch, ["text", "picture"])

        textes = [e for e in elements if e["label"] == "text"]
        assert textes and all(not e.get("minio_url") for e in textes), elements

    def test_un_desaccord_de_comptes_laisse_les_images_sans_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le refus est atteint lui aussi : deux images pour une seule URL."""
        elements = self._convertir(tmp_path, monkeypatch, ["picture", "picture"])

        images = [e for e in elements if e["label"] == "picture"]
        assert len(images) == 2
        assert all(not e.get("minio_url") for e in images), (
            "devant un desaccord, aucune URL ne doit etre posee : une URL fausse "
            "servirait l'illustration d'un autre passage"
        )
