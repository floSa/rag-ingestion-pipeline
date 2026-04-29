# RAG Assistant Pipeline 🚀

Ce projet est un pipeline d'ingestion de documents (HTML et PDF) conçu pour alimenter un assistant RAG (Retrieval-Augmented Generation). Il utilise **Docling** pour l'extraction structurée intelligente, **NebulaGraph** pour le Knowledge Graph, **ChromaDB** pour le stockage vectoriel, **MinIO** pour les médias, et **Dagster** pour l'orchestration.

---

## 🛠 Architecture & Technologies

- **Docling-Service (FastAPI)** : Microservice dédié à l'extraction de documents via Docling (sur GPU). Les images et tableaux complexes en sont extraits et découpés *(crop)* avec PyMuPDF.
- **Orchestration (Dagster)** : Capteurs intelligents par type de ficher (HTML vs PDF) et gestion d'Assets.
- **Base Graphe** : [NebulaGraph](https://nebula-graph.io/) couplé au Studio pour créer la cartographie relationnelle (Document > Section > Text > Image/Table).
- **Base Vectorielle** : [ChromaDB](https://www.trychroma.com/) couplé à des modèles d'embeddings locaux (`SentenceTransformers`).
- **Stockage Objet** : [MinIO](https://min.io/) pour héberger les images extraites et récupérables via la clé `minio_url`.
- **Plateforme** : Entièrement déployé sous forme de conteneurs multi-services via Docker-Compose.

---

## 🚀 Quickstart

### 1. Configurer l'environnement
```bash
# Copier le gabarit et remplir les valeurs (notamment les mots de passe)
cp .env.example .env
# Générer un mot de passe MinIO sécurisé :
# openssl rand -base64 24
```

### 2. Démarrer les services
Assurez-vous d'avoir Docker et le plugin NVIDIA Container Toolkit installés (si utilisation GPU).
```bash
# Construire et lancer toute la stack en arrière-plan
docker compose up -d --build
```

### 3. Accéder aux interfaces
| Service | URL | Note |
| :--- | :--- | :--- |
| **Dagster (UI)** | [http://localhost:3000](http://localhost:3000) | Gestion, exécution des assets et activation des Sensors. |
| **Nebula Studio** | [http://localhost:7001](http://localhost:7001) | **Host:** `graphd` \| **Port:** `9669` \| Credentials : voir `.env` |
| **MinIO Console** | [http://localhost:9101](http://localhost:9101) | Credentials : voir `.env` |
| **Docling API** | `http://localhost:8000/extract` | API interne (accessible côté host via port 8000). |
| **ChromaDB** | `http://localhost:8080/api/v1` | Point d'entrée de la base vectorielle. |

### 4. Lancer l'ingestion
1. Placez vos fichiers dans le dossier `./Datas` de la racine du projet.
2. Ouvrez l'interface **Dagster**.
3. Activez le **Sensor PDF** ou le **Sensor HTML** (ou les deux) dans l'onglet **Overview -> Sensors**. 
4. Le système détectera automatiquement un nouveau fichier et lancera le pipeline complet pour l'ingérer dans Nebula, ChromaDB, et MinIO !

---

## 🗺️ Exploration du Graphe (NebulaGraph)

Le pipeline génère un graphe sémantique où chaque document est un nœud central relié à ses composants (titres, paragraphes, images, etc.).

### 🔍 Requêtes nGQL types (à taper dans l'onglet Console)

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

### 🎨 Guide de Visualisation (Studio v3.8.0)

Pour un rendu optimal, configurez les couleurs par **Tag** dans l'interface :
1. Sélectionnez l'espace **`rag_space`** en haut à droite.
2. Dans l'onglet **Console** ou **Visualisation** :
   - **Document** : 🔴 Rouge (Nœud racine)
   - **SectionHeader** : 🔵 Bleu (Structure)
   - **Paragraph** : ⚪ Gris (Contenu)
   - **Table / Picture** : 🟢 Vert (Ressources riches)
   - **Caption** : 🟡 Jaune (Métadonnées liées)
3. Utilisez le **Vertex Filter** pour isoler des types spécifiques (ex: ne montrer que `Code` et `Formula`).

---

## 📁 Structure du Projet

```text
RAG_Assistant/
├── Datas/                      # Dossier source partagé pour vos livres (HTML/PDF)
├── documentation/              # Documentation technique détaillée de l'architecture
├── src/
│   ├── docling_service/        # API FastAPI pour l'extraction et PyMuPDF
│   └── pipeline/               # Orchestration Dagster
├── docker-compose.yml          # Configuration de la stack
├── Dockerfile.dagster          # Environnement Dagster
└── Dockerfile.docling          # Environnement extraction GPU
```
