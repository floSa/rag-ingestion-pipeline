# Graphe de Connaissances (NebulaGraph)

## Présentation du service
**NebulaGraph** conserve la structure des documents ingérés : l'ordre de lecture exact et l'imbrication des éléments (titres, paragraphes, tableaux, images, légendes, code…) des documents PDF, HTML et Markdown. Il permet de reconstituer un document à partir de ses éléments.

À noter : **le graphe conserve tous les éléments extraits**, y compris ceux que l'index vectoriel écarte (fragments de mise en page, éléments trop courts pour être retrouvés utilement). C'est ici, et seulement ici, que la structure du document est complète.

Le graphe complète la recherche sémantique. Quand ChromaDB rend un fragment de page, l'agent suit l'identifiant commun (`element_id` = `graph_node_id`) jusqu'à NebulaGraph pour lire la section qui l'entoure ou la figure qui l'illustre.

## Accès au service
- **Interface (Nebula Studio v3.8.0)** : [http://localhost:7001](http://localhost:7001). Se connecter avec l'hôte `graphd`, le port `9669`, le compte `root` et le mot de passe `nebula`.
- **Moteur de requête (graphd)** : `graphd:9669`, sur le réseau `rag_network` uniquement.

## Structure et définition des données
Toutes les données vivent dans l'espace `rag_space`, avec un tag par type d'élément. Le schéma exact (propriétés et types) est dans [services/nebulagraph.md](services/nebulagraph.md).

**Nœuds (vertices) :**
L'identifiant d'un élément est un hash de dix caractères ; celui d'un document dérive de son **chemin** (`doc_htms/Practical MLOps/Preface`), ce qui le rend lisible dans le Studio et distingue deux chapitres homonymes.
- **`Document`** : racine du document. Contient `filename` (le chapitre), `collection` (l'ouvrage dont il vient), `source_path` (chemin relatif à `Datas/`), `type_file` (`pdf`, `html` ou `md`), `total_pages` (1 pour les formats non paginés), `language` (code ISO 639-1, vide si indéterminée) et `content_hash` (SHA-256 du fichier source, qui sert à reconnaître un ouvrage déjà ingéré sous un autre nom).
- **`Paragraph`** / **`Formula`** / **`Code`** / **`ListItem`** / **`Caption`** / **`Footnote`** : éléments textuels.
- **`PageHeader`** / **`PageFooter`** : en-têtes et pieds de page, conservés dans le graphe.
- **`Picture`** / **`Table`** : éléments visuels portant `media_url` (l'adresse de l'objet) et `object_key` (sa clé nue), qui désignent l'objet stocké dans le [stockage d'objets](stockage_objets.md).
- **`SectionHeader`** : titres, qui structurent la hiérarchie.

Tous les tags d'élément portent les mêmes propriétés : `label`, `page_no`, `page_no_end`, `text`, `media_url`, `object_key`, `depth`.

**Arêtes (edges) :**
- **`PARENT_OF`** : orientée d'un parent vers un élément. Le parent d'un titre est **le titre qui le domine** : un sous-titre est rattaché à sa section, elle-même rattachée à son chapitre ; un titre de premier niveau est rattaché au `Document`. **La profondeur n'a pas de plafond** (registre §4.24). La règle, les **deux échelles** de `depth` et la distribution mesurée sont décrites dans `ChunkMetadata.depth` (`src/pipeline/schemas.py`). Voir [extraction_donnees.md](extraction_donnees.md#4-hierarchie-et-positions) pour les signaux utilisés selon la source. L'arête porte la propriété `sequence` (int), l'ordre de lecture, qui permet de reconstituer le document dans l'ordre.
- **`LINKED_TO`** : relie une légende (`Caption`) détectée par Docling à l'élément visuel qu'elle décrit (`Picture`, `Table`). Porte la propriété `relation` (string).

## Commandes utiles (Recherche / Agentique RAG)
Dans Nebula Studio, ne pas placer `USE rag_space;` dans la console : Studio le refuse (« DO NOT switch between graph spaces »). Sélectionner l'espace dans la liste déroulante en haut à droite, puis lancer les requêtes :

1.  **Lister les documents :**
    ```ngql
    MATCH (d:Document)
    RETURN d.Document.collection AS ouvrage,
           d.Document.filename AS chapitre,
           d.Document.type_file AS format
    LIMIT 50;
    ```

1bis. **Lister les chapitres d'un ouvrage donné :**
    ```ngql
    MATCH (d:Document)
    WHERE d.Document.collection == "Practical MLOps"
    RETURN d.Document.filename AS chapitre, id(d) AS identifiant;
    ```
2.  **Lister les enfants directs d'un document :**
    ```ngql
    MATCH (d:Document)-[r:PARENT_OF]->(e)
    WHERE id(d) == "doc_pdfs/statisticsfordatascience"
    RETURN e;
    ```
3.  **Récupérer un document complet (structure et contenu) :**
    ```ngql
    MATCH p=(d:Document)-[:PARENT_OF*..]->(e)
    WHERE id(d) == "doc_pdfs/statisticsfordatascience"
    RETURN p;
    ```
4.  **Lister les éléments rattachés à un titre donné :**
    ```ngql
    MATCH p=(s:SectionHeader)-[:PARENT_OF*..]->(e)
    WHERE id(s) == "<INSCRIRE_ID_DU_TITRE>"
    RETURN p;
    ```
5.  **Afficher un élément à partir de son identifiant (par exemple l'`element_id` rendu par ChromaDB) :**
    ```ngql
    MATCH (v)
    WHERE id(v) == "<INSCRIRE_ID_ICI>"
    RETURN tags(v), properties(v);
    ```

## Problèmes rencontrés et solutions
- **Arrêts de NebulaGraph pendant l'ingestion** :
  - *Problème* : sur une machine sans marge mémoire, NebulaGraph s'arrêtait pendant les écritures continues de Docling, et ses conteneurs ne repartaient pas après un arrêt de la machine (par exemple une fermeture de WSL).
  - *Solution* : `restart: unless-stopped` sur `metad`, `storaged` et `graphd`, et une limite mémoire de **10 Go** pour `docling-service` (`deploy.resources.limits.memory`), qui laisse de la marge au moteur de graphe.
- **« DO NOT switch between graph spaces » dans Studio** :
  - *Problème* : Studio refuse une requête qui commence par `USE rag_space; MATCH ...`.
  - *Solution* : sélectionner l'espace dans l'interface ; les requêtes de ce document n'incluent pas de `USE`.

## Vérification de la hiérarchie

Le code qui reconstruit la hiérarchie des titres est dans `hierarchy.py` (assemblage de l'arbre) et `ranking.py` (rang de chaque titre). `ranking.py` reçoit des mesures déjà prises et ne fait que décider : il n'importe ni Docling ni PyMuPDF, et se teste donc hors de l'image d'extraction.

`tests/unit/test_hierarchie_bout_en_bout.py` part d'items tels que Docling les rend, traverse le calcul du rang, et vérifie à l'arrivée la forme de l'arbre. Un test qui injecterait le rang à la main resterait vert même si `flat_rank` et `pdf_heading_rank` rendaient toujours `None` : tous les titres deviendraient alors frères sous le document, et le graphe serait plat.

La profondeur réelle sur le corpus est mesurée après ingestion : `src/index_report.py` donne le rapport de profondeur par document, et la distribution mesurée sur l'index vivant (maximum 5) est consignée dans `ChunkMetadata.depth`.
