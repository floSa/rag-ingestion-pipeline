# Microservice d'Extraction de Données (Docling)

## 📌 Présentation du Service
Le service d'extraction, surnommé `docling-service`, est le moteur d'ingestion principal du projet RAG Assistant. Développé en Python avec l'aide du framework FastAPI, ce microservice se charge de traiter les documents lourds (PDF, HTML) de manière asynchrone et structurée.

Il repose sur **Docling** (une puissante bibliothèque d'IBM pour l'analyse de layout assistée par IA) et sur des modèles de SentenceTransformers (`all-MiniLM-L6-v2`) pour générer les embeddings vectoriels. Ce service découpe le document intelligemment, isole les images et les tableaux, envoie les images au stockage objet, construit les nœuds sémantiques pour le graphe de connaissances, et vectorise les paragraphes textuels.

## 🔗 Accès au service
- **Type** : API REST (FastAPI)
- **URL** : `http://localhost:8000` (En interne réseau Docker : `docling-service:8000`)
- **Point d'entrée** : `POST /extract` avec body JSON `{"filepath": "/chemin/vers/le/fichier.pdf"}`
- **Note** : Ce service n'a pas d'interface graphique (UI).

## 🗂️ Structure et définition des données
Ce service ne stocke pas directement de données persistantes sur son propre disque. Il agit comme un **routeur et transformateur sémantique** :
- **Entrées** : Fichiers PDF et HTML lus depuis le volume monté `/opt/dagster/app/Datas`.
- **Mémoire tampon** : Utilise une variable Python `deque` (file d'attente) avec un `BUFFER_SIZE` défini à 1 pour transmettre presque en "temps réel" les résultats.
- **Sorties** :
  - **Identifiant Unique (Hash ID)** : Chaque élément extrait génère un ID cryptographique unique calculé à partir de `(filename, page_no, order, text[:50])`.
  - **Éléments Sémantiques** : Les données ingérées possèdent les labels : `Paragraph`, `SectionHeader`, `ListItem`, `Table`, `Picture`, `Caption`, `Code`, etc.
  - **Images `minio_url`** : Pour les figures et les tableaux, les données brutes sont redécoupées (crop) via *PyMuPDF* puis transférées à MinIO qui renvoie une URL stockée.

## 💻 Commandes Utiles
Puisque c'est un microservice sans base de données propre, la commande principale concerne la relance ou l'investigation des logs :
- **Voir en direct l'avancement de l'extraction (Batchs)** :
  ```bash
  docker compose logs docling-service --tail 100 -f
  ```
- **Lancer une requête d'extraction manuelle (sans passer par Dagster)** :
  ```bash
  curl -X POST "http://localhost:8000/extract" -H "Content-Type: application/json" -d '{"filepath": "/opt/dagster/app/Datas/pdfs/mon_livre.pdf"}'
  ```

## 🛠️ Problèmes rencontrés et Solutions
- **Problème de Saturation de Mémoire (Crash de la Base)** : Allouer 14Go de RAM au service Docling sur une machine WSL limitée à 16Go provoquait le crash par étouffement ("Out Of Memory") des autres services (Nebula, MinIO, Dagster).
  - *Solution* : Baisse de la RAM à **10 Go**, désactivation de la reconnaissance structurelle lourde des tableaux (`do_table_structure=False`) et réduction du flux (`CHUNK_SIZE = 5` pages et `BUFFER_SIZE = 1`).
- **Découpage des Images (Crop) muet et inactif** : Les images n'étaient pas envoyées par `PyMuPDF` dans MinIO, sans qu'aucune erreur ne s'affiche.
  - *Solution* : Docling fournit des coordonnées d'image "mathématiques" (origine de l'axe Y en Bas à Gauche), alors que PyMuPDF lit l'axe Y en Haut à Gauche. Les hauteurs des images calculées étaient donc inversées (négatives). Une conversion Bottom-Left vers Top-Left a été rajoutée dans `crop_and_upload_image`.
