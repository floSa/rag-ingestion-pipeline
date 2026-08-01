# Extraction des donnees (Docling)

## Presentation

Le service `docling-service` est le moteur d'ingestion du projet. Developpe avec FastAPI,
il traite les documents (PDF, HTML, Markdown) de maniere structuree via **Docling**
(bibliotheque IBM d'analyse de layout assistee par IA).

- **URL interne** : `http://docling-service:8000`
- **Soumission** : `POST /extract` avec `{"filepath": "/opt/dagster/app/Datas/pdfs/mon_livre.pdf"}`,
  qui rend un `job_id` a suivre sur `GET /jobs/{job_id}`
- **GPU** : CUDA 12.1, `shm_size: 2gb`, limite memoire 10 Go

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

Les lots de pages **ne se chevauchent pas**. Un chevauchement de deux pages existait
pour dedupliquer, mais les identifiants sont deterministes depuis, et les ecritures
sont des upserts : le recouvrement ne faisait plus que re-convertir les memes pages.

#### Normalisation prealable du Markdown

Docling convertit le Markdown **ligne a ligne**. Un fichier dont les paragraphes sont
coupes a 80 colonnes — la forme la plus courante des exports et des notes ecrites a la
main — produisait donc un element par ligne source : la recherche vectorielle portait
sur des fragments de 75 caracteres au lieu de paragraphes.

Les lignes d'un meme paragraphe sont desormais recollees avant la conversion
(`markdown.normalize_markdown`). Le fichier source n'est pas touche : la version
normalisee vit dans un fichier temporaire. Tout ce qui n'est pas de la prose est laisse
intact — blocs de code clotures ou indentes, tableaux, titres, listes, citations, filets
horizontaux, HTML inline — ainsi que les retours a la ligne explicites du Markdown (deux
espaces finaux, antislash final). Un fichier dont les paragraphes tiennent deja sur une
ligne est rendu inchange.

### 2. Identite des elements

Chaque element recoit un identifiant court et deterministe :

```
sha256(filename | page_no | position_in_page | text[:50])[:10]
```

La position retenue est celle **dans la page**, pas l'ordre global de lecture. C'est ce
qui rend l'identifiant stable d'une ingestion a l'autre : reconvertir un document produit
les memes identifiants, et les ecritures ecrasent au lieu de dupliquer.

Le format — dix caracteres hexadecimaux — est celui qu'attend `rag-agent-chat`, qui
valide `/context/{element_id}` sur `^[a-f0-9]{10}$`.

### 3. Hierarchie et positions

Le parcours maintient la section courante : un `section_header` (ou un `title`) ouvre une
nouvelle section et reste rattache au document ; tout autre element se rattache au dernier
en-tete rencontre. La section courante **survit aux lots de pages**, de sorte que la
hierarchie d'un livre ne se brise pas toutes les cinq pages.

Chaque element porte donc :

| Champ           | Signification                                          |
|-----------------|--------------------------------------------------------|
| `reference_id`  | Parent : identifiant de la section, ou `DOC`           |
| `page_position` | Rang de l'element dans sa page                         |
| `ref_position`  | Rang de l'element sous son parent                      |
| `order`         | Ordre de lecture global, porte par l'arete `PARENT_OF` |

### 4. Contenu des tables

Une table Docling ne porte pas de texte : son `text` vaut `None` et le contenu vit dans
une structure dediee. Faute d'export explicite, les tables ressortaient vides de
l'extraction — presentes dans le graphe, mais introuvables par la recherche vectorielle.
Leur contenu est desormais recupere via `export_to_markdown()`, ce qui les rend
interrogeables en texte tout en conservant, pour les PDF, le crop image sur MinIO.

### 5. Liaison legende -> ressource

Une legende (`caption`) est reliee par une arete `LINKED_TO(describes)` au dernier
element visuel rencontre avant elle (`table` ou `picture`), dans l'ordre de lecture.

### 5. Crop et upload des medias

Pour les elements visuels d'un PDF (`picture`, `table`, `figure`, `graphic`), le service :

1. Utilise le document **PyMuPDF** deja ouvert pour le fichier — une seule ouverture par
   document, et non une par image ;
2. Crop aux coordonnees de la bounding box, avec un facteur de zoom (`IMAGE_CROP_ZOOM`) ;
3. Pousse le PNG sur le bucket MinIO `documents` ;
4. Stocke l'URL resultante dans `minio_url`.

**Attention** : Docling raisonne en axe Y Bottom-Left, PyMuPDF en Top-Left. La conversion
de coordonnees est faite dans `images.crop_and_upload`.

Les images des captures HTML, elles, ont deja ete exportees vers MinIO par l'asset de
nettoyage en amont : le service se contente de propager leur URL.

### 6. Persistance

Chaque lot d'elements est valide contre le schema partage
(`src/pipeline/schemas.py`), puis ecrit dans le graphe **puis** dans l'index vectoriel.
L'ordre compte : si NebulaGraph refuse le lot, les vecteurs correspondants ne sont pas
indexes et l'erreur remonte jusqu'au job.

Les textes longs sont **decoupes** en fenetres recouvrantes (`CHUNK_SIZE` / `CHUNK_OVERLAP`)
avant vectorisation, au lieu d'etre tronques. Un element tenant en un seul chunk garde son
identifiant nu ; un element decoupe produit des identifiants `{element_id}#0`, `#1`, etc.,
mais ses metadonnees `element_id` et `graph_node_id` restent le hash de l'element.

## Format d'un element

```json
{
  "id": "023351d5f4",
  "label": "section_header",
  "page_no": 1,
  "bbox": {"l": 108.0, "t": 267.8, "r": 190.81, "b": 257.05},
  "text": "1 Introduction",
  "order": 7,
  "reference_id": "DOC",
  "page_position": 7,
  "ref_position": 0
}
```

Les elements visuels portent en plus `minio_url`. `bbox` vaut `null` pour les formats
non pagines (HTML, Markdown), qui n'ont pas de coordonnees.

## Configuration Docling

```python
pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
converter = DocumentConverter(
    format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
)
```

L'OCR et la reconstruction de structure de tables sont desactives : ils multiplient le
temps de conversion, et les tables sont de toute facon croppees en image et poussees sur
MinIO. A reactiver seulement si le corpus contient des scans.

## Problemes connus et solutions

- **OOM (Out Of Memory)** : 14 Go de RAM sur une machine WSL de 16 Go faisait tomber les
  autres services. Solution : limite a 10 Go, `do_table_structure=False`,
  `PDF_BATCH_PAGES=5`, et le backend Docling est dechargé entre deux lots.

- **Crop muet** : les images n'arrivaient pas sur MinIO, sans erreur. Cause : axe Y
  inverse entre Docling (Bottom-Left) et PyMuPDF (Top-Left).

- **Formules LaTeX perdues** : l'echappement nGQL traitait le guillemet mais pas
  l'antislash, si bien qu'un texte contenant `\frac` ou `\alpha` produisait une requete
  invalide. Les noeuds `Formula` d'un livre de mathematiques etaient rejetes en silence.
  L'antislash est desormais echappe en premier (`ngql.escape_ngql`, couvert par des tests).

- **Lots perdus en silence** : une erreur de conversion etait journalisee puis oubliee, et
  le run se terminait au vert sur un document incomplet. Les lots en echec sont desormais
  collectes — les autres pages sont bien ingerees — et le job echoue a la fin en listant
  les pages manquantes.

## Commandes utiles

```bash
# Logs en temps reel
docker compose logs docling-service --tail 100 -f

# Extraction manuelle (sans Dagster)
curl -X POST "http://localhost:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{"filepath": "/opt/dagster/app/Datas/pdfs/mon_livre.pdf"}'

# Suivi du job retourne
curl -s "http://localhost:8000/jobs/a1b2c3d4e5f6"
```
