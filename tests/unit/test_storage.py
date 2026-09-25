"""La purge d'un document, sur ses deux chemins d'appel.

Une seule mecanique, `storage.forget_document`, pour deux cas :

- **registre 4.2** — sans purge, une reingestion d'un document modifie
  laisserait des orphelins. Les identifiants derivent du texte, donc un texte
  modifie produit de nouveaux identifiants et les anciens survivraient dans les
  deux stores. Le capteur Dagster declenchant sur `mtime`, c'est le cas nominal ;
- **registre 4.1** — sans purge, un lot PDF en echec laisserait un document
  partiel dans les stores : partition en echec, ouvrage tronque dans l'index.
  `verify_contract` ne peut pas le voir : les `element_id` ecrits sont valides.

L'invariant que ces deux appels installent : **un document est entierement dans
les stores, ou pas du tout.**
"""

from __future__ import annotations

from typing import Any

import pytest

from src.docling_service import storage
from src.docling_service.elements import DocumentIdentity

IDENTITE = DocumentIdentity(
    source_path="htms/MLOps with Databricks/Preface.html",
    key="htms/MLOps with Databricks/Preface",
    filename="Preface",
    collection="MLOps with Databricks",
)


class EcrivainEspion:
    """`NebulaWriter` bouchonne : retient les cles purgees."""

    def __init__(self) -> None:
        self.purges: list[str] = []

    def delete_document(self, document_key: str) -> None:
        self.purges.append(document_key)


@pytest.fixture
def stores_espionnes(monkeypatch: pytest.MonkeyPatch) -> tuple[EcrivainEspion, list[Any]]:
    """Bouchonne les deux stores et retient ce qui leur est demande."""
    ecrivain = EcrivainEspion()
    purges_vectorielles: list[Any] = []

    monkeypatch.setattr(storage, "get_writer", lambda: ecrivain)
    monkeypatch.setattr(
        storage.vectors,
        "delete_document",
        lambda identity: purges_vectorielles.append(identity) or 7,
    )
    return ecrivain, purges_vectorielles


class TestLaPurgeToucheLesDeuxStores:
    """Un seul store purge laisse la moitie des orphelins, en silence."""

    def test_le_graphe_est_purge_sur_la_cle(
        self, stores_espionnes: tuple[EcrivainEspion, list[Any]]
    ) -> None:
        ecrivain, _ = stores_espionnes
        storage.forget_document(IDENTITE)

        assert ecrivain.purges == [IDENTITE.key]

    def test_l_index_vectoriel_est_purge_sur_la_meme_identite(
        self, stores_espionnes: tuple[EcrivainEspion, list[Any]]
    ) -> None:
        _, purges = stores_espionnes
        storage.forget_document(IDENTITE)

        assert purges == [IDENTITE]

    def test_les_deux_stores_sont_purges_par_un_seul_appel(
        self, stores_espionnes: tuple[EcrivainEspion, list[Any]]
    ) -> None:
        """Purger un seul store laisserait l'autre en orphelins, et le graphe et
        les vecteurs divergeraient sans erreur."""
        ecrivain, purges = stores_espionnes
        storage.forget_document(IDENTITE)

        assert len(ecrivain.purges) == 1 and len(purges) == 1

    def test_un_echec_du_graphe_n_empeche_pas_la_purge_vectorielle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le cas qui compte : un store a terre ne doit pas laisser l'autre sale.

        Une purge qui s'arreterait au premier echec laisserait l'etat qu'elle
        doit eviter : la moitie du document.
        """
        purges: list[Any] = []

        class EcrivainCasse:
            def delete_document(self, document_key: str) -> None:
                raise RuntimeError("graphd injoignable")

        monkeypatch.setattr(storage, "get_writer", lambda: EcrivainCasse())
        monkeypatch.setattr(
            storage.vectors, "delete_document", lambda identity: purges.append(identity) or 0
        )

        with pytest.raises(storage.PurgeIncompleteError) as leve:
            storage.forget_document(IDENTITE)

        assert purges == [IDENTITE], "l'index vectoriel doit avoir ete purge quand meme"
        assert "graphd injoignable" in str(leve.value)

    def test_la_purge_leve_quand_les_deux_stores_echouent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Casse:
            def delete_document(self, document_key: str) -> None:
                raise RuntimeError("graphd injoignable")

        def vectoriel_casse(identity: Any) -> int:
            raise RuntimeError("chromadb injoignable")

        monkeypatch.setattr(storage, "get_writer", lambda: Casse())
        monkeypatch.setattr(storage.vectors, "delete_document", vectoriel_casse)

        with pytest.raises(storage.PurgeIncompleteError) as leve:
            storage.forget_document(IDENTITE)

        assert "graphd injoignable" in str(leve.value)
        assert "chromadb injoignable" in str(leve.value)

    def test_une_purge_reussie_ne_leve_pas(
        self, stores_espionnes: tuple[EcrivainEspion, list[Any]]
    ) -> None:
        """Contre-epreuve des tests d'echec : une purge qui leverait toujours
        les passerait."""
        storage.forget_document(IDENTITE)
