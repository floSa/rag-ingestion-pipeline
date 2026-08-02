# Orchestrateur ETL (Dagster)

## Présentation du service
Dagster est le système nerveux central du projet RAG Assistant. C'est l'orchestrateur de données chargé de détecter les ajouts de documents dans les répertoires et d'automatiser (trigger) l'exécution des requêtes vers le service d'extraction, sans intervention humaine.

Il se compose de plusieurs sous-services distincts :
- **postgres-dagster** : Base de données PostgreSQL pour stocker les métadonnées de l'orchestrateur (historique d'exécution, états des senseurs).
- **dagster-webserver** : Interface utilisateur pour gérer et visualiser les pipelines (Jobs, Assets, Sensors).
- **dagster-daemon** : Composant de fond chargé d'activer régulièrement les Sensors définis dans le code Python.

## Accès au service
- **Interface UI Webserver** : [http://localhost:3002](http://localhost:3002)
- **Base de données interne** : `postgres-dagster:5432` (credentials : voir `.env`)

## Structure et définition des données
Côté développement, les éléments vitaux composant le graphe de données Dagster sont :
- **La déclaration des sources (`sources.yaml` + `sources.py`)** : Chaque source de documents (un dossier de PDFs, une capture de site en HTML, un dossier de notes Markdown...) est un bloc YAML : nom, motif glob relatif à `/opt/dagster/app/Datas`, type (`pdf`, `html` ou `md`) et options de nettoyage. Ajouter une source ne demande aucun code Python.
- **La factory (`factory.py`)** : Pour chaque source déclarée, elle génère les partitions dynamiques (une par fichier), les assets, le job (`{name}_job`) et le sensor (`{name}_sensor`). Les trois types suivent le même mécanisme ; les sources HTML ont simplement un asset de nettoyage (`cleaned_html`) en amont de l'extraction, dont PDF et Markdown n'ont pas besoin.
- **Le nettoyage HTML (`cleaning.py`)** : Pré-passe déterministe (scripts, styles, nav, images `data:` SingleFile) puis extraction du contenu principal via trafilatura, avec readability-lxml en secours et conservation du HTML pré-nettoyé en dernier recours.
- **La persistance des tâches (Le Curseur)** : Pour éviter qu'un livre ne soit ingéré à chaque redémarrage, chaque sensor sauvegarde la date de modification (`mtime`) de chaque fichier dans son curseur PostgreSQL. Si le fichier n'a pas été modifié depuis son traitement, Dagster l'ignore de manière silencieuse et robuste.
- **Les Partitions** : Définies dynamiquement, chaque fichier est une "Partition" (clé = chemin relatif) pour simplifier la réexécution d'un échec sur un livre précis (au lieu de réexécuter tout le pipeline global).
- **L'extraction** : L'asset `extracted_document` soumet le document au service Docling (`POST /extract`), qui rend un identifiant de job, puis suit son avancement (`GET /jobs/{id}`) jusqu'à la fin. Le service persiste lui-même les résultats dans NebulaGraph, ChromaDB et MinIO. Le bilan (éléments, chunks, pages, durée) est publié dans les métadonnées de l'asset.

## Cadencer le débit

Un corpus de plusieurs dizaines de livres crée autant de partitions et de runs. Deux limites empilées évitent de saturer la machine :

1. **La file Dagster** — `QueuedRunCoordinator` avec `max_concurrent_runs: 2` dans `dagster.yaml`. Sans limite explicite, le coordinateur en lance jusqu'à dix, soit autant de processus dans le conteneur daemon.
2. **Le worker du service d'extraction** — un seul document converti à la fois, la conversion saturant déjà le GPU.

Les runs en attente sont visibles dans **Runs → Queued**. Relever `max_concurrent_runs` n'accélère rien tant que le service reste mono-worker : c'est un levier à ne toucher que si l'extraction est parallélisée.

Avant de soumettre, l'asset attend que le service se déclare prêt (`GET /health`) : au démarrage de la stack, le chargement des modèles et l'initialisation du schéma NebulaGraph prennent plusieurs minutes, et le premier run échouerait pour une raison sans rapport avec le document.

## Combien de temps prend une ingestion

Chiffres mesurés sur 439 runs enregistrés dans la base Dagster, machine WSL2, extraction mono-worker, deux runs Dagster en parallèle. Ils servent à dimensionner une campagne, pas à qualifier le matériel : sur une autre machine, seuls les ordres de grandeur tiennent.

| Unité ingérée | Le plus rapide | Habituel (médiane) | Le plus lent |
|---|---|---|---|
| Une note Markdown | 2,5 s | **2,6 s** | 35 s |
| Un chapitre HTML (≈ 40 000 caractères) | 4,5 s | **6 s** | 68 s |
| Un PDF de 280 pages | 62 s | **1 min 50** | 4 min 11 |

Mesures de bout en bout sur des ensembles complets :

| Ensemble | Volume | Temps constaté |
|---|---|---|
| Practical MLOps | 22 chapitres HTML | 1 min 54 |
| The Statistics and Calculus with Python Workshop | 14 chapitres HTML | 1 min 08 |
| Notes Obsidian | 6 fichiers Markdown | 19 s |
| **Corpus complet** | **43 documents** | **4 min 32** |

Ramené à l'unité, pour estimer une campagne :

| Coût unitaire | Valeur |
|---|---|
| Un chapitre HTML | ≈ 6 s |
| Un livre en chapitres HTML | ≈ 5 s × nombre de chapitres |
| 100 pages de PDF | ≈ 22 s au mieux, **40 s** en régime courant |
| Un livre PDF de 300 pages | **1 à 2 min** |
| 50 livres de 300 pages | **1 h 30 à 2 h** |

Les écarts entre le meilleur et le pire temps ne viennent pas des documents mais de la concurrence : un chapitre HTML monte à 68 s lorsqu'un PDF de 280 pages occupe le worker au même moment. Le débit global reste stable, c'est la latence individuelle qui varie.

## Commandes utiles
Lors des phases d'architecture ou lorsque vous surveillez RAG Assistant :
- Dans l'interface Web (`http://localhost:3002`), allez dans l'onglet **Overview > Sensors** pour activer/désactiver l'ingestion automatique logicielle.
- **Vérifier l'état de l'Orchestrateur** (en cas de plantage d'un Job) :
  ```bash
  docker compose logs dagster-daemon --tail 50
  docker compose logs dagster-webserver --tail 50
  ```

## Problèmes rencontrés et solutions
- **Rechargement Intempestif des Tâches lors du Reboot** : 
  - *Problème* : Au redémarrage (ex: fermeture WSL), tous les services Docker n'étaient pas gardés persistants sauf Docling. La base PostgreSQL de Dagster étant perdue lors d'un restart, l'orchestrateur relançait le processus de traitement de tous les livres à zéro car il avait oublié les curseurs.
  - *Solution* : Ajout de la contrainte `restart: unless-stopped` sur tous les conteneurs dans le `docker-compose.yml`, pour qu'ils soient tous persistants tout comme la base d'états Dagster, verrouillant ainsi et pour de bon les données extraites au premier passage.
