"""Configuration centralisee du microservice Docling via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from src.docling_service.embedding import CONTRACT_MODEL
from src.reglages_s3 import ReglagesDuStockageObjet


class DoclingSettings(ReglagesDuStockageObjet):
    """Variables d'environnement du service d'extraction Docling."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Stores ───────────────────────────────────────────────────────────────
    # Les quatre reglages du stockage objet — `s3_endpoint`, `s3_access_key`,
    # `s3_secret_key`, `s3_bucket` — viennent de `ReglagesDuStockageObjet`, que
    # `PipelineSettings` herite aussi (registre 4.29.b). `s3_endpoint` n'a pas
    # de defaut.
    nebula_host: str = "graphd"
    nebula_port: int = 9669
    # Lus depuis `NEBULA_USER` et `NEBULA_PASSWORD` (`.env.example`). Les
    # defauts sont ceux de `docker-compose.yml` : les identifiants publics d'un
    # graphd de developpement, pas un secret.
    nebula_user: str = "root"
    nebula_password: str = "nebula"  # pragma: allowlist secret

    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    # Le defaut est le modele du contrat, sans litteral en double. La seule
    # divergence possible vient de l'environnement, que verify_model_name()
    # refuse.
    embedding_model_name: str = CONTRACT_MODEL

    # ── Connexion NebulaGraph ────────────────────────────────────────────────
    nebula_max_attempts: int = 15
    nebula_retry_seconds: float = 10.0
    # Nombre de tentatives de CREATE SPACE : le storaged doit avoir termine son
    # heartbeat d'enregistrement, ce qui peut prendre une minute au demarrage.
    nebula_space_attempts: int = 12
    # Attente entre les etapes d'amorcage de `init_nebula.py`. Reglable pour
    # un poste lent, et mise a zero par les tests du script.
    nebula_amorcage_pause_seconds: float = 5.0

    # ── Extraction ───────────────────────────────────────────────────────────
    # Pages converties par passe. Les batchs bornent la memoire sur les gros
    # PDF ; ils ne se chevauchent pas (les ids sont deterministes, un
    # chevauchement ne ferait que re-convertir les memes pages).
    pdf_batch_pages: int = 5
    # Facteur d'agrandissement des crops d'images extraites des PDF.
    image_crop_zoom: float = 2.0

    # ── Vectorisation ────────────────────────────────────────────────────────
    # Pas de taille de chunk ni de recouvrement en caracteres : le decoupage
    # est fait par `HybridChunker`, sur la structure du document et la fenetre
    # du tokenizer du modele (registre 5.1).
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
    # Le graphe porte la structure, pas le corpus : il stocke un apercu du
    # texte. Le texte integral vit dans ChromaDB, decoupe.
    #
    # Le texte stocke dans ChromaDB est integral ; le vecteur, lui, ne couvre
    # que la fenetre du modele (chiffres a `vectors.get_chunker`). Le graphe
    # coupe a cette limite, ce qui fait diverger les deux stores sur les
    # elements concernes (registre 4.23) : `nebula.write_elements` compte ces
    # coupes et emet un avertissement.
    graph_text_max_chars: int = 2000

    # ── File de jobs ─────────────────────────────────────────────────────────
    job_history_size: int = 500


@lru_cache(maxsize=1)
def get_settings() -> DoclingSettings:
    """Retourne l'instance unique des settings (cached)."""
    return DoclingSettings()
