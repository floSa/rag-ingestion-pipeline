# Docling Service (Extraction documentaire)

## Role

Microservice FastAPI d'extraction structuree de documents. Utilise Docling (IBM)
pour l'analyse de layout et PyMuPDF pour le crop d'images. **Seul service a ecrire
dans NebulaGraph, ChromaDB et MinIO.**

*(Cette phrase disait aussi « seul service avec acces GPU ». Le compose principal
ne reserve AUCUN GPU — la reservation ecrite en dur rendait le service
INCREABLE sans runtime nvidia, et elle vit desormais dans
`docker-compose.gpu.yml`, superposable. L'ingestion tourne sur processeur.
Registre §6.7.)*

## Container

- `docling-service` : FastAPI, port interne 8000

L'image embarque les wheels `torch` de l'index CUDA 12.1 — les wheels `+cu121`
n'existent pas sur PyPI — mais **elle n'exige pas de GPU** : sans runtime nvidia,
`torch` retombe sur le processeur et l'ingestion se deroule. C'est ce qui rend le
`docker-compose.gpu.yml` optionnel plutot que necessaire. Le prix est le poids de
l'image, **10,4 Go** : consigne au registre §6.12, non traite par le lot 5 — voir
la reserve en fin de section « Ressources ».

## Modele d'execution

L'extraction d'un livre de plusieurs centaines de pages dure des heures : elle ne
se fait donc pas dans la requete HTTP.

1. `POST /extract` valide le fichier, le met dans une file et rend un `job_id`.
2. Un **worker unique** deroule les jobs les uns apres les autres. Il est unique a
   dessein : la conversion sature deja la machine — le GPU s'il y en a un, les
   coeurs sinon — et c'est la file Dagster en amont qui cadence le debit global
   (`max_concurrent_runs` dans `dagster.yaml`).
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
| `chunking.py`   | Ce que le modele d'embedding recoit, la forme de l'id de chunk, le filtre du bruit |
| `images.py`     | Crop PyMuPDF, envoi de fichiers, export MinIO                         |

*(`blocks.py` a ete retire : il portait `build_blocks`, un regroupement maison
que `HybridChunker` a remplace et que plus rien n'appelait, plus une doctrine de
33 lignes que la production n'applique pas — registre §5.2. Son seul symbole
encore appele, `has_content`, a rejoint `chunking.py`.)*

**Quatorze modules ne dependent que de la bibliotheque standard**, et leur
logique est donc testee sans Docling, sans torch et sans GPU.

**LE CRITERE, ecrit parce qu'un balayage dont le critere n'est pas ecrit n'est
pas reproductible.** Le perimetre est les **18 modules** de
`src/docling_service/` — les `*.py` du repertoire, moins le marqueur de paquet
`__init__.py`, qui est vide. Un module porte une dependance externe quand une
instruction `import` ou `from ... import` du **corps du module** — donc ni dans
une fonction, ni dans une methode, ni derriere un `if` — nomme un paquet racine
qui n'est ni un import relatif, ni `src` ou `docling_service`, ni membre de
`sys.stdlib_module_names`. `mesure` le 2 septembre 2026, lecture a l'AST ; le
balayage est rejoue par `tests/unit/test_dependances_de_niveau_module.py`.

Les quatorze : `anchoring.py`, `chunking.py`, `elements.py`, `embedding.py`,
`hierarchy.py`, `jobs.py`, `language.py`, `markdown.py`, `matter.py`,
`nebula.py`, `ngql.py`, `ranking.py`, `storage.py`, `vectors.py`.

**Les quatre autres**, et il y en a quatre : `extraction.py` (`bs4`),
`images.py` (`minio`), `main.py` (`fastapi`), `settings.py`
(`pydantic_settings`).

> **Cette phrase annoncait ONZE et SEPT, et le sept NIAIT le deverrouillage
> livre par les lots 3 et 4.** Elle rangeait `embedding.py`, `nebula.py` et
> `vectors.py` parmi les modules a dependance de niveau module : leurs imports
> lourds sont **differes** dans la fonction qui en a besoin, et c'est
> precisement ce qui permet a `tests/unit/test_vectors.py` et
> `tests/unit/test_nebula.py` d'exister (registre §3.4, §4.4, §4.28.d). La
> preuve est dure et elle tient en deux mesures : `chromadb`, `nebula3` et
> `sentence_transformers` **ne sont pas dans le venv du depot**, et
> `uv run python -c "import src.docling_service.vectors"` — de meme pour
> `nebula` et `embedding` — rend `rc=0` cote hote.
>
> La liste d'origine ne venait pas d'un balayage : elle venait du registre §6.8
> corrige a la main. C'est la famille que ce lot existe pour fermer, dans le
> fichier ou il la ferme.

Ce compte n'est pas celui des modules **inimportables** cote hote — `bs4`,
`minio` et `pydantic_settings` vivent dans le venv du depot, donc trois des
quatre s'importent quand meme, et le seul qui ne s'importe pas est `main.py`
(`fastapi` absent du venv). Cette propriete-la, elle, est gardee par
`tests/unit/test_importabilite_cote_hote.py`.

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
| EMBEDDING_MODEL_NAME | Modele SentenceTransformers (multilingue). Verrouille : le service refuse de demarrer sur un autre modele (cf. base_vectorielle.md) | paraphrase-multilingual-MiniLM-L12-v2 |
| PDF_BATCH_PAGES      | Pages converties par passe             | 5                |
| MIN_CHUNK_CHARS      | Plancher d'indexation d'un bloc (car.) | 24               |
| EMBED_SECTION_CONTEXT| Titre de section prepose a l'embedding | true             |
| EMBEDDING_BATCH_SIZE | Textes encodes par appel au modele     | 32               |
| CHROMA_UPSERT_BATCH  | Chunks par upsert ChromaDB             | 500              |
| GRAPH_TEXT_MAX_CHARS | Apercu du texte stocke dans le graphe  | 2000             |
| JOB_HISTORY_SIZE     | Jobs termines conserves en memoire     | 500              |

> **`CHUNK_SIZE` et `CHUNK_OVERLAP` etaient annonces ici, avec les valeurs 900 et
> 150. Ils ne faisaient RIEN.** Aucun code ne les lisait (registre 5.1), et les
> defauts declares dans `settings.py` valaient de surcroit 450 et 75 : cette
> table contredisait le code sur des variables que le code ignorait. Le decoupage
> est confie a `HybridChunker`, qui coupe sur la **structure** du document et sur
> la fenetre du tokenizer du modele d'embedding, jamais sur un compte de
> caracteres. Il n'y a donc aucune taille de chunk a regler, et le debat « 900
> contre 450 » qui a occupe cette documentation etait vide.

## Volumes

Le cache des modeles est le volume nomme **`docling_models`**, monte sur
`/tmp/.cache` (`docker-compose.yml`). Le registre §6.13 consignait une divergence
de nommage — de la documentation mentionnant `rag_hf_cache` ou
`rag_models_cache` — : `mesure` le 2 septembre 2026, ces deux noms
n'apparaissent **nulle part** dans le depot, ni dans `docker-compose.yml`, ni
dans la documentation. La divergence est donc **sans objet**, et c'est ecrit ici
pour que personne ne la redecouvre comme un defaut. Sans consequence
fonctionnelle dans les deux cas.

## Dependances

- `minio` (stockage images/tables croppees)
- `graphd` (insertion noeuds NebulaGraph)
- `chromadb` (vectorisation des elements texte)

## Ressources

- GPU NVIDIA (CUDA 12.1) — **optionnel**, via `docker-compose.gpu.yml` ; sinon processeur

> **L'image pese 10,4 Go, et c'est un cout connu et NON traite** (registre §6.12).
> `Dockerfile.docling` installe `torch`, `torchvision` et `torchaudio` depuis
> l'index CUDA 12.1, ce qui embarque les bibliotheques CUDA alors que la chaine
> tourne sur processeur. Passer aux wheels CPU allegerait l'image de plusieurs
> gigaoctets.
>
> **Le lot 5 ne l'a pas fait, et voici pourquoi** : ce lot traite le code mort et
> l'ecart entre la documentation et le code. Changer l'index des wheels change
> l'IMAGE, donc demande une reconstruction et une reingestion pour verifier que
> l'extraction et l'encodage donnent les memes resultats — un chantier avec sa
> propre campagne de validation, pas une correction de documentation. La ligne
> reste ouverte au registre.
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
