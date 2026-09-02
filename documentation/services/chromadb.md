# ChromaDB (Base vectorielle)

## Role

Base de donnees vectorielle stockant les embeddings des elements textuels extraits
des documents. Utilisee pour la recherche semantique.

## Container

- `chromadb` : image `chromadb/chroma:0.6.3`, port interne 8000

Le client Python (`chromadb==0.6.3`, dans `src/docling_service/requirements.txt`)
est tenu sur la meme version majeure que l'image serveur, alors que la branche
1.x existe. Ce pin ne suit plus `rag-agent-chat`, passe de son cote en 1.5.9 :
monter le 0.x vers le 1.x ici suppose de bouger le client et l'image ensemble,
puis de verifier que les collections deja ecrites restent lisibles.

## API

API REST standard ChromaDB. Ecrite uniquement par le service Docling
(`src/docling_service/vectors.py`) ; le pipeline Dagster n'y touche pas.

## Collection

- `rag_documents` : collection principale
  - **ids** : `element_id` (hash sha256[:10]), suffixe `#n` si le bloc a du
    etre decoupe en plusieurs fenetres
  - **embeddings** : vecteurs 384 dimensions (paraphrase-multilingual-MiniLM-L12-v2,
    fenetre de **128** tokens), calcules sur le texte precede du titre de sa section
  - **metadatas** : `element_id`, `graph_node_id`, `filename`, `label`,
    `page_no`, `minio_url`, `reference_id`, `section_title`, `page_position`,
    `ref_position`, `chunk_index`, `chunk_count`, `block_size`
  - **documents** : texte du chunk, integral

**Granularité** : un vecteur par **chunk**, pas par élément — et le mot « bloc » qui vivait ici était le vocabulaire de `build_blocks`, un regroupement maison retiré du dépôt (registre §5.2, §6.4). Le découpage est confié à `HybridChunker` de Docling, qui regroupe ce qui va ensemble en respectant la **structure** du document ; « les éléments consécutifs d'une même section et de même nature sont fusionnés » décrivait l'algorithme disparu, pas celui-ci.

Ce que la production écarte, `mesuré` à `src/docling_service/vectors.py:230` : un chunk sans aucun caractère alphanumérique, ou plus court que `MIN_CHUNK_CHARS` — **et seulement s'il est le seul chunk de son élément**. Une fenêtre du milieu d'un texte continu est conservée même courte, sans quoi l'agent concaténerait un texte troué (registre §4.28.a). Les éléments écartés de l'index restent présents dans NebulaGraph.

Voir [extraction_donnees.md](../extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

> **La fenetre valait 256 dans ce document, et le texte n'est pas « sans
> troncature ».** Les deux affirmations tombent ensemble (registre §6.2). La
> fenetre du modele du contrat vaut **128** tokens (`mesure` le 2 septembre 2026,
> `python -m src.index_report` : « limite : 128 tokens »), et **ce n'est pas un
> reglage** : elle est lue au runtime sur le modele lui-meme
> (`modele.max_seq_length`), a `vectors.py` et `index_report.py`. Ecrire 256
> laissait croire a une fenetre configurable qui n'existe pas — la famille des
> `CHUNK_SIZE=900` du registre §5.1.
>
> Le texte **stocke** est bien integral. Le **vecteur** ne l'est pas : le modele
> tronque ce qui depasse la fenetre, et cela arrive. Le chiffre et ses deux
> causes ont un seul site, `vectors.get_chunker`.

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
