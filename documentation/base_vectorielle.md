# Stockage et Recherche Vectorielle (ChromaDB)

## Présentation du service
La base vectorielle **ChromaDB** porte la recherche sémantique du RAG. NebulaGraph conserve la structure du document et l'ordre des éléments ; ChromaDB retrouve les passages dont le sens est proche d'une question.

Chaque chunk est représenté par un *embedding* calculé par `paraphrase-multilingual-MiniLM-L12-v2` : un vecteur de 384 dimensions. Deux textes de sens voisin ont des vecteurs proches.

## Accès au service
- **Type** : API HTTP de ChromaDB
- **Adresse** : `chromadb:8000`, sur le réseau `rag_network` uniquement (le port n'est pas publié sur l'hôte)
- Le client se connecte par la bibliothèque Python officielle : `chromadb.HttpClient`.

## Structure et définition des données
La collection utilisée est **`rag_documents`**.

- **Identifiant du chunk** : l'identifiant cryptographique (Hash ID) de l'élément d'où part la lecture du chunk. Un élément réparti sur plusieurs chunks leur donne un suffixe : `023351d5f4#0`, `023351d5f4#1`… Un élément tenant en un seul chunk garde son identifiant nu. C'est une clause du contrat, et `verify_contract` la compte : **974 ids suffixés sur 4 365** (mesuré le 2 septembre 2026 sur l'index vivant). Ce suffixe ne protège pas de la duplication à la réingestion : c'est `extraction.extract` qui s'en charge, en purgeant le document par `source_path` avant de le réécrire (registre §4.2, §4.31.B3).
- **Embeddings** : représentation mathématique du texte du chunk, produite par `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions), encodée par lots. Le modèle est **multilingue** : une question française retrouve les passages anglais pertinents, et réciproquement.
- **Documents** : le texte intégral du chunk. Le texte *stocké* n'est jamais tronqué ; le *vecteur* peut l'être, car le modèle tronque ce qui dépasse sa fenêtre. Le chiffre et ses deux causes sont documentés à un seul endroit, `vectors.get_chunker` dans `src/docling_service/vectors.py` (registre §3.4 bis).

Un point important : **la collection ne contient pas un vecteur par élément du document, mais un vecteur par chunk**. Le découpage est confié à `HybridChunker`, le découpeur de Docling : il regroupe ce qui va ensemble en respectant la structure du document, et reçoit le tokenizer du modèle d'embedding. Tous les éléments restent en revanche dans NebulaGraph : la structure du document est intacte, et `/context/{element_id}` la reconstruit. Voir [extraction_donnees.md](extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

Les fragments isolés : `HybridChunker` fusionne par défaut les éléments voisins de même métadonnée (`merge_peers`). Ensuite, dans `vectors.build_chunks`, un chunk est **écarté** s'il n'a aucun caractère alphanumérique ou s'il est plus court que `MIN_CHUNK_CHARS`, **et seulement s'il est le seul chunk de son élément**. Une fenêtre du *milieu* d'un texte continu est conservée même courte : sinon, l'agent qui concatène les chunks d'un élément obtiendrait un texte troué (registre §4.28.a).

Le vecteur est par ailleurs calculé sur le texte **précédé du titre de sa section**, alors que le document stocké reste le texte brut. Le passage s'affiche donc tel quel côté agent, mais le vecteur porte son contexte.

**Métadonnées** — définies par `ChunkMetadata` dans `src/pipeline/schemas.py`, qui est le contrat de référence avec `rag-agent-chat` :

| Clé | Rôle |
|---|---|
| `element_id` | Hash 10 hexadécimaux de l'élément. **Toujours l'élément, jamais le chunk** : `rag-agent-chat` valide `/context/{element_id}` sur `^[a-f0-9]{10}$` |
| `graph_node_id` | Clé de pivot vers NebulaGraph (même valeur) |
| `filename` | Nom du fichier source — le **chapitre** |
| `collection` | Dossier parent — l'**ouvrage** dont vient le chapitre |
| `source_path` | Chemin complet relatif à `Datas/`, identité unique du document |
| `label` | Tag Docling, pour filtrer par type (`table`, `formula`, `text`…) |
| `page_no` | **Première** page du chunk, pour citer la référence à l'utilisateur |
| `page_no_end` | **Dernière** page couverte. Égale à `page_no` sauf pour un élément que Docling a fusionné par-dessus une frontière de page — citer « page N » seule est alors inexact |
| `media_url` | Adresse de l'image associée, le cas échéant. **Interne et authentifiée** : l'agent est le proxy, il ne la passe jamais à un navigateur |
| `object_key` | La clé nue de ce même objet, celle passée à `put_object`. L'adresse porte l'hôte et devient fausse s'il change ; la clé reste valable |
| `reference_id` | Section parente (ou `DOC`) |
| `language` | Langue du document (`en`, `fr`…), vide si indéterminée. Voir plus bas |
| `depth` | Profondeur dans la hiérarchie des titres. **Aucun plafond**, et **deux échelles s'y croisent** : c'est `label` qui dit laquelle. Définition de référence : `ChunkMetadata.depth` |
| `section_title` | Titre de la section, exploitable pour l'affichage des citations |
| `page_position` | Rang de l'élément dans sa page |
| `ref_position` | Rang de l'élément sous son parent |
| `chunk_index` / `chunk_count` | Position du chunk parmi les chunks de son élément, et leur nombre |
| `block_size` | Nombre d'éléments du document fusionnés dans ce chunk |

## Pourquoi un modèle d'embedding multilingue

Le corpus mélange le français et l'anglais, et les questions arrivent dans l'une ou l'autre langue. L'ancien modèle, `all-MiniLM-L6-v2`, n'était entraîné que sur de l'anglais : il **classait par langue avant de classer par sens**.

Mesure sur une question française, face à six passages :

| Rang | `all-MiniLM-L6-v2` | `paraphrase-multilingual-MiniLM-L12-v2` |
|---|---|---|
| 1 | FR pertinent (0,453) | **EN pertinent (0,746)** |
| 2 | FR proche (0,433) | **FR pertinent (0,741)** |
| 3 | **FR hors sujet (0,397)** | FR proche (0,492) |
| 4 | **EN pertinent (0,366)** | EN proche (0,441) |
| 5 | EN proche (0,267) | FR hors sujet (0,338) |
| 6 | EN hors sujet (0,105) | EN hors sujet (0,313) |

Avec l'ancien modèle, un **hors-sujet français** devançait la **bonne réponse anglaise** : poser sa question en français revenait à se couper de toute la bibliothèque anglaise. Avec le nouveau, les deux bonnes réponses arrivent en tête à 0,005 d'écart, quelle que soit leur langue.

### Comment lire ces scores

Ce sont des **similarités cosinus**, pas des pourcentages :

| Valeur | Signification |
|---|---|
| 1,0 | même sens exactement |
| 0,7 – 0,8 | dit la même chose autrement |
| 0,4 – 0,5 | même domaine, sujet différent |
| 0,0 | aucun rapport |

Ce qui compte n'est pas la valeur absolue mais **l'écart entre les candidats**.

### Ce que ça impose à `rag-agent-chat`

Le modèle se change par `EMBEDDING_MODEL_NAME` dans `.env`, mais **ce n'est pas une décision locale** : l'agent doit encoder ses questions avec le même modèle, sans quoi les vecteurs ne sont plus comparables et les réponses deviennent aberrantes **sans qu'aucune erreur n'apparaisse**. La dimension étant identique (384), c'est le seul changement à faire de son côté.

### Le service refuse de démarrer sur un autre modèle

Cette panne est la plus coûteuse de la chaîne et la seule qui ne laisse **aucune trace** : ni exception, ni journal, ni sonde de santé. La recherche rend des passages plausibles et faux.

Elle est muette pour une raison qui dicte la forme de la protection : `all-MiniLM-L6-v2` produit lui aussi des vecteurs de **384 dimensions**. ChromaDB les accepte sans broncher. **Vérifier la dimension ne suffit donc pas** : le contrôle passerait avec les deux modèles. C'est le **nom** qui les distingue.

La dérive vient de l'environnement plutôt que du code. `DoclingSettings` dérive de `BaseSettings` : `EMBEDDING_MODEL_NAME` **écrase le défaut du code**. Un `.env` resté à `all-MiniLM-L6-v2`, non suivi par git, a déjà survécu à une réingestion complète avec le modèle multilingue.

Deux contrôles, dans `src/docling_service/embedding.py`, tous deux du côté qui **produit** les vecteurs :

| Contrôle | Où | Ce qu'il attrape |
|---|---|---|
| `verify_model_name()` | au démarrage du service, et avant chaque chargement de modèle | un `EMBEDDING_MODEL_NAME` hors contrat, qu'il vienne du code ou de l'environnement |
| `verify_dimension()` | sur le modèle réellement chargé | un artefact qui ne correspond pas au nom sous lequel il a été chargé |

L'échec au démarrage est voulu : l'exception n'est pas rattrapée dans `lifespan`, et le conteneur s'arrête. Un service arrêté se voit ; un index encodé avec le mauvais modèle, non.

Le défaut de `embedding_model_name` est la constante `CONTRACT_MODEL`, et non un littéral recopié : le code ne peut pas diverger du contrat. Seul l'environnement le peut, et le contrôle ci-dessus le couvre.

> Le préfixe d'organisation Hugging Face est accepté : `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` désigne le même artefact, et le refuser serait un faux positif.

### Les quatre façons de traiter le multilingue

Quand la question et le corpus ne sont pas dans la même langue, quatre approches existent. Elles ne se valent pas.

| Approche | Ce que ça coûte | Ce que ça vaut |
|---|---|---|
| **1. Modèle d'embedding multilingue** | Une ré-ingestion, et le même modèle des deux côtés | **La plus simple et la plus sûre.** Un seul index, aucune latence ajoutée, rien à traduire. Le texte original reste la source citée. |
| **2. Traduire la question au moment de la recherche** | Un appel de modèle par question (~1 s), et le risque de traduire de travers un terme technique | Honorable si le corpus est **d'une seule langue**. Ingérable quand il en mélange plusieurs : traduire vers quoi ? |
| **3. Traduire les documents à l'ingestion** | Très cher (des heures de calcul), et lourd de conséquences | **À éviter.** Une traduction automatique déforme le vocabulaire technique, et le texte cité n'est plus celui de l'auteur. |
| **4. Double index, original et traduit** | Deux fois la place, deux fois l'ingestion | Se défend pour un corpus critique. Disproportionné ici. |

**C'est l'approche 1 qui est en place.** Quelle que soit la langue de la question, la recherche porte sur l'intégralité du corpus. Le modèle place « livraison continue » et « continuous delivery » au même endroit de l'espace vectoriel — il n'y a rien à traduire, et le texte cité reste celui de l'auteur.

La métadonnée `language` reste utile pour dire à l'utilisateur dans quelle langue sont les sources trouvées, ou pour filtrer quand la question porte explicitement sur un corpus donné.

### D'où vient la métadonnée `language`

Détectée par comptage de mots-outils sur les 20 000 premiers caractères du document ([`language.py`](../src/docling_service/language.py)). À l'échelle d'un ouvrage, c'est très discriminant — ce qui serait fragile sur une seule phrase.

Sept langues reconnues : `en`, `fr`, `es`, `de`, `it`, `pt`, `nl`. La valeur est **vide** dès que le doute est permis : mieux vaut pas de réponse qu'une mauvaise. Les mots partagés entre plusieurs langues (`la`, `de`, `on`…) sont retirés des listes au chargement, sinon ils feraient pencher un score au hasard.

Vérifié sur le corpus : 6 notes françaises et les chapitres anglais correctement identifiés, aucune erreur.

## Commandes utiles
- **Interroger la collection en Python**, depuis le conteneur `docling-service` (`docker compose exec docling-service python`). La question doit être encodée avec le modèle du contrat : `query_texts` utiliserait la fonction d'embedding par défaut de ChromaDB, qui n'est pas ce modèle.
  ```python
  import chromadb
  from sentence_transformers import SentenceTransformer

  client = chromadb.HttpClient(host="chromadb", port=8000)
  collection = client.get_collection(name="rag_documents")
  modele = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

  # Filtrer sur le type d'élément : ici, les paragraphes et les formules.
  results = collection.query(
      query_embeddings=modele.encode(["Comment calculer la médiane ?"]).tolist(),
      n_results=3,
      where={"label": {"$in": ["text", "paragraph", "formula"]}},
  )
  ```
- **Journaux du service** :
  ```bash
  docker compose logs chromadb --tail 50
  ```

## Problèmes rencontrés et solutions
- **Service absent après un redémarrage de la machine** :
  - *Problème* : sans politique de redémarrage, le conteneur ChromaDB ne repartait pas après un arrêt de la machine (par exemple une fermeture de WSL). `docling-service` ne trouvait plus la base et l'écriture des vecteurs échouait.
  - *Solution* : `restart: unless-stopped` sur le service dans `docker-compose.yml`. ChromaDB repart et relit ses collections depuis le volume `Datas/database/chromadb/`.
