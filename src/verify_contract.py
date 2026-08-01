"""Verification mecanique du contrat d'interface avec rag-agent-chat.

A lancer depuis le reseau Docker, apres une ingestion :

    docker compose exec docling-service python -m src.verify_contract

Le contrat vit dans ``src/pipeline/schemas.py`` et dans
``documentation/llm_integration_plan.md``. Ce script verifie qu'il est tenu
dans les faits — c'est le genre de derive qui ne se voit autrement qu'a
l'usage, dans les reponses de l'agent :

- chaque ``element_id`` respecte ``^[a-f0-9]{10}$``, format que l'agent valide
  sur ``/context/{element_id}`` ;
- ``element_id`` et ``graph_node_id`` coincident ;
- toutes les cles de metadonnees attendues sont presentes ;
- les ancres designees existent reellement comme noeuds du graphe.

Sort en code d'erreur si une anomalie est detectee, pour un usage en
pre-deploiement.
"""

from __future__ import annotations

import random
import re
import sys

import chromadb
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from src.docling_service.nebula import SPACE
from src.docling_service.settings import get_settings
from src.docling_service.vectors import COLLECTION_NAME
from src.pipeline.schemas import ChunkMetadata

FORMAT_ELEMENT_ID = re.compile(r"^[a-f0-9]{10}$")
# Verifier chaque ancre dans le graphe serait long sur un gros corpus ; un
# echantillon suffit a detecter une rupture de contrat, qui est systematique.
TAILLE_ECHANTILLON = 400


def main() -> None:
    """Execute les verifications et sort en erreur si l'une echoue."""
    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    result = collection.get(include=["metadatas"])
    metadatas = result["metadatas"]
    chunk_ids = result["ids"]

    if not metadatas:
        print("Index vide : rien a verifier.")
        return

    anomalies: list[str] = []
    print(f"chunks examines                : {len(metadatas)}")

    mauvais = [
        str(m.get("element_id", ""))
        for m in metadatas
        if not FORMAT_ELEMENT_ID.match(str(m.get("element_id", "")))
    ]
    print(f"element_id au mauvais format   : {len(mauvais)}")
    if mauvais:
        anomalies.append(f"element_id invalides (ex. {mauvais[:3]})")

    divergents = sum(1 for m in metadatas if m.get("element_id") != m.get("graph_node_id"))
    print(f"element_id != graph_node_id    : {divergents}")
    if divergents:
        anomalies.append("element_id et graph_node_id divergent")

    attendues = set(ChunkMetadata.model_fields)
    manquantes = sorted({cle for m in metadatas for cle in attendues - set(m)})
    print(f"cles de metadonnees manquantes : {manquantes or 'aucune'}")
    if manquantes:
        anomalies.append(f"metadonnees manquantes : {manquantes}")

    print(f"ids de chunk suffixes en #n    : {sum(1 for i in chunk_ids if '#' in i)}")

    random.seed(0)
    identifiants = sorted({str(m["element_id"]) for m in metadatas})
    echantillon = random.sample(identifiants, min(TAILLE_ECHANTILLON, len(identifiants)))
    trouves = _compter_dans_le_graphe(echantillon)
    print(f"ancres presentes dans le graphe: {trouves}/{len(echantillon)}")
    if trouves != len(echantillon):
        anomalies.append(f"{len(echantillon) - trouves} ancres absentes du graphe")

    print()
    if anomalies:
        for anomalie in anomalies:
            print(f"ANOMALIE : {anomalie}")
        sys.exit(1)
    print("Contrat respecte.")


def _compter_dans_le_graphe(identifiants: list[str]) -> int:
    """Retourne le nombre d'identifiants presents comme noeuds du graphe."""
    settings = get_settings()
    pool = ConnectionPool()
    if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
        print("NebulaGraph injoignable.")
        return -1
    session = pool.get_session("root", "nebula")
    try:
        session.execute(f"USE {SPACE};")
        liste = ", ".join(f'"{identifiant}"' for identifiant in identifiants)
        result = session.execute(
            f"MATCH (v) WHERE id(v) IN [{liste}] RETURN count(DISTINCT id(v));"
        )
        if not result.is_succeeded():
            print(f"Requete nGQL en echec : {result.error_msg()}")
            return -1
        compte: int = result.rows()[0].values[0].get_iVal()
        return compte
    finally:
        session.release()
        pool.close()


if __name__ == "__main__":
    main()
