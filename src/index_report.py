"""Rapport de qualite de l'index vectoriel.

A lancer depuis le reseau Docker :

    docker compose exec docling-service python -m src.index_report

Repond aux questions qu'on se pose apres une ingestion : l'index contient-il du
bruit, les chunks ont-ils une taille exploitable, et depassent-ils la fenetre du
modele d'embedding ? Un chunk plus long que cette fenetre est tronque par le
modele lui-meme, en silence.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

import chromadb

from src.docling_service.blocks import has_content
from src.docling_service.settings import get_settings
from src.docling_service.vectors import COLLECTION_NAME, get_embedding_model

# En deca, un chunk ne porte pas assez de matiere pour etre retrouve utilement.
FAIBLE_CONTENU_CARACTERES = 40


def _pourcentage(part: int, total: int) -> str:
    return f"{100 * part / total:.1f} %" if total else "—"


def main() -> None:
    """Affiche le rapport."""
    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    result: dict[str, Any] = collection.get(include=["documents", "metadatas"])
    documents: list[str] = result["documents"]
    metadatas: list[dict[str, Any]] = result["metadatas"]

    if not documents:
        print("Index vide.")
        return

    total = len(documents)
    lengths = [len(d.strip()) for d in documents]

    print("=== Volume ===")
    print(f"chunks indexes            : {total}")
    print(f"documents distincts       : {len({m.get('filename') for m in metadatas})}")

    print("\n=== Qualite du contenu ===")
    bruit = sum(1 for d in documents if not has_content(d))
    faibles = sum(1 for length in lengths if length < FAIBLE_CONTENU_CARACTERES)
    print(f"sans caractere alphanumerique : {bruit:>6}  ({_pourcentage(bruit, total)})")
    print(
        f"moins de {FAIBLE_CONTENU_CARACTERES} caracteres        : "
        f"{faibles:>6}  ({_pourcentage(faibles, total)})"
    )

    print("\n=== Taille des chunks (caracteres) ===")
    print(f"mediane                   : {int(statistics.median(lengths))}")
    print(f"moyenne                   : {int(statistics.fmean(lengths))}")
    print(f"minimum / maximum         : {min(lengths)} / {max(lengths)}")

    fusionnes = [int(m.get("block_size") or 1) for m in metadatas]
    print(
        f"elements fusionnes / chunk: mediane {int(statistics.median(fusionnes))}, "
        f"maximum {max(fusionnes)}"
    )

    print("\n=== Fenetre du modele d'embedding ===")
    model = get_embedding_model()
    limite = int(model.max_seq_length)
    tokenizer = model.tokenizer
    tokens = [len(tokenizer.encode(d, add_special_tokens=True)) for d in documents]
    depassements = sum(1 for t in tokens if t > limite)
    print(f"modele                    : {settings.embedding_model_name}")
    print(f"limite                    : {limite} tokens")
    print(
        f"chunks tronques par le modele : {depassements:>6}  ({_pourcentage(depassements, total)})"
    )
    print(f"tokens : mediane {int(statistics.median(tokens))}, maximum {max(tokens)}")

    print("\n=== Repartition par label ===")
    for label, count in Counter(str(m.get("label") or "?") for m in metadatas).most_common(10):
        print(f"{count:>7}  {label}")


if __name__ == "__main__":
    main()
