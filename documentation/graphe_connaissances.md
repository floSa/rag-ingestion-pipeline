# Graphe de Connaissances (NebulaGraph)

## Présentation du service
Le système **NebulaGraph** est le garant de la hiérarchisation intellectuelle du RAG Assistant. S’il fallait vulgariser, on dirait qu'il "remonte le livre d’origine". Contrairement aux bases de données relationnelles traditionnelles, cette base structure et sauvegarde l'ordre de lecture exact ainsi que les imbrications complexes trouvées dans vos documents PDF & HTML (Tableaux, Images, Légendes, Code...).

Le graphe sémantique permet lors d'une interrogation RAG agentique d'aller bien au-delà de la recherche mot par mot. Si une réponse trouvée dans ChromaDB est juste le fragment abstrait d'une page, l'agent pourra se tourner vers ce NebulaGraph via l'ID commun afin d'interroger la section qui précède ou la figure qui illustre l'idée, ajoutant ainsi une extrême puissance de contexte.

## Accès au service
- **Interface UI Officiel (Studio)** : [http://localhost:7001](http://localhost:7001)
  - Il s'agit de **Nebula Studio v3.8.0**, sur le port standard de l'application visuelle. Vous y entrez le Node IP `graphd` et le port Core API `9669`. Compte : `root` (Mdp: `nebula`).
- **Base Centralisée (Graph Daemon API)** : `graphd:9669` (Intra-Docker).

## Structure et définition des données
Ce graphe repose fortement sur deux aspects logiques clés : **L'espace isolé** (`rag_space`) et Les **Tags ultra-granulaires**.
A chaque lecture d'un livre, et à chaque bloc d'ingestion envoyé par l'API Docling, les données suivantes grandissent en base :

**Nœuds (Les Vertices) :**
Ils stockent les informations inhérentes. L'ID standard du vertex provient lui encore du fameux Hash généré en Python.
- **`Document`** : Noeud Root ou Racine (Contient: `filename` (string), `type_file` (string)).
- **`Paragraph`** / **`Formula`** / **`Code`** / **`ListItem`** : Noeuds riches contenant du texte.
- **`Picture`** / **`Table`** : Noeuds Média contenant un `minio_url` qui pointe vers l'autre composant clé du projet MinIO.
- **`SectionHeader`** : Noeuds Titre à part entière, pour la re-narration hiérarchique agentique.

**Arêtes (Les Edges) :**
Elles définissent l'orientation et comment interagir.
- **`PARENT_OF`** : Orientée d'un conteneur parent (comme `Document`) vers un élément. Celle-ci intègre la propriété vitale `sequence` (integer) permettant la reconstruction temporelle/littérale du fichier lors d'une requête "Lis moi le livre".
- **`LINKED_TO`** : Arête relationnelle entre une légende (`Caption`) découverte par l'IA d'extraction Docling et le conteneur visuel le plus proche à qui elle appartient (`Picture`, `Table`). Contient la propriété `relation` (string).

## Commandes utiles (Recherche / Agentique RAG)
Dans Nebula Studio, **il est formellement interdit sur cette nouvelle version de placer un `USE rag_space;` directement dans le bloc de la console**. Vous devez présélectionner l'espace depuis la liste déroulante en haut à droite !

Une fois accompli, voici le catalogue de recherches possibles (idéal lors du développement des agents LLM Python futurs) :

1.  **Récupérer la liste des tous les documents (cataloging) :**
    ```ngql
    MATCH (d:Document) RETURN id(d), d.Document.filename, d.Document.type_file LIMIT 50;
    ```
2.  **Récupérer tous les éléments liés directement au document (ex. Les enfants de la Racine) :**
    ```ngql
    MATCH (d:Document)-[r:PARENT_OF]->(e) 
    WHERE id(d) == "doc_statisticsfordatascience" 
    RETURN e;
    ```
3.  **Récupérer l'intégralité d'un document complet (Squelette et corps) :**
    ```ngql
    MATCH p=(d:Document)-[:PARENT_OF*..]->(e) 
    WHERE id(d) == "doc_statisticsfordatascience" 
    RETURN p;
    ```
4.  **Lister tous les éléments rattachés à un "Section Header" donné (Titre/Chapitre) :**
    ```ngql
    MATCH p=(s:SectionHeader)-[:PARENT_OF*..]->(e) 
    WHERE id(s) == "<INSCRIRE_ID_DU_TITRE>" 
    RETURN p;
    ```
5.  **Rechercher un élément précis et voir sa nature à partir de son Hash ID (Généré par une recherche ChromaDB) :**
    ```ngql
    MATCH (v) 
    WHERE id(v) == "<INSCRIRE_ID_ICI>" 
    RETURN tags(v), properties(v);
    ```

## Problèmes rencontrés et solutions
- **Lenteurs et "Empty Set" en temps de Crash Console** : 
  - *Problème* : Avec une ingestion sans micro-régulation ou une machine manquant de "Swap Memory", `Nebula` s'évanouissait soudainement et fermait ses conteneurs lors des flux continus de Docling. À la fermeture du terminal Ubuntu de commande manuel (WSL), plus aucun environnement daemon n'était maintenu par Docker Daemon.
  - *Solution système* : Application sur `storaged`, `metad` et `graphd` des politiques Docker `restart: unless-stopped`. La RAM de Docling ayant de plus été diminuée à **10 Go**, cela laisse **6 Go** de sécurité ferme pour ce moteur base de donnée C++, supprimant l'éventualité des crashs intempestifs.
- **La commande interdite "DO NOT switch between graph spaces"** :
  - *Problème* : L'interface Nebula Studio bloquait systématiquement le système (avec le code de warning) suite au copier-coller de l'instruction globale "USE rag_space; MATCH ...".
  - *Solution conceptuelle* : Les requêtes s'exécutent manuellement sous l'onglet pré-orienté via le menu UI Studio, requérant une mise à jour exhaustive de ces documentations et du Readme sans mention de ces switchs.
