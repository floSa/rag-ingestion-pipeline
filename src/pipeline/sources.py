"""Declaration des sources d'ingestion via ``sources.yaml``.

Ajouter une source = ajouter un bloc dans le YAML, aucun code Python a modifier.
La factory (``src.pipeline.factory``) genere ensuite partitions, assets, job et
sensor pour chaque source declaree.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_SOURCES_FILE = Path(__file__).resolve().parent / "sources.yaml"

SourceType = str  # voir SourceConfig.type pour les valeurs admises


class ExtractionProfile(BaseModel):
    """Profil d'extraction dedie a un site precis (prioritaire sur le generique).

    Si ``detect`` matche dans la page, seul le contenu de ``content`` est garde,
    apres suppression des selecteurs ``strip``.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    detect: str
    content: str
    strip: list[str] = Field(default_factory=list)


class CleaningOptions(BaseModel):
    """Options de nettoyage HTML, ajustables par source."""

    extra_remove_selectors: list[str] = Field(
        default_factory=list,
        description="Selecteurs CSS supplementaires a supprimer (ex: '.cookie-banner').",
    )
    profiles: list[ExtractionProfile] = Field(
        default_factory=list,
        description="Profils d'extraction par site, prioritaires sur le mode generique.",
    )
    export_images: bool = Field(
        default=True,
        description="Exporter les images base64 volumineuses vers MinIO "
        "au lieu de les supprimer.",
    )
    min_text_chars: int = Field(
        default=250,
        description="Longueur de texte minimale pour accepter le resultat d'une strategie.",
    )
    min_text_ratio: float = Field(
        default=0.05,
        description="Ratio minimal texte extrait / texte pre-nettoye.",
    )
    max_data_uri_bytes: int = Field(
        default=4096,
        description="Taille au-dela de laquelle les URI data: (images inline SingleFile) "
        "sont supprimees.",
    )


class SourceConfig(BaseModel):
    """Une source de documents a ingerer."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    glob: str
    type: str = Field(pattern=r"^(pdf|html)$")
    cleaning: CleaningOptions = Field(default_factory=CleaningOptions)


class SourcesFile(BaseModel):
    """Racine du fichier sources.yaml."""

    sources: list[SourceConfig]

    @field_validator("sources")
    @classmethod
    def _unique_names(cls, value: list[SourceConfig]) -> list[SourceConfig]:
        names = [s.name for s in value]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"Noms de sources dupliques : {sorted(duplicates)}")
        return value


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    """Charge et valide la liste des sources depuis le YAML.

    Args:
        path: Chemin du fichier de sources. Par defaut ``src/pipeline/sources.yaml``.

    Returns:
        Liste des sources validees.
    """
    sources_path = path or DEFAULT_SOURCES_FILE
    with open(sources_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return SourcesFile.model_validate(raw).sources
