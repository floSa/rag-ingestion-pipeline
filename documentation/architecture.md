# Architecture du RAG Ingestion Pipeline

## Vue d'ensemble

Pipeline d'ingestion documentaire qui transforme des PDF, HTML et Markdown en données structurées,
stockées dans une base vectorielle (ChromaDB) et un graphe de connaissances (NebulaGraph).
La couche LLM/agent vit dans un projet séparé, [rag-agent-chat](https://github.com/floSa/rag-agent-chat),
qui consomme ces stores en lecture via le réseau Docker `rag_network` (nom stable,
déclaré en externe côté agent).

## Services Docker

| Service           | Image / Build          | Port interne | Port hôte        | Rôle                                   |
|-------------------|------------------------|--------------|------------------|-----------------------------------------|
| chromadb          | chromadb/chroma:0.6.3  | 8000         | — (expose only)  | Base vectorielle                        |
| metad             | nebula-metad:v3.6.0    | 9559         | —                | NebulaGraph — métadonnées               |
| storaged          | nebula-storaged:v3.6.0 | 9779         | —                | NebulaGraph — stockage distribué        |
| graphd            | nebula-graphd:v3.6.0   | 9669         | — (expose only)  | NebulaGraph — moteur de requête         |
| nebula-studio     | nebula-studio:v3.8.0   | 7001         | 7001             | UI de visualisation du graphe           |
| minio             | minio (pinned)         | 9000, 9001   | — (expose only)  | Object storage S3-compatible            |
| postgres-dagster  | postgres:15-alpine     | 5432         | — (expose only)  | Métadonnées Dagster                     |
| dagster-webserver | Dockerfile.dagster     | 3000         | 3000             | UI Dagster                              |
| dagster-daemon    | Dockerfile.dagster     | —            | —                | Exécution des sensors et runs           |
| docling-service   | Dockerfile.docling     | 8000         | — (expose only)  | Extraction documentaire (GPU, FastAPI)  |

Tous les services communiquent sur le réseau bridge `rag_network`.
Pour le debug local, `docker-compose.override.yml` expose les ports internes.

## Workflow de bout en bout

1. **Dépôt** d'un fichier dans `Datas/pdfs/`, `Datas/htms/` ou `Datas/mds/`
2. **Dagster Sensor** (un par source déclarée) détecte le nouveau fichier et crée une
   partition + un run ; la file Dagster en exécute deux à la fois
3. **Pre-process** (HTML uniquement) : nettoyage universel — pré-passe d'hygiène puis
   comparaison de candidats (conteneurs sémantiques, trafilatura, readability-lxml) ;
   les images base64 volumineuses partent sur MinIO et leur `src` est réécrit
4. **Soumission** : l'asset poste le chemin au service Docling, qui met le document en
   file et rend un `job_id` ; l'asset suit l'avancement jusqu'au terme
5. **Extraction** : Docling analyse le layout — les PDF par lots de pages, HTML et
   Markdown d'un seul tenant — et PyMuPDF crop les images et tableaux vers MinIO
6. **Flush NebulaGraph** : nœuds et hiérarchie `Document → SectionHeader → Éléments`
   (chaque élément rattaché au dernier en-tête rencontré), écrits par INSERT groupés ;
   tout échec nGQL fait échouer le job — pas de perte silencieuse
7. **Flush ChromaDB** : le découpage est confié à `HybridChunker` de Docling, qui respecte la structure du document et la fenêtre du modèle d'embedding — **mais pas
   « aucune troncature »** : `mesuré` le 31 août 2026, **137 chunks sur 4 365 (3,1 %)** dépassent la fenêtre de 128 tokens et sont tronqués par le modèle. Deux causes distinctes,
   toutes deux structurelles : une table sérialisée en Markdown est indivisible pour le découpeur (les 65 chunks trop longs *avant* préfixe sont 65 tables sur 65), et le titre de section est préposé **après** le découpage, ce que le découpeur ne pouvait pas prévoir (72 chunks de plus). Voir `vectors.get_chunker` et le registre §3.4. Les chunks sont
   encodés par lots avec `paraphrase-multilingual-MiniLM-L12-v2` (384 dim), et upsertés avec les
   métadonnées du contrat d'interface : `element_id`, `graph_node_id`, `filename`,
   `label`, `page_no`, `minio_url`, `reference_id`, `page_position`, `ref_position`

## Décisions d'architecture

- **ChromaDB** plutôt que Weaviate : plus simple, pas besoin d'UI intégrée pour le vectoriel
- **NebulaGraph** pour le graphe de connaissances : distribué (metad/storaged/graphd),
  Studio UI pour la visualisation
- **Volume partagé** `/Datas` monté dans Dagster et Docling : évite le transfert réseau
  de gros fichiers PDF
- **Docling, seul service à pouvoir prendre le GPU** : la charge lourde y est isolée. La
  réservation `nvidia` vit dans `docker-compose.gpu.yml` et n'est pas appliquée par
  défaut — écrite en dur, elle rendait le service incréable sans runtime nvidia. Le cas
  nominal est donc le processeur
- **Embeddings locaux et multilingues** : `paraphrase-multilingual-MiniLM-L12-v2` via SentenceTransformers, pas d'appel API. Une question française retrouve les passages anglais, et réciproquement
  externe (pas d'OpenAI)
- **Un sensor par source** : découplage des pipelines, chacun avec son job Dagster
- **Extraction asynchrone** : une conversion de livre dure des heures, ce qui ne tient
  pas dans une requête HTTP. Le service met en file et rend un `job_id` ; Dagster suit
  l'avancement. L'event loop reste libre, et une coupure réseau ne condamne plus un run
- **Un seul worker d'extraction** : la conversion sature déjà le GPU. C'est la file
  Dagster en amont qui cadence le débit, et elle le fait visiblement dans l'UI
- **Écriture par lots** : INSERT nGQL groupés, pool NebulaGraph partagé et embeddings
  encodés par batch. Un aller-retour par élément mettait les livres hors d'atteinte
- **Le graphe garde tout, l'index vectoriel garde ce qui a du sens** : l'analyse de
  layout produit quantité de fragments isolés (`x`, `and`, `Note`, `-`) — 36 % de
  l'index sur le corpus de référence. Ils sont fusionnés avec leurs voisins de même
  section, et les résidus sont écartés de la recherche sémantique tout en restant
  dans NebulaGraph
- **Contextualisation des vecteurs** : le titre de section est préposé au texte envoyé
  au modèle d'embedding, pas au texte stocké. Le passage s'affiche tel quel côté agent,
  mais son vecteur porte le contexte qui lui manquait
- **Découpage plutôt que troncature** : les textes longs sont fenêtrés avant
  vectorisation. Tronquer à 1000 caractères amputait silencieusement les paragraphes
- **Identifiants déterministes** : `sha256(filename|page|position_dans_la_page|texte)`,
  ce qui rend la ré-ingestion idempotente (upsert, pas de doublon)

## Dossiers de données

| Dossier                    | Contenu                              |
|----------------------------|--------------------------------------|
| `Datas/pdfs/`              | Documents PDF sources                |
| `Datas/htms/`              | Documents HTML sources               |
| `Datas/mds/`               | Documents Markdown sources           |
| `Datas/.cleaned/`          | HTML nettoyés (générés par le pipeline) |
| `Datas/database/chromadb/` | Persistence ChromaDB                 |
| `Datas/database/nebula/`   | Persistence NebulaGraph              |
| `Datas/database/minio/`    | Persistence MinIO                    |
| `Datas/database/postgres/` | Persistence PostgreSQL (Dagster)     |
