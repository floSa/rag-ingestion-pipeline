"""Rapport de qualite de l'index vectoriel.

A lancer dans un conteneur jetable du service d'extraction :

    docker compose run --rm --no-deps -T -e PYTHONPATH=/app -w /app \\
      docling-service python -m src.index_report

Repond aux questions qu'on se pose apres une ingestion : l'index contient-il du
bruit, les chunks ont-ils une taille exploitable, et depassent-ils la fenetre du
modele d'embedding ? Un chunk plus long que cette fenetre est tronque par le
modele lui-meme, en silence.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.docling_service.chunking import embedding_inputs, has_content
from src.docling_service.settings import get_settings

# `chromadb`, le modele d'embedding et `vectors` sont importes dans ``main`` :
# ce module reste importable sans eux, ce qui permet de tester la mesure de
# fenetre ci-dessous.

# En deca, un chunk ne porte pas assez de matiere pour etre retrouve utilement.
FAIBLE_CONTENU_CARACTERES = 40


@dataclass(frozen=True)
class FenetreMesuree:
    """Ce que le modele d'embedding tronque reellement."""

    mediane: int
    maximum: int
    depassements: int
    total: int
    prefixe_du_titre: bool


def mesurer_la_fenetre(
    documents: Sequence[str],
    metadatas: Sequence[Mapping[str, Any]],
    tokeniser: Callable[[str], int],
    limite: int,
    embed_section_context: bool,
) -> FenetreMesuree:
    """Compte les chunks que le modele tronque, sur le texte qu'il recoit.

    Le texte mesure est le texte encode, prefixe du titre de section quand
    ``embed_section_context`` est vrai, et non le texte stocke : ce dernier
    sous-estime le nombre de chunks tronques (chiffres a
    :func:`~src.docling_service.vectors.get_chunker`).

    Ce texte vient de :func:`~src.docling_service.chunking.embedding_inputs`,
    la fonction qui le produit pour l'encodage : il n'est pas recalcule ici.

    Args:
        documents: Textes stockes, lus dans ChromaDB.
        metadatas: Metadonnees alignees, dont ``section_title``.
        tokeniser: Rend le nombre de tokens d'un texte. Injecte pour que la
            mesure se verifie sans charger le modele.
        limite: Fenetre du modele, en tokens.
        embed_section_context: Reglage ``settings.embed_section_context``.

    Returns:
        Les chiffres, et laquelle des deux positions du reglage ils decrivent.
    """
    encodes = embedding_inputs(documents, metadatas, embed_section_context)
    tokens = [tokeniser(texte) for texte in encodes]
    return FenetreMesuree(
        mediane=int(statistics.median(tokens)),
        maximum=max(tokens),
        depassements=sum(1 for valeur in tokens if valeur > limite),
        total=len(tokens),
        prefixe_du_titre=embed_section_context,
    )


def compter_les_documents(metadatas: Sequence[Mapping[str, Any]]) -> int:
    """Compte les documents distincts par ``source_path``, jamais par ``filename``.

    ``source_path`` est l'identite d'un document (exigence 3 du contrat). Le
    corpus contient deux ``Preface.html``, un par ouvrage : compter par
    ``filename`` rendrait 22 documents au lieu de 23 (`mesure` le 31 aout 2026
    sur l'index complet).

    Args:
        metadatas: Metadonnees des chunks.

    Returns:
        Le nombre de documents distincts.
    """
    return len({str(meta.get("source_path") or "") for meta in metadatas})


def _pourcentage(part: int, total: int) -> str:
    return f"{100 * part / total:.1f} %" if total else "—"


def main() -> None:
    """Affiche le rapport."""
    import chromadb

    from src.docling_service.embedding import get_embedding_model
    from src.docling_service.vectors import COLLECTION_NAME

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
    print(f"documents distincts       : {compter_les_documents(metadatas)}")

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
    fenetre = mesurer_la_fenetre(
        documents,
        metadatas,
        lambda texte: int(len(tokenizer.encode(texte, add_special_tokens=True))),
        limite,
        settings.embed_section_context,
    )
    mesure = (
        "texte encode, titre de section compris"
        if fenetre.prefixe_du_titre
        else "texte encode (EMBED_SECTION_CONTEXT est a faux : pas de prefixe)"
    )
    print(f"modele                    : {settings.embedding_model_name}")
    print(f"limite                    : {limite} tokens")
    print(f"mesure sur                : {mesure}")
    print(
        f"chunks tronques par le modele : {fenetre.depassements:>6}  "
        f"({_pourcentage(fenetre.depassements, total)})"
    )
    print(f"tokens : mediane {fenetre.mediane}, maximum {fenetre.maximum}")

    print("\n=== Profondeur de hierarchie ===")
    par_doc: dict[str, int] = {}
    for meta in metadatas:
        chemin = str(meta.get("source_path") or "?")
        par_doc[chemin] = max(par_doc.get(chemin, 0), int(meta.get("depth") or 0))
    for profondeur, count in sorted(Counter(par_doc.values()).items(), reverse=True):
        suffixe = "  <- restes plats" if profondeur <= 1 else ""
        print(f"   niveau {profondeur} : {count:>4} documents{suffixe}")

    print("\n=== Repartition par langue ===")
    for langue, count in Counter(
        str(m.get("language") or "indeterminee") for m in metadatas
    ).most_common():
        print(f"{count:>7}  {langue}")

    print("\n=== Repartition par label ===")
    for label, count in Counter(str(m.get("label") or "?") for m in metadatas).most_common(10):
        print(f"{count:>7}  {label}")


if __name__ == "__main__":
    main()
