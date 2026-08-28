"""Tests du garde-fou sur le modele d'embedding.

Ces tests s'attachent au chemin qui PRODUIT les vecteurs — ``get_embedding_model``
— et non au fichier de configuration qui les decrit. Un test qui relirait
``settings.embedding_model_name`` pour le comparer au contrat serait vert des
deux cotes du defaut : il n'exercerait jamais le chargement, donc n'attraperait
jamais une ingestion lancee avec le mauvais modele.

Le faux modele rend 384 dimensions **quel que soit son nom**, exactement comme
la realite : ``all-MiniLM-L6-v2`` et ``paraphrase-multilingual-MiniLM-L12-v2``
sortent tous deux en 384. Un garde-fou qui se contenterait de verifier la
dimension serait donc vert sur les deux, et c'est ce que
``TestDimensionNeSuffitPas`` verrouille.
"""

from __future__ import annotations

import pytest

from src.docling_service.embedding import (
    CONTRACT_DIMENSION,
    CONTRACT_MODEL,
    EmbeddingContractError,
    canonical_name,
    get_embedding_model,
    reset_model,
    verify_dimension,
    verify_model_name,
)
from src.docling_service.settings import get_settings

# Le modele anglais d'avant la reingestion multilingue. Meme dimension de
# sortie que le modele du contrat : c'est pour cela que la panne est muette.
MODELE_ANGLAIS = "all-MiniLM-L6-v2"


class FauxModele:
    """Modele minimal, qui note sous quel nom on l'a charge."""

    def __init__(self, nom: str, dimension: int = CONTRACT_DIMENSION) -> None:
        self.nom = nom
        self._dimension = dimension

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension


@pytest.fixture()
def modele_vierge(monkeypatch):
    """Vide les deux caches : celui du modele et celui des settings.

    Sans cela, un test verrait le modele charge par le precedent, ou des
    settings resolus avant le monkeypatch de l'environnement.
    """
    monkeypatch.delenv("EMBEDDING_MODEL_NAME", raising=False)
    reset_model()
    get_settings.cache_clear()
    yield
    reset_model()
    get_settings.cache_clear()


def charge(dimension: int = CONTRACT_DIMENSION) -> list[str]:
    """Lance le chargement reel et retourne les noms effectivement demandes."""
    demandes: list[str] = []

    def loader(nom: str):
        demandes.append(nom)
        return FauxModele(nom, dimension)

    get_embedding_model(loader=loader)
    return demandes


class TestCheminNominal:
    def test_charge_le_modele_du_contrat(self, modele_vierge):
        assert charge() == [CONTRACT_MODEL]

    def test_le_modele_est_mis_en_cache(self, modele_vierge):
        premier = get_embedding_model(loader=lambda nom: FauxModele(nom))
        second = get_embedding_model(loader=lambda nom: FauxModele("autre"))
        assert premier is second

    def test_le_defaut_du_code_est_le_contrat(self, modele_vierge):
        # Ferme la derive code/contrat : le defaut n'est pas un litteral
        # recopie, c'est la constante elle-meme.
        from src.docling_service.settings import DoclingSettings

        assert DoclingSettings(_env_file=None).embedding_model_name == CONTRACT_MODEL


class TestDeriveParLEnvironnement:
    """Le chemin reel de la panne : le .env ecrase le defaut du code.

    C'est ce qui s'est produit — un .env reste a all-MiniLM-L6-v2 a survecu a
    toute la reingestion multilingue, hors de portee du versionnement.
    """

    def test_env_hors_contrat_refuse_le_chargement(self, modele_vierge, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", MODELE_ANGLAIS)
        get_settings.cache_clear()
        with pytest.raises(EmbeddingContractError):
            charge()

    def test_aucun_vecteur_n_est_produit_apres_le_refus(self, modele_vierge, monkeypatch):
        # Le refus doit tomber AVANT le chargement : si le loader a ete appele,
        # c'est qu'un modele hors contrat a pu encoder.
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", MODELE_ANGLAIS)
        get_settings.cache_clear()
        demandes: list[str] = []
        with pytest.raises(EmbeddingContractError):
            get_embedding_model(loader=lambda nom: demandes.append(nom) or FauxModele(nom))
        assert demandes == []

    def test_le_message_nomme_le_coupable_et_le_remede(self, modele_vierge, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", MODELE_ANGLAIS)
        get_settings.cache_clear()
        with pytest.raises(EmbeddingContractError) as capture:
            charge()
        message = str(capture.value)
        assert MODELE_ANGLAIS in message
        assert CONTRACT_MODEL in message
        # Sans cette precision, la correction du .env reste sans effet.
        assert "--force-recreate" in message

    def test_env_conforme_laisse_passer(self, modele_vierge, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", CONTRACT_MODEL)
        get_settings.cache_clear()
        assert charge() == [CONTRACT_MODEL]


class TestDimensionNeSuffitPas:
    """Verrouille la raison pour laquelle la panne est silencieuse."""

    def test_les_deux_modeles_ont_la_meme_dimension(self):
        # Si ce test devenait faux, le garde-fou pourrait se simplifier. Tant
        # qu'il est vrai, verifier la dimension est vert des deux cotes.
        verify_dimension(CONTRACT_DIMENSION, CONTRACT_MODEL)
        verify_dimension(CONTRACT_DIMENSION, MODELE_ANGLAIS)

    def test_le_nom_discrimine_la_ou_la_dimension_est_aveugle(self, modele_vierge, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", MODELE_ANGLAIS)
        get_settings.cache_clear()
        # Le faux modele rend 384, comme le vrai all-MiniLM-L6-v2 : seul le nom
        # peut le rejeter.
        with pytest.raises(EmbeddingContractError):
            charge(dimension=CONTRACT_DIMENSION)

    def test_une_dimension_inattendue_est_refusee(self, modele_vierge):
        # L'autre panne, moins probable : le nom est bon, l'artefact non.
        with pytest.raises(EmbeddingContractError):
            charge(dimension=768)


class TestNomCanonique:
    """Le prefixe d'organisation Hugging Face designe le meme artefact."""

    def test_prefixe_hf_accepte(self, modele_vierge, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", f"sentence-transformers/{CONTRACT_MODEL}")
        get_settings.cache_clear()
        assert charge() == [f"sentence-transformers/{CONTRACT_MODEL}"]

    def test_espaces_parasites_acceptes(self):
        verify_model_name(f"  {CONTRACT_MODEL}  ")

    def test_prefixe_hf_sur_le_modele_anglais_reste_refuse(self):
        with pytest.raises(EmbeddingContractError):
            verify_model_name(f"sentence-transformers/{MODELE_ANGLAIS}")

    def test_canonical_name_retire_le_prefixe(self):
        assert canonical_name(f"sentence-transformers/{CONTRACT_MODEL}") == CONTRACT_MODEL

    def test_un_nom_qui_contient_le_contrat_sans_l_etre_est_refuse(self):
        # Une comparaison par sous-chaine laisserait passer ces deux-la.
        with pytest.raises(EmbeddingContractError):
            verify_model_name(f"{CONTRACT_MODEL}-distill")
        with pytest.raises(EmbeddingContractError):
            verify_model_name(f"org/{CONTRACT_MODEL}")


class TestBalayageDesModelesCourants:
    """Balaye au-dela du seul all-MiniLM-L6-v2.

    La derive suivante ne reprendra pas le nom de la precedente : le document
    de cadrage cite bge-m3, multilingual-e5-large et Qwen3-Embedding comme
    candidats a une future migration. Aucun ne doit pouvoir entrer sans
    reingestion, et la bande balayee inclut donc des modeles de dimension
    differente ET des modeles a 384 dimensions comme le contrat.
    """

    @pytest.mark.parametrize(
        "nom",
        [
            "all-MiniLM-L6-v2",
            "all-MiniLM-L12-v2",
            "paraphrase-MiniLM-L6-v2",
            "paraphrase-multilingual-mpnet-base-v2",
            "multi-qa-MiniLM-L6-cos-v1",
            "BAAI/bge-m3",
            "intfloat/multilingual-e5-large",
            "Qwen/Qwen3-Embedding-0.6B",
            "",
        ],
    )
    def test_tout_autre_modele_est_refuse(self, nom):
        with pytest.raises(EmbeddingContractError):
            verify_model_name(nom)

    def test_le_contrat_lui_meme_passe(self):
        verify_model_name(CONTRACT_MODEL)
