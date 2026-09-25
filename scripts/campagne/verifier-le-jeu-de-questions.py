"""Relit chaque ancrage du jeu de questions dans l'index vivant, et sort en 1 au desaccord.

`tests/unit/test_jeu_de_questions.py` verifie la forme du jeu : les cinq
strates, leurs effectifs, le format des `element_id`, les contraintes propres a
chaque strate. Il ne peut pas verifier la carte `ancrages` contre l'index :
confirmer que `4b1d79b83a` designe bien « Embedding window considerations »
demande de lire ChromaDB, et `chromadb` n'est installe que dans l'image
d'extraction (10,4 Go), pas dans les dependances du depot.

Ce script fait cette verification, dans l'image d'extraction. Commande
(registre 4.27), a lancer depuis l'arbre de travail de la branche mesuree :
`docker-compose.yml` y monte son `src/`, et la commande ajoute ses `scripts/`
et `documentation/` :

    docker compose run --rm --no-deps -T \\
      -v "$PWD/scripts":/app/scripts:ro \\
      -v "$PWD/documentation":/app/documentation:ro \\
      -e PYTHONPATH=/app -w /app \\
      docling-service \\
      python scripts/campagne/verifier-le-jeu-de-questions.py \\
        documentation/campagnes/2026-09-02-jeu-de-questions.yaml

Le code de sortie (0 ou 1) dit si le jeu est encore valide contre cet index ;
c'est ce qu'un `&&` lit dans une procedure d'avant-vol. Un renommage de fichier
du corpus, une reingestion apres un changement d'extraction ou un `element_id`
qui change rendent le jeu faux sans aucune autre erreur.

Le script ne verifie pas que la reponse attendue est juste : cela demande une
relecture humaine.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import yaml

# Les champs de l'ancrage compares un par un. `chunk_count` en fait partie
# volontairement : il a deja annonce plus de chunks qu'il n'en existait sur deux
# elements du corpus (registre 4.28.a), et un rappel calcule sur un element qui
# perd un morceau a un denominateur faux.
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
    # Le jeu de chunks reellement present, et non celui que `chunk_count`
    # annonce : un morceau manquant ne se voit qu'en comptant tous les chunks.
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
    # denominateur a change. Un ecart de volume compte donc comme un echec.
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
