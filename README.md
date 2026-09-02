# RAG Assistant Pipeline

**Pipeline d'ingestion de documents PDF, HTML et Markdown pour un assistant RAG : extraction structurée via Docling, graphe de connaissances NebulaGraph, base vectorielle ChromaDB, médias sur MinIO, orchestration Dagster.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Dagster](https://img.shields.io/badge/Dagster-1.13.16-654FF0?logo=dagster&logoColor=white)

---

## Architecture & Technologies

- **Docling-Service (FastAPI)** : Microservice dédié à l'extraction de documents via Docling (sur GPU). Les images et tableaux complexes en sont extraits et découpés *(crop)* avec PyMuPDF. L'extraction est **asynchrone** : `POST /extract` met en file et rend un identifiant de job, qu'un worker unique déroule pendant que Dagster suit l'avancement.
- **Orchestration (Dagster)** : Les sources sont déclarées dans `src/pipeline/sources.yaml` ; une factory génère pour chacune ses partitions (une par fichier), son job et son sensor. Les sources HTML passent par un asset de nettoyage universel (trafilatura + readability) avant extraction ; les PDF et les Markdown partent directement à l'extraction.
- **Base Graphe** : [NebulaGraph](https://nebula-graph.io/) couplé au Studio pour créer la cartographie relationnelle (Document > Section > Text > Image/Table).
- **Base Vectorielle** : [ChromaDB](https://www.trychroma.com/) couplé à des modèles d'embeddings locaux (`SentenceTransformers`).
- **Stockage Objet** : [MinIO](https://min.io/) pour héberger les images extraites et récupérables via la clé `minio_url`.
- **Plateforme** : Entièrement déployé sous forme de conteneurs multi-services via Docker-Compose.

### Schéma du pipeline

```mermaid
flowchart TD
    A["Datas/ — tes fichiers PDF, HTML & Markdown"] --> S1["pdfs_sensor (scan 30 s)"]
    A --> S2["livres_html_sensor (scan 30 s)"]
    A --> S3["markdown_sensor (scan 30 s)"]
    S1 -- "1 partition + 1 run par fichier" --> E1["asset pdfs/extracted_document"]
    S3 -- "1 partition + 1 run par fichier" --> E3["asset markdown/extracted_document"]
    S2 -- "1 partition + 1 run par fichier" --> C["asset livres_html/cleaned_html<br/>nettoyage universel"]
    C -- "chemin du HTML nettoyé" --> E2["asset livres_html/extracted_document"]
    E1 -- "POST /extract → job_id" --> D["Service Docling (GPU)<br/>file de jobs, 1 document à la fois<br/>PDF : lots de 5 pages<br/>HTML & Markdown : conversion directe"]
    E2 -- "POST /extract → job_id" --> D
    E3 -- "POST /extract → job_id" --> D
    D -. "GET /jobs/{id} — avancement" .-> E1
    D --> N["NebulaGraph<br/>graphe du document"]
    D --> V["ChromaDB<br/>vecteurs du texte, découpés"]
    D --> M["MinIO<br/>images croppées"]
```

Chaque source déclarée dans `sources.yaml` génère sa propre chaîne (sensor → partitions → assets → job), préfixée par son nom. Le service Docling est le seul à écrire dans les trois stores.

Le débit est cadencé à deux niveaux : la file Dagster (`max_concurrent_runs: 2` dans `dagster.yaml`) limite le nombre de runs simultanés, et le service Docling ne convertit qu'un document à la fois. Un corpus de plusieurs dizaines de livres se déroule donc sans saturer la machine — il prend le temps qu'il prend, et l'avancement de chaque document est visible dans les logs de son run.

> Détails : [documentation/architecture.md](documentation/architecture.md)

## Documentation

| Document | Contenu |
|---|---|
| **[CHANGEMENTS.md](documentation/CHANGEMENTS.md)** | **Ce qui a changé et ce que ça implique côté `rag-agent-chat` — à lire en premier** |
| [architecture.md](documentation/architecture.md) | Services, flux de bout en bout, décisions d'architecture |
| [SECURITY.md](documentation/SECURITY.md) | Secrets, isolation réseau, audit des dépendances |
| [services/](documentation/services/) | Une fiche technique par service (ChromaDB, Dagster, Docling, MinIO, NebulaGraph, PostgreSQL) |
| [extraction_donnees.md](documentation/extraction_donnees.md) | Algorithme d'extraction Docling, crop des médias, format JSON |
| [orchestration.md](documentation/orchestration.md) | Détail de l'orchestrateur Dagster |
| [graphe_connaissances.md](documentation/graphe_connaissances.md) | Modèle de graphe NebulaGraph et requêtes nGQL |
| [base_vectorielle.md](documentation/base_vectorielle.md) | Stockage et recherche vectorielle ChromaDB |
| [stockage_objets.md](documentation/stockage_objets.md) | Stockage des médias sur MinIO |
| [llm_integration_plan.md](documentation/llm_integration_plan.md) | Contrat d'interface avec l'agent RAG (projet séparé) |
| [rag_evaluation_strategy.md](documentation/rag_evaluation_strategy.md) | Stratégie d'évaluation du RAG |
| **[axes_amelioration.md](documentation/axes_amelioration.md)** | **Le registre du chantier : le contrat avec `rag-agent-chat`, ce qui est ouvert, ce qui est traité — chaque ligne vérifiable dans le code** |
| **[pilotage_du_chantier.md](documentation/pilotage_du_chantier.md)** | **Le mandat du pilote : reprise sur un poste neuf, état du chantier, plan de lots, conventions** |

## Quickstart

### 1. Configurer l'environnement
```bash
# Copier le gabarit et remplir les valeurs (notamment les mots de passe)
cp .env.example .env
# Générer un mot de passe MinIO sécurisé :
# openssl rand -base64 24
```

### 2. Démarrer les services
Docker suffit : **la stack démarre sur processeur, sans GPU ni NVIDIA Container Toolkit**.
```bash
# Construire et lancer toute la stack en arrière-plan
docker compose up -d --build
```

> **Vérifiez le modèle d'embedding avant d'ingérer quoi que ce soit.** Il doit être identique
> à celui de `rag-agent-chat`, et un désaccord ne lève aucune erreur — la recherche rend des
> passages plausibles et faux. Le service refuse désormais de démarrer sur un autre modèle ;
> si le conteneur meurt au lancement, lisez son journal avant toute autre hypothèse :
> ```bash
> docker compose exec docling-service printenv EMBEDDING_MODEL_NAME
> ```

> **Après avoir modifié `.env`**, `docker compose restart` ne suffit pas : il relance le conteneur avec son ancien environnement. Utilisez `docker compose up -d --force-recreate <service>`, puis vérifiez avec `docker compose exec <service> printenv <VARIABLE>`.

> Construisez bien **toute** la stack. `dagster-webserver` et `dagster-daemon` partagent le même `Dockerfile.dagster` mais donnent deux images distinctes : n'en reconstruire qu'une laisse l'autre sur l'ancienne base, et les runs s'exécutent dans le *daemon*.

**Machine avec GPU ?** Le compose principal ne réserve **aucun** GPU : une réservation
`nvidia` écrite en dur rend le service *incréable* là où le runtime manque, avec un
`could not select device driver "nvidia"` qui bloque toute la stack. Pour rendre un GPU à
Docling, superposez le fichier prévu :
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```
L'extraction et l'encodage y gagnent en vitesse ; rien d'autre ne change.

### 3. Accéder aux interfaces
| Service | URL | Note |
| :--- | :--- | :--- |
| **Dagster (UI)** | [http://localhost:3002](http://localhost:3002) | Gestion, exécution des assets et activation des Sensors. |
| **Nebula Studio** | [http://localhost:7001](http://localhost:7001) | **Host:** `graphd` \| **Port:** `9669` \| Credentials : voir `.env` |
| **MinIO Console** | [http://localhost:9001](http://localhost:9001) | Credentials : voir `.env` |
| **Docling API** | `http://localhost:8000/health` | `POST /extract` rend un `job_id`, suivi sur `GET /jobs/{id}`. |
| **ChromaDB** | `http://localhost:8080/api/v1` | Point d'entrée de la base vectorielle. |

Seuls Dagster et Nebula Studio sont exposés par `docker-compose.yml`. Les autres services ne le sont que via un `docker-compose.override.yml` (gitignoré) que vous créez : si l'un de ces ports est déjà pris sur votre machine, c'est là qu'il faut le décaler.

### 4. Lancer l'ingestion
1. Placez vos fichiers dans le dossier `./Datas` de la racine du projet (par défaut : `Datas/pdfs/` pour les PDF, `Datas/htms/` pour les HTML, `Datas/mds/` pour le Markdown).
2. Ouvrez l'interface **Dagster** : chaque source déclarée dans `src/pipeline/sources.yaml` a son propre sensor (`pdfs_sensor`, `livres_html_sensor`, ...), actif par défaut dans **Overview -> Sensors**.
3. Le système détecte automatiquement un nouveau fichier (une partition par fichier) et lance le pipeline complet pour l'ingérer dans Nebula, ChromaDB, et MinIO !

---

## Ajouter une nouvelle source

Ajouter une source (ex: un site capturé avec [SingleFile](https://github.com/gildas-lormeau/SingleFile)) ne demande **aucun code Python** :

1. Déposez les fichiers dans un sous-dossier de `./Datas`, ex. `Datas/captures/monsite/`.
2. Déclarez la source dans `src/pipeline/sources.yaml` :
   ```yaml
   - name: capture_monsite
     glob: "captures/monsite/**/*.html"
     type: html
     cleaning:                                    # optionnel
       extra_remove_selectors: [".cookie-banner"]
   ```
3. Rechargez le code location dans l'UI Dagster (bouton **Reload definitions**). Un sensor `capture_monsite_sensor` apparaît et ingère les fichiers.

### Les trois types de sources

| `type` | Chaîne d'assets | Quand l'utiliser |
|---|---|---|
| `pdf` | extraction directe, par lots de pages, images croppées vers MinIO | Livres et documents paginés |
| `html` | nettoyage universel puis extraction | Captures de sites, livres découpés en chapitres HTML |
| `md` | extraction directe | Markdown déjà propre : notes, exports, documentation |

Le Markdown ne passe pas par le nettoyage : il n'a ni boilerplate à retirer ni image inline à exporter.

```yaml
- name: markdown
  glob: "mds/**/*.md"
  type: md
```

Deux points méritent d'être connus.

**Docling convertit le Markdown ligne par ligne.** Un fichier dont les paragraphes sont coupés à 80 colonnes produirait donc un élément par ligne, et la recherche porterait sur des fragments de 75 caractères. Les paragraphes sont pour cette raison recollés avant conversion — sans toucher au fichier source, et en laissant intacts blocs de code, tableaux, listes, titres et retours à la ligne explicites.

**Un Markdown ne contient jamais ses images, il les désigne.** Les deux syntaxes sont reconnues — `![[fichier.jpg|1000]]` d'Obsidian et `![légende](chemin)` du standard — et les images sont envoyées sur MinIO puis rattachées à leur place exacte dans le document. Corollaire : **copiez le dossier entier**, notes *et* pièces jointes. Une note copiée seule perd ses figures.

### Nettoyage HTML universel

Les sources HTML passent par un nettoyage en étages, sans configuration par site :
1. **Formules mathématiques** : les formules rendues (KaTeX, MathJax v2/v3, MathML) sont remplacées par leur source LaTeX — `$...$` (inline) ou `$$...$$` (bloc) — récupérée dans le DOM avant toute suppression. Sans ça, le rendu web produit du texte dupliqué illisible.
2. **Pré-passe d'hygiène** : suppression des scripts, styles, éléments cachés (`sf-hidden`, `display:none`), chrome de page (nav, rôles ARIA), commentaires, icônes inline (< 4 Ko) et décorations d'ancres dans les titres. Les **images base64 volumineuses sont exportées vers MinIO** et leur `src` réécrit (comme les crops PDF) ; les `header`, `footer` et `aside` internes à un `<article>`/`<main>` sont conservés — le premier porte le titre de chapitre, le dernier les encadrés du livre (interviews, notes, avertissements).
3. **Extraction de contenu** : un profil par site (s'il est déclaré) gagne directement ; sinon les conteneurs sémantiques HTML5 (`<article>`, `<main>`) font autorité ; sinon [trafilatura](https://trafilatura.readthedocs.io/) et readability-lxml sont comparés et le plus complet gagne. Si aucun `<h1>` ne survit, le titre de la page est réinjecté (structure propre pour Docling).
4. **Garde-fou** : si trop peu de texte est extrait, le HTML pré-nettoyé est conservé tel quel (rien n'est perdu) et un warning apparaît dans les logs Dagster.

**Ce que « nettoyer » retire exactement.** Le fichier maigrit énormément — une capture SingleFile de 2,8 Mo tombe à 62 Ko — mais **cette division par 45 porte sur le poids du fichier, pas sur le contenu**. Le volume d'une capture est fait de scripts, de feuilles de style et d'images encodées en base64 dans le HTML lui-même. Mesuré sur les chapitres de `Practical MLOps` :

| | Avant nettoyage | Après nettoyage |
|---|---|---|
| Poids du fichier | 2,77 Mo | 61,6 Ko |
| Caractères de texte | 34 704 | 34 316 |
| Blocs de code | 186 | 186 |
| Images | 13 | 13 (déplacées sur MinIO) |
| Tableaux | conservés | conservés |

**Le texte perd environ 1 %**, et ce 1 % est le chrome du lecteur : « Table of contents », « Search », « Sign out ». Code, images, tableaux et titres passent intégralement.

La stratégie retenue, les tailles avant/après et le nombre d'images exportées sont visibles dans les métadonnées de l'asset `cleaned_html` de chaque partition. Si un site ressort mal, déclarez-lui un profil `detect`/`content`/`strip` dans `sources.yaml` (voir l'exemple en tête du fichier).

### Ce qui n'est ingéré qu'une fois : détection des doublons

Sur une bibliothèque constituée au fil des années, le même ouvrage revient sous deux noms — une copie de sauvegarde, un téléchargement refait. Deux cas, deux traitements :

| Cas | Traitement |
|---|---|
| **Le même fichier, même chemin, ré-ingéré** | Les identifiants d'éléments sont déterministes et les écritures sont des *upserts* : la nouvelle version écrase l'ancienne. Aucun doublon possible, c'est acquis depuis le début. |
| **Le même fichier, sous un autre nom ou un autre dossier** | L'empreinte SHA-256 du fichier est portée par le nœud `Document`. Avant toute conversion, le service cherche si un autre document porte la même empreinte : si oui, le fichier est **ignoré**, le run réussit et signale `duplicate_of` avec le chemin de l'original. |

Le contrôle a lieu **avant la conversion** : reconnaître un doublon coûte une lecture de fichier, le convertir pour rien coûte plusieurs minutes de GPU.

**Ce que ça ne détecte pas, volontairement** : deux éditions différentes du même livre, ou le même ouvrage en PDF et en HTML. Les fichiers diffèrent, donc les empreintes aussi. Les rapprocher demanderait une comparaison approximative, qui écarterait à tort des ouvrages légitimes — un risque plus grave que le doublon lui-même.

### La hiérarchie des titres

Un chapitre contient des sections, qui contiennent des sous-sections. Cette imbrication est reconstruite à l'ingestion, **avec une règle unique pour les trois formats** :

> Le parent d'un titre est le titre précédent de **rang supérieur**. Les autres éléments se rattachent au titre le plus profond encore ouvert.

Ce qui change d'un format à l'autre n'est pas la règle, mais d'où vient le rang :

| Format | Signal | Résultat |
|---|---|---|
| **HTML** | le parent que Docling déclare | hiérarchie fidèle, jusqu'à 4 niveaux |
| **Markdown** | l'attribut `level` (1 pour `##`, 2 pour `###`) | fidèle aux dièses du fichier |
| **PDF** | la **taille de police**, lue dans le fichier | reconstruite, voir ci-dessous |

Le code n'a aucune branche par format : il essaie les signaux dans l'ordre et prend le premier qui répond. **Si aucun ne répond, tous les titres restent frères sous le document** — le comportement d'avant. La hiérarchie n'est jamais inventée.

**Le cas des PDF.** Docling ne déclare aucun parent sur un PDF et met tous les titres au même niveau — mesuré : 333 en-têtes, tous au niveau 1. Mais la taille de police est **écrite en clair dans le fichier** ; on la lit, on ne l'estime pas. Le relevé se fait une fois par document : la taille qui porte le plus de caractères est celle du corps du texte, les tailles supérieures sont celles des titres, et leur rang donne le niveau.

**Aucune valeur n'est écrite en dur** — un ouvrage composé en 24/22/20 points se segmente exactement comme un ouvrage en 20/18/16.

Deux garde-fous : un titre dont la boîte est **contenue dans une image ou un tableau** est écarté (le texte d'une figure peut être grand), et un titre **pas plus grand que le corps du texte** n'ouvre pas de niveau. Un titre écarté prend le rang le plus profond, jamais le rang zéro — le promouvoir chapitre remettrait tout l'arbre à zéro.

La profondeur est plafonnée à 3 : l'objectif est de reconstruire un bloc avec ses titres parents pour l'agent, pas de reproduire une arborescence complète.

**Vérifié contre le sommaire imprimé de l'ouvrage, ligne à ligne :**

```
[0] 3
[0] A Developer's Approach to Data Cleaning
    [1] Understanding basic data cleaning
        [2] Common data issues
        [2] Contextual data issues
    [1] R and common data issues
        [2] Outliers
            [3] Step 1 – Profiling the data
            [3] Step 2 – Addressing the outliers
        [2] Domain expertise
    [1] Summary
```

Chaque chunk porte sa profondeur sous la clé `depth`. Le rapport d'index (`src/index_report.py`) compte les documents par profondeur atteinte, ce qui montre d'un coup d'œil lesquels sont restés plats.

### La langue de chaque document

Chaque document est identifié dans l'une de sept langues (`en`, `fr`, `es`, `de`, `it`, `pt`, `nl`), et cette langue est portée par le nœud `Document` **et par chaque chunk** — l'agent peut donc filtrer sans repasser par le graphe. La valeur reste vide quand le doute est permis.

C'est important : le modèle qui transforme le texte en vecteurs n'est entraîné que sur de l'anglais. Une question française sur un livre anglais fait remonter les passages français, même hors sujet. Le détail, la mesure et les quatre façons de traiter le problème sont dans [base_vectorielle.md](documentation/base_vectorielle.md#limite-mesurée--le-modèle-dembedding-ne-parle-quanglais).

### Ce qui n'est pas ingéré : index, sommaire, pages liminaires

Un livre ne contient pas que du livre. **L'index est le pire cas pour un RAG** : une liste de mots suivis de numéros de page, sans une seule phrase à indexer, mais qui contient tout le vocabulaire de l'ouvrage — il ressort donc sur presque toutes les questions sans jamais rien apporter. Sommaire, couverture et page de copyright ont le même profil.

Sont écartés par défaut : `Index`, `Table of Contents`, `Contents`, `Cover`, `Copyright`, `Credits`, `Colophon`, `Title page`, `Dedication`, `About the author`, `About the reviewer`, et leurs équivalents français. La liste complète est `FRONT_BACK_MATTER_TITLES` dans [`matter.py`](src/docling_service/matter.py).

**Ne sont pas écartés, volontairement** : préface, glossaire (`Key Terms`) et annexes. C'est de la prose, et un glossaire répond même très bien aux questions « c'est quoi X ? ».

Le repérage dépend du format :

| Format | Comment la partie est reconnue |
|---|---|
| Livre découpé en fichiers (HTML, MD) | **Par le nom du fichier**. `Index.html` n'est même pas transformé en partition : ni run, ni place, ni bruit. |
| PDF | **Par les signets du document** — l'arborescence cliquable du volet gauche d'un lecteur PDF. Elle donne le titre de chaque partie et sa page de début. |

**Le décalage des pages ne se pose pas.** C'est le piège attendu : dans un livre, le sommaire imprimé annonce des numéros qui ne correspondent pas au rang réel de la page dans le fichier, avec un écart d'une à deux pages. Les signets, eux, ne portent pas un numéro imprimé mais une **destination interne** que le format résout en page physique. Vérifié sur `statisticsfordatascience.pdf` : le sommaire imprimé annonce la préface page 1, alors qu'elle commence physiquement page 19 — et le signet donne bien 19.

**Filet de sortie.** Beaucoup de PDF n'ont pas de signets, ou en ont d'incomplets. Dans ce cas l'index est reconnu **à sa forme** : des lignes courtes terminées par un ou plusieurs numéros de page, cherchées uniquement dans le dernier quart du document pour ne pas confondre un index avec un tableau de résultats en plein chapitre.

Un garde-fou refuse d'écarter plus de 35 % d'un document : au-delà, c'est forcément un signet parent mal interprété, pas un index.

Pour ajuster, par source dans `sources.yaml` :

```yaml
  - name: livres_html
    glob: "htms/**/*.html"
    type: html
    skip_front_back_matter: true          # défaut
    extra_skip_titles: ["About this book"] # ajouts à la liste par défaut
```

Le nombre de pages écartées apparaît dans les métadonnées du job (`skipped_pages`) et dans les logs du service.

---

## Ingestion à grande échelle

Un corpus de plusieurs dizaines de livres de 300 à 400 pages se déroule sans intervention, mais demande de savoir quoi regarder.

**Comment le débit est cadencé.** Le sensor crée une partition et un run par fichier. La file Dagster n'en exécute que deux à la fois (`max_concurrent_runs` dans `dagster.yaml`), et le service Docling ne convertit qu'un document à la fois : les autres runs attendent visiblement dans **Runs → Queued**. Rien ne sature, rien ne se perd, et l'ordre est celui de la découverte.

Validé en conditions réelles : 120 fichiers déposés d'un coup produisent **120 partitions et 120 runs dans un seul passage de sensor**, drainés en 6 minutes. Un test unitaire garde cette propriété jusqu'à 250 fichiers.

**Si le service redémarre en cours de route.** Sa file de jobs vit en mémoire : les documents en cours sont perdus. Les assets portent pour cette raison une politique de reprise (deux tentatives, délai croissant) qui rattrape le cas sans intervention. Les échecs propres à un document, eux, ne sont pas retentés — inutile de reconvertir 400 pages pour retomber sur la même page illisible.

**Suivre un document.** Chaque run journalise l'avancement de son job toutes les 15 secondes : pages traitées, éléments extraits, chunks écrits. À la fin, les métadonnées de l'asset `extracted_document` récapitulent le total et la durée. Côté service :

```bash
docker compose logs -f docling-service
```

**Ce qui fait échouer un run — et ce que ça veut dire.**

| Message | Cause | Quoi faire |
|---|---|---|
| `Job ... inconnu du service Docling (redémarrage ?)` | Le service a redémarré, la file est en mémoire | Relancer la partition depuis l'UI Dagster |
| `N batch(s) non convertis` | Des pages n'ont pas pu être lues par Docling | Les autres pages sont bien ingérées ; le message liste les pages manquantes |
| `Service Docling toujours pas prêt` | Modèles ou schéma NebulaGraph pas encore initialisés | Attendre la fin du démarrage (`docker compose ps` : `healthy`) |
| `nGQL rejeté ...` | Écriture refusée par le graphe | Le run échoue volontairement plutôt que de laisser un graphe incomplet |

**Ré-ingérer proprement.** Les identifiants d'éléments sont déterministes : ré-ingérer un document écrase ses nœuds et ses vecteurs au lieu de les dupliquer. Pour repartir de zéro, le script tourne **dans le réseau Docker** (il s'adresse à `chromadb`, `graphd` et `minio` par leur nom de service) :

```bash
docker compose exec docling-service python -m src.wipe_stores
```

Il purge **quatre** choses, et non trois. Cette phrase disait « les trois stores — collection ChromaDB, space NebulaGraph et bucket MinIO » : c'était une phrase d'exhaustivité, et le lot 4 l'a rendue fausse en ajoutant la quatrième sans la compter ici. Le compte est `mesuré` sur la sortie du script, qui titre chacune (`--- ChromaDB ---`, `--- MinIO ---`, `--- NebulaGraph ---`, `--- HTML nettoyé ---`) :

| Ce qui est purgé | Pourquoi il y est |
|---|---|
| la collection ChromaDB `rag_documents` | les vecteurs |
| le space NebulaGraph `rag_space` | le graphe |
| le bucket MinIO `documents` | les crops d'images ; ils survivaient à toute purge avant le lot 4 |
| `Datas/.cleaned/` | **le piège le plus discret.** Le HTML nettoyé porte les URL MinIO des images, et l'asset `cleaned_html` ne se rematérialise pas si son fichier existe déjà : une purge suivie d'une réingestion repartait du HTML **périmé**, pointant les objets que la purge venait de supprimer |

**Le sous-répertoire `.cleaned` n'est pas configurable, et c'est délibéré.** `CLEANED_SUBDIR` a été un réglage annoncé dans `.env.example`, et il décidait à lui seul de la cible de ce `rmtree`. Quatre valeurs faisaient viser `Datas/` ou son parent, et deux autres, **bien contenues donc acceptées par le garde**, en détruisaient le contenu : `mesuré` sur un faux corpus jetable, `htms` emportait 24 des 25 fichiers du corpus versionné et `database` les cinq stores. Toute valeur autre que le défaut déplaçait de surcroît les `element_id` de tout le corpus, en silence — le nettoyage écrivait selon le réglage, l'identité du document retirait la constante. Le sous-répertoire est désormais une constante du code ; le contrôle de containment, lui, **reste** : `SOURCE_DIR` demeure un réglage, et une racine mal réglée fait toujours sortir le script en 1 plutôt que de supprimer ce qu'elle désigne.

Le space NebulaGraph étant supprimé, redémarrez ensuite le service pour qu'il recrée le schéma. C'est aussi le seul moyen de faire évoluer le schéma du graphe : NebulaGraph ne sait pas modifier la longueur des identifiants après coup.

> **REDÉMARREZ `docling-service` AVANT toute réingestion, y compris sans purge.**
> C'est `init_schema()` qui joue les `ALTER TAG … ADD`, et il n'est appelé **qu'au
> démarrage du service** (`main.py`, dans le `lifespan`). Le lot 4 ajoute la
> colonne `page_no_end` aux onze tags d'élément : `mesuré` le 1er septembre 2026,
> `DESCRIBE TAG Paragraph` sur le space vivant rend
> `label, page_no, text, minio_url, depth` — **la colonne n'existe pas encore**.
> Une réingestion lancée avant le redémarrage écrit donc contre un tag qui n'a pas
> la colonne, et le graphd rejette chaque `INSERT`.
>
> L'ordre est **redémarrer, puis réingérer**, et jamais l'inverse. Un opérateur qui
> lit « il faut une réingestion » dans un message d'anomalie et s'exécute sans
> redémarrer ne répare rien — voir le registre, le message d'anomalie
> `page_no_end` de `verify_contract` égare sur ce point précis.

> Le bucket MinIO était auparavant laissé intact, et les crops d'images des ingestions précédentes s'y accumulaient. Ce n'était pas une fuite — l'agent ne sert que les objets référencés par le graphe (`RESTRICT_MEDIA_TO_GRAPH=true`), donc un objet dont le nœud a disparu est déjà inaccessible — mais c'était de la place perdue à chaque réingestion. Le script sort en **code d'erreur** si l'un des trois stores résiste : une purge partielle est pire qu'une purge absente, on croit repartir propre et on réingère par-dessus des restes.

```bash
docker compose restart docling-service
```

**Réindexation lexicale de l'agent.** Une fois l'ingestion retombée, le pipeline appelle
`POST /reindex` sur `rag-agent-chat`. L'agent tient son index BM25 **en mémoire** : sans cet
appel, un document ingéré après son démarrage reste trouvable en recherche dense — la requête
part à ChromaDB à chaque fois — mais **invisible en recherche lexicale** jusqu'à son prochain
redémarrage. La recherche devient silencieusement asymétrique.

L'agent possède bien un filet — il compare le nombre de chunks de sa collection à celui qu'il a
indexé — mais ce filet **ne voit pas** une réingestion qui retire autant de chunks qu'elle en
ajoute, ce qui est précisément le cas d'une réingestion. D'où un contrat, et non une option.

**Une fois par rafale, pas une fois par document.** L'appel ne vit pas dans l'asset
d'extraction : il a son propre job, `agent_reindex_job`, et son propre sensor. Ce sensor
regarde l'état des runs d'ingestion, et n'arme le job que lorsque **plus aucun n'est en vol**
— ni en cours, ni en attente dans la file — et qu'au moins un a réussi depuis la dernière
réindexation. Le nombre d'appels ne suit donc pas le nombre de documents : une rafale de N
documents donne **une** reconstruction BM25 au lieu de N, chacune étant complète et synchrone
côté agent. Un corpus déposé en goutte-à-goutte donne en revanche une
réindexation par rafale, ce qui est le comportement voulu — un document ingéré doit devenir
cherchable.

Un échec d'appel **ne fait jamais échouer une ingestion réussie** : il ne le peut plus, l'appel
vivant dans son propre run.

**Mais il fait rougir le sien, et il est retenté.** Le run de réindexation échoue pour de bon,
donc il apparaît là où l'on regarde les échecs. Et le sensor le retente au tick suivant, aussi
longtemps qu'il le faut : il ne tient aucun curseur à lui, il compare le repère de la dernière
ingestion réussie à celui de la dernière **réindexation réussie**. Tant que ce second repère
n'existe pas, il reste quelque chose à faire. L'agent peut être légitimement arrêté — c'est le
cas d'une première mise en route — et il verra alors des runs rouges jusqu'à ce qu'il réponde.
C'est bruyant, et c'est voulu : la version précédente avançait son repère à l'**émission** de
la demande, si bien qu'une réindexation manquée était perdue définitivement, sans que rien ne
rougisse nulle part.

| Variable | Rôle | Défaut |
|---|---|---|
| `AGENT_SERVICE_URL` | Racine de l'API de l'agent sur `rag_network`. **Vide = appel désactivé**, annoncé au démarrage de Dagster et à chaque tick du sensor | `http://agent-api:8000` |
| `AGENT_API_KEY` | Clé d'API, si l'agent en exige une | *(vide)* |

**Contrôle avant-vol.** Avant de lancer le gros corpus, vérifiez que les trois stores répondent :

```bash
docker compose exec docling-service python -m src.verify_data
```

**Contrôle après ingestion.** Un rapport sur la qualité de l'index vectoriel : volume, bruit résiduel, taille des chunks, et surtout part des chunks qui dépassent la fenêtre du modèle d'embedding — ceux-là sont tronqués par le modèle lui-même, en silence.

```bash
docker compose exec docling-service python -m src.index_report
```

**Volumétrie.** Mesurée sur le corpus de référence — 1 PDF de 280 pages, 35 chapitres HTML de deux ouvrages, 6 notes Markdown, soit 42 documents : 750 Mo dans `Datas/database/` — 401 Mo pour ChromaDB, 264 Mo pour NebulaGraph, 86 Mo pour MinIO — soit 23 741 nœuds de graphe et 5 592 chunks vectorisés. Une bonne part de ces 750 Mo est de l'amorce fixe : les 280 pages de PDF pèsent à elles seules 31 Mo d'images sur MinIO. Comptez de l'ordre de **15 à 25 Go pour 300 livres**, dominés par les images.

**Débit.** Voir le tableau détaillé dans [orchestration.md](documentation/orchestration.md#combien-de-temps-prend-une-ingestion) : un chapitre HTML en 6 s, un PDF de 300 pages en 1 à 2 min, **1 h 30 à 2 h pour 50 livres**, sans surveillance.

**Mémoire.** Relevée toutes les 20 à 30 secondes pendant une demi-heure d'ingestion continue (111 mesures en régime) : **médiane 6,08 Gio, pic 6,43 Gio**, contre une limite de 10 Go fixée dans `docker-compose.yml` — soit 3,5 Gio de marge. L'essentiel est constitué des modèles, chargés une fois pour toutes ; la consommation ne monte pas avec le nombre de documents traités.

Si vous relevez une consommation qui grimpe au fil des livres, c'est anormal : coupez et signalez-le, plutôt que d'attendre le plafond.

---

## Exploration du Graphe (NebulaGraph)

Le pipeline génère un graphe sémantique où chaque document est un nœud central relié à ses composants (titres, paragraphes, images, etc.).

### Requêtes nGQL types (à taper dans l'onglet Console)

**IMPORTANT : Ne tapez pas `USE rag_space;` dans la console !**
Dans Nebula Studio, vous devez **d'abord** sélectionner l'espace `rag_space` depuis le menu déroulant en haut à droite. Ensuite, vous pourrez exécuter les requêtes suivantes :

1. **Voir un document complet et sa structure** (ronds reliés) :
   ```ngql
   MATCH p=(d:Document)-[r:PARENT_OF]->(e)
   WHERE d.filename == "statisticsfordatascience"
   RETURN p;
   ```

2. **Visualiser uniquement le squelette (titres et sections)** :
   ```ngql
   MATCH p=(d:Document)-[:PARENT_OF]->(s:SectionHeader)
   RETURN p;
   ```

3. **Trouver les images et leurs légendes** (relations sémantiques) :
   ```ngql
   MATCH p=(c:Caption)-[:LINKED_TO]->(res)
   RETURN p;
   ```

### Guide de Visualisation (Studio v3.8.0)

Pour un rendu optimal, configurez les couleurs par **Tag** dans l'interface :
1. Sélectionnez l'espace **`rag_space`** en haut à droite.
2. Dans l'onglet **Console** ou **Visualisation** :
   - **Document** : Rouge (Nœud racine)
   - **SectionHeader** : Bleu (Structure)
   - **Paragraph** : Gris (Contenu)
   - **Table / Picture** : Vert (Ressources riches)
   - **Caption** : Jaune (Métadonnées liées)
3. Utilisez le **Vertex Filter** pour isoler des types spécifiques (ex: ne montrer que `Code` et `Formula`).

---

## Structure du Projet

```text
RAG_Assistant/
├── Datas/                      # Dossier source partagé pour vos livres (HTML/PDF)
│   └── .cleaned/               # HTML nettoyés (générés par le pipeline)
├── documentation/              # Documentation technique détaillée de l'architecture
├── src/
│   ├── docling_service/        # Microservice d'extraction (GPU)
│   │   ├── main.py             # Application FastAPI : /extract, /jobs, /health
│   │   ├── jobs.py             # File de jobs et worker unique
│   │   ├── extraction.py       # Conversion Docling (PDF paginé, HTML/MD direct)
│   │   ├── elements.py         # Taxonomie des labels, hiérarchie et positions
│   │   ├── storage.py          # Persistance d'un lot : graphe puis vecteurs
│   │   ├── nebula.py           # Écritures NebulaGraph groupées, pool partagé
│   │   ├── ngql.py             # Échappement et construction des requêtes nGQL
│   │   ├── vectors.py          # Embeddings par lots et upsert ChromaDB
│   │   ├── chunking.py         # Découpage des textes longs, contextualisation
│   │   ├── markdown.py         # Normalisation du Markdown avant conversion
│   │   ├── matter.py           # Index, sommaire, pages liminaires : hors contenu
│   │   ├── hierarchy.py        # Arbre des titres : pile et profondeur
│   │   ├── ranking.py          # Rang d'un titre (parent Docling, level, police)
│   │   ├── language.py         # Détection de la langue par mots-outils
│   │   └── images.py           # Crop PyMuPDF et export MinIO
│   └── pipeline/               # Orchestration Dagster
│       ├── sources.yaml        # Déclaration des sources (1 bloc = 1 source)
│       ├── sources.py          # Modèles de configuration des sources
│       ├── factory.py          # Génération assets/jobs/sensors par source
│       ├── cleaning.py         # Nettoyage HTML universel (trafilatura/readability)
│       ├── schemas.py          # Contrat de données partagé avec rag-agent-chat
│       └── definitions.py      # Point d'entrée Dagster
├── docker-compose.yml          # Configuration de la stack
├── Dockerfile.dagster          # Environnement Dagster
└── Dockerfile.docling          # Environnement extraction GPU
```

---

## Tests

```bash
make install && make all
```

`make install` appelle `uv sync`, qui installe les dépendances de production
**et** le groupe `dev` déclaré dans `pyproject.toml` : `pytest`, `ruff`, `mypy`
et les stubs de typage. La porte qualité est donc reproductible depuis la seule
source de vérité du dépôt, sans liste annexe à se rappeler. `make all` enchaîne
`lint`, `typecheck`, `test` et `format-check` — chaque outil derrière `uv run`,
donc aux versions épinglées par `uv.lock` — et s'arrête à la première étape
rouge.

### La porte ne réécrit plus le dépôt qu'elle contrôle

`make all` appelait `format`, c'est-à-dire `ruff format src/`, qui **écrit**. La
porte réécrivait donc trois fichiers avant de les contrôler, et chaque
développeur devait se souvenir de révoquer `git checkout -- src/` avant chaque
commit, sous peine de livrer du reformatage sans rapport avec son sujet. Un
garde-fou qui repose sur la mémoire du suivant n'est pas un garde-fou.

Les deux gestes sont désormais séparés :

| Cible | Ce qu'elle fait | Où elle vit |
|---|---|---|
| `make format` | **écrit** — `ruff format src/ tests/` | geste volontaire, dans aucune porte |
| `make format-check` | **constate** — `ruff format --check src/ tests/` | dernière étape de `make all` |
| `make lint` | **constate** — `ruff check src/ tests/` | première étape de `make all` |

**`make lint` a porté `src/` seul jusqu'au lot 4, et c'était le MÊME angle mort
que D7, d'un cran plus loin.** Le hook `ruff` voit tout ce qui est **indexé**,
donc `tests/` ; la cible ne voyait que `src/`. `make all` rendait donc 0 sur un
arbre dont le hook refusait le commit, et le développeur apprenait la faute au
moment du commit, pas au moment du contrôle. `mesuré` le 1er septembre 2026 :
**deux commits du lot 4 ont été refusés pour des règles — `N802`, `SIM223`,
`E402`, `I001` — que `make all` venait de déclarer propres.** Les deux gardes
voient désormais la même chose. `typecheck` reste borné à `src/` : c'est
`pyproject.toml` qui exclut `tests/` de `mypy`, un choix déclaré et non une
divergence de portée.

**Cette table a porté `src/` seul pendant quelques heures, et c'était faux.**
Le commit qui a étendu les deux cibles à `tests/` — pour fermer l'angle mort D7,
où `test_wipe_stores.py` n'était vu par aucune des deux — a changé le `Makefile`
sans changer la table qui le décrit. Le lot 5 s'appelle « la documentation contre
le code » : le défaut qu'il doit chasser est né dans le commit qui fermait un
angle mort, ce qui est exactement la façon dont ce défaut-là naît.

**`make all` rend 0, et l'exception « rc=2 est le rouge attendu » n'existe
plus.** Elle a vécu du lot 0b au lot 3 : le dépôt portait quatre fichiers pliés
à la main, `format-check` les signalait, et chaque conversation redécouvrait
qu'un rc=2 sur `main` était normal. Cette exception a **déjà masqué un vrai
rouge une fois**. Les quatre fichiers sont désormais format-propres, et la
porte n'a plus qu'un seul verdict à rendre : 0 ou un défaut.

L'état, `mesuré` sur cette révision :

```bash
uv run ruff format --check src/ tests/
```

→ « 72 files already formatted », `rc=0`. Et `make all` → `rc=0`.

*(Ce nombre valait **67**, et il était faux sur la révision qui le portait —
mot pour mot le défaut que le pilote venait de corriger en `39ce91a`, « le README
annonçait 66 fichiers formatés », refait par le lot suivant. Un `mesuré` n'est pas
une étiquette de véracité, c'est une étiquette de **provenance**, et une
provenance comprend l'arbre. Une phrase qui dit « SUR CETTE RÉVISION » se
remesure à chaque révision qui la traverse, ou elle se supprime.)*

**Ce que le prochain développeur doit en faire : un rc non nul est un défaut,
sans exception à connaître.** C'est tout l'intérêt du geste. Le détail de ce
qu'il a coûté — quatre fichiers, un total de **20 lignes** de diff réparties
sur trois commits de style, dont **7** pour celui-ci (`mesuré`,
`git show --numstat --format= <commit>` sur les trois) — vit au registre §5.4.

`format-check` passe **en dernier** dans `all`, et cela n'a plus de conséquence
pratique : `lint`, `typecheck` et `test` rendent leur verdict, puis
`format-check` rend le sien, et l'ensemble est vert. Pour lire les trois
premiers seuls :

```bash
make lint typecheck test
```

### Les garde-fous du dépôt — une seule installation

```bash
make install
```

Ce geste, et lui seul, arme les hooks déclarés dans `.pre-commit-config.yaml`
**et** le contrôle d'identité d'auteur. **Il n'était fait nulle part avant le
lot 0b** : les garde-fous étaient déclarés et rien ne les exécutait
(registre §5.5). Un garde-fou déclaré et non installé est pire qu'absent — on
croit l'avoir.

`make install` fait `uv sync`, puis `sh scripts/installer-les-garde-fous.sh`.
Ce script monte les deux couches **dans l'ordre**, ne passe **jamais** `-f`, et
**vérifie son propre résultat** : il sort en erreur si le montage n'est pas
celui qu'il annonce.

**`uv` est requis.** Cette phrase disait « si `uv` manque, il s'exécute seul » :
c'est faux, et `mesuré` — `env PATH=/usr/bin:/bin sh
scripts/installer-les-garde-fous.sh` rend **`rc=1`** et ne pose que les deux
copies du contrôle d'identité, sans aucun hook du framework ni `.legacy`. Le
script le dit sur sa sortie d'erreur ; c'était la documentation qui mentait.

**Lance-le depuis le clone principal.** `pre-commit install` grave dans le hook
généré une ligne `INSTALL_PYTHON=<interpréteur de l'arbre d'où on l'a lancé>`, et
`.git/hooks` est partagé par tout le clone. Si cet arbre disparaît — un
`git worktree remove` après fusion, par exemple — et qu'aucun `pre-commit` n'est
au PATH, **tout commit du dépôt et de tous ses arbres de travail** est refusé,
sur le seul message « `` `pre-commit` not found. `` », qui ne nomme pas la cause
(`mesuré`, 31 août 2026 : `rc=1`, HEAD inchangé). C'est fail-closed, donc sans
danger pour l'historique. **Le geste de sortie :** relancer `make install` depuis
le clone principal.

L'ordre n'est pas cosmétique, et il ne pouvait pas rester une consigne écrite.
Le lot 0b, tel qu'il avait été livré, demandait deux gestes dans un sens précis ;
l'inversion ne produit aucune erreur, seulement l'absence d'une protection. Un
garde-fou qui repose sur la mémoire du suivant n'est pas un garde-fou — c'est la
phrase que ce même lot a fait respecter à `make all`, et elle valait aussi ici.

| Hook | Ce qu'il fait | Écrit ? |
|---|---|---|
| `identite-auteur` | refuse un commit dont l'adresse d'auteur **ou** de committer n'est pas dans la liste blanche — voir la réserve ci-dessous, tous les chemins qui créent un commit ne sont pas couverts | non |
| `trailing-whitespace`, `end-of-file-fixer` | hygiène de fin de ligne et de fin de fichier | oui, sur les fichiers **indexés** |
| `check-yaml` | YAML valide | non |
| `check-added-large-files` | refuse un fichier **nouvellement ajouté** de plus de 500 ko. Ne voit **pas** un fichier déjà suivi qu'on modifie, quelle que soit sa taille | non |
| `ruff` | `--fix` sur les violations de lint | oui, sur les fichiers **indexés** |
| `ruff-format` | `--check` — **constate**, ne reformate pas | non |
| `detect-secrets` | refuse un secret dans un fichier **indexé** | non |

#### Le contrôle d'identité tient sur `pre-commit.legacy`, et c'est essentiel

Il est déclaré **deux fois**, et ce n'est pas une redondance décorative :

- comme hook `repo: local` dans `.pre-commit-config.yaml` — donc dans l'**arbre
  de travail** ;
- comme `.git/hooks/pre-commit.legacy`, la copie manuelle que
  `pre-commit install` déplace et continue d'exécuter — donc **hors** de l'arbre
  de travail.

Les deux pointent le même script versionné, `scripts/git-hooks/pre-commit` : la
liste blanche d'adresses n'a **qu'un site versionné**.

**Elle en a deux une fois armée, et il faut le savoir.** Cette phrase disait
« un seul site, et il ne peut pas diverger » : c'est vrai du dépôt, faux du
montage. `pre-commit.legacy` est une **copie figée à l'installation** ; le hook
`repo: local`, lui, relit le script versionné à chaque commit. `mesuré` le
31 août 2026, dans un clone frais armé par le script livré, une édition
**commitée** de `ADRESSES_AUTORISEES` :

| Édition | Effet au commit suivant |
|---|---|
| **ajouter** une adresse | **sans effet** — la couche `repo: local` l'accepte, puis `pre-commit.legacy` refuse (`rc=1`, HEAD inchangé) en affichant l'**ancienne** liste |
| **retirer** une adresse | **appliqué** — la couche `repo: local` refuse (`rc=1`, HEAD inchangé) |

C'est **fail-closed dans les deux sens** : de la friction, jamais une exposition.
Et le montage n'est pas changé pour autant — la copie figée **est** la propriété
qui rend le contrôle indépendant de l'arbre de travail (section ci-dessous).
**Le geste : après toute édition de `ADRESSES_AUTORISEES`, relancer
`make install`**, qui réécrit la copie. Le message de refus énumère alors
l'ancienne liste : une adresse fraîchement ajoutée qui n'y figure pas est ce
cas-là, et rien d'autre.

**Seule la seconde couche est inconditionnelle.** Le hook généré par le
framework ouvre sa configuration en chemin **relatif** : un arbre de travail
dont `.pre-commit-config.yaml` ne déclare pas le contrôle est désarmé, en
silence. Sur les 111 commits de `main`, aucun ne le déclare (`mesuré` le 31 août
2026 sur `a005172`) — donc tout `git checkout` d'un commit ancien, tout
`git bisect`, tout HEAD détaché. C'est pour cela que
`scripts/installer-les-garde-fous.sh` copie le script **avant** d'appeler
`pre-commit install`, et que **`-f` ne doit jamais être passé** : `-f` supprime
`pre-commit.legacy`, et `pre-commit install` le suggère lui-même dans sa sortie.

Un test garde cette propriété plutôt que de la documenter :
`tests/unit/test_installation_des_garde_fous.py` monte un dépôt jetable dont la
configuration ne porte **pas** le contrôle, y exécute le script livré, et prouve
que le refus tient.

Les hooks qui écrivent ne touchent que ce qui est **déjà indexé**, refusent le
commit, et **nomment chaque fichier qu'ils ont corrigé** — sans montrer le diff.
Cette phrase disait « et montrent leur diff » : c'était faux. `mesuré` le 31 août
2026, la sortie se limite à `- files were modified by this hook` puis
`Fixing <fichier>`. Le diff s'obtient avec `--show-diff-on-failure`, un drapeau
de la **ligne de commande** que le hook généré ne porte pas et qu'aucune clé de
`.pre-commit-config.yaml` ne peut activer. Pour le lire :

```bash
git diff                                   # ce que le hook vient d'écrire
uv run pre-commit run --show-diff-on-failure --all-files
```

On relit, on réindexe. C'est la différence de fond avec le défaut que le lot 0b
vient de corriger dans `make all` (section précédente), qui réécrivait tout
`src/` — y compris des fichiers qu'on n'avait pas touchés — et sortait **vert**.

#### Ce que le contrôle d'identité couvre, et ce qu'il ne couvre pas

**Cette réserve remplace une phrase sans réserve, et elle est mesurée.** Le
README affirmait que le hook « refuse un commit dont l'adresse d'auteur ou de
committer n'est pas dans la liste blanche », sans dire *quels commits*. Or
`git commit` n'est pas le seul chemin qui en crée un, et git ne déclenche pas les
mêmes hooks sur tous. `mesuré` le 31 août 2026, mouchards posés sur chaque hook
de `.git/hooks` :

| Geste | Couvert | Par quoi |
|---|---|---|
| `git commit` | **oui** | `pre-commit` |
| `git commit --amend` | **oui** | `pre-commit` |
| `git merge --no-ff` | **oui** | `pre-merge-commit` |
| `git revert` | **non** | git n'y déclenche ni `pre-commit` ni `commit-msg` |
| `git cherry-pick` | **non** | idem |
| `git rebase` | **non** | aucun hook de la famille ; le rebase réécrit le committer |
| `git commit --no-verify` | **non** | par construction. Ne l'utilise jamais |

Les fusions ont été fermées par la réparation du lot 0b : `pre-commit install`
n'installe que le type `pre-commit`, et un commit de fusion portant `@aosis.net`
partait sans rencontrer quoi que ce soit (`mesuré`).

**`git revert` et `git cherry-pick` restent ouverts, et ce n'est pas un oubli.**
Le seul point d'accroche que git y déclenche est `prepare-commit-msg` — et un
contrôle posé là serait **vert sur le défaut** : `mesuré`, lors d'un
`cherry-pick`, `git var GIT_AUTHOR_IDENT` y rend l'identité **locale**, pas celle
du commit produit. Un commit d'auteur `@aosis.net` cueilli depuis un arbre
configuré en `@gmail.com` se présente au hook comme `@gmail.com`, et passe.
La fermeture honnête est un hook `pre-push` ; elle est ouverte au registre.

#### Ce que `detect-secrets` protège, et ce qu'il ne protège pas

À ne pas survendre. `.env` porte les mots de passe MinIO et PostgreSQL, mais
**un hook `pre-commit` ne voit que les fichiers indexés, et `.env` est dans
`.gitignore` : il n'est donc jamais indexé, et installer ce hook ne le fera
jamais scanner.** Le gain est ailleurs, et il est réel : empêcher qu'un secret
parte un jour dans un fichier **versionné**. Aujourd'hui `docker-compose.yml`
passe par `${MINIO_ROOT_PASSWORD}` et ne porte aucun secret en clair (`mesuré`,
31 août 2026).

Il n'y a **pas de baseline**. Le dépôt en portait une, `.secrets.baseline`,
générée le 30 avril 2026 et jamais regénérée ; le lot 0b l'a supprimée après
avoir mesuré qu'elle avait pourri (registre §5.5). Un faux positif se déclare
désormais **au site**, avec sa justification, par un commentaire
`# pragma: allowlist secret`.

**Distingue les lignes qui PORTENT le pragma de celles qui le citent en prose :
les deux comptes ne sont pas le même.** Une ligne porteuse annote une valeur, et
c'est celle-là que `detect-secrets` lit ; les autres sont les commentaires qui la
justifient, cette section, et la ligne de `scripts/capturer-larbre-docling.py`
qui *écrit* le pragma dans la capture YAML.

Les porteuses se comptent ainsi (`mesuré` le 1er septembre 2026) :

```bash
git ls-files -z -- '*.py' '*.yaml' | xargs -0 grep -nE '#[[:space:]]*pragma: allowlist secret$'
```

Elle en rend **11** — **7** en Python et **4** dans la capture YAML —, contre
**25** occurrences si l'on compte toute mention dans tout fichier versionné :

| Site | Ce que le scanner y lit |
|---|---|
| `src/pipeline/reindex.py:69` | `API_KEY_HEADER`, dont la valeur est un **nom** d'en-tête HTTP |
| `src/docling_service/settings.py:34` | `NEBULA_PASSWORD`, le mot de passe **public** du graphd de développement, celui de `docker-compose.yml` |
| `tests/unit/test_reindex.py:91` et `:92` | l'argument `api_key`, dont la valeur d'essai est le mot « secret » lui-même |
| `tests/unit/test_nebula.py:201`, `tests/unit/test_verify_data.py:246`, `tests/unit/test_init_nebula.py:186` | les mêmes identifiants publics, posés en variables d'environnement d'essai |
| `tests/fixtures/arbres_docling.yaml`, 4 lignes | les empreintes SHA-256 des captures, lues comme des « Hex High Entropy String » |

Le compte est passé de 2 à 3, puis à 6, puis à 11, et **jamais parce qu'un secret
était apparu** : à 3, la troisième ligne était née du piège de déduplication
décrit au registre ; les suivantes sont les identifiants du graphd exposés en
réglages et les empreintes de la capture. Ne recopie pas ce compte : la commande
ci-dessus se relance, et c'est elle qui fait foi.

**La commande porte `'*.py' '*.yaml'` et non `--include='*.py'`, et c'est un
correctif.** Bornée aux fichiers Python, elle ne voyait pas les 4 porteuses de la
capture YAML — elle annonçait donc un dépôt plus propre qu'il n'est, sur la
mesure même qui existe pour compter les exceptions.

**Le pragma doit annoter la ligne qui porte la VALEUR**, pas la ligne qui ouvre le
dictionnaire ou l'appel : posé une ligne trop haut, il ne filtre rien et le commit
est refusé sans que le message dise pourquoi (`mesuré` le 1er septembre 2026, deux
refus consécutifs sur `tests/unit/test_verify_data.py`).

> Cette section a été perdue une fois, et c'est consigné plutôt que tu. Le commit
> `0217bab` l'avait écrite ; `a54636c`, juste après la réparation d'un incident de
> procédé — un `git checkout <branche> -- .` dans un arbre portant des commits —,
> a réintroduit le texte de `main` par-dessus. Un `make all` vert ne pouvait pas
> le montrer : rien de ce qui se perd dans un document ne rougit. Le geste pour
> lire un fichier d'une autre révision est `git show <rev>:<fichier>`, et rien
> d'autre.

```bash
git ls-files -z -- ':!Datas/' | xargs -0 uv run --with detect-secrets==1.5.0 detect-secrets-hook
```

**Le `:!Datas/` n'est pas un détail de confort, et il n'était pas là.** Sans lui,
cette commande contredit la section suivante : `detect-secrets` est appelé
**directement**, donc l'`exclude: '^Datas/'` de `.pre-commit-config.yaml` — que
seul le framework applique — ne le filtre pas, et le scan bute sur les deux faux
positifs du corpus. `mesuré` le 31 août 2026 **sur le résultat d'une fusion
d'essai `--no-ff` avec `a005172`**, sans le `:!Datas/` : deux
`Hex High Entropy String`, aux deux emplacements que la section suivante nomme.
Avec le `:!Datas/` : aucune sortie, `rc=0`, 99 fichiers scannés. La commande
**ne doit rien rendre** — c'est cela, son verdict.

Deux réserves à connaître avant de lire son code de retour :

- **le défaut ne se voit pas sur la branche seule.** Tant que `Datas/` n'est pas
  versionné, `git ls-files` ne le nomme pas et la commande est verte pour une
  raison qui disparaît le jour de la fusion. Mesure-la sur le résultat de la
  fusion, jamais sur la branche ;
- **le code de retour est celui de `xargs`, pas celui de `detect-secrets`** :
  `xargs` traduit un échec du programme appelé en **123**, jamais en 1. Ne teste
  donc pas `rc = 1`, teste `rc ≠ 0` — et prends-le sans pipe supplémentaire, un
  `| tail` rendrait le code de `tail`.

Ce `:!Datas/` et l'`exclude: '^Datas/'` de `.pre-commit-config.yaml` disent la
même chose à deux endroits : si l'un bouge, l'autre doit suivre. Le second est
gardé par `tests/unit/test_hooks_contre_le_corpus.py` ; le premier ne l'est pas,
et c'est consigné au registre.

#### Le corpus est hors de portée des hooks, et voici comment l'étendre

`Datas/htms/` et `Datas/pdfs/` sont **versionnés** — 25 fichiers, 55 Mo. Ce sont
des données d'entrée, pas du code, et `.pre-commit-config.yaml` les soustrait à
**tous** les hooks par un `exclude: '^Datas/'` au niveau racine.

**Ce n'est pas de la commodité.** Sans cette exclusion, mesuré sur le résultat
d'une fusion d'essai :

| Hook | Ce qu'il faisait au corpus |
|---|---|
| `detect-secrets` | **refusait** le commit — deux `Hex High Entropy String`, faux positifs. Et un `# pragma` est impossible ici : le **contenu** entre dans le calcul de `element_id` (contrat, exigences 2 et 3) |
| `trailing-whitespace`, `end-of-file-fixer` | **écrivaient** — 24 fichiers sur 25, 240 lignes. Au commit, `git add` puis recommit faisait entrer le fichier **altéré**, sans erreur |
| `check-added-large-files` | **refusait** tout fichier **nouveau** de plus de 500 ko : le corpus ne pouvait plus grandir |

La deuxième ligne est la plus grave. Le corpus est une donnée de mesure : deux
postes dont les fichiers diffèrent d'un caractère produisent des `element_id`
différents, donc des campagnes que rien ne permet de comparer, **sans qu'aucune
erreur ne le signale** (mandat §2.2). Un hook qui « corrige » une fin de ligne
dans un HTML capturé fait exactement ce dégât.

**Le geste pour étendre le corpus, aujourd'hui :**

```bash
git add "Datas/htms/<ouvrage>/<chapitre>.html" && git commit
```

Rien de particulier — c'est le point de l'exclusion. Aucun `--no-verify`, aucun
seuil à relever, aucune exception à ajouter. Deux choses seulement :

- **ne renomme rien, jamais**, et surtout pas pour « ranger » : `source_path`
  entre dans `element_id`. Les noms doivent être identiques au caractère près
  d'un poste à l'autre (mandat §2.2) ;
- au-delà de **50 Mo par fichier**, GitHub avertit ; au-delà de **100 Mo**, il
  refuse. Aucun fichier du corpus n'approche ces bornes aujourd'hui — mais
  **pas « de loin » comme ce paragraphe l'a affirmé**. Il disait « le plus gros
  fichier du corpus pèse aujourd'hui moins de 1 Mo » : c'est **faux**, et de
  presque un ordre de grandeur. `mesuré` le 31 août 2026 **sur le résultat de la
  fusion d'essai**, `git ls-files -z -- Datas | xargs -0 stat -c '%s %n'` :
  le plus gros pèse **6 362 475 o** — `Datas/htms/Practical MLflow for
  Generative AI on Databricks/8. Deploying a GenAI Application with
  MLflow.html` — le plus petit **671 707 o**, et **19 des 25** dépassent 1 Mo.
  La conclusion tient, la marge est de **8×** et non de 50× : une capture plus
  lourde, ou un PDF d'images, la rapprocherait vite.

**Ce que l'exclusion coûte**, écrit pour que personne ne le découvre : un secret
réel déposé sous `Datas/` ne serait pas vu par `detect-secrets`. C'est accepté —
le corpus est une capture de documentation publique, et l'alternative consiste à
altérer les données de mesure du chantier. La borne est étroite : ce chemin-là,
et lui seul.

**834 tests verts** (`mesuré` le 2 septembre 2026 par `make test` sur cette
révision ; `ruff` et `mypy --strict` propres au même moment). C'est le site
canonique de ce chiffre : il n'est écrit nulle part ailleurs dans le dépôt, et
toute autre mention doit renvoyer ici plutôt que le recopier. Un chiffre
recopié cesse d'être une mesure — « 407 tests » a circulé pour une révision qui
en comptait 477, sans qu'aucune commande ne l'ait jamais produit.

La logique sensible du service d'extraction vit dans des modules sans dépendance lourde : elle est donc testée sans Docling, torch ni NebulaGraph, et couverte à 100 %.

| Module | Rôle | Couverture |
|---|---|---|
| `ngql.py` | Échappement et construction des requêtes du graphe | 100 % |
| `chunking.py` | Ce que le modèle d'embedding reçoit, la forme de l'id de chunk, le filtre du bruit | 100 % |
| `elements.py` | Hiérarchie, positions, identifiants | 100 % |
| `markdown.py` | Normalisation avant conversion | 100 % |
| `matter.py` | Repérage des parties hors contenu (index, sommaire) | 100 % |
| `hierarchy.py` | Assemblage de l'arbre des titres | 100 % |
| `ranking.py` | Rang d'un titre selon la source | 100 % |
| `language.py` | Détection de la langue d'un document | 100 % |
| `jobs.py` | File de jobs et worker | 99 % |
| `cleaning.py` | Nettoyage HTML universel | 94 % |

Les modules restants, `vectors.py` et `main.py`, sont des adaptateurs — vers
ChromaDB et vers FastAPI : ils ne sont pas couverts en unitaire et se valident par
une ingestion réelle. Chacun garde tout de même la propriété qui décide :
`vectors.py` le contrôle du modèle d'embedding et la page de fin des chunks
(`tests/unit/test_vectors.py`), `main.py` le refus de démarrer hors contrat
(`tests/unit/test_main.py`, par sous-processus). *(Cette phrase ne nommait que
`vectors.py` : elle avait été écrite en supposant `main.py` déverrouillé, ce qu'il
n'est pas — voir juste en dessous.)*

**Deux modules ont été DÉVERROUILLÉS au lot 4, et un troisième a été atteint
autrement** — la distinction n'est pas cosmétique, et la première rédaction de ce
paragraphe la gommait. La cause est la même dans les trois cas : ils étaient
**inimportables côté hôte**, donc rien de ce qu'ils décident ne pouvait être testé
— *ce qu'un test n'importe pas, il ne teste pas*.

| Module | Ce qui l'empêchait | Ce qui a changé | Ce qui le garde désormais |
|---|---|---|---|
| `nebula.py` | `import nebula3` au niveau du module | l'import est **différé** dans la fonction qui en a besoin | `test_nebula.py` — l'identité du document, `source_path` et jamais `filename` |
| `extraction.py` | `import docling` au niveau du module | l'import est **différé** | `test_extraction.py` — l'oubli avant réécriture, le retrait d'un document partiel, la chaîne d'images, le compteur de pages perdues |
| `main.py` | `fastapi` absent du venv du dépôt | **rien : aucune ligne du module n'est modifiée** | `test_main.py` — le refus de démarrer hors contrat, atteint par un bouchon `fastapi` posé comme un vrai paquet en tête de `PYTHONPATH`, par sous-processus |

Les deux premiers ont vu leur import différé, le geste de `vectors.get_collection`.
Le troisième n'a **pas** été déverrouillé : `main.py` **est** l'application
FastAPI, différer cet import-là n'aurait aucun sens, et c'est le TEST qui va le
chercher derrière un bouchon. Un module atteint par un bouchon n'est pas un module
importable, et l'écrire comme tel surdisait ce qui avait été fait.

**Un module du dépôt reste inimportable côté hôte, et c'est le seul.** `mesuré` le
1er septembre 2026 : **33 modules sous `src/`, 1 inimportable** —
`src/docling_service/main.py`, `ModuleNotFoundError: No module named 'fastapi'`.
Ce paragraphe affirmait « Aucun module du dépôt n'est plus inimportable côté
hôte » : c'était une **phrase d'exhaustivité**, la famille que le mandat §10 nomme
comme un défaut en attente, et elle contredisait le tableau situé juste au-dessus,
qui dit lui-même que `main.py` est atteint par un bouchon.

L'affirmation est désormais **bornée à sa portée réelle, et gardée** :
`tests/unit/test_importabilite_cote_hote.py` importe les 33 modules dans un
sous-processus et rougit dès qu'un module inimportable n'est pas déclaré. C'était
la cause mécanique de six angles morts du chantier (registre §3.4, §4.4, §4.5,
§4.19, §4.28.d) — la convertir en garde plutôt qu'en phrase est ce qui empêche le
septième.

---

## Licences & composants

| Composant | Rôle | Licence |
|---|---|---|
| Dagster | Orchestration du pipeline | Apache-2.0 |
| Docling | Extraction de documents | MIT |
| BeautifulSoup4 / lxml | Parsing HTML | MIT / BSD-3-Clause |
| trafilatura | Extraction de contenu web | Apache-2.0 |
| readability-lxml | Extraction d'article | Apache-2.0 |
| ChromaDB | Base vectorielle | Apache-2.0 |
| Nebula Graph | Graphe de connaissances | Apache-2.0 |
| PostgreSQL | Métadonnées Dagster | PostgreSQL License (open-source) |
| MinIO | Stockage d'objets | AGPL-3.0 |
| requests | Client HTTP | Apache-2.0 |
| **Ce projet** | Code applicatif | MIT — Copyright (c) 2026 floSa `<à confirmer : aucun fichier LICENSE présent>` |
