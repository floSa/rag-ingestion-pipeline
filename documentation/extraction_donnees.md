# Extraction des donnees (Docling)

## Presentation

Le service `docling-service` est le moteur d'ingestion du projet. Developpe avec FastAPI,
il traite les documents (PDF, HTML, Markdown) de maniere structuree via **Docling**
(bibliotheque IBM d'analyse de layout assistee par IA).

- **URL interne** : `http://docling-service:8000`
- **Soumission** : `POST /extract` avec `{"filepath": "/opt/dagster/app/Datas/pdfs/mon_livre.pdf"}`,
  qui rend un `job_id` a suivre sur `GET /jobs/{job_id}`
- **Ressources** : processeur par defaut, GPU CUDA 12.1 optionnel
  (`docker-compose.gpu.yml`), `shm_size: 2gb`, limite memoire 10 Go

Le detail de l'API et du modele d'execution vit dans
[services/docling.md](services/docling.md) ; cette page decrit ce que le service
fait des documents.

## Chaine d'extraction

### 1. Conversion

Docling itere sur les elements du document via `document.iterate_items()`. Chaque item
porte un label (`section_header`, `text`, `picture`, `table`, `code`, `formula`...), un
numero de page et une bounding box.

Le regime depend du format :

| Format             | Regime                                                                    |
|--------------------|---------------------------------------------------------------------------|
| PDF                | Converti par lots de `PDF_BATCH_PAGES` pages (5 par defaut), pour borner la memoire |
| HTML, Markdown     | Converti d'un seul tenant : ces formats ne sont pas pagines               |

Les lots de pages **ne se chevauchent pas** : les identifiants sont deterministes et
les ecritures sont des upserts, un recouvrement ne ferait que re-convertir les memes
pages.

#### Pages ecartees d'un PDF

Avant de decouper en lots, `matter.py` etablit la liste des pages a ne pas convertir :
couverture, page de copyright, sommaire, index. Elle vient des **signets** du PDF, qui
donnent le titre de chaque partie et sa page **physique** — la destination est resolue
par le format, elle n'est pas le numero imprime dans l'ouvrage, et le decalage habituel
d'une a deux pages entre les deux ne s'applique donc pas.

Quand les signets manquent ou ne mentionnent pas l'index, celui-ci est reconnu a sa
forme : des lignes courtes terminees par des numeros de page, cherchees dans le dernier
quart du document seulement.

Les lots sont ensuite construits **a l'interieur des plages conservees**, jamais a
cheval sur une page ecartee. Les numeros de page restent ceux du fichier : rien n'est
renumerote, et `total_pages` reste le nombre reel de pages de l'ouvrage.

#### Images des documents Markdown

Un Markdown ne contient jamais ses images : il les **designe**. Deux syntaxes
coexistent, et Docling n'en reconnait aucune — il les rend en texte brut.

| Syntaxe | Origine | Ce qu'elle designe |
|---------|---------|--------------------|
| `![[fichier.jpg\|1000]]` | Obsidian | un nom de fichier, resolu par le coffre |
| `![legende](chemin)` | Markdown standard | un chemin relatif a la note |

Les liens sont donc extraits **avant** la conversion et remplaces par une
balise placee exactement ou etait l'image. Apres conversion, la balise redevient
un element de type `picture` portant l'adresse et la cle de l'objet televerse.

La position compte autant que l'image : l'element occupe le meme rang dans
l'ordre de lecture, si bien que la legende qui suit la figure lui reste
adjacente et que la section qui la contient reste la sienne. C'est precisement
ce que le graphe est cense preserver.

La resolution des chemins reproduit le comportement d'Obsidian, qui ne met que
le nom du fichier dans le lien : chemin relatif d'abord, puis recherche par nom
parmi les fichiers voisins de la note (typiquement un dossier
`Pièces jointes/`). Les liens situes dans un bloc de code sont laisses intacts.

**Consequence pratique** : copier une note sans son dossier de pieces jointes
fait perdre ses images. Copier le dossier entier.

#### Normalisation prealable du Markdown

Docling convertit le Markdown **ligne a ligne**. Un fichier dont les paragraphes sont
coupes a 80 colonnes — la forme la plus courante des exports et des notes ecrites a la
main — produirait donc un element par ligne source, et la recherche vectorielle
porterait sur des fragments de 75 caracteres au lieu de paragraphes.

Les lignes d'un meme paragraphe sont donc recollees avant la conversion
(`markdown.normalize_markdown`). Le fichier source n'est pas touche : la version
normalisee vit dans un fichier temporaire. Tout ce qui n'est pas de la prose est laisse
intact — blocs de code clotures ou indentes, tableaux, titres, listes, citations, filets
horizontaux, HTML inline — ainsi que les retours a la ligne explicites du Markdown (deux
espaces finaux, antislash final). Un fichier dont les paragraphes tiennent deja sur une
ligne est rendu inchange.

### 2. Identite du document

Le nom du fichier ne suffit pas a identifier un document. Un livre decoupe en
chapitres donne des noms qui se repetent d'un ouvrage a l'autre — « Preface »,
« Index », « Appendix ». Deux chapitres homonymes produiraient les memes
identifiants d'elements et se recouvriraient en silence.

C'est donc le **chemin relatif a `Datas/`** — la cle de partition Dagster — qui
porte l'identite. Le pipeline le transmet au service dans `source_path`, et
trois informations en sont derivees :

| Champ | Valeur pour `htms/Practical MLOps/1. Introduction.html` |
|-------|--------------------------------------------------------|
| `filename` | `1. Introduction` — le chapitre |
| `collection` | `Practical MLOps` — l'ouvrage |
| cle des identifiants | `htms/Practical MLOps/1. Introduction` |

Sans `collection`, une reponse du RAG pourrait citer le chapitre sans pouvoir
dire de quel livre il vient.

### 3. Identite des elements

Chaque element recoit un identifiant court et deterministe :

```
sha256(filename | page_no | position_in_page | text[:50])[:10]
```

La position retenue est celle **dans la page**, pas l'ordre global de lecture. C'est ce
qui rend l'identifiant stable d'une ingestion a l'autre : reconvertir un document produit
les memes identifiants, et les ecritures ecrasent au lieu de dupliquer.

Le format — dix caracteres hexadecimaux — est celui qu'attend `rag-agent-chat`, qui
valide `/context/{element_id}` sur `^[a-f0-9]{10}$`.

### 4. Hierarchie et positions

**Une seule regle, quelle que soit la source :**

> Le parent d'un titre est le titre precedent de **rang superieur**. Tout autre element se
> rattache au titre le plus profond encore ouvert.

Le rang est un petit entier ou 0 designe le niveau le plus haut. Ce qui change d'un format
a l'autre n'est pas la regle, c'est seulement d'ou vient ce nombre :

| Source       | Signal utilise                          | Ce que ca donne                    |
|--------------|-----------------------------------------|------------------------------------|
| HTML         | le parent que Docling declare           | hierarchie fidele, jusqu'a 4 niveaux |
| Markdown     | l'attribut `level` (1 pour `##`, 2 pour `###`) | fidele aux dieses           |
| PDF          | la **taille de police**                 | reconstruite, voir plus bas        |

Le code n'a aucune branche par format : il essaie les signaux dans l'ordre, du plus fiable
au plus indirect, et prend le premier qui repond. Quand aucun ne repond, tous les titres
recoivent le rang 0 et restent freres sous le document : c'est le pire cas, un graphe
plat. **La hierarchie n'est jamais inventee.**

La section courante **survit aux lots de pages**, de sorte que la hierarchie d'un livre ne
se brise pas toutes les cinq pages.

#### Pourquoi la taille de police pour les PDF

Docling ne declare aucun parent sur un PDF et attribue le meme niveau a tous les titres —
mesure sur `statisticsfordatascience` : 333 en-tetes, tous au niveau 1, tous rattaches au
corps du document. La taille de police, elle, est **ecrite en clair dans le fichier** :
chaque bloc de texte porte l'instruction qui la fixe. Elle est lue, pas estimee.

Le releve se fait une fois par document, avec PyMuPDF, sans modele :

1. la taille qui porte le plus de caracteres est celle du **corps du texte** ;
2. les tailles superieures sont celles des titres, classees de la plus grande a la plus
   petite ;
3. le rang dans ce classement donne le niveau.

**Aucune valeur n'est ecrite en dur.** Le classement est recalcule pour chaque fichier :
un ouvrage compose en 24/22/20 points se segmente exactement comme un ouvrage en 20/18/16.

Deux garde-fous, parce que ce signal est le seul indirect :

- un titre dont la boite est **contenue dans une image ou un tableau** est ecarte du
  classement : le texte d'une figure peut etre grand sans etre un titre de section ;
- un titre **pas plus grand que le corps du texte** ne cree pas de niveau. Sans cela, un
  faux positif de detection ouvrirait une branche parasite.

Un titre ecarte par l'un de ces garde-fous recoit le rang le plus profond, **jamais le rang
zero** : le promouvoir chapitre remettrait tout l'arbre a zero.

#### Profondeur : aucun plafond

`depth` est le nombre d'aretes `PARENT_OF` qui separent l'element de la racine de son
document. Elle n'a pas de plafond (registre 4.24) et depasse 3 sur une part mesurable du
corpus. La regle, les deux echelles qui s'y croisent (titre ou autre element) et la
distribution mesuree sont decrites dans `ChunkMetadata.depth` (`src/pipeline/schemas.py`).

La profondeur est toujours celle du parent plus un, jamais le rang brut. Un faux titre
minuscule se range donc juste sous son predecesseur au lieu de tomber au niveau 9 et de
trouer l'arbre.

#### Resultat verifie

Chapitre 3 de `statisticsfordatascience`, reconstruit par le pipeline et compare **ligne a
ligne** au sommaire imprime de l'ouvrage :

```
[0] 3
[0] A Developer's Approach to Data Cleaning
    [1] Understanding basic data cleaning
        [2] Common data issues
        [2] Contextual data issues
        [2] Cleaning techniques
    [1] R and common data issues
        [2] Outliers
            [3] Step 1 - Profiling the data
            [3] Step 2 - Addressing the outliers
        [2] Domain expertise
        [2] Validity checking
    [1] Summary
```

Chaque element porte donc :

| Champ           | Signification                                            |
|-----------------|----------------------------------------------------------|
| `reference_id`  | Parent : identifiant du titre dominant, ou `DOC`         |
| `depth`         | Profondeur dans la hierarchie, 0 pour un titre de tete   |
| `page_position` | Rang de l'element dans sa page                           |
| `ref_position`  | Rang de l'element sous son parent                        |
| `order`         | Ordre de lecture global, porte par l'arete `PARENT_OF` (propriete `sequence`) |

### 5. Contenu des tables

Une table Docling ne porte pas de texte : son `text` vaut `None` et le contenu vit dans
une structure dediee. Faute d'export explicite, les tables ressortaient vides de
l'extraction : presentes dans le graphe, mais introuvables par la recherche vectorielle.
Leur contenu est donc recupere via `export_to_markdown()`, ce qui les rend
interrogeables en texte tout en conservant, pour les PDF, le crop image dans le
stockage d'objets.

### 6. Liaison legende -> ressource

Une legende (`caption`) est reliee par une arete `LINKED_TO` (`relation = "describes"`) au dernier
element visuel rencontre avant elle (`table` ou `picture`), dans l'ordre de lecture.

### 7. Crop et upload des medias

Pour les elements visuels d'un PDF (`picture`, `table`, `figure`, `graphic`), le service :

1. Utilise le document **PyMuPDF** deja ouvert pour le fichier — une seule ouverture par
   document, et non une par image ;
2. Crop aux coordonnees de la bounding box, avec un facteur de zoom (`IMAGE_CROP_ZOOM`) ;
3. Pousse le PNG sur le bucket `documents` du stockage d'objets ;
4. Stocke l'adresse resultante dans `media_url`, **et la cle nue dans
   `object_key`**. Les deux sont posees d'un seul geste, par
   `extraction.poser_le_media`. Un element a demi renseigne ne serait corrige par
   rien, le graphe n'etant ecrit qu'une fois.

**Attention** : Docling raisonne en axe Y Bottom-Left, PyMuPDF en Top-Left. La conversion
de coordonnees est faite dans `images.crop_and_upload`.

Les images des captures HTML, elles, ont deja ete televersees par l'asset de
nettoyage en amont : le service se contente de propager leur adresse, lue dans le
HTML nettoye, et d'en deriver la cle.

### 8. Persistance

Chaque lot d'elements est valide contre le schema partage
(`src/pipeline/schemas.py`), puis ecrit dans le graphe **puis** dans l'index vectoriel.
L'ordre compte : si NebulaGraph refuse le lot, les vecteurs correspondants ne sont pas
indexes et l'erreur remonte jusqu'au job.

#### Ce qui part dans l'index vectoriel

Le graphe recoit **tous** les elements. L'index vectoriel, lui, recoit des chunks
decoupes par **`HybridChunker`**, le decoupeur de Docling.

**Pourquoi `HybridChunker`.** Un decoupage a la longueur en caracteres coupe sans savoir
ou il coupe. `HybridChunker` respecte la structure du document et recoit **le tokenizer
du modele d'embedding lui-meme**, la ou une approximation en caracteres laisse toujours
une marge d'erreur. Il peut malgre tout depasser la fenetre : il ne fractionne pas une
table, et le titre de section est prepose apres son travail. Le chiffre et ses deux
causes sont documentes dans `vectors.get_chunker` (registre §3.4 bis).

Comparaison avec l'ancien decoupage en caracteres, retire du depot, sur le chapitre 1 de
`Practical MLOps`. Cet ouvrage n'est pas dans le corpus actuel (`MLOps with Databricks`
et `Practical MLflow for Generative AI on Databricks`) : la mesure n'est pas rejouable
(registre 5.1, 6.10).

| Mesure                  | Decoupage en caracteres (450 car.) | `HybridChunker` |
|-------------------------|-----------------------------|-----------------|
| chunks produits         | 146                         | **100**         |
| tokens, mediane         | 67                          | **91**          |
| caracteres, mediane     | 269                         | **353**         |

A contenu egal, quarante-six chunks de moins, chacun portant davantage de contexte.

**Le plancher, et sa borne.** Un chunk sans caractere alphanumerique, ou plus court que
`MIN_CHUNK_CHARS`, est ecarte de l'index ; il demeure dans le graphe. Ce rejet ne
s'applique qu'a un chunk qui est le **seul** de son element (`vectors.build_chunks`).
Une fenetre du milieu d'un texte continu est conservee meme courte : sinon, l'agent qui
concatene les chunks d'un element obtiendrait un texte troue (registre 4.28.a). Un chunk
court n'est « trop court pour porter du sens » que s'il est autonome.

**Les identifiants restent ceux du contrat.** `HybridChunker` rend ses chunks avec ses
references internes (`#/texts/18`), alors que le contrat impose les hash de dix
hexadecimaux du service. Le module [`anchoring.py`](../src/docling_service/anchoring.py) fait le
pont, et couvre les deux cas qui se presentent :

- **un chunk couvre plusieurs elements** (c'est le but du regroupement) : l'ancre est le
  **premier** element, celui d'ou part la lecture ;
- **plusieurs chunks partagent une ancre** (un element trop long pour la fenetre est
  reparti) : ils recoivent les suffixes `#0`, `#1`, comme le prevoit le contrat.

Un chunk dont aucune reference n'est connue est **ecarte** plutot que rattache au hasard.

`element_id` et `graph_node_id` designent donc toujours un noeud reel du graphe, au format
attendu par `rag-agent-chat`. La metadonnee `block_size` indique combien d'elements le
chunk couvre.

#### Effet mesure

Mesure historique, sans date, sur un corpus de reference qui n'est plus sur la machine
(registre 6.10) : mixte francais/anglais, 42 documents dont un PDF de 280 pages,
36 chapitres HTML et des notes Markdown. Ces chiffres documentent la decision de
regrouper ; ils ne decrivent pas l'index actuel.

| Mesure (corpus de reference disparu)  | Avant   | Apres  |
|---------------------------------------|---------|--------|
| chunks indexes                        | 22 937  | 5 246  |
| sans aucun caractere alphanumerique   | 5,0 %   | 0,0 %  |
| de moins de 15 caracteres             | 36,0 %  | 0,0 %  |
| taille mediane d'un chunk             | —       | 277 car. |
| chunks issus d'une fusion             | 0 %     | 51,5 % |
| chunks portant un titre de section    | 0 %     | 100 %  |

L'index perdait 77 % de ses entrees sans perdre de contenu : ce qui disparaissait, ce
sont les fragments de mise en page et les doublons de granularite.

Index vivant, mesure le 2 septembre 2026 (24 chapitres HTML de deux ouvrages et un PDF
de 71 pages, en anglais) : **4 365 chunks, 15 196 sommets, 23 documents, 15 173 aretes
PARENT_OF**. L'etat courant se lit par `python -m src.index_report`, dans le conteneur
d'extraction.

**Limite connue et mesuree.** Une part des chunks depasse la fenetre du modele
d'embedding (**128** tokens, lue au runtime sur le modele) et est donc tronquee par le
modele ; le texte stocke reste integral. `python -m src.index_report` donne ce chiffre
apres chaque ingestion, en tokenisant le texte tel que le modele le recoit (prefixe du
titre de section). Le chiffre du corpus actuel et ses causes sont documentes dans
`vectors.get_chunker` : avant prefixe, les chunks qui depassent sont des tables
(registre §3.4).

#### Contextualisation des vecteurs

Un passage isole de son titre perd une part de son sens : « la moyenne est sensible aux
valeurs extremes » ne dit pas de quoi elle est la moyenne. Le titre de la section courante
est donc prepose au texte **envoye au modele d'embedding**, sans cout de calcul — technique
`contextualize()` de Docling, principe du *contextual retrieval*.

Le texte **stocke** reste le texte brut : cote agent, l'utilisateur voit le passage tel
qu'il figure dans le document. Le titre part aussi en metadonnee `section_title`.
Reglable par `EMBED_SECTION_CONTEXT`.

## Format d'un element

```json
{
  "id": "023351d5f4",
  "label": "section_header",
  "page_no": 1,
  "bbox": {"l": 108.0, "t": 267.8, "r": 190.81, "b": 257.05},
  "page_no_end": 1,
  "text": "1 Introduction",
  "order": 7,
  "reference_id": "DOC",
  "depth": 0,
  "section_title": "1 Introduction",
  "page_position": 7,
  "ref_position": 0
}
```

Les elements visuels portent en plus `media_url` et `object_key`. `bbox` vaut
`null` pour les formats non pagines (HTML, Markdown), qui n'ont pas de
coordonnees.

## Configuration Docling

```python
options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=False)
converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
)
```

(`extraction.get_converter`.) La reconstruction de structure des tables est desactivee :
elle multiplie le temps de conversion, et les tables sont de toute facon croppees en
image et poussees sur le stockage d'objets. L'OCR n'est active que pour un PDF sans
couche texte (un scan), detecte avant la conversion (`extraction._has_text_layer`).

## Problemes connus et solutions

- **OOM (Out Of Memory)** : 14 Go de RAM consommes sur une machine WSL de 16 Go faisaient
  tomber les autres services. Solution : limite a 10 Go, `do_table_structure=False`,
  `PDF_BATCH_PAGES=5`, et le backend Docling est dechargé entre deux lots.

- **Crop muet** : les images n'arrivaient pas dans le bucket, sans erreur. Cause : axe Y
  inverse entre Docling (Bottom-Left) et PyMuPDF (Top-Left).

- **Formules LaTeX perdues** : l'echappement nGQL traitait le guillemet mais pas
  l'antislash, si bien qu'un texte contenant `\frac` ou `\alpha` produisait une requete
  invalide. Les noeuds `Formula` d'un livre de mathematiques etaient rejetes en silence.
  L'antislash est echappe en premier (`ngql.escape_ngql`, couvert par des tests).

- **Lots perdus en silence** : une erreur de conversion etait journalisee puis oubliee, et
  le run se terminait en succes sur un document incomplet. Les lots en echec sont
  collectes — les autres pages sont bien ingerees — et le job echoue a la fin en listant
  les pages manquantes.

## Commandes utiles

```bash
# Logs en temps reel
docker compose logs docling-service --tail 100 -f

# Extraction manuelle (sans Dagster), depuis le conteneur : le port n'est pas publie
docker compose exec docling-service python -c "
import json, urllib.request
corps = json.dumps({'filepath': '/opt/dagster/app/Datas/pdfs/mon_livre.pdf'}).encode()
requete = urllib.request.Request('http://localhost:8000/extract', data=corps,
                                 headers={'Content-Type': 'application/json'})
print(urllib.request.urlopen(requete).read().decode())"

# Suivi du job retourne
docker compose exec docling-service python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/jobs/a1b2c3d4e5f6').read().decode())"
```
