# Plan d'integration LLM / Agent RAG

> **Statut du document.** Deux natures de contenu coexistent ici :
>
> - la **section 4** (« Modele de donnees ») decrit le **systeme actuel** : c'est
>   le contrat d'interface entre ce pipeline et l'agent, tenu a jour avec le
>   code (`src/pipeline/schemas.py`, `src/docling_service/ngql.py`) ;
> - les **sections 2, 3 et 5 a 11** sont le **plan initial** de l'agent. L'agent
>   est implemente dans un projet separe, `rag-agent-chat`, dont les choix
>   peuvent differer de ce plan : sa propre documentation fait foi.

## 1. Contexte et vision

Le pipeline d'ingestion (`rag-ingestion-pipeline`) transforme des documents
PDF, HTML et Markdown par extraction structuree (Docling) et les ecrit dans un
graphe de connaissances (NebulaGraph), une base vectorielle (ChromaDB) et un
stockage objet S3 pour les medias.

L'agent RAG est un **projet separe** (`rag-agent-chat`) qui consomme ces stores
en lecture.

### Principe directeur

Un RAG classique transmet au modele des chunks isoles. Ici, le **graphe de
connaissances sert a reconstruire le contexte structurel** du document autour de
chaque chunk trouve. L'utilisateur garde le controle en selectionnant les sources
avant la generation.

---

## 2. Architecture du flux agent

```
Utilisateur
    |
    | 1. Question
    v
[Query Processing]
    |
    | 2. Embedding de la question
    v
[ChromaDB] -----> top-K chunks + metadatas (graph_node_id, section_title, document, score)
    |
    | 3. Reranking (cross-encoder)
    v
[Source Preview]
    |
    | 4. Affichage groupé par document, avec extraits et scores
    | 5. L'utilisateur selectionne / elimine des sources
    v
[Graph Context Reconstruction]  <-- ETAPE CLE
    |
    | 6. Pour chaque chunk selectionne :
    |    - NebulaGraph: remonter PARENT_OF jusqu'au section_header
    |    - NebulaGraph: redescendre pour recuperer tous les enfants de la section
    |    - Stockage objet: recuperer les images/tables de la section
    v
[Enriched Context Builder]
    |
    | 7. Assemblage : markdown structure avec hierarchie,
    |    images en base64/URL, metadatas de position
    v
[LLM Generation]
    |
    | 8. Reponse avec citations [source:element_id]
    |    + images jointes si pertinentes
    v
[Post-processing]
    |
    | 9. Extraction des citations, validation guardrails
    | 10. Si le modele a besoin de plus : TOOL search_vectors(query)
    |     -> retour a l'etape 6 (max 3 iterations)
    v
Reponse finale a l'utilisateur
    (texte + citations + images)
```

---

## 3. Detail des etapes

### 3.1 Retrieval initial (ChromaDB)

```python
collection.query(
    query_embeddings=[embed(question)],
    n_results=20,                          # sur-recuperer pour le reranking
    include=["documents", "metadatas", "distances"],
)
```

Chaque resultat contient dans ses metadatas :
- `graph_node_id` : ID du noeud NebulaGraph (= `element_id`)
- `element_id` : hash sha256[:10] de l'element
- `page_position`, `ref_position` : position dans la page et sous le parent
- `media_url` : adresse de l'image/table si applicable
- `object_key` : la cle nue du meme objet

### 3.2 Reranking

Apres le retrieval brut, un **cross-encoder** re-score les chunks par rapport
a la question pour ameliorer la precision. Les 10 meilleurs sont conserves.

Pourquoi : les embeddings bi-encoder (paraphrase-multilingual-MiniLM-L12-v2) sont rapides mais
imprecis. Le cross-encoder est lent mais beaucoup plus precis sur le ranking.

### 3.3 Source Preview et selection utilisateur

Affichage groupe par document :

```
Resultats pour "Comment Docling gere-t-il les tableaux ?"

[x] 2408.09869.pdf (score: 0.92)
    - "Table structure recognition..." (p.4, section 3.3)
    - "TableFormer model..." (p.5, section 3.3)

[x] docling_manual.pdf (score: 0.78)
    - "Configuration options..." (p.12, section 5.1)

[ ] unrelated_paper.pdf (score: 0.45)
    - "Table of contents..." (p.1)

> Deselectionner les sources non pertinentes, puis valider.
```

L'utilisateur peut :
- Decocher des documents entiers
- Decocher des chunks individuels
- Valider pour lancer la generation

### 3.4 Graph Context Reconstruction (etape cle)

C'est l'etape qui distingue cette approche. Pour chaque chunk selectionne :

**Phase 1 — Remonter au section_header**

```ngql
-- Trouver le chemin du chunk vers le section_header parent
GO FROM "element_id" OVER PARENT_OF REVERSELY
YIELD dst(edge) AS parent_id
| GO FROM $-.parent_id OVER PARENT_OF REVERSELY
YIELD dst(edge) AS grandparent_id;
```

Les edges `PARENT_OF` sont remontees jusqu'a un noeud portant le tag
`SectionHeader` (ou `Document` s'il n'y a pas de section parente). Par defaut,
la remontee s'arrete au premier `SectionHeader` rencontre.

**Strategie de profondeur** : si le chunk est dans la section 3.2.1 :
- la section **immediate** (3.2.1) est reconstruite avec tous ses enfants ;
- la **chaine de breadcrumbs** est remontee jusqu'au Document :
  `Document > 3. Processing Pipeline > 3.2 Layout Analysis > 3.2.1 Table Recognition`
- Les sections parentes sont **mentionnees par leur titre** (pas reconstruites)
  pour donner au modele le contexte hierarchique sans exploser le budget tokens

Exemple de contexte injecte :

```
[Breadcrumb] 2408.09869.pdf > 3. Processing Pipeline > 3.2 Layout Analysis

## 3.2.1 Table Recognition

TableFormer is a deep learning model that predicts the structure of tables...
[paragraphe complet]

[Table: structure_example.png] (img:28b88acbd9)

Caption: Figure 3 - Example of table structure prediction.
```

**Phase 2 — Redescendre pour recuperer le contexte complet**

```ngql
-- Recuperer tous les enfants de la section, dans l'ordre
GO FROM "section_header_id" OVER PARENT_OF
YIELD properties($$).label AS label,
      properties($$).text AS text,
      properties($$).media_url AS media_url,
      properties($$).object_key AS object_key,
      properties(edge).sequence AS seq
| ORDER BY $-.seq ASC;
```

**Phase 3 — Recuperer les images/tables**

Pour chaque enfant ayant un `media_url` non vide :
- telecharger l'image depuis le stockage objet par `object_key`, qui est
  l'identite de l'objet, plutot qu'en decomposant l'adresse ;
- l'encoder en base64 pour injection dans le prompt (LLM multimodal) ;
- ou re-servir l'objet a l'affichage. **L'adresse ne va jamais au navigateur** :
  elle est interne et authentifiee, un `GET` anonyme y rend 403, et l'agent sert
  de proxy.

**Resultat** : au lieu d'un chunk isole de 500 caracteres, le modele recoit
la section complete avec sa hierarchie, ses images, et ses tableaux.

### 3.5 Generation LLM

Le prompt systeme :

```
Tu es un assistant qui repond aux questions en te basant UNIQUEMENT
sur les sources fournies. Chaque source est une section de document
avec sa hierarchie, ses images et ses tableaux.

Regles :
- Cite tes sources avec [src:ELEMENT_ID] apres chaque affirmation
- Si une image ou un tableau est pertinent, inclus-le dans ta reponse
  avec la reference [img:ELEMENT_ID]
- Si tu as besoin de plus d'informations, utilise l'outil search_vectors
- Ne reponds JAMAIS au-dela de ce que disent les sources
- Si les sources ne permettent pas de repondre, dis-le explicitement
```

### 3.6 Agentic loop (recherche iterative)

Le modele dispose d'un tool `search_vectors(query: str)` qui :
1. Effectue une nouvelle recherche ChromaDB avec la sous-question
2. Reranke les resultats
3. Reconstruit le contexte via le graphe (SANS repasser par la selection user)
4. Injecte le nouveau contexte dans la conversation

**Garde-fous** :
- Maximum **3 iterations** de recherche par question
- Budget total de tokens (ex: 100K tokens de contexte max)
- Le modele doit justifier pourquoi il a besoin de plus d'info

### 3.7 Post-processing de la reponse

1. **Extraction des citations** : parser les `[src:ELEMENT_ID]` pour construire
   la liste des sources utilisees
2. **Inclusion des images** : pour chaque `[img:ELEMENT_ID]`, recuperer l'objet
   et l'attacher a la reponse
3. **Validation guardrails** : verifier que la reponse ne contient pas de PII,
   que chaque affirmation a une citation, etc.

---

## 4. Modele de donnees (contrat d'interface)

### 4.1 ChromaDB — Collection `rag_documents`

| Champ          | Type          | Description                                |
|----------------|---------------|--------------------------------------------|
| id             | string        | `element_id`, ou `element_id#n` si le bloc a du etre decoupe |
| embedding      | float[384]    | Vecteur paraphrase-multilingual-MiniLM-L12-v2                   |
| document       | string        | Texte du chunk, integral (le vecteur, lui, est tronque au-dela de la fenetre) |
| metadata.element_id    | string | Hash ID de l'**ancre** du bloc, toujours au format `^[a-f0-9]{10}$` |
| metadata.graph_node_id | string | = element_id, cle pour NebulaGraph  |
| metadata.filename      | string | Nom du fichier source, sans extension — le **chapitre** |
| metadata.collection    | string | Dossier parent — l'**ouvrage** dont vient le chapitre ("" si le fichier est à plat) |
| metadata.source_path   | string | Chemin complet relatif à `Datas/`, identité unique du document |
| metadata.label         | string | Label Docling de l'ancre (text, table, code, ...) |
| metadata.page_no       | int    | Numero de page (1 pour les formats non pagines) |
| metadata.media_url     | string | Adresse de l'objet si image/table ("" sinon). **Interne et authentifiee**, jamais servie telle quelle a un navigateur |
| metadata.object_key    | string | La cle nue du meme objet, celle passee a `put_object`. L'adresse porte l'hote et devient fausse s'il change ; la cle reste valable |
| metadata.reference_id  | string | Section parente, ou `DOC`            |
| metadata.page_no_end   | int    | **Derniere** page du chunk. Egale a `page_no` sauf pour un element que Docling a fusionne par-dessus une frontiere de page : citer « page N » seule est alors inexact |
| metadata.language      | string | Code ISO 639-1 du document (`en`, `fr`...), vide si indeterminee |
| metadata.depth         | int    | Profondeur dans la hierarchie. **Deux echelles s'y croisent** : sur un titre elle compte les titres au-dessus, sur tout autre element elle vaut celle de son titre + 1. C'est `label` qui dit laquelle. Aucun plafond. Definition de reference : `ChunkMetadata.depth` |
| metadata.section_title | string | Titre de la section, pour l'affichage des citations |
| metadata.page_position | int    | Rang de l'element dans sa page       |
| metadata.ref_position  | int    | Rang de l'element sous son parent    |
| metadata.chunk_index / chunk_count | int | Position du chunk parmi ceux de son element, et leur nombre |
| metadata.block_size    | int    | Nombre d'elements fusionnes dans ce chunk |

**Modele d'embedding** : `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, fenetre de
**128** tokens). L'agent doit utiliser le MEME modele pour encoder les questions.

La fenetre vaut **128** tokens (mesure le 2 septembre 2026,
`python -m src.index_report` : « limite : 128 tokens »). Ce n'est pas un
reglage : elle est lue au runtime sur le modele (`modele.max_seq_length`). Le
texte **stocke** est integral ; le **vecteur** ne l'est pas toujours, car le
modele tronque ce qui depasse. Le chiffre et ses deux causes sont documentes
dans `vectors.get_chunker` (registre §6.2). Un budget de contexte cote agent se
calcule donc sur 128 tokens par chunk.

**Granularite** : un vecteur par **chunk**, pas par element. Le decoupage est
confie a `HybridChunker` de Docling, qui regroupe ce qui va ensemble en
respectant la **structure** du document.

Ce que la production ecarte, dans `vectors.build_chunks` : un chunk sans aucun
caractere alphanumerique, ou plus court que `MIN_CHUNK_CHARS`, **et seulement
s'il est le seul chunk de son element**. Une fenetre du milieu d'un texte
continu est conservee meme courte, sans quoi l'agent concatenerait un texte
troue (registre §4.28.a). Les elements ecartes de l'index restent presents dans
NebulaGraph.

**Consequences pour l'agent** :

- `id` (le `chunk_id`) peut porter un suffixe `#n` ; **`element_id` n'en porte
  jamais** et reste exploitable tel quel par `/context/{element_id}` ;
- `element_id` designe le **premier** element couvert par le chunk. C'est un noeud reel du
  graphe : la reconstruction de contexte fonctionne a l'identique ;
- tous les elements ecartes de l'index vectoriel **restent dans NebulaGraph**.
  Le graphe est la source de verite de la structure, l'index vectoriel celle de
  la recherche ;
- le vecteur est calcule sur le texte **precede du titre de sa section**, alors
  que `document` contient le texte brut. L'agent affiche donc le passage tel
  quel, sans prefixe parasite.

**Citer une source complete.** `filename` seul ne suffit pas : un livre decoupe
en chapitres donne des noms qui se repetent d'un ouvrage a l'autre — « Preface »,
« Index », « Appendix ». Une citation lisible se construit avec les trois :

```
{collection} > {filename} > {section_title}
Practical MLOps > 1. Introduction to MLOps > Qu'est-ce que le MLOps
```

`source_path` sert quand il faut remonter au fichier lui-meme, ou distinguer
deux documents sans ambiguite.

L'id est stable d'une ingestion a l'autre : il derive de la position dans la
page, pas de l'ordre global de lecture, et du **chemin** du document et non de
son seul nom — deux chapitres homonymes de deux ouvrages differents ne se
recouvrent donc pas. Attention toutefois : l'id derive aussi du texte, si bien
que **toute evolution de la chaine d'extraction change les ids** et laisse les
anciennes entrees orphelines. Les stores sont a purger avant une re-ingestion
qui suit une evolution du pipeline.

### 4.2 NebulaGraph — Space `rag_space`

**Tags (types de noeuds)** :

| Tag            | Proprietes                                      |
|----------------|-------------------------------------------------|
| Document       | `filename`: string, `type_file`: string, `total_pages`: int, `collection`: string, `source_path`: string, `language`: string, `content_hash`: string |
| les **11** tags d'element — SectionHeader, Paragraph, Table, Picture, ListItem, Caption, Code, Formula, Footnote, PageHeader, PageFooter | `label`: string, `page_no`: int, `page_no_end`: int, `text`: string, `media_url`: string, `object_key`: string, `depth`: int |

Les onze tags d'element partagent le meme schema, defini une seule fois par
`VERTEX_PROPERTIES` / `VERTEX_TYPES` dans `src/docling_service/ngql.py`.

**`NULL` n'est pas `0`.** Le schema Nebula migre en place, les **donnees** non :
un `ALTER TAG … ADD` laisse a `NULL` tous les sommets deja ecrits, et seule une
reecriture du document les renseigne. Un `page_no_end` absent signifie « fin
inconnue », jamais « tient sur une page ». `verify_contract` le compte.

`depth` est le nombre d'aretes `PARENT_OF` qui separent le noeud de la racine
de son document. C'est le **seul niveau declare** lisible sur un titre : aucun
`section_header` n'est jamais un chunk, donc la metadonnee `depth` de ChromaDB
n'en decrit jamais un (registre 4.24). Un noeud ecrit avant l'ajout de la
colonne porte `NULL` : le schema migre en place, les donnees non.

**Edges (relations)** :

| Edge       | Proprietes      | Description                                |
|------------|------------------|--------------------------------------------|
| PARENT_OF  | sequence: int    | Document -> SectionHeader -> Elements      |
| LINKED_TO  | relation: string | Caption -> Picture/Table ("describes")     |

#### `sequence` : trois reserves de lecture

**L'exigence 4 du contrat est tenue** : `sequence` est presente sur **toutes** les
aretes `PARENT_OF`, et triee par `sequence`, `page_no` ne decroit jamais dans un
document. `verify_contract` le verifie sur la totalite des aretes.

Trois proprietes sont a connaitre avant de s'en servir :

1. **`sequence` repart a 0 dans chaque document.** Elle n'est **pas** globalement
   monotone : tout « avant / apres » doit etre **borne au document**. Comparer
   deux `sequence` de documents differents n'a aucun sens.
2. **Elle n'est pas contigue sous un parent, par construction.** C'est un ordre
   de lecture **global au document**, pas un rang sous le parent : l'ecart entre
   deux enfants consecutifs est la taille du sous-arbre du frere precedent.
3. **Les trous sont grands.** Le plus grand ecart entre deux enfants consecutifs
   d'un meme parent se compte en **centaines**.

**Consequences pour l'agent :**

- une « fenetre d'elements » implementee comme « les enfants de P dont `sequence`
  est dans `[s-k, s+k]` » rendra **silencieusement moins** d'elements que demande.
  Pour obtenir les `k` voisins, il faut **trier les enfants de P par `sequence`
  puis prendre les rangs voisins**, jamais filtrer sur un intervalle de valeurs ;
- lire la contiguite comme un indice d'integrite ferait **conclure a une perte de
  donnees qui n'existe pas**.

Les chiffres de ces trois reserves sont documentes a un seul endroit, le
docstring de `verify_contract.inversions_de_page`, mesures sur le corpus complet.

Le registre §6.16 reste **ouvert** : ces reserves decrivent la facon dont l'agent
lit `sequence`, et doivent aussi etre reportees dans la documentation de
`rag-agent-chat` (`pour_le_pipeline_ingestion.md`), dans l'autre depot.

**VID format** : `FIXED_STRING(256)`, defini par `VID_MAX_BYTES` dans
`src/docling_service/ngql.py`. Un space cree a 64 refuserait **16 des
23 documents du corpus**, dont l'identifiant va jusqu'a **111** octets, et un
`vid_type` ne se modifie pas apres coup. Mesures et methode :
[services/nebulagraph.md](services/nebulagraph.md), section « Schema nGQL ».
- Document : `doc_{source_path sans extension}` — **la clé, jamais le nom de
  fichier seul** : le corpus porte deux `Preface.html`, et `doc_{filename}`
  les ferait collisionner sur un seul sommet (contrat, exigence 3)
- Elements : hash sha256[:10] (ex: `a950b65a3b`)

**Requetes utiles pour l'agent** :

```ngql
-- Trouver les parents d'un element (remonter la hierarchie)
GO FROM "element_id" OVER PARENT_OF REVERSELY YIELD dst(edge) AS parent;

-- Trouver les enfants d'une section (reconstruire le contexte)
GO FROM "section_id" OVER PARENT_OF
YIELD dst(edge) AS child, properties(edge).sequence AS seq
| ORDER BY $-.seq;

-- Trouver le document d'un element
GO FROM "element_id" OVER PARENT_OF REVERSELY
YIELD dst(edge) AS p
| GO FROM $-.p OVER PARENT_OF REVERSELY
YIELD dst(edge) AS doc;

-- Trouver les images liees a un element
GO FROM "element_id" OVER LINKED_TO
YIELD dst(edge) AS linked, properties(edge).relation AS rel;
```

### 4.3 Stockage objet — Bucket `documents`

| Champ         | Description                                        |
|---------------|----------------------------------------------------|
| Endpoint      | la valeur de `S3_ENDPOINT` (`seaweedfs:8333` sur la pile actuelle). **Aucune valeur par defaut** |
| Bucket        | `documents`                                         |
| Object path   | PDF : `images/{filename_stem}/{element_id}_{type}.png` ; Markdown : `images/md/{doc_key}/…` ; HTML : `images/html/{doc_key}/…` |
| Content-Type  | `image/png` pour les crops PDF ; type d'origine pour les images HTML et Markdown |
| Acces         | Par un client S3 generique, avec le jeu d'identifiants **en lecture seule** cote agent. Credentials dans `.env` |

**Le code ne nomme pas le serveur** (SeaweedFS) : seul `S3_ENDPOINT` le designe.
Changer de serveur ne touche donc a aucune ligne de televersement.

### 4.4 Schemas Pydantic (reutilisables)

Les modeles de `src/pipeline/schemas.py` dans le projet d'ingestion :

```python
class BoundingBox(BaseModel):
    left: float = Field(alias="l")
    top: float = Field(alias="t")
    right: float = Field(alias="r")
    bottom: float = Field(alias="b")

class DocumentElement(BaseModel):
    id: str                        # hash sha256[:10]
    label: str                     # section_header, text, picture, table, ...
    page_no: int = 1
    page_no_end: int = 1
    bbox: BoundingBox | None = None
    text: str = ""
    order: int = 0
    media_url: str | None = None
    object_key: str | None = None
    content: str | None = None
    reference_id: str = "DOC"      # ID du parent
    depth: int = 0
    section_title: str = ""
    page_position: int = 0
    ref_position: int = 0
    type: str = "text"             # "text" ou "resource"

class DocumentMetadata(BaseModel):
    filename: str
    type_file: str
    total_pages: int = 0

class ExtractedDocument(BaseModel):
    metadata: DocumentMetadata
    elements: list[DocumentElement] = Field(default_factory=list)
```

L'agent peut copier ces schemas ou les importer comme dependance.

---

## 5. Stack technologique recommandee

| Composant        | Choix                   | Raison                                          |
|------------------|-------------------------|-------------------------------------------------|
| Framework agent  | **LangGraph**           | Machine a etats, tools natifs, debug avec LangSmith |
| LLM principal    | **Claude Sonnet/Opus**  | Long context (200K), multimodal natif, tool-use |
| LLM fallback     | **GPT-4o**              | Alternative si besoin                           |
| Embedding query  | **paraphrase-multilingual-MiniLM-L12-v2**   | Obligatoire : meme modele que l'ingestion       |
| Reranking        | **A TRANCHER — voir la reserve ci-dessous** | `ms-marco-MiniLM-L6-v2` est ANGLAIS |
| Frontend         | **Streamlit** ou **Gradio** | Prototypage rapide, selection interactive    |
| API backend      | **FastAPI**             | Meme stack que le service Docling               |
| Observabilite    | **Langfuse**            | Open-source, self-hostable en Docker            |

| Guardrails       | **NeMo Guardrails**     | Validation input/output, anti-hallucination     |
| PII detection    | **Presidio**            | Detection/anonymisation d'informations personnelles |

**Reranker : a choisir multilingue** (registre §6.6). `cross-encoder/ms-marco-MiniLM-L6-v2`
est entraine sur MS MARCO, un jeu anglais. Cote agent, il a ete mesure avec une
etendue de scores de **0,0 %** sur 20 candidats en francais : il ne classe plus
rien et renvoie l'ordre d'entree, sans erreur ni journal. Le defaut est sans effet
tant que corpus et questions sont en anglais, mais il apparait des qu'une question
francaise vise un passage anglais, ce que l'embedder multilingue rend possible. Le
choix d'un reranker multilingue releve de `rag-agent-chat` et d'une campagne de
mesure ; ce plan ne prescrit plus `ms-marco-MiniLM-L6-v2`.

### Pourquoi LangGraph plutot que LangChain classique

L'agentic loop (le modele qui re-cherche) est un **workflow a etats** :
- Etat initial -> Retrieval -> Attente selection user -> Reconstruction ->
  Generation -> (boucle si besoin) -> Reponse finale

LangGraph le modelise comme un graphe d'etats avec des
transitions conditionnelles. LangChain classique (chains) ne gere pas
bien les boucles ni le human-in-the-loop.

---

## 6. Architecture projet agent-llm-rag

```
agent-llm-rag/
    docker-compose.yml        # Langfuse + agent API + Streamlit
    .env.example
    requirements.txt
    src/
        agent/
            __init__.py
            graph.py           # LangGraph state machine (noeud central)
            state.py           # AgentState dataclass
            retriever.py       # ChromaDB query + reranking
            graph_context.py   # Reconstruction via NebulaGraph
            objets_client.py   # Recuperation des images du stockage objet
            llm.py             # Client LLM (Claude/GPT)
            tools.py           # Tool search_vectors pour l'agentic loop
            guardrails.py      # Validation input/output
            settings.py        # pydantic-settings
        api/
            __init__.py
            main.py            # FastAPI endpoints
            schemas.py         # Request/Response models
        frontend/
            app.py             # Streamlit UI
        prompts/
            system.txt
            answer_with_context.j2
            rewrite_query.j2
    tests/
    documentation/
```

---

## 7. LangGraph State Machine

```python
from langgraph.graph import StateGraph, END

class AgentState(TypedDict):
    question: str
    chat_history: list[dict]
    retrieved_chunks: list[ChunkResult]
    reranked_chunks: list[ChunkResult]
    selected_sources: list[str]           # element_ids apres selection user
    enriched_context: list[SectionContext] # sections reconstruites
    response: str
    citations: list[Citation]
    images: list[ImageRef]
    search_count: int                     # compteur agentic loop (max 3)
    needs_more_info: bool

graph = StateGraph(AgentState)

graph.add_node("retrieve", retrieve_chunks)
graph.add_node("rerank", rerank_chunks)
graph.add_node("await_source_selection", present_sources_to_user)
graph.add_node("reconstruct_context", reconstruct_via_graph)
graph.add_node("generate", generate_response)
graph.add_node("postprocess", extract_citations_and_images)

graph.add_edge("retrieve", "rerank")
graph.add_edge("rerank", "await_source_selection")
graph.add_edge("await_source_selection", "reconstruct_context")
graph.add_edge("reconstruct_context", "generate")
graph.add_edge("generate", "postprocess")

# Agentic loop : si le modele veut plus d'info ET < 3 iterations
graph.add_conditional_edges("postprocess", should_search_more, {
    True: "retrieve",    # re-chercher avec la sous-question du modele
    False: END,
})

graph.set_entry_point("retrieve")
agent = graph.compile(interrupt_before=["await_source_selection"])
```

---

## 8. Connexion aux stores (acces reseau)

L'agent doit acceder aux 3 stores de donnees. Deux options :

**Option A — Meme reseau Docker** (recommande pour le dev)
L'agent tourne sur `rag_network` et accede directement :
- ChromaDB : `http://chromadb:8000`
- NebulaGraph : `graphd:9669`
- Stockage objet : la valeur de `S3_ENDPOINT` (`seaweedfs:8333`)

**Option B — Acces externe** (prod ou projet separe)
Publier les ports voulus par un `docker-compose.override.yml` local du projet
d'ingestion (fichier non versionne) ; les adresses sont alors celles que cet
override publie sur l'hote.

Les credentials sont les memes que dans `.env` du projet d'ingestion.

---

## 9. Variables d'environnement de l'agent

```env
# --- Stores (du projet d'ingestion) ---
CHROMA_HOST=chromadb
CHROMA_PORT=8000
NEBULA_HOST=graphd
NEBULA_PORT=9669
NEBULA_USER=root
NEBULA_PASSWORD=nebula
S3_ENDPOINT=seaweedfs:8333      # aucune valeur par defaut : sans elle, l'agent ne demarre pas
S3_ACCESS_KEY=                  # le jeu LECTURE SEULE, pas celui du pipeline
S3_SECRET_KEY=
S3_BUCKET=documents

# --- LLM ---
ANTHROPIC_API_KEY=              # ou OPENAI_API_KEY
LLM_MODEL=claude-sonnet-4-20250514
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=4096

# --- Retrieval ---
EMBEDDING_MODEL_NAME=paraphrase-multilingual-MiniLM-L12-v2   # DOIT etre le meme que l'ingestion
# A choisir multilingue : ms-marco-MiniLM-L6-v2 est anglais (voir la section 5).
RERANK_MODEL=<a-trancher-multilingue>
RETRIEVAL_TOP_K=20
RERANK_TOP_K=10
MAX_SEARCH_ITERATIONS=3
CONTEXT_DEPTH=1                  # profondeur de reconstruction graphe

# --- Observabilite ---
LANGFUSE_HOST=http://langfuse:3000
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

---

## 10. Plan d'implementation par phases

### Phase 1 — Retrieval basique + UI (2-3 jours)
- Setup projet, settings, ChromaDB client
- Retrieval simple (query -> top-K chunks)
- Frontend Streamlit : input question, affichage resultats
- Pas de reranking, pas de graphe

### Phase 2 — Reranking + selection sources (2 jours)
- Integrer cross-encoder pour le reranking
- UI : affichage groupe par document, checkboxes de selection
- API FastAPI pour le backend

### Phase 3 — Graph Context Reconstruction (3-4 jours)
- Client NebulaGraph (nebula3-python)
- Algorithme de remontee PARENT_OF -> section_header
- Algorithme de descente -> enfants de la section
- Recuperation des images du stockage objet
- Assemblage du contexte enrichi en markdown structure

### Phase 4 — LLM Generation (2 jours)
- Integration Claude via Anthropic SDK
- Prompt systeme avec instructions de citation
- Injection du contexte enrichi (texte + images multimodal)
- Streaming de la reponse

### Phase 5 — Agentic Loop + LangGraph (3 jours)
- Modeliser le flux complet en LangGraph
- Tool `search_vectors` pour la recherche iterative
- Human-in-the-loop pour la selection sources (interrupt)
- Garde-fous : max iterations, budget tokens

### Phase 6 — Post-processing + Guardrails (2 jours)
- Extraction automatique des citations
- Attachement des images dans la reponse
- Integration NeMo Guardrails ou Presidio
- Multi-turn (historique de conversation)

### Phase 7 — Observabilite + Evaluation (2 jours)
- Deploy Langfuse en Docker
- Tracer chaque requete (retrieval, generation, latence, tokens)
- Evaluer avec Ragas sur le jeu golden (voir `rag_evaluation_strategy.md`)

---

## 11. Risques et mitigations

| Risque                                    | Impact | Mitigation                              |
|-------------------------------------------|--------|-----------------------------------------|
| Contexte trop large (section entiere)     | Tokens | Budget max par section, truncation       |
| Boucle infinie de recherche               | Cout   | Max 3 iterations, budget tokens global   |
| Latence NebulaGraph sur graphes larges    | UX     | Cache des reconstructions recentes       |
| Images trop lourdes en base64             | Tokens | Redimensionner avant injection (max 1MB) |
| Modele d'embedding different query/index  | Qualite| Forcer paraphrase-multilingual-MiniLM-L12-v2 dans les settings|
| Utilisateur deselectionne toutes sources  | UX     | Minimum 1 source requise pour generer    |
