"""Gardes de l'ecriture du graphe.

Ce fichier n'existait pas, et pour une raison MECANIQUE : ``nebula.py``
importait ``nebula3`` au niveau du module, or ``nebula3`` n'est pas dans le venv
du depot — les dependances lourdes d'extraction vivent dans
``Dockerfile.docling``. Aucun test ne pouvait donc importer le module, et *ce
qu'un test n'importe pas, il ne teste pas*. C'etait le cinquieme et dernier
module dans ce cas, apres ``index_report`` (registre 3.4), ``verify_contract``
(4.4), ``verify_data`` (4.5) et ``vectors`` (4.4).

Le garde central : **la CLE du document, et non son nom de fichier.**
``document_vid`` recoit ``identity.key`` de ses trois appelants, et le remplacer
par ``identity.filename`` laissait la suite entierement verte (registre 4.28.d)
— alors que cela ferait collisionner les deux ``Preface.html`` du corpus sur un
seul sommet, c'est-a-dire la perte silencieuse d'un document entier et la
violation directe de l'exigence 3 du contrat.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.docling_service.elements import DocumentFacts, DocumentIdentity
from src.docling_service.nebula import NebulaWriter
from src.docling_service.ngql import document_vid

# Les deux vrais chemins du corpus qui portent le meme nom de fichier. C'est le
# cas d'ecole que l'exigence 3 du contrat cite comme sa preuve.
PREFACE_MLOPS = DocumentIdentity(
    source_path="htms/MLOps with Databricks/Preface.html",
    key="htms/MLOps with Databricks/Preface",
    filename="Preface",
    collection="MLOps with Databricks",
)
PREFACE_PRACTICAL = DocumentIdentity(
    source_path="htms/Practical MLflow for Generative AI on Databricks/Preface.html",
    key="htms/Practical MLflow for Generative AI on Databricks/Preface",
    filename="Preface",
    collection="Practical MLflow for Generative AI on Databricks",
)
FACTS = DocumentFacts(type_file="html", total_pages=1, language="en", content_hash="abc")

ELEMENTS: list[dict[str, Any]] = [
    {
        "id": "0123456789",
        "label": "text",
        "page_no": 1,
        "text": "Un paragraphe.",
        "minio_url": "",
        "depth": 1,
        "order": 0,
        "reference_id": "DOC",
    }
]


class ResultatVide:
    """Resultat nGQL en succes et vide."""

    def is_succeeded(self) -> bool:
        return True

    def is_empty(self) -> bool:
        return True

    def error_msg(self) -> str:
        return ""

    def rows(self) -> list[Any]:
        return []


class SessionEspionne:
    """Session NebulaGraph bouchonnee qui retient les requetes qu'on lui passe."""

    def __init__(self) -> None:
        self.requetes: list[str] = []

    def execute(self, requete: str) -> ResultatVide:
        self.requetes.append(requete)
        return ResultatVide()

    def release(self) -> None:
        pass


class PoolEspion:
    """Pool bouchonne : retient les identifiants recus par ``get_session``."""

    def __init__(self) -> None:
        self.session = SessionEspionne()
        self.identifiants: list[tuple[Any, ...]] = []

    def get_session(self, *args: Any) -> SessionEspionne:
        self.identifiants.append(args)
        return self.session


def writer_espionne(monkeypatch: pytest.MonkeyPatch) -> tuple[NebulaWriter, PoolEspion]:
    """Un ``NebulaWriter`` dont le pool est bouchonne — aucun graphd requis."""
    pool = PoolEspion()
    writer = NebulaWriter()
    monkeypatch.setattr(writer, "_get_pool", lambda: pool)
    return writer, pool


def insertion_du_document(pool: PoolEspion) -> str:
    """La requete qui insere le sommet ``Document``.

    Leve si elle n'existe pas : un test qui choisit lui-meme son cas doit
    prouver qu'il l'a atteint.
    """
    insertions = [r for r in pool.session.requetes if "INSERT VERTEX Document" in r]
    assert insertions, f"aucune insertion du sommet Document parmi {pool.session.requetes!r}"
    return insertions[0]


class TestLeSommetDocumentPorteLaCleEtNonLeNomDeFichier:
    """Le garde de l'exigence 3 : ``source_path`` est l'identite, jamais ``filename``.

    Ces trois tests rougissent a la mutation
    ``document_vid(identity.key)`` -> ``document_vid(identity.filename)`` dans
    ``write_elements``, qui laissait la suite entierement verte avant ce fichier.
    """

    def test_l_identifiant_du_sommet_derive_de_la_cle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        writer, pool = writer_espionne(monkeypatch)
        writer.write_elements(ELEMENTS, PREFACE_MLOPS, FACTS)

        assert document_vid(PREFACE_MLOPS.key) in insertion_du_document(pool)

    def test_le_nom_de_fichier_seul_ne_sert_jamais_d_identifiant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le temoin du precedent.

        Sans lui, un identifiant qui porterait la cle ET le nom de fichier
        passerait le test ci-dessus.
        """
        writer, pool = writer_espionne(monkeypatch)
        writer.write_elements(ELEMENTS, PREFACE_MLOPS, FACTS)

        interdit = document_vid(PREFACE_MLOPS.filename)
        assert interdit not in insertion_du_document(pool), (
            f"{interdit!r} est l'identifiant que produirait identity.filename : "
            "les deux Preface.html du corpus fusionneraient sur un seul sommet"
        )

    def test_les_deux_preface_du_corpus_ne_collisionnent_pas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le cas d'ecole de l'exigence 3, sur les deux vrais chemins du corpus."""
        insertions = []
        for identity in (PREFACE_MLOPS, PREFACE_PRACTICAL):
            writer, pool = writer_espionne(monkeypatch)
            writer.write_elements(ELEMENTS, identity, FACTS)
            insertions.append(insertion_du_document(pool))

        assert insertions[0] != insertions[1], (
            "les deux Preface.html ecrivent le meme sommet Document : "
            "un document entier est perdu en silence"
        )

    def test_les_aretes_partent_du_meme_sommet_que_l_insertion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le sommet insere et le parent des aretes sont le MEME identifiant.

        Deux derivations independantes de l'identifiant pourraient diverger :
        le document serait insere sous une cle et ses elements rattaches sous
        une autre, laissant un sommet Document sans enfant et des elements
        orphelins — sans qu'aucune requete n'echoue.
        """
        writer, pool = writer_espionne(monkeypatch)
        writer.write_elements(ELEMENTS, PREFACE_MLOPS, FACTS)

        aretes = [r for r in pool.session.requetes if "INSERT EDGE PARENT_OF" in r]
        assert aretes, "aucune arete PARENT_OF emise"
        assert document_vid(PREFACE_MLOPS.key) in aretes[0]


class TestLesIdentifiantsDuGrapheViennentDesReglages:
    """Registre 4.3 : ``get_session("root", "nebula")`` etait ecrit en dur.

    ``NEBULA_USER`` et ``NEBULA_PASSWORD`` existent dans ``.env.example`` et
    n'etaient exposes par AUCUN settings : le ``.env`` mentait sur ce qui est
    reellement lu. Changer le mot de passe du graphd rendait le service
    inutilisable sans qu'aucun reglage ne l'explique.
    """

    def test_la_session_recoit_les_identifiants_des_reglages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.docling_service.settings import get_settings

        monkeypatch.setenv("NEBULA_USER", "utilisateur_du_env")
        monkeypatch.setenv("NEBULA_PASSWORD", "phrase_du_env")  # pragma: allowlist secret
        get_settings.cache_clear()
        try:
            writer, pool = writer_espionne(monkeypatch)
            writer.write_elements(ELEMENTS, PREFACE_MLOPS, FACTS)
            assert pool.identifiants == [("utilisateur_du_env", "phrase_du_env")]
        finally:
            get_settings.cache_clear()

    def test_les_valeurs_par_defaut_restent_celles_de_la_pile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN. Sans variable, les defauts historiques valent toujours.

        Sans lui, exposer les reglages avec de mauvais defauts casserait tout
        poste dont le ``.env`` ne les declare pas — et le test ci-dessus
        resterait vert, puisqu'il fournit les deux variables.
        """
        from src.docling_service.settings import get_settings

        monkeypatch.delenv("NEBULA_USER", raising=False)
        monkeypatch.delenv("NEBULA_PASSWORD", raising=False)
        get_settings.cache_clear()
        try:
            writer, pool = writer_espionne(monkeypatch)
            writer.write_elements(ELEMENTS, PREFACE_MLOPS, FACTS)
            assert pool.identifiants == [("root", "nebula")]
        finally:
            get_settings.cache_clear()


class TestLaPurgeDUnDocumentViseSaCle:
    """``delete_document`` derive lui aussi son identifiant de la cle."""

    def test_delete_document_vise_le_sommet_de_la_cle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        writer, pool = writer_espionne(monkeypatch)
        writer.delete_document(PREFACE_MLOPS.key)

        suppressions = [r for r in pool.session.requetes if "DELETE VERTEX" in r]
        assert len(suppressions) == 1, f"attendu une seule suppression, vu {suppressions!r}"
        assert "WITH EDGE" in suppressions[0], (
            "sans WITH EDGE, les aretes du document survivent a la suppression "
            "de ses sommets et le graphe garde des references mortes"
        )
        assert document_vid(PREFACE_MLOPS.key) in suppressions[0]
        assert document_vid(PREFACE_MLOPS.filename) not in suppressions[0]
