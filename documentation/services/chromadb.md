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
  - **metadatas** : **19** cles, definies par `ChunkMetadata` dans
    `src/pipeline/schemas.py`, qui est le contrat de reference. Leur role est
    documente dans [base_vectorielle.md](../base_vectorielle.md) et dans
    [llm_integration_plan.md](../llm_integration_plan.md). La liste n'est pas
    recopiee ici : une liste recopiee diverge du contrat au premier ajout.
  - **documents** : texte du chunk, integral

**Granularite** : un vecteur par **chunk**, pas par element. Le decoupage est
confie a `HybridChunker` de Docling, qui regroupe ce qui va ensemble en
respectant la **structure** du document.

Ce que la production ecarte, dans `vectors.build_chunks` : un chunk sans aucun
caractere alphanumerique, ou plus court que `MIN_CHUNK_CHARS` — **et seulement
s'il est le seul chunk de son element**. Une fenetre du milieu d'un texte
continu est conservee meme courte, sans quoi l'agent concatenerait un texte
troue (registre §4.28.a). Les elements ecartes de l'index restent presents dans
NebulaGraph.

Voir [extraction_donnees.md](../extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

### Fenetre du modele et troncature

La fenetre du modele du contrat vaut **128** tokens (mesure le 2 septembre 2026,
`python -m src.index_report` : « limite : 128 tokens »). Ce n'est pas un
reglage : elle est lue au runtime sur le modele lui-meme
(`modele.max_seq_length`), dans `vectors.py` et `index_report.py`.

Le texte **stocke** est integral. Le **vecteur** ne l'est pas toujours : le
modele tronque ce qui depasse la fenetre. Le chiffre et ses causes sont
documentes a un seul endroit, `vectors.get_chunker`.

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

Depuis un conteneur du reseau `rag_network` (le port n'est pas publie sur l'hote) :

```bash
curl -s http://chromadb:8000/api/v1/heartbeat
```
