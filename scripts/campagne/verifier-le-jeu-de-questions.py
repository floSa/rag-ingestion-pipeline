"""Relit chaque ancrage du jeu de questions dans l'index vivant, et sort en 1 au desaccord.

**CE QUE CE SCRIPT GARDE, ET POURQUOI IL N'EST PAS UN TEST.**
`tests/unit/test_jeu_de_questions.py` garde la FORME du jeu — les cinq strates,
leurs effectifs, le format des `element_id`, les proprietes de serrage de chaque
strate. Il ne peut pas garder la PROVENANCE de la carte `ancrages` : verifier
que `4b1d79b83a` designe bien « Embedding window considerations » demande de lire
ChromaDB, et `chromadb` n'appartient pas aux dependances du depot mais a celles
du service d'extraction. Un test qui l'importerait ne serait collectable sur
aucun poste sans l'image d'extraction (10,4 Go).

C'est ce script qui le fait, et il doit tourner DANS l'image d'extraction. Le
geste est celui du registre section 4.27, qui monte le `src` de la branche
mesuree plutot que celui du clone principal :

    docker run --rm --network rag_network \\
      -v "$PWD/src":/app/src:ro -v "$PWD/scripts":/app/scripts:ro \\
      -v "$PWD/documentation":/app/documentation:ro \\
      -v /var/lib/docker/volumes/rag-ingestion-pipeline_docling_models/_data:/tmp/.cache \\
      --env-file <clone principal>/.env \\
      -e HOME=/tmp -e PYTHONPATH=/app -w /app \\
      rag-ingestion-pipeline-docling-service \\
      python scripts/campagne/verifier-le-jeu-de-questions.py \\
        documentation/campagnes/2026-09-02-jeu-de-questions.yaml

**Le code de sortie EST le comportement**, pas son temoin : c'est ce qu'un `&&`
lit dans une procedure d'avant-vol, et c'est ce qui distingue « le jeu est
encore valide contre cet index » de « le jeu a ete ecrit contre un autre index ».
Un renommage de fichier du corpus, une reingestion apres un changement
d'extraction, un `element_id` qui bouge : chacun rend le jeu faux EN SILENCE,
parce qu'un jeu de questions ne rougit pas tout seul.

Ce que le script NE verifie pas, et il faut le dire : que la reponse attendue
soit juste. Cela demande une relecture humaine.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import yaml

# Les champs de l'ancrage compares un par un. `chunk_count` en fait partie
# volontairement : c'est lui qui a menti sur deux elements du corpus jusqu'au
# lot 4 (registre 4.28.a), et un jeu de questions ecrit contre un index dont un
# element perd un morceau calcule son rappel sur un denominateur faux.
CHAMPS = (
    "section_title",
    "source_path",
    "label",
    "page_no",
    "page_no_end",
    "depth",
    "chunk_count",
)


def lire_le_store(collection: Any, element_id: str) -> dict[str, Any] | None:
    """Retourne l'ancrage tel que l'index le porte, ou ``None`` s'il a disparu.

    Args:
        collection: Collection ChromaDB.
        element_id: Identifiant d'element du contrat, 10 hexadecimaux.

    Returns:
        Les champs de :data:`CHAMPS` lus sur le premier chunk de l'element, plus
        le nombre de chunks reellement presents. ``None`` si l'element est absent
        de l'index.
    """
    got = collection.get(where={"element_id": element_id}, include=["metadatas"])
    metadonnees = got["metadatas"]
    if not metadonnees:
        return None
    premier = metadonnees[0]
    ancrage: dict[str, Any] = {champ: premier.get(champ) for champ in CHAMPS}
    # Le jeu de chunks REELLEMENT present, et non celui que `chunk_count`
    # annonce : c'est la distinction que le lot 4 a ferme, et la seule facon de
    # voir un morceau manquant depuis un chunk isole est de les compter tous.
    ancrage["chunks_presents"] = len(metadonnees)
    return ancrage


def comparer(attendu: dict[str, Any], mesure: dict[str, Any]) -> list[str]:
    """Retourne la liste des desaccords, champ par champ.

    Args:
        attendu: Ancrage tel que le jeu de questions le declare.
        mesure: Ancrage tel que l'index le porte.

    Returns:
        Un message par champ en desaccord ; la liste vide si tout concorde.
    """
    ecarts = []
    for champ in CHAMPS:
        if attendu.get(champ) != mesure.get(champ):
            ecarts.append(f"{champ} : jeu={attendu.get(champ)!r} index={mesure.get(champ)!r}")
    if mesure["chunks_presents"] != mesure["chunk_count"]:
        ecarts.append(
            f"jeu de chunks TROUE : chunk_count={mesure['chunk_count']} "
            f"mais {mesure['chunks_presents']} chunks presents"
        )
    return ecarts


def main() -> None:
    """Verifie le jeu passe en argument contre l'index vivant."""
    import chromadb

    if len(sys.argv) != 2:
        print(f"usage : {sys.argv[0]} <jeu-de-questions.yaml>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as flux:
        jeu = yaml.safe_load(flux)
    ancrages = jeu["ancrages"]

    client = chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "chromadb"),
        port=int(os.environ.get("CHROMA_PORT", "8000")),
    )
    collection = client.get_collection(os.environ.get("CHROMA_COLLECTION", "rag_documents"))

    total_chunks = collection.count()
    print(f"index interroge           : {total_chunks} chunks")
    print(f"chunks annonces par le jeu: {jeu['index']['chunks']}")
    print(f"ancrages a verifier       : {len(ancrages)}")

    echecs: list[str] = []

    # Le volume de l'index n'est pas un ancrage, mais un jeu ecrit contre 4367
    # chunks et rejoue contre 3000 ne mesure plus le meme rappel : le
    # denominateur a bouge. On le dit plutot que de le taire.
    if total_chunks != jeu["index"]["chunks"]:
        echecs.append(
            f"VOLUME : le jeu a ete ecrit contre {jeu['index']['chunks']} chunks, "
            f"l'index en porte {total_chunks}. Le rappel n'est plus comparable."
        )

    for element_id in sorted(ancrages):
        mesure = lire_le_store(collection, element_id)
        if mesure is None:
            echecs.append(f"{element_id} : ABSENT de l'index")
            continue
        ecarts = comparer(ancrages[element_id], mesure)
        if ecarts:
            echecs.append(f"{element_id} : " + " ; ".join(ecarts))

    if echecs:
        print(f"\nJEU INVALIDE CONTRE CET INDEX — {len(echecs)} desaccord(s) :")
        for ligne in echecs:
            print(f"  {ligne}")
        sys.exit(1)

    print(f"\nJeu valide : les {len(ancrages)} ancrages concordent avec l'index, champ par champ.")


if __name__ == "__main__":
    main()
