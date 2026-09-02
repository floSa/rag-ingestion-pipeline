"""Rappel vectoriel BRUT du jeu de questions, contre le corpus ENTIER.

Ce que cette mesure est, et ce qu'elle n'est pas — a lire avant tout chiffre :

- elle mesure la recherche DENSE seule, celle que ce depot produit : encoder la
  question avec le meme modele que l'index, interroger ChromaDB, regarder si les
  `element_id` attendus reviennent dans les k premiers ;
- elle NE mesure PAS l'agent. Ni BM25, ni la reconstruction par le graphe, ni le
  reranker, ni l'abstention : tout cela vit dans `rag-agent-chat`. Un rappel
  dense bas ici ne condamne rien, et un rappel haut ne garantit rien la-bas ;
- 30 questions ne suffisent pas a arbitrer un reglage. Un ecart de deux points
  est du bruit (registre 1).

**Le geste**, celui du registre section 4.27 — monter le `src` de la branche
mesuree, jamais celui du clone principal :

    docker run --rm --network rag_network \\
      -v "$PWD/src":/app/src:ro -v "$PWD/scripts":/app/scripts:ro \\
      -v "$PWD/documentation":/app/documentation:ro \\
      -v /var/lib/docker/volumes/rag-ingestion-pipeline_docling_models/_data:/tmp/.cache \\
      --env-file <clone principal>/.env \\
      -e HOME=/tmp -e PYTHONPATH=/app -w /app \\
      rag-ingestion-pipeline-docling-service \\
      python scripts/campagne/mesurer-le-rappel-vectoriel.py \\
        documentation/campagnes/2026-09-02-jeu-de-questions.yaml 5,10,20

Lecture seule : ce script n'ecrit dans aucun store.
"""

import json
import os
import sys

import chromadb
import yaml

from src.docling_service.embedding import get_embedding_model


def main() -> None:
    chemin = sys.argv[1]
    ks = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["5", "10", "20"])]
    with open(chemin, encoding="utf-8") as flux:
        jeu = yaml.safe_load(flux)
    questions = jeu["questions"]

    client = chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "chromadb"),
        port=int(os.environ.get("CHROMA_PORT", "8000")),
    )
    col = client.get_collection(os.environ.get("CHROMA_COLLECTION", "rag_documents"))
    total_chunks = col.count()
    modele = get_embedding_model()

    kmax = max(ks)
    resultats = []
    for q in questions:
        # Encode EXACTEMENT comme la production : `vectors.write_elements` appelle
        # `encode(...)` sans `normalize_embeddings`, donc vecteurs non normes, et
        # la collection ne declare pas `hnsw:space` — ChromaDB retombe sur `l2`
        # (registre 4.29.f). Normaliser ici comparerait deux espaces differents.
        vec = modele.encode([q["question"]], show_progress_bar=False)[0].tolist()
        res = col.query(query_embeddings=[vec], n_results=kmax, include=["metadatas", "distances"])
        rendus = [m["element_id"] for m in res["metadatas"][0]]
        attendus = list(q.get("element_ids") or [])
        ligne = {
            "id": q["id"],
            "strate": q["strate"],
            "attendus": len(attendus),
            "rendus_tete": rendus[:3],
            "distance_tete": round(res["distances"][0][0], 4) if res["distances"][0] else None,
        }
        for k in ks:
            tete = rendus[:k]
            trouves = [e for e in attendus if e in tete]
            ligne[f"trouves@{k}"] = len(trouves)
            ligne[f"rappel@{k}"] = (len(trouves) / len(attendus)) if attendus else None
            ligne[f"au_moins_un@{k}"] = bool(trouves) if attendus else None
        resultats.append(ligne)

    print(json.dumps({"chunks_interroges": total_chunks, "k": ks, "lignes": resultats}, indent=2))


if __name__ == "__main__":
    main()
