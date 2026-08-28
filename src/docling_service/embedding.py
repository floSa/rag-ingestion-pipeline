"""Le modele d'embedding, et le refus d'en charger un autre.

Un desaccord de modele entre ce pipeline et ``rag-agent-chat`` est la panne la
plus couteuse de la chaine, et la seule qui ne laisse aucune trace : pas
d'exception, pas de ligne de journal, aucune sonde qui la voie. La recherche
rend des passages plausibles et faux, et rien ne le signale.

Elle est muette pour une raison precise, et cette raison dicte la forme du
garde-fou : ``all-MiniLM-L6-v2``, le modele anglais d'avant la reingestion
multilingue, produit lui aussi des vecteurs de **384 dimensions**. ChromaDB les
accepte sans broncher. Verifier la dimension ne protege donc de rien — c'est le
controle qui serait vert des deux cotes du defaut. C'est le NOM qui discrimine.

Le chemin reel de la derive n'est pas le code mais l'environnement.
``DoclingSettings`` derive de ``BaseSettings`` : ``EMBEDDING_MODEL_NAME`` ecrase
le defaut du code. Un ``.env`` reste a ``all-MiniLM-L6-v2`` a survecu ici a
toute la reingestion multilingue sans qu'aucun commit ne puisse le corriger,
puisque ce fichier n'est pas versionne. Un garde-fou qui ne couvrirait que la
mutation du code manquerait donc exactement la panne qui s'est produite.

D'ou deux verifications, toutes deux posees du cote qui PRODUIT les vecteurs,
et non sur le fichier de configuration qui les decrit :

- :func:`verify_model_name`, appelee avant le chargement, donc sur tout chemin qui
  mene a un vecteur. Le service l'appelle aussi au demarrage et refuse de
  demarrer si elle echoue : un service mort se voit, un index silencieusement
  anglais, non ;
- :func:`verify_dimension`, appelee sur le modele reellement charge, dont on
  interroge la sortie. Elle ne distingue pas les deux MiniLM et n'en a pas la
  charge : elle attrape le cas ou le nom est bon mais l'artefact ne l'est pas.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Le modele du contrat avec rag-agent-chat. Il doit etre identique des deux
# cotes : c'est le defaut de EMBEDDING_MODEL_NAME ici comme dans le settings.py
# de l'agent. Changer de modele impose une reingestion complete.
CONTRACT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Prefixe d'organisation que Hugging Face admet sur le meme modele.
# « sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 » et le nom nu
# designent le meme artefact : refuser le premier serait un faux positif, et un
# garde-fou qui cure-dent finit desactive.
HF_ORG_PREFIX = "sentence-transformers/"

# Dimension de sortie du modele du contrat. all-MiniLM-L6-v2 rend exactement la
# meme : ce nombre ne sert donc PAS a distinguer les deux modeles, seulement a
# detecter un artefact qui ne serait pas celui qu'il annonce.
CONTRACT_DIMENSION = 384


class EmbeddingContractError(RuntimeError):
    """Le modele d'embedding demande ou charge n'est pas celui du contrat."""


def canonical_name(nom: str) -> str:
    """Retire le prefixe d'organisation et les espaces autour du nom.

    Args:
        nom: Nom du modele, tel qu'il vient de la configuration.

    Returns:
        Le nom nu, comparable a :data:`CONTRACT_MODEL`.
    """
    return nom.strip().removeprefix(HF_ORG_PREFIX)


def verify_model_name(nom: str) -> None:
    """Refuse tout modele qui n'est pas celui du contrat.

    Args:
        nom: Nom du modele demande, typiquement ``EMBEDDING_MODEL_NAME``.

    Raises:
        EmbeddingContractError: Si le nom n'est pas celui du contrat.
    """
    if canonical_name(nom) == CONTRACT_MODEL:
        return
    raise EmbeddingContractError(
        f"Modele d'embedding hors contrat : « {nom} » au lieu de "
        f"« {CONTRACT_MODEL} ». rag-agent-chat interroge l'index avec le modele "
        "du contrat ; indexer avec un autre rend une recherche plausible et "
        "fausse, sans erreur ni journal. Corriger EMBEDDING_MODEL_NAME dans "
        ".env, puis « docker compose up -d --force-recreate » : un restart ne "
        "relit pas le .env."
    )


def verify_dimension(dimension: int, nom: str) -> None:
    """Verifie la sortie du modele reellement charge.

    Ne distingue pas les deux MiniLM, qui rendent tous deux 384 : c'est
    :func:`verify_model_name` qui s'en charge. Sert a detecter un artefact qui ne
    correspond pas au nom sous lequel il a ete charge.

    Args:
        dimension: Dimension annoncee par le modele charge.
        nom: Nom sous lequel il a ete charge, pour le message.

    Raises:
        EmbeddingContractError: Si la dimension n'est pas celle du contrat.
    """
    if dimension == CONTRACT_DIMENSION:
        return
    raise EmbeddingContractError(
        f"Le modele « {nom} » rend des vecteurs de {dimension} dimensions, "
        f"le contrat en attend {CONTRACT_DIMENSION}. L'index serait illisible "
        "pour rag-agent-chat."
    )


def _load_sentence_transformer(nom: str) -> Any:
    """Charge le modele avec SentenceTransformers.

    L'import est local : ce module doit rester importable sans torch, pour que
    le garde-fou soit testable hors de l'image d'extraction.

    Args:
        nom: Nom du modele a charger.

    Returns:
        Le modele charge.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(nom)


_model: Any = None
_model_lock = threading.Lock()


def get_embedding_model(loader: Callable[[str], Any] | None = None) -> Any:
    """Retourne le modele d'embedding, verifie puis charge au premier appel.

    C'est le point de passage unique vers un vecteur : tout ce qui encode passe
    par ici, donc tout ce qui encode est verifie.

    Args:
        loader: Fonction de chargement. Laissee vide en production ; les tests
            y injectent un faux modele pour exercer ce chemin sans torch.

    Returns:
        Le modele d'embedding.

    Raises:
        EmbeddingContractError: Si le modele configure ou charge n'honore pas le
            contrat.
    """
    global _model
    with _model_lock:
        if _model is None:
            # Import local : settings.py lit CONTRACT_MODEL ici, un import de
            # module a module serait circulaire.
            from src.docling_service.settings import get_settings

            nom = get_settings().embedding_model_name
            verify_model_name(nom)
            logger.info("Chargement du modele d'embedding %s...", nom)
            modele = (loader or _load_sentence_transformer)(nom)
            verify_dimension(int(modele.get_sentence_embedding_dimension()), nom)
            _model = modele
        return _model


def reset_model() -> None:
    """Oublie le modele charge. Reservee aux tests."""
    global _model
    with _model_lock:
        _model = None
