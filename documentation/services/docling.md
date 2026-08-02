# Docling Service (Extraction documentaire)

## Role

Microservice FastAPI d'extraction structuree de documents. Utilise Docling (IBM)
pour l'analyse de layout et PyMuPDF pour le crop d'images. Seul service avec acces GPU,
et seul service a ecrire dans NebulaGraph, ChromaDB et MinIO.

## Container

- `docling-service` : FastAPI + CUDA 12.1, port interne 8000

## Modele d'execution

L'extraction d'un livre de plusieurs centaines de pages dure des heures : elle ne
se fait donc pas dans la requete HTTP.

1. `POST /extract` valide le fichier, le met dans une file et rend un `job_id`.
2. Un **worker unique** deroule les jobs les uns apres les autres. Il est unique a
   dessein : la conversion sature deja le GPU, et c'est la file Dagster en amont
   qui cadence le debit global (`max_concurrent_runs` dans `dagster.yaml`).
3. L'asset Dagster interroge `GET /jobs/{job_id}` toutes les 15 secondes et
   journalise l'avancement, jusqu'a l'etat terminal.

L'event loop reste libre pendant la conversion : `/health` et `/jobs` repondent
meme au milieu d'un livre de 400 pages.

La file vit **en memoire**. Si le service redemarre, les jobs en cours sont perdus
et Dagster recoit un 404 explicite sur son prochain sondage : le run echoue avec
un message qui invite a relancer la partition, plutot que d'attendre indefiniment.

## API

| Methode | Endpoint         | Body                                   | Reponse                                              |
|---------|------------------|----------------------------------------|------------------------------------------------------|
| POST    | `/extract`       | `{"filepath": "/opt/.../fichier.pdf"}` | `{"job_id": "a1b2c3d4e5f6", "status": "pending"}`    |
| GET     | `/jobs/{job_id}` | —                                      | Etat, avancement, erreur eventuelle, duree           |
| GET     | `/health`        | —                                      | Etat de la file et disponibilite des stores          |

### Codes de retour

| Code | Endpoint     | Signification                                            |
|------|--------------|----------------------------------------------------------|
| 404  | `/extract`   | Fichier introuvable                                       |
| 415  | `/extract`   | Extension non prise en charge                             |
| 404  | `/jobs/{id}` | Job inconnu (service redemarre)                           |
| 503  | `/health`    | Worker ou stores pas encore prets                         |

### Exemple de reponse `/jobs/{job_id}`

```json
{
  "job_id": "a1b2c3d4e5f6",
  "filepath": "/opt/dagster/app/Datas/pdfs/statisticsfordatascience.pdf",
  "status": "running",
  "error": null,
  "progress": {
    "pages_total": 412,
    "pages_done": 145,
    "elements": 3820,
    "chunks": 4611,
    "failed_batches": []
  },
  "elapsed_seconds": 1832.4
}
```

`status` vaut `pending`, `running`, `success` ou `failed`.

## Formats pris en charge

| Extension            | Traitement                                                              |
|----------------------|-------------------------------------------------------------------------|
| `.pdf`               | Conversion par lots de pages, crop des images et tables vers MinIO       |
| `.html`, `.htm`      | Conversion d'un seul tenant ; les images ont deja ete exportees en amont |
| `.md`, `.markdown`   | Images extraites vers MinIO, paragraphes recolles, puis conversion       |

## Modules

| Module          | Responsabilite                                                       |
|-----------------|-----------------------------------------------------------------------|
| `main.py`       | Application FastAPI, endpoints, initialisation au demarrage           |
| `jobs.py`       | File de jobs et worker unique                                         |
| `extraction.py` | Conversion Docling, pagination des PDF, orchestration d'un document   |
| `elements.py`   | Taxonomie des labels, hierarchie et positions des elements            |
| `markdown.py`   | Markdown : extraction des images, normalisation des paragraphes        |
| `storage.py`    | Persistance d'un lot : graphe puis vecteurs                           |
| `nebula.py`     | Pool partage, sessions, ecritures groupees, schema                    |
| `ngql.py`       | Echappement et construction des requetes nGQL                         |
| `vectors.py`    | Embeddings par lots et upsert ChromaDB                                |
| `blocks.py`     | Regroupement des elements en blocs, filtrage du bruit de mise en page  |
| `chunking.py`   | Decoupage des textes longs, contextualisation des embeddings          |
| `images.py`     | Crop PyMuPDF, envoi de fichiers, export MinIO                         |

`ngql.py`, `chunking.py`, `elements.py`, `markdown.py` et `jobs.py` ne dependent que de
la bibliotheque standard : leur logique est testee sans Docling ni GPU.

## Variables d'environnement

| Variable             | Description                            | Defaut           |
|----------------------|----------------------------------------|------------------|
| MINIO_ENDPOINT       | Endpoint MinIO                         | minio:9000       |
| MINIO_ROOT_USER      | Access key MinIO                       | (voir .env)      |
| MINIO_ROOT_PASSWORD  | Secret key MinIO                       | (voir .env)      |
| MINIO_BUCKET         | Bucket pour les medias                 | documents        |
| NEBULA_HOST          | Hostname NebulaGraph                   | graphd           |
| NEBULA_PORT          | Port NebulaGraph                       | 9669             |
| CHROMA_HOST          | Hostname ChromaDB                      | chromadb         |
| CHROMA_PORT          | Port ChromaDB                          | 8000             |
| EMBEDDING_MODEL_NAME | Modele SentenceTransformers (multilingue) | paraphrase-multilingual-MiniLM-L12-v2 |
| PDF_BATCH_PAGES      | Pages converties par passe             | 5                |
| CHUNK_SIZE           | Taille d'un chunk vectorise (car.)     | 900              |
| CHUNK_OVERLAP        | Recouvrement entre chunks (car.)       | 150              |
| MIN_CHUNK_CHARS      | Plancher d'indexation d'un bloc (car.) | 24               |
| EMBED_SECTION_CONTEXT| Titre de section prepose a l'embedding | true             |
| EMBEDDING_BATCH_SIZE | Textes encodes par appel au modele     | 32               |
| CHROMA_UPSERT_BATCH  | Chunks par upsert ChromaDB             | 500              |
| GRAPH_TEXT_MAX_CHARS | Apercu du texte stocke dans le graphe  | 2000             |
| JOB_HISTORY_SIZE     | Jobs termines conserves en memoire     | 500              |

## Dependances

- `minio` (stockage images/tables croppees)
- `graphd` (insertion noeuds NebulaGraph)
- `chromadb` (vectorisation des elements texte)

## Ressources

- GPU NVIDIA (CUDA 12.1)
- RAM : 10 Go max (`deploy.resources.limits.memory`)
- SHM : 2 Go (`shm_size`)

## Healthcheck

Le service ne se declare pret qu'une fois les modeles charges, le schema
NebulaGraph initialise et le bucket MinIO disponible. Le healthcheck compose
interroge `/health` avec un `start_period` de 10 minutes, le temps du premier
telechargement des modeles.

```bash
curl -s http://localhost:8000/health
```
