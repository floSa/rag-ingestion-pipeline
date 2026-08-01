# Stockage et Recherche Vectorielle (ChromaDB)

## Présentation du service
La base vectorielle **ChromaDB** est le composant indispensable à l'algorithme "Retrieval" de tout système RAG. Pendant que NebulaGraph gère la logique de la structure et les relations d'ordres, ChromaDB va chercher précisément le fond, l'idée et la signification textuelle à la demande d'un Agent IA.

Grâce aux *embeddings* générés par le composant IA (`all-MiniLM-L6-v2`), ChromaDB place chaque paragraphe extrait dans un espace mathématique multi-dimensionnel permettant de trouver instantanément un texte ayant un sens et un contexte similaire à la requête utilisateur.

## Accès au service
- **Type** : API Serveur Vectoriel HTTP
- **URL / Point d'Entrée API** : `http://localhost:8080/api/v1` (Accès Docker interne : `chromadb:8000`)
- Le client se connecte via la librairie Python officielle : `chromadb.HttpClient`.

## Structure et définition des données
La collection utilisée est **`rag_documents`**.

- **Identifiant du chunk** : l'identifiant cryptographique (Hash ID) de l'élément. Un élément dont le texte dépasse `CHUNK_SIZE` est découpé en plusieurs fenêtres recouvrantes, et ses chunks reçoivent alors un suffixe : `023351d5f4#0`, `023351d5f4#1`… Un élément court garde son identifiant nu, de sorte que les documents déjà ingérés sont mis à jour et non dupliqués.
- **Embeddings** : représentation mathématique du texte du chunk, produite par `all-MiniLM-L6-v2` (384 dimensions), encodée par lots.
- **Documents** : le contenu en texte pur du chunk. Rien n'est tronqué : le découpage remplace l'ancienne coupe à 1000 caractères, qui amputait silencieusement les paragraphes longs.

Un point important : **la collection ne contient pas un vecteur par élément du document, mais un vecteur par bloc**. L'analyse de layout produit quantité de fragments isolés (`x`, `and`, `Note`, `-`) qui n'ont aucun sens une fois vectorisés ; ils sont fusionnés avec leurs voisins de même section, et les résidus sont écartés. Tous les éléments restent en revanche dans NebulaGraph : la structure du document est intacte, et `/context/{element_id}` la reconstruit. Voir [extraction_donnees.md](extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

Le vecteur est par ailleurs calculé sur le texte **précédé du titre de sa section**, alors que le document stocké reste le texte brut. Le passage s'affiche donc tel quel côté agent, mais le vecteur porte son contexte.
- **Métadonnées intégrées** — définies par `ChunkMetadata` dans `src/pipeline/schemas.py`, qui est le contrat de référence avec `rag-agent-chat` :

| Clé | Rôle |
|---|---|
| `element_id` | Hash 10 hexadécimaux de l'élément. **Toujours l'élément, jamais le chunk** : `rag-agent-chat` valide `/context/{element_id}` sur `^[a-f0-9]{10}$` |
| `graph_node_id` | Clé de pivot vers NebulaGraph (même valeur) |
| `filename` | Fichier source, pour restreindre une recherche à un document |
| `label` | Tag Docling, pour filtrer par type (`table`, `formula`, `text`…) |
| `page_no` | Page source, pour citer la référence à l'utilisateur |
| `minio_url` | URL de l'image associée, le cas échéant |
| `reference_id` | Section parente (ou `DOC`) |
| `section_title` | Titre de la section, exploitable pour l'affichage des citations |
| `page_position` | Rang de l'élément dans sa page |
| `ref_position` | Rang de l'élément sous son parent |
| `chunk_index` / `chunk_count` | Position du chunk dans son bloc |
| `block_size` | Nombre d'éléments du document fusionnés dans ce chunk |

## Commandes utiles
Lors de vos futurs développements du système RAG Agentique, vous nécessiterez régulièrement ces concepts :
- **Intérroger la collection en Python :** 
  ```python
  import chromadb
  client = chromadb.HttpClient(host='localhost', port=8080)
  collection = client.get_or_create_collection(name="rag_documents")
  
  # Requête sur un contexte RAG métier (ici: cibler que les paragraphes et les formules avec un texte lié aux analyses statistiques)
  results = collection.query(
      query_texts=["Comment calculer la médiane ?"],
      n_results=3,
      where={"label": {"$in": ["paragraph", "formula"]}} # Utilisation filtrée indispensable !
  )
  ```
- **Diagnostic système global de maintien docker** :
  ```bash
  docker compose logs chromadb --tail 50
  ```

## Problèmes rencontrés et solutions
- **Intégrité de Base Perdue au Reboot** : 
  - *Problème* : L'inexistence de `restart: unless-stopped` en politique de redémarrage sur le conteneur ChromaDB faisait disparaître ou arrêter inopinément le service dès réveil d'une nuit de fermeture du terminal (WSL). Le service demandeur `docling-service` ne parvenait alors plus à trouver son système cible et jetait les paquets vectoriels dans le vide.
  - *Solution* : Ajouté ce jour des conditions optimales `restart: unless-stopped` sur la déclaration docker, forçant chroma à se relancer instantanément et récupérer automatiquement ses collections depuis son montage `/Datas/database/chromadb`.
