"""Configuration centralisee du pipeline Dagster via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    """Variables d'environnement du pipeline d'ingestion.

    Les credentials des stores (MinIO, Nebula, Chroma) vivent dans
    ``src.docling_service.settings`` : seul le service Docling y ecrit.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    source_dir: str = "/opt/dagster/app/Datas"
    cleaned_subdir: str = ".cleaned"
    docling_service_url: str = "http://docling-service:8000"

    # MinIO : utilise uniquement pour exporter les images base64 des captures
    # HTML (le service Docling gere lui-meme les images des PDF).
    minio_endpoint: str = "minio:9000"
    minio_root_user: str = ""
    minio_root_password: str = ""
    minio_bucket: str = "documents"

    # Suivi des jobs d'extraction. L'extraction est asynchrone : Dagster
    # soumet le document puis interroge le service jusqu'a son terme, plutot
    # que de maintenir une requete HTTP ouverte pendant des heures.
    extraction_submit_timeout: int = 60
    extraction_poll_seconds: float = 15.0
    # Attente maximale du demarrage du service (chargement des modeles et
    # initialisation du schema NebulaGraph) avant de soumettre un document.
    extraction_readiness_timeout: int = 900
    # Plafond de securite par document (24 h) : un livre de plusieurs centaines
    # de pages prend le temps qu'il faut, mais un job fige doit finir par sortir.
    extraction_timeout_seconds: int = 86_400
    # Nombre d'echecs de sondage consecutifs toleres avant d'abandonner
    # (redemarrage du service, coupure reseau passagere).
    extraction_max_poll_failures: int = 20

    # ── rag-agent-chat : POST /reindex une fois l'ingestion retombee ─────────
    # L'agent tient son index lexical BM25 en memoire. Sans cet appel, un
    # document ingere apres son demarrage reste invisible en recherche
    # lexicale. Le defaut vise le service tel qu'il se nomme sur rag_network,
    # le reseau que ce pipeline cree et auquel l'agent s'attache.
    #
    # Vider cette URL DESACTIVE l'appel. C'est un choix possible, pas un
    # oubli : definitions.py l'annonce alors au chargement du code location, et
    # le sensor de reindexation le redit a chaque tick au lieu de lancer des
    # runs qui n'ont rien a faire.
    agent_service_url: str = "http://agent-api:8000"
    # Cle d'API de l'agent, si le sien en exige une (sa route /reindex est
    # protegee des que API_KEY est renseignee de son cote).
    agent_api_key: str = ""
    # La reconstruction parcourt tout le corpus, de maniere synchrone cote
    # agent : elle est lente par nature sur un gros index.
    reindex_timeout_seconds: float = 300.0


@lru_cache(maxsize=1)
def get_settings() -> PipelineSettings:
    """Retourne l'instance unique des settings (cached)."""
    return PipelineSettings()
