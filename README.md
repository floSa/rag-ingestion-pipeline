# RAG Assistant Pipeline

Pipeline d'ingestion : il lit des livres techniques (PDF, HTML, Markdown) et en
produit trois stores qu'un agent conversationnel interroge — un **graphe** (la
structure), un **index vectoriel** (la recherche par le sens) et un **stockage
d'objets** (les images). Il ne répond à aucune question : ce rôle est celui de
[`rag-agent-chat`](https://github.com/floSa/rag-agent-chat), un autre dépôt.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Dagster](https://img.shields.io/badge/Dagster-1.13.16-654FF0?logo=dagster&logoColor=white)

## Les services

- **`docling-service`** (FastAPI) : extraction Docling, découpage, encodage.
  Seul service à écrire dans les trois stores ; un document à la fois.
- **Dagster** : une fabrique lit `src/pipeline/sources.yaml` et génère pour
  chaque source ses partitions, son job et son capteur.
- **NebulaGraph** (le graphe), **ChromaDB** (l'index vectoriel), **SeaweedFS**
  (le stockage d'objets, par sa passerelle S3 ; seule `S3_ENDPOINT` le désigne).
- Tout tourne par Docker Compose, sur processeur.

## Où commencer à lire

1. **Ce `README.md`.**
2. **[`documentation/livraison.md`](documentation/livraison.md)** — le mode
   d'emploi complet : lancer, ingérer, réingérer, purger, vérifier, revenir en
   arrière, défauts connus, prochaines étapes. Chaque commande y est vérifiée et
   datée.
3. [`documentation/etat_des_lieux.md`](documentation/etat_des_lieux.md) — ce que
   le projet garantit, sans le lancer.
4. [`documentation/guide_du_depot.md`](documentation/guide_du_depot.md) —
   l'exploitation courante et l'organisation du dépôt (licences comprises).
5. Les fiches : [architecture](documentation/architecture.md),
   [extraction](documentation/extraction_donnees.md),
   [graphe](documentation/graphe_connaissances.md),
   [index vectoriel](documentation/base_vectorielle.md),
   [stockage d'objets](documentation/stockage_objets.md),
   [orchestration](documentation/orchestration.md),
   [sécurité](documentation/SECURITY.md),
   [services](documentation/services/).

**Archives datées, pas mode d'emploi** :
[`documentation/axes_amelioration.md`](documentation/axes_amelioration.md) (le
registre : contrat avec l'agent, défauts, mesures),
[`documentation/pilotage_du_chantier.md`](documentation/pilotage_du_chantier.md)
et [`documentation/campagnes/`](documentation/campagnes/) (comptes rendus de
mesure). Elles gardent l'historique et les mesures ; elles ne décrivent pas
forcément l'état actuel.

## Démarrer

```bash
cp .env.example .env          # puis remplir (voir livraison.md §2.2)
docker compose up -d --build  # pile neuve seulement (voir livraison.md §6)
docker compose ps             # seaweedfs et docling-service : « healthy »
```

Dagster : `http://localhost:3002`. Nebula Studio : `http://localhost:7001`.
Un fichier déposé dans `Datas/pdfs/`, `Datas/htms/` ou `Datas/mds/` est vu dans
les 30 s par le capteur de sa source — `pdfs_sensor`, `livres_html_sensor` ou
`markdown_sensor` — et ingéré automatiquement.

Un fichier inchangé n'est pas réingéré. Pour le réingérer, poser sur le curseur
du capteur le marqueur `reingerer:<étiquette>`, avec une **étiquette neuve**
à chaque fois (commande, purge et contrôles : livraison.md §3).

## Vérifier

```bash
uv sync && make all   # porte qualité : lint, typecheck, test, mutations, format-check
```

Les sondes des stores (`verify_contract`, `index_report`, `comparer`, jeu de
questions, rappel) et leurs dernières mesures sont au §4 de livraison.md.
