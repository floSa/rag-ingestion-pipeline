"""Persistance d'un lot d'elements : graphe puis index vectoriel.

Point d'entree unique des ecritures, pour que les deux stores restent
coherents : si NebulaGraph refuse le lot, on n'indexe pas les vecteurs
correspondants, et l'erreur remonte jusqu'au job.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.docling_service import vectors
from src.docling_service.elements import DocumentFacts, DocumentIdentity
from src.docling_service.nebula import get_writer
from src.pipeline.schemas import DocumentElement

logger = logging.getLogger(__name__)


def validate_elements(elements: Sequence[dict[str, Any]]) -> None:
    """Verifie que les elements respectent le schema partage.

    Garde-fou contre la derive de contrat : ``DocumentElement`` est le format
    documente cote ``rag-agent-chat``, et le service construit des dicts. Sans
    cette validation, un champ renomme ou oublie ne se voyait qu'a l'usage,
    dans les reponses de l'agent.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.

    Raises:
        pydantic.ValidationError: Si un element ne respecte pas le schema.
    """
    for element in elements:
        DocumentElement.model_validate(element)


def persist(
    elements: Sequence[dict[str, Any]],
    identity: DocumentIdentity,
    facts: DocumentFacts,
) -> int:
    """Ecrit un lot d'elements dans NebulaGraph puis ChromaDB.

    Args:
        elements: Elements a persister.
        identity: Identite du document (chemin, ouvrage, nom).
        facts: Format, pagination, langue et empreinte du document.

    Returns:
        Nombre de chunks ecrits dans ChromaDB.

    Raises:
        NebulaError: Si l'ecriture du graphe echoue.
        Exception: Si l'encodage ou l'ecriture vectorielle echoue.
    """
    if not elements:
        return 0

    validate_elements(elements)
    get_writer().write_elements(elements, identity, facts)
    return vectors.write_elements(elements, identity, facts)
