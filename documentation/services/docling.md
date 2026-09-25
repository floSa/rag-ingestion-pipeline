# Docling Service (Extraction documentaire)

## Role

Microservice FastAPI d'extraction structuree de documents. Utilise Docling (IBM)
pour l'analyse de layout et PyMuPDF pour le crop d'images. **Seul service a ecrire
dans NebulaGraph, ChromaDB et le stockage objet.**

Le compose principal ne reserve aucun GPU : l'ingestion tourne sur processeur.
Une reservation ecrite en dur empecherait de creer le service sur une machine
sans runtime nvidia. Elle vit donc dans `docker-compose.gpu.yml`, a superposer
au besoin (registre §6.7).

## Container

- `docling-service` : FastAPI, port interne 8000

L'image embarque les wheels `torch` de l'index CUDA 12.1 — les wheels `+cu121`
n'existent pas sur PyPI — mais **elle n'exige pas de GPU** : sans runtime nvidia,
`torch` retombe sur le processeur et l'ingestion se deroule. C'est ce qui rend le
`docker-compose.gpu.yml` optionnel plutot que necessaire. Le prix est le poids de
l'image, **10,4 Go** (registre §6.12) — voir la section « Ressources ».

## Modele d'execution

L'extraction d'un livre de plusieurs centaines de pages dure des heures : elle ne
se fait donc pas dans la requete HTTP.

1. `POST /extract` valide le fichier, le met dans une file et rend un `job_id`.
2. Un **worker unique** deroule les jobs les uns apres les autres. Il est unique a
   dessein : la conversion sature deja la machine — le GPU s'il y en a un, les
   coeurs sinon — et c'est la file Dagster en amont qui cadence le debit global
   (`max_concurrent_runs` dans `dagster.yaml`).
3. L'asset Dagster interroge `GET /jobs/{job_id}` et journalise l'avancement,
   jusqu'a l'etat terminal. Le premier sondage est immediat ; l'intervalle
   double ensuite jusqu'a 15 secondes (`extraction_poll_seconds`).

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
| `.pdf`               | Conversion par lots de pages, crop des images et tables vers le stockage |
| `.html`, `.htm`      | Conversion d'un seul tenant ; les images ont deja ete exportees en amont |
| `.md`, `.markdown`   | Images extraites vers le stockage, paragraphes recolles, puis conversion |

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
| `chunking.py`   | Ce que le modele d'embedding recoit, la forme de l'id de chunk, le filtre du bruit |
| `images.py`     | Crop PyMuPDF, envoi d'objets ; seul endroit ou le client S3 est construit |
| `anchoring.py`  | Rattachement des chunks Docling aux elements du contrat               |
| `hierarchy.py`  | Rattachement de chaque titre a son titre parent                       |
| `ranking.py`    | Rang d'un titre, quelle que soit la source                            |
| `matter.py`     | Reperage des parties d'un ouvrage qui ne sont pas du contenu (index, TDM...) |
| `language.py`   | Detection de la langue d'un document                                  |
| `embedding.py`  | Chargement et verrouillage du modele d'embedding                      |
| `settings.py`   | Configuration du service (pydantic-settings)                          |

**Quatorze modules ne dependent que de la bibliotheque standard**, et leur
logique est donc testee sans Docling, sans torch et sans GPU.

**Le critere.** Le perimetre est les **18 modules** de `src/docling_service/` :
les `*.py` du repertoire, moins le marqueur de paquet `__init__.py`, qui est
vide. Un module porte une dependance externe quand une instruction `import` ou
`from ... import` du **corps du module** (donc ni dans une fonction, ni dans une
methode, ni derriere un `if`) nomme un paquet racine qui n'est ni un import
relatif, ni `src` ou `docling_service`, ni membre de `sys.stdlib_module_names`.
Le balayage se fait a l'AST et est rejoue par
`tests/unit/test_dependances_de_niveau_module.py`.

Les quatorze : `anchoring.py`, `chunking.py`, `elements.py`, `embedding.py`,
`hierarchy.py`, `jobs.py`, `language.py`, `markdown.py`, `matter.py`,
`nebula.py`, `ngql.py`, `ranking.py`, `storage.py`, `vectors.py`.

**Les quatre autres** : `extraction.py` (`bs4`),
`images.py` (`minio`, la bibliotheque cliente S3), `main.py` (`fastapi`),
`settings.py` (`pydantic_settings`).

`embedding.py`, `nebula.py` et `vectors.py` figurent parmi les quatorze parce
que leurs imports lourds (`chromadb`, `nebula3`, `sentence_transformers`) sont
**differes** dans la fonction qui en a besoin. C'est ce qui permet a
`tests/unit/test_vectors.py` et `tests/unit/test_nebula.py` de s'executer sans
ces paquets, absents du venv du depot (registre §3.4, §4.4, §4.28.d).

Ce compte n'est pas celui des modules **inimportables** cote hote. `bs4`,
`minio` et `pydantic_settings` sont dans le venv du depot : trois des quatre
s'importent donc quand meme. Seul `main.py` ne s'importe pas (`fastapi` est
absent du venv). Cette propriete est verifiee par
`tests/unit/test_importabilite_cote_hote.py`.

## Variables d'environnement

| Variable             | Description                            | Defaut           |
|----------------------|----------------------------------------|------------------|
| S3_ENDPOINT          | Adresse de la passerelle S3            | **AUCUN** (exige)|
| S3_ACCESS_KEY        | Cle d'acces au stockage objet          | (voir .env)      |
| S3_SECRET_KEY        | Cle secrete du stockage objet          | (voir .env)      |
| S3_BUCKET            | Bucket pour les medias                 | documents        |
| NEBULA_HOST          | Hostname NebulaGraph                   | graphd           |
| NEBULA_PORT          | Port NebulaGraph                       | 9669             |
| CHROMA_HOST          | Hostname ChromaDB                      | chromadb         |
| CHROMA_PORT          | Port ChromaDB                          | 8000             |
| EMBEDDING_MODEL_NAME | Modele SentenceTransformers (multilingue). Verrouille : le service refuse de demarrer sur un autre modele (cf. base_vectorielle.md) | paraphrase-multilingual-MiniLM-L12-v2 |
| PDF_BATCH_PAGES      | Pages converties par passe             | 5                |
| MIN_CHUNK_CHARS      | Plancher d'indexation d'un bloc (car.) | 24               |
| EMBED_SECTION_CONTEXT| Titre de section prepose a l'embedding | true             |
| EMBEDDING_BATCH_SIZE | Textes encodes par appel au modele     | 32               |
| CHROMA_UPSERT_BATCH  | Chunks par upsert ChromaDB             | 500              |
| GRAPH_TEXT_MAX_CHARS | Apercu du texte stocke dans le graphe  | 2000             |
| JOB_HISTORY_SIZE     | Jobs termines conserves en memoire     | 500              |

Il n'existe pas de taille de chunk a regler : aucune variable `CHUNK_SIZE` ni
`CHUNK_OVERLAP` n'est lue (registre 5.1). Le decoupage est confie a
`HybridChunker`, qui coupe sur la **structure** du document et sur la fenetre du
tokenizer du modele d'embedding, jamais sur un compte de caracteres.

## Volumes

Le cache des modeles est le volume nomme **`docling_models`**, monte sur
`/tmp/.cache` (`docker-compose.yml`). Il conserve les modeles Docling et
d'embedding entre deux recreations du conteneur. Les noms `rag_hf_cache` et
`rag_models_cache`, cites au registre §6.13, n'existent nulle part dans le
depot.

## Dependances

- le stockage objet (images et tables croppees) — fiche :
  [`stockage_objet.md`](stockage_objet.md)
- `graphd` (insertion noeuds NebulaGraph)
- `chromadb` (vectorisation des elements texte)

## Ressources

- GPU NVIDIA (CUDA 12.1) — **optionnel**, via `docker-compose.gpu.yml` ; sinon processeur
- RAM : 10 Go max (`deploy.resources.limits.memory`)
- SHM : 2 Go (`shm_size`)

**Poids de l'image : 10,4 Go** (registre §6.12, ouvert). `Dockerfile.docling`
installe `torch`, `torchvision` et `torchaudio` depuis l'index CUDA 12.1, ce qui
embarque les bibliotheques CUDA alors que la chaine tourne sur processeur.
Passer aux wheels CPU allegerait l'image de plusieurs gigaoctets. Ce changement
modifie l'image : il demande une reconstruction et une reingestion de controle
pour verifier que l'extraction et l'encodage donnent les memes resultats.

## Healthcheck

Le service ne se declare pret qu'une fois les modeles charges, le schema
NebulaGraph initialise et le bucket du stockage objet disponible. Le healthcheck compose interroge `/health` avec un `start_period`
de 10 minutes, le temps du premier telechargement des modeles.

Le port n'est pas publie sur l'hote : la sonde se lance dans le conteneur.

```bash
docker compose exec docling-service python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```
