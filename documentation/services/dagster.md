# Dagster (Orchestrateur ETL)

## Role

Orchestre le pipeline d'ingestion documentaire : detection de fichiers, appel au
service Docling, construction du graphe de connaissances, vectorisation.

## Containers

- `dagster-webserver` : UI web (port 3000)
- `dagster-daemon` : execution des sensors et runs en arriere-plan
- `postgres-dagster` : base PostgreSQL pour les metadonnees Dagster

## Variables d'environnement

| Variable               | Description                     | Defaut            |
|------------------------|---------------------------------|-------------------|
| DAGSTER_POSTGRES_USER  | Utilisateur PostgreSQL          | dagster           |
| DAGSTER_POSTGRES_PASSWORD | Mot de passe PostgreSQL      | (voir .env)       |
| DAGSTER_POSTGRES_DB    | Nom de la base                  | dagster           |
| DAGSTER_POSTGRES_HOST  | Hostname du conteneur Postgres  | postgres-dagster  |

## Dependances

- `postgres-dagster` (metadonnees, curseurs des sensors)
- `docling-service` (extraction et persistance via HTTP)
- `minio` (export des images inline des captures HTML, pendant le nettoyage)

Dagster n'ecrit ni dans ChromaDB ni dans NebulaGraph : c'est le service
d'extraction qui persiste dans les trois stores.

## Assets

Une factory genere les assets par source declaree dans `sources.yaml`, prefixes
par le nom de la source :

- `{source}/cleaned_html` — sources `html` uniquement : nettoyage universel du
  document et export des images inline vers MinIO ;
- `{source}/extracted_document` — soumet le document au service Docling, suit
  le job jusqu'a son terme, et publie le bilan (elements, chunks, pages, duree)
  dans les metadonnees de l'asset.

Les sources `pdf` et `md` n'ont que le second : elles n'ont rien a nettoyer.

## Sensors

Un sensor par source, nomme `{source}_sensor`, actif par defaut, evalue toutes
les 30 secondes. Chaque fichier trouve par le motif glob de la source devient
une partition dynamique (cle = chemin relatif a `Datas/`), et un run est
demande pour chaque fichier nouveau ou modifie. Le curseur, stocke en
PostgreSQL, retient la `mtime` deja traitee : un fichier inchange est ignore.

Avec les sources declarees par defaut : `pdfs_sensor`, `livres_html_sensor`,
`markdown_sensor`.

## File d'execution

`QueuedRunCoordinator` avec `max_concurrent_runs: 2` (voir `dagster.yaml`).
Cette limite est ce qui cadence l'ingestion d'un gros corpus : les runs en
attente sont visibles dans **Runs -> Queued**. La relever n'accelere rien tant
que le service d'extraction ne traite qu'un document a la fois.

## Healthcheck

```bash
curl -s http://localhost:3002/server_info | python3 -m json.tool
```

## Volumes

- `./src` monte dans `/opt/dagster/app/src`
- `./Datas` monte dans `/opt/dagster/app/Datas`
- `./dagster.yaml` monte dans `/opt/dagster/dagster_home/dagster.yaml`
