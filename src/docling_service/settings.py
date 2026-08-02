"""Configuration centralisee du microservice Docling via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DoclingSettings(BaseSettings):
    """Variables d'environnement du service d'extraction Docling."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Stores ───────────────────────────────────────────────────────────────
    minio_endpoint: str = "minio:9000"
    minio_root_user: str = ""
    minio_root_password: str = ""
    minio_bucket: str = "documents"

    nebula_host: str = "graphd"
    nebula_port: int = 9669

    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    embedding_model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ── Connexion NebulaGraph ────────────────────────────────────────────────
    nebula_max_attempts: int = 15
    nebula_retry_seconds: float = 10.0
    # Nombre de tentatives de CREATE SPACE : le storaged doit avoir termine son
    # heartbeat d'enregistrement, ce qui peut prendre une minute au demarrage.
    nebula_space_attempts: int = 12

    # ── Extraction ───────────────────────────────────────────────────────────
    # Pages converties par passe. Les batchs bornent la memoire sur les gros
    # PDF ; ils ne se chevauchent plus (les ids sont deterministes, un
    # chevauchement ne servait qu'a re-convertir les memes pages pour rien).
    pdf_batch_pages: int = 5
    # Facteur d'agrandissement des crops d'images extraites des PDF.
    image_crop_zoom: float = 2.0

    # ── Vectorisation ────────────────────────────────────────────────────────
    # 450 caracteres : le modele multilingue encode 128 tokens, soit environ
    # 500 caracteres de prose. A 900, un tiers des chunks etait tronque — le
    # vecteur ne representait que le debut du texte, sans que rien ne le
    # signale. Mesure sur le corpus : 31 % de troncature a 900, 1,3 % a 450.
    chunk_size: int = 450
    chunk_overlap: int = 75
    embedding_batch_size: int = 32
    chroma_upsert_batch: int = 500
    # Plancher en caracteres sous lequel un bloc est ecarte de l'index
    # vectoriel. Il reste present dans le graphe : seule la recherche
    # semantique est debarrassee des fragments de mise en page.
    min_chunk_chars: int = 24
    # Prepose le titre de la section au texte envoye au modele d'embedding.
    # Le document stocke, lui, reste le texte brut : l'utilisateur voit le
    # passage sans prefixe, mais le vecteur porte son contexte.
    embed_section_context: bool = True

    # ── Graphe ───────────────────────────────────────────────────────────────
    # Le graphe porte la structure, pas le corpus : on y stocke un apercu du
    # texte. Le texte integral vit dans ChromaDB, decoupe et sans troncature.
    graph_text_max_chars: int = 2000

    # ── File de jobs ─────────────────────────────────────────────────────────
    job_history_size: int = 500


@lru_cache(maxsize=1)
def get_settings() -> DoclingSettings:
    """Retourne l'instance unique des settings (cached)."""
    return DoclingSettings()
