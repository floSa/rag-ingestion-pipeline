# Stockage et Recherche Vectorielle (ChromaDB)

## Présentation du service
La base vectorielle **ChromaDB** est le composant indispensable à l'algorithme "Retrieval" de tout système RAG. Pendant que NebulaGraph gère la logique de la structure et les relations d'ordres, ChromaDB va chercher précisément le fond, l'idée et la signification textuelle à la demande d'un Agent IA.

Grâce aux *embeddings* générés par le composant IA (`paraphrase-multilingual-MiniLM-L12-v2`), ChromaDB place chaque paragraphe extrait dans un espace mathématique multi-dimensionnel permettant de trouver instantanément un texte ayant un sens et un contexte similaire à la requête utilisateur.

## Accès au service
- **Type** : API Serveur Vectoriel HTTP
- **URL / Point d'Entrée API** : `http://localhost:8080/api/v1` (Accès Docker interne : `chromadb:8000`)
- Le client se connecte via la librairie Python officielle : `chromadb.HttpClient`.

## Structure et définition des données
La collection utilisée est **`rag_documents`**.

- **Identifiant du chunk** : l'identifiant cryptographique (Hash ID) de l'élément d'où part la lecture du chunk. Un élément réparti sur plusieurs chunks leur donne un suffixe : `023351d5f4#0`, `023351d5f4#1`… Un élément tenant en un seul chunk garde son identifiant nu, de sorte que les documents déjà ingérés sont mis à jour et non dupliqués.
- **Embeddings** : représentation mathématique du texte du chunk, produite par `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions), encodée par lots. Le modèle est **multilingue** : une question française retrouve les passages anglais pertinents, et réciproquement.
- **Documents** : le contenu en texte pur du chunk. Rien n'est tronqué : le découpage remplace l'ancienne coupe à 1000 caractères, qui amputait silencieusement les paragraphes longs.

Un point important : **la collection ne contient pas un vecteur par élément du document, mais un vecteur par chunk**. Le découpage est confié à `HybridChunker`, le découpeur de Docling : il regroupe ce qui va ensemble en respectant la structure du document, et reçoit le tokenizer du modèle d'embedding. Tous les éléments restent en revanche dans NebulaGraph : la structure du document est intacte, et `/context/{element_id}` la reconstruit. Voir [extraction_donnees.md](extraction_donnees.md#ce-qui-part-dans-lindex-vectoriel).

> **Les fragments isolés ne sont pas « absorbés dans leur paragraphe d'origine ».** Cette phrase décrivait `build_blocks`, un regroupement maison retiré du dépôt : sa doctrine était « fusionner plutôt que jeter », et `blocks.py` affirmait au même moment que `HybridChunker` « n'a pas de `min_tokens` : les fragments isolés y survivent ». Les deux ne pouvaient pas être vrais ensemble, et aucun des deux ne décrivait le code (registre §5.2).
>
> Ce que la production fait, `mesuré` à `src/docling_service/vectors.py:230` : `HybridChunker` fusionne les pairs de même métadonnée (`merge_peers`), puis un chunk est **écarté** — pas absorbé — s'il n'a aucun caractère alphanumérique ou s'il est plus court que `MIN_CHUNK_CHARS`.
>
> **Et ce rejet est borné, ce qui compte autant que le rejet lui-même.** Il ne s'applique qu'à un chunk qui est le **seul** de son élément. Une fenêtre du *milieu* d'un texte continu est conservée même courte : sans cette borne, l'agent concaténerait les chunks d'un élément et obtiendrait un texte troué, ce qui est arrivé sur deux éléments de l'index (registre §4.28.a).

Le vecteur est par ailleurs calculé sur le texte **précédé du titre de sa section**, alors que le document stocké reste le texte brut. Le passage s'affiche donc tel quel côté agent, mais le vecteur porte son contexte.
- **Métadonnées intégrées** — définies par `ChunkMetadata` dans `src/pipeline/schemas.py`, qui est le contrat de référence avec `rag-agent-chat` :

| Clé | Rôle |
|---|---|
| `element_id` | Hash 10 hexadécimaux de l'élément. **Toujours l'élément, jamais le chunk** : `rag-agent-chat` valide `/context/{element_id}` sur `^[a-f0-9]{10}$` |
| `graph_node_id` | Clé de pivot vers NebulaGraph (même valeur) |
| `filename` | Nom du fichier source — le **chapitre** |
| `collection` | Dossier parent — l'**ouvrage** dont vient le chapitre |
| `source_path` | Chemin complet relatif à `Datas/`, identité unique du document |
| `label` | Tag Docling, pour filtrer par type (`table`, `formula`, `text`…) |
| `page_no` | Page source, pour citer la référence à l'utilisateur |
| `minio_url` | URL de l'image associée, le cas échéant |
| `reference_id` | Section parente (ou `DOC`) |
| `language` | Langue du document (`en`, `fr`…), vide si indéterminée. Voir plus bas |
| `depth` | Profondeur du chunk dans la hiérarchie des titres (0 = titre de tête) |
| `section_title` | Titre de la section, exploitable pour l'affichage des citations |
| `page_position` | Rang de l'élément dans sa page |
| `ref_position` | Rang de l'élément sous son parent |
| `chunk_index` / `chunk_count` | Position du chunk dans son bloc |
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

### Le garde-fou : le service refuse de démarrer sur un autre modèle

Cette panne est la plus coûteuse de la chaîne et la seule qui ne laisse **aucune trace** : ni exception, ni journal, ni sonde de santé. La recherche rend des passages plausibles et faux.

Elle est muette pour une raison qui dicte la forme de la protection : `all-MiniLM-L6-v2` produit lui aussi des vecteurs de **384 dimensions**. ChromaDB les accepte sans broncher. **Vérifier la dimension ne protège donc de rien** — ce serait le contrôle vert des deux côtés du défaut. C'est le **nom** qui discrimine.

Et le chemin réel de la dérive n'est pas le code, c'est l'environnement. `DoclingSettings` dérive de `BaseSettings` : `EMBEDDING_MODEL_NAME` **écrase le défaut du code**. Un `.env` resté à `all-MiniLM-L6-v2` a survécu ici à toute la réingestion multilingue, hors de portée du versionnement — le fichier n'est pas suivi par git.

Deux verrous, posés dans `src/docling_service/embedding.py`, tous deux du côté qui **produit** les vecteurs :

| Verrou | Où | Ce qu'il attrape |
|---|---|---|
| `verify_model_name()` | au démarrage du service, et avant chaque chargement de modèle | un `EMBEDDING_MODEL_NAME` hors contrat, qu'il vienne du code ou de l'environnement |
| `verify_dimension()` | sur le modèle réellement chargé | un artefact qui ne correspond pas au nom sous lequel il a été chargé |

Le démarrage est **délibérément brutal** : l'exception n'est pas rattrapée dans `lifespan`, le conteneur meurt. Un service mort se voit ; un index silencieusement anglais, non.

Le défaut de `embedding_model_name` **est** la constante `CONTRACT_MODEL`, et non un littéral recopié : il ne reste aucune dérive possible entre le code et le contrat. Le seul chemin qui subsiste est l'environnement, que le garde-fou ferme.

> Le préfixe d'organisation Hugging Face est accepté : `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` désigne le même artefact. Un garde-fou qui produit des faux positifs finit désactivé.

### Les quatre façons de traiter le multilingue

Quand la question et le corpus ne sont pas dans la même langue, quatre approches existent. Elles ne se valent pas.

| Approche | Ce que ça coûte | Ce que ça vaut |
|---|---|---|
| **1. Modèle d'embedding multilingue** | Une ré-ingestion, et le même modèle des deux côtés | **La plus simple et la plus sûre.** Un seul index, aucune latence ajoutée, rien à traduire. Le texte original reste la source citée. |
| **2. Traduire la question au moment de la recherche** | Un appel de modèle par question (~1 s), et le risque de traduire de travers un terme technique | Honorable si le corpus est **d'une seule langue**. Ingérable quand il en mélange plusieurs : traduire vers quoi ? |
| **3. Traduire les documents à l'ingestion** | Très cher (des heures de calcul), et lourd de conséquences | **À éviter.** Une traduction automatique déforme le vocabulaire technique, et on cite alors un texte que l'auteur n'a jamais écrit. |
| **4. Double index, original et traduit** | Deux fois la place, deux fois l'ingestion | Se défend pour un corpus critique. Disproportionné ici. |

**C'est l'approche 1 qui est en place.** Elle répond exactement au besoin : quelle que soit la langue de la question, on interroge l'intégralité des ressources. Le modèle place « livraison continue » et « continuous delivery » au même endroit de l'espace vectoriel — il n'y a rien à traduire, et le texte cité reste celui de l'auteur.

La métadonnée `language` reste utile pour dire à l'utilisateur dans quelle langue sont les sources trouvées, ou pour filtrer quand la question porte explicitement sur un corpus donné.

### D'où vient la métadonnée `language`

Détectée par comptage de mots-outils sur les 20 000 premiers caractères du document ([`language.py`](src/docling_service/language.py)). À l'échelle d'un ouvrage, c'est très discriminant — ce qui serait fragile sur une seule phrase.

Sept langues reconnues : `en`, `fr`, `es`, `de`, `it`, `pt`, `nl`. La valeur est **vide** dès que le doute est permis : mieux vaut pas de réponse qu'une mauvaise. Les mots partagés entre plusieurs langues (`la`, `de`, `on`…) sont retirés des listes au chargement, sinon ils feraient pencher un score au hasard.

Vérifié sur le corpus : 6 notes françaises et les chapitres anglais correctement identifiés, aucune erreur.

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
