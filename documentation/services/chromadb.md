# ChromaDB (Base vectorielle)

## Role

Base de donnees vectorielle stockant les embeddings des elements textuels extraits
des documents. Utilisee pour la recherche semantique.

## Container

- `chromadb` : image `chromadb/chroma:0.6.3`, port interne 8000

## API

API REST standard ChromaDB. Ecrite uniquement par le service Docling
(`src/docling_service/vectors.py`) ; le pipeline Dagster n'y touche pas.

## Collection

- `rag_documents` : collection principale
  - **ids** : `element_id` (hash sha256[:10]), suffixe `#n` si le bloc a du
    etre decoupe en plusieurs fenetres
  - **embeddings** : vecteurs 384 dimensions (paraphrase-multilingual-MiniLM-L12-v2, fenetre de
    256 tokens), calcules sur le texte precede du titre de sa section
  - **metadatas** : `element_id`, `graph_node_id`, `filename`, `label`,
    `page_no`, `minio_url`, `reference_id`, `section_title`, `page_position`,
    `ref_position`, `chunk_index`, `chunk_count`, `block_size`
  - **documents** : texte du chunk, integral (aucune troncature)

**Granularite** : un vecteur par **bloc**, pas par element. Les fragments de
mise en page produits par l'analyse de layout sont fusionnes avec leurs voisins
de meme section, et les residus sont ecartes de l'index — ils restent presents
dans NebulaGraph. Voir
[extraction_donnees.md](../extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

## Variables d'environnement

| Variable   | Description    | Defaut   |
|------------|----------------|----------|
| CHROMA_HOST | Hostname      | chromadb |
| CHROMA_PORT | Port          | 8000     |

## Dependances

Aucune (service autonome).

## Persistence

Volume : `./Datas/database/chromadb:/chroma/chroma`

## Healthcheck

```bash
curl -s http://chromadb:8000/api/v1/heartbeat
```
