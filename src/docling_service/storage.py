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


class PurgeIncompleteError(RuntimeError):
    """Au moins un store n'a pas pu etre purge d'un document.

    Elle doit etre bruyante : une purge partielle laisse la moitie d'un document
    derriere elle, et c'est exactement l'etat que la purge existe pour eviter.
    """


def forget_document(identity: DocumentIdentity) -> None:
    """Retire un document des DEUX stores, ou dit lequel a resiste.

    **Point d'appel unique de la purge**, comme `persist` l'est de l'ecriture, et
    pour la meme raison : les deux stores doivent rester coherents. Purger le
    graphe seul laisserait les vecteurs en orphelins, et l'agent servirait des
    passages dont aucun sommet ne repond — sans qu'aucune erreur ne le signale.

    Deux appelants, et un seul mecanisme (registre 4.1 et 4.2) :

    - **avant de reecrire un document** : les identifiants derivent du texte, donc
      un texte modifie produit de NOUVEAUX identifiants et les anciens survivent.
      Le capteur declenchant sur ``mtime``, c'est le chemin NOMINAL qui casse ;
    - **apres l'echec d'un lot PDF** : sans cette purge, l'ouvrage reste dans
      l'index, tronque, sur une partition rouge — et `verify_contract` ne peut pas
      le voir, les `element_id` ecrits etant valides.

    Les deux stores sont TENTES meme si le premier echoue : s'arreter au premier
    echec laisserait precisement l'etat mixte a eviter.

    Args:
        identity: Identite du document a oublier.

    Raises:
        PurgeIncompleteError: Si au moins un store a refuse la purge. Le message
            nomme les stores et leur cause.
    """
    echecs: list[str] = []

    try:
        get_writer().delete_document(identity.key)
    except Exception as exc:
        # LARGEUR VOULUE : l'index vectoriel doit etre tente quand meme, sans
        # quoi une panne du graphd laisse la moitie du document en place. Le
        # graphd leve `NebulaError`, et son pool leve ses propres types de
        # transport avant d'y arriver. L'echec n'est pas avale : il est nomme
        # et il fait lever plus bas.
        echecs.append(f"NebulaGraph ({exc})")

    try:
        retires = vectors.delete_document(identity)
    except Exception as exc:
        # LARGEUR VOULUE, meme motif : le bilan doit se former meme si ChromaDB
        # est a terre. `chromadb` leve selon la couche qui echoue — HTTP,
        # protocole, collection absente — sans base commune utile.
        echecs.append(f"ChromaDB ({exc})")
    else:
        logger.info("Oubli de %s : %d chunks retires", identity.source_path, retires)

    if echecs:
        raise PurgeIncompleteError(
            f"purge incomplete de {identity.source_path} : {', '.join(echecs)}. "
            "Le document est a moitie dans les stores ; l'agent peut servir des "
            "passages dont aucun sommet ne repond, ou l'inverse"
        )


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
    document: Any = None,
) -> int:
    """Ecrit un lot d'elements dans NebulaGraph puis ChromaDB.

    Args:
        elements: Elements a persister.
        identity: Identite du document (chemin, ouvrage, nom).
        facts: Format, pagination, langue et empreinte du document.
        document: Document Docling converti, dont le decoupeur a besoin pour
            respecter la structure.

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
    return vectors.write_elements(elements, identity, facts, document)
