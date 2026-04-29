# Architecture Déployée et Mise en Place du RAG Assistant

Ce document constitue la référence exhaustive de l'architecture déployée sur votre instance RAG Assistant. 
L'ensemble de ces implémentations ont été conceptualisées et validées au sein du pipeline de production. Les directives de l'extraction, de la vectorialisation, et du stockage sont scrupuleusement respectées selon les derniers standards de l'art.

---

## 🏗 Topologie de la Déclaration Infrastructurelle (Docker-Compose)

Au cœur du projet, le fichier `docker-compose.yml` a subi une refonte complète. Nous avons retiré Weaviate/Neo4j pour instancier la stack validée ensemble :

1. **La Base Graphe (NebulaGraph)**
   * Démultiplication en trois composants natifs `metad` (Métadonnées), `storaged` (Stockage distribué), et `graphd` (Moteur de requête).
   * Intégration de l'UI **Nebula Studio** monté sur le port **7001**, paramétré sur le réseau bridge pour visualiser de façon graphique la création de vos noeuds et relation (`edge`) d'un seul clic.

2. **La Base Vectorielle (ChromaDB)**
   * Déployée sous forme de base autonome. L'API est logée sur le port host `8080` (pour éviter les interférences avec le Docling de FastAPI parqué en 8000).

3. **L'Object Storage (MinIO)**
   * Persistance totale des images massives. Le serveur tourne sur les ports `9000` (API S3) et `9001` (Dashboard de gestion MinIO UI).

4. **L'Orchestrateur ETL (Dagster)**
   * Découpage standard Dagster avec `postgres-dagster` comme instance de métadonnées Dagster, `dagster-webserver` (Port 3000) et `dagster-daemon` autorisant les lancements de job par écoute d'événement sensoriel asynchrone (Sensors).
   * Support de volume strict partagé (`/Datas`) avec le Docling Service et monté de part en part.

5. **Le Moteur d'Extraction sur GPU (Docling FastAPI)**
   * Microservice `docling-service` embarqué sous Python 3.10-slim.
   * Révolution dans la chaîne : l'OS possède les drivers système (`libgl1`, etc.) nécessaires à PyMuPDF.

---

## 🚀 L'intelligence de l'Extraction (Docling-Service API)

Toute la charge complexe n'est plus traitée par Dagster, afin de soulager sa mémoire virtuelle. Le service Dagster appelle en POST `http://docling-service:8000/extract`.

* **Algorithmique Docling v9.1 Intégrée** : Le microservice FastApi lance l'analyse via la classe `DocumentConverter` et son dictionnaire d'arborescence (Sections > Sous sections > Textes / Listes / Captions).
* **Crop Asynchrone de Médias PyMuPDF (fitz)** : L'extraction Media (Tableaux & Images) devient absolue. Lors de la structuration, Docling ramène des *Bounding Boxes*. Dès lors :
  1. FastAPI ouvre un client **PyMuPDF** directement sur l'archive binaire PDF mappée dans le volume partagé.
  2. Il "Crop" aux coordonnées exactes le cadre en lui affligeant une matrice de redimentionnement (Zoom) pour conserver la résolution haute définition (très critique pour l'OCR ultérieure par un LLM Multimodal sur des Tableaux).
  3. L'extrait Pixmap transigé en Bytes Buffer se voit directement poussé sur le Bucket **`documents`** du serveur MinIO.
  4. L'URL générée est instantanément assignée à la variable sémantique JSON `'minio_url'` du noeud en cours ! Le Tableau Markdown reste néanmoins encapsulé dans `'content'` pour ne rien jeter de potentiellement utile.

---

## 🧠 Le Séquençage Algorithmique par Dagster (`src/pipeline`)

Dagster décompose mathématiquement le reste du processus à travers des Jobs/Sensors ciblés.

### Séparation des capteurs autonomes (Sensors)
Au lieu d'un simple déclenchement sur "Datas", Dagster dispose de deux sentinelles logicielles indépendantes qui comparent l'état d'intégrité MD5/modtime : 
- `pdf_sensor` > Déclenche `pdf_pipeline_job`
- `html_sensor` > Déclenche `html_pipeline_job`

### Pré-traitement curatif du code (BeautifulSoup)
Les livres Packt, O'Reilly et la majorité du HTML corrompt totalement un pipeline d'IA s'il n'est pas purgé.
- L'asset `pre_process_html` utilise la librairie `bs4` pour disséquer le DOM entier. Les balises parasites (`nav`, `header`, `footer`) et classes réputées de ces éditeurs (`.packt-header`, `.sbo-site-nav`) sont brutalement expurgées (fonction `.decompose()`). 
- Cela certifie que Docling ne lira qu'un texte pure player 100% "cours", évitant l'angoisse d'avoir des listes menus "Chapitre Précédent/Suivant" dans nos vecteurs d'apprentissage RAG !

### Convergence Graphe et Sémantique Locale
- `build_knowledge_graph` : Asset qui initie un pool connexion `nebula3-python`. À l'avenir, toutes les clés "parents" récupérées y seront structurées via du code N-GQL (ex: `CREATE SPACE...`, `INSERT VERTEX... EDGE...`).
- `vectorize_content` : Asset terminal d'une très grande maturité orientée "Open-Source/Prodution commerciale". 
  * Aucun appel coûteux OpenAI. Modèle 100% local ultra-rapide opéré via **SentenceTransformers** (`all-MiniLM-L6-v2`).
  * Processus de "Chunking intelligent" des gros blocs `text`, garantissant qu'aucun morceau n'oubliera son origine en dupliquant sur chaque bloc les identifiants clés du parent : `page_position`, `ref_position`, et surtout l'identifiant asynchrone principal `graph_node_id`.

## Synthèse du Workflow de bout-en-bout
1. **Dépôt** d'un fichier (ex: livre.pdf) dans `/Datas`.
2. **Dagster (Sensor)** s'éveille et lance le job concerné `pdf_pipeline_job`.
3. L'Asset **Pre-process** vérifie ledit document (Poids, intégrité).
4. Soumission par requête POST de l'URL virtuelle au container **Docling**.
5. Docling découpe au rayon laser, coupe l'Image via **PyMuPdf**, sauvegarde l'Image sur **MinIO**, et rend un JSON global certifié !
6. **Dagster** récolte le JSON en retour.
7. L'Asset Mémoriel se connecte à **NebulaGraph** (Création de la hiérarchie).
8. L'Asset Vectoriel découpe le JSON, vectorise les occurrences via le moteur local `SentenceTransformers` et les transmet à **ChromaDB** tout en incluant l'identifiant pour requêter de nouveau Nebula Graph plus tard.
