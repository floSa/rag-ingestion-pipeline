
## Le pipe et l'ochestration avec Dagster

0. On garde les assests de modification des pdf et html avant docling

1. Le Dossier (Volume Partagé) 

     Rôle : Il contient vos PDFs/HTML sources.
     Montage : Ce dossier est "monté" (visible) à la fois dans le conteneur Dagster et le conteneur Docling.
     Pourquoi ? Pour éviter de transférer des gros fichiers via le réseau. Dagster dit à Docling "Regarde dans /data/input/fichier.pdf", et Docling le lit directement.
     

2. Le Conteneur Docling (Orange) 

     Service Docling : C'est le seul à avoir accès au GPU.
     Stockage MinIO : Oui, c'est bien Docling qui stocke dans MinIO. Lorsqu'il extrait une image, il ne la renvoie pas à Dagster. Il l'envoie direct vers MinIO et récupère l'URL (ex: http://minio/bucket/img.png).
     Sortie : Il renvoie uniquement un JSON au conteneur Dagster. Ce JSON contient le texte extrait et les URLs des images qu'il vient de stocker.
     

3. Les Assets dans le Conteneur Dagster (Bleu) 

C'est ici que se trouve la logique de votre futur agent RAG : 

     

    Asset : Scan_Dossier 
         Surveille le dossier.
         Dès qu'un nouveau PDF arrive, il déclenche le pipeline pour ce fichier spécifique.
         
     

    Asset : Process_Docling (on garde le meme principe )
         Appelle le conteneur Docling.
         Reçoit le JSON (Texte + Liens MinIO).
         Ce JSON devient la donnée d'entrée pour les deux assets suivants.
         
     

    Asset : Build_Knowledge_Graph (Votre Graph de Connaissance) On utilisera NebulaGraph + Studio en sertvice docker avec son interface visuelle qui me permettra d'avoir une vu plus claire 
         Prend le JSON de Process_Docling.
         Utilise un LLM ou un NLP pour extrair des entités (Personnes, Dates, Lieux) et des relations.
         Écrit ces nœuds et relations directement dans Neo4j.
         
     

    Asset : Vectorize_Content  On utilisera Chroma en service docker plus simple dans un premier temps on se débarrasse de weaviate car tu m'a induit en erreur j'avais pourtant demandé une interface graphique pour le knoledge graph. 
    Dans la base de données vectorielle on aura comme metadat/tag les informations pour aller chercher le noeud dans le
         Prend le même JSON.
         Découpe le texte en chunks (morceaux).
         Génère les embeddings (vecteurs).
         Envoie le tout dans la Base Vectorielle pour la recherche sémantique.
         
     

## Extraction avec Docling 

Vu que tu m'a fait un peu de la merde j'ai testé de mon coté et voici ce que j'ai réussi à faire :

```python 
# ==========================================================
# DOCling Extraction Structurée v9.1 (KeyError corrigé)
# ==========================================================
import re, hashlib, json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

# 🔧 CONFIGURATION
PDF_SOURCE = "https://arxiv.org/pdf/2408.09869.pdf"
PATH = Path(PDF_SOURCE)
FILENAME_STEM = PATH.stem
TYPE_FILE = PATH.suffix.lstrip('.')

print(f"⏳ Conversion de {FILENAME_STEM}.{TYPE_FILE}...")

pipeline_options = PdfPipelineOptions(
    do_ocr=False,
    do_table_structure=True,
    table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST),
    do_code_enrichment=False,
    do_formula_enrichment=False,
)

converter = DocumentConverter(
    format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
)
doc = converter.convert(PDF_SOURCE).document
print(f"✅ Document chargé : {doc.num_pages()} pages\n")

# 🛠️ HELPERS
def compute_id(filename: str, page_no: int, order: int, text: str) -> str:
    return hashlib.sha256(f"{filename}|{page_no}|{order}|{text[:50]}".encode()).hexdigest()[:10]

def extract_bbox(bbox) -> Dict[str, float]:
    if not bbox: return {}
    return {"l": round(bbox.l, 2), "t": round(bbox.t, 2), "r": round(bbox.r, 2), "b": round(bbox.b, 2)}

def parse_header_numbering(text: str) -> Tuple[Optional[tuple], str]:
    t = text.strip()
    m = re.match(r'^(\d+(?:\.\d+)*)\s*[.\-–—]?\s*', t)
    if m: return tuple(int(x) for x in m.group(1).split('.')), t[m.end():].strip()
    m = re.match(r'^([A-Z])\s*[.\)]\s*', t)
    if m: return (ord(m.group(1)) - ord('A') + 1,), t[m.end():].strip()
    m = re.match(r'^([IVXLCDM]{1,4})\s*[.\)]\s*', t)
    if m:
        vals = {'I':1, 'V':5, 'X':10, 'L':50, 'C':100, 'D':500, 'M':1000}
        num, prev = 0, 0
        for c in reversed(m.group(1)):
            num += vals[c] if vals[c] >= prev else -vals[c]
            prev = vals[c]
        return (num,), t[m.end():].strip()
    return None, t

LABEL_TO_TYPE = {
    "title": "text", "section_header": "text", "text": "text", "paragraph": "text",
    "list_item": "text", "caption": "text", "page_header": "text", "page_footer": "text", "footnote": "text",
    "picture": "resource", "table": "resource", "code": "resource", "formula": "resource"
}
RESOURCE_LABELS = {"picture", "table", "code", "formula"}

# 📦 EXTRACTION PRINCIPALE
def extract_structured_elements(doc, filename: str):
    raw_items = []
    stack = []
    
    # PASS 1: Extraction brute & hiérarchie
    for item, _ in doc.iterate_items():
        lbl = str(getattr(item, 'label', 'text')).lower()
        prov = item.prov[0] if item.prov else None
        page_no = prov.page_no if prov else 1
        bbox = extract_bbox(prov.bbox if prov else None)
        text = getattr(item, 'text', '').strip() if hasattr(item, 'text') else ""

        elem = {
            "id": compute_id(filename, page_no, len(raw_items), text),
            "label": lbl, "page_no": page_no, "bbox": bbox,
            "original_text": text,
            "reference_id": "DOC", "reference_ressources": None, "content": None
        }

        if lbl == "title":
            elem["reference_id"] = "DOC"
        elif lbl == "section_header":
            numbering_tuple, _ = parse_header_numbering(text)
            parent = None
            if numbering_tuple:
                for h in reversed(stack):
                    if numbering_tuple[:len(h["tuple"])] == h["tuple"]: parent = h; break
            elem["reference_id"] = parent["id"] if parent else "DOC"
            if numbering_tuple:
                stack = [h for h in stack if numbering_tuple[:len(h["tuple"])] == h["tuple"]]
                stack.append({"id": elem["id"], "tuple": numbering_tuple})
        elif lbl in RESOURCE_LABELS | {"text", "paragraph", "list_item", "code", "formula"}:
            active = stack[-1] if stack else None
            elem["reference_id"] = active["id"] if active else "DOC"
            
        raw_items.append(elem)

    # PASS 1.5: Liaison Caption -> Ressource (géométrie)
    resources_by_page = defaultdict(list)
    for e in raw_items:
        if e["label"] in RESOURCE_LABELS: resources_by_page[e["page_no"]].append(e)

    for cap in raw_items:
        if cap["label"] != "caption" or not cap["bbox"]: continue
        cx = (cap["bbox"].get("l",0) + cap["bbox"].get("r",0)) / 2
        best_res, min_dist = None, float("inf")
        
        for res in resources_by_page[cap["page_no"]]:
            if not res["bbox"]: continue
            rx = (res["bbox"]["l"] + res["bbox"]["r"]) / 2
            ry = (res["bbox"]["t"] + res["bbox"]["b"]) / 2
            cy = (cap["bbox"]["t"] + cap["bbox"]["b"]) / 2
            dist = abs(rx - cx) + abs(ry - cy)
            if dist < min_dist: min_dist = dist; best_res = res
                
        if best_res and min_dist < 300:
            cap["reference_id"] = best_res["id"]
            cap["reference_ressources"] = best_res["id"]
            best_res["caption_text"] = cap["original_text"]

    # PASS 2: Positions, mapping final & nettoyage (CORRIGÉ)
    page_pos = defaultdict(int)
    ref_pos = defaultdict(int)
    
    # ✅ Sauvegarde immuable des textes AVANT modification
    ref_text_map = {e["id"]: e["original_text"] for e in raw_items}
    final_elements = []

    for e in raw_items:
        page_pos[e["page_no"]] += 1
        e["page_position"] = page_pos[e["page_no"]]
        
        ref = e["reference_id"] or "DOC"
        ref_pos[ref] += 1
        e["ref_position"] = ref_pos[ref]
        e["type"] = LABEL_TO_TYPE.get(e["label"], "text")
        
        # Transformation text / content
        if e["label"] in RESOURCE_LABELS:
            if e.get("caption_text"):
                e["text"] = e["caption_text"]
            else:
                ref_id = e["reference_id"]
                # Lookup sécurisé dans le dictionnaire immuable
                e["text"] = ref_text_map.get(ref_id, "") if ref_id != "DOC" else ""
            
            if e["label"] in ["code", "formula"]:
                e["content"] = e["original_text"]
        else:
            e["text"] = e["original_text"]

        # Nettoyage des clés temporaires
        e.pop("original_text", None)
        e.pop("caption_text", None)
        final_elements.append(e)

    return {
        "metadata": {"filename": filename, "type_file": TYPE_FILE, "total_pages": doc.num_pages()},
        "elements": final_elements
    }

# 📤 EXPORT & VÉRIFICATION
if __name__ == "__main__":
    result = extract_structured_elements(doc, FILENAME_STEM)
    
    print(f"📊 {result['metadata']['filename']}.{result['metadata']['type_file']} | {len(result['elements'])} éléments")
    for e in result['elements'][:15]:
        txt = (e['text'][:40] + '...') if e['text'] else '(vide)'
        print(f"[P{e['page_no']}] {e['id'][:8]} | {e['type']:<8} | {e['label']:<12} | ref:{e['reference_id'][:8]} | pos:{e['ref_position']} | txt:{txt}")
    
    out_path = f"{FILENAME_STEM}_structured.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Exporté : {out_path}")
```

On a un json bien calibré avec toutes les information que je souhaite pour créer les vecteurs et surtout le graph de connaissance !

Il faut quand même gérer l'envoie et l'enregistrement des images et des tables dans le bucket MinIO.
et garder le lien du stockage dans le graph de connaissance.

Ya deux dossier data est ça me va pas le volume minio se trouvera Dans Datas au coté des dossier qui héberge les documents pdf et html. 

Voici un exempl de json que me sort docling avec mon code plus haut

```json 
{
  "metadata": {
    "filename": "2408.09869",
    "type_file": "pdf",
    "total_pages": 9
  },
  "elements": [
    {
      "id": "a950b65a3b",
      "label": "picture",
      "page_no": 1,
      "bbox": {
        "l": 256.38,
        "t": 719.3,
        "r": 355.54,
        "b": 622.85
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 1,
      "ref_position": 1,
      "type": "resource",
      "text": ""
    },
    {
      "id": "e46d31d006",
      "label": "section_header",
      "page_no": 1,
      "bbox": {
        "l": 212.59,
        "t": 566.64,
        "r": 399.41,
        "b": 551.16
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 2,
      "ref_position": 2,
      "type": "text",
      "text": "Docling Technical Report"
    },
    {
      "id": "e86e6e9d87",
      "label": "section_header",
      "page_no": 1,
      "bbox": {
        "l": 283.31,
        "t": 511.98,
        "r": 328.69,
        "b": 503.43
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 3,
      "ref_position": 3,
      "type": "text",
      "text": "Version 1.0"
    },
    {
      "id": "62acc9893f",
      "label": "text",
      "page_no": 1,
      "bbox": {
        "l": 113.64,
        "t": 481.53,
        "r": 498.36,
        "b": 439.85
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 4,
      "ref_position": 4,
      "type": "text",
      "text": "Christoph Auer Maksym Lysak . Staar"
    },
    {
      "id": "b4543302d9",
      "label": "text",
      "page_no": 1,
      "bbox": {
        "l": 249.28,
        "t": 427.54,
        "r": 362.72,
        "b": 408.08
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 5,
      "ref_position": 5,
      "type": "text",
      "text": "AI4K Group, IBM Research R¨ uschlikon, Switzerland"
    },
    {
      "id": "7d6c9891d3",
      "label": "section_header",
      "page_no": 1,
      "bbox": {
        "l": 283.76,
        "t": 393.16,
        "r": 328.24,
        "b": 382.41
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 6,
      "ref_position": 6,
      "type": "text",
      "text": "Abstract"
    },
    {
      "id": "74bdd656e6",
      "label": "text",
      "page_no": 1,
      "bbox": {
        "l": 143.86,
        "t": 364.01,
        "r": 468.14,
        "b": 300.74
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 7,
      "ref_position": 7,
      "type": "text",
      "text": "This technical report introduces Docling ."
    },
    {
      "id": "023351d5f4",
      "label": "section_header",
      "page_no": 1,
      "bbox": {
        "l": 108.0,
        "t": 267.8,
        "r": 190.81,
        "b": 257.05
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 8,
      "ref_position": 8,
      "type": "text",
      "text": "1 Introduction"
    },
    {
      "id": "93c9713358",
      "label": "text",
      "page_no": 1,
      "bbox": {
        "l": 108.0,
        "t": 239.37,
        "r": 504.0,
        "b": 143.55
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 9,
      "ref_position": 1,
      "type": "text",
      "text": "Converting PDF"
    },
    {
      "id": "a77931a0bb",
      "label": "text",
      "page_no": 1,
      "bbox": {
        "l": 108.0,
        "t": 135.89,
        "r": 504.0,
        "b": 83.52
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 10,
      "ref_position": 2,
      "type": "text",
      "text": "With Docling , we open-source a very capable"
    },
    {
      "id": "49ebd3dd8b",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 716.52,
        "r": 253.97,
        "b": 707.97
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 1,
      "ref_position": 3,
      "type": "text",
      "text": "Here is what Docling delivers today:"
    },
    {
      "id": "d13c2b277b",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 695.23,
        "r": 468.4,
        "b": 686.68
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 2,
      "ref_position": 4,
      "type": "text",
      "text": "Converts PDF documents to JSON or Markdown format, stable and lightning fast"
    },
    {
      "id": "cf280b752e",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 680.37,
        "r": 504.0,
        "b": 660.9
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 3,
      "ref_position": 5,
      "type": "text",
      "text": "Understands detailed page layout, reading order, locates figures and recovers table structures"
    },
    {
      "id": "0a3cfab7d2",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 654.59,
        "r": 480.85,
        "b": 646.04
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 4,
      "ref_position": 6,
      "type": "text",
      "text": "Extracts metadata from the document, such as title, authors, references and language"
    },
    {
      "id": "0d5a16d7de",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 639.73,
        "r": 333.46,
        "b": 631.18
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 5,
      "ref_position": 7,
      "type": "text",
      "text": "Optionally applies OCR, e.g. for scanned PDFs"
    },
    {
      "id": "955bba7986",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 624.87,
        "r": 504.0,
        "b": 605.4
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 6,
      "ref_position": 8,
      "type": "text",
      "text": "Can be configured to be optimal for batch-mode"
    },
    {
      "id": "3c81490c2a",
      "label": "list_item",
      "page_no": 2,
      "bbox": {
        "l": 135.4,
        "t": 599.09,
        "r": 355.41,
        "b": 590.54
      },
      "reference_id": "023351d5f4",
      "reference_ressources": null,
      "content": null,
      "page_position": 7,
      "ref_position": 9,
      "type": "text",
      "text": "Can leverage different accelerators (GPU, MPS, etc)."
    },
    {
      "id": "13f0118447",
      "label": "section_header",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 572.49,
        "r": 205.29,
        "b": 561.74
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 8,
      "ref_position": 9,
      "type": "text",
      "text": "2 Getting Started"
    },
    {
      "id": "40c735abf6",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 547.82,
        "r": 504.0,
        "b": 506.36
      },
      "reference_id": "13f0118447",
      "reference_ressources": null,
      "content": null,
      "page_position": 9,
      "ref_position": 1,
      "type": "text",
      "text": "To use Docling, you can simply install the docling package from PyPI."
    },
    {
      "id": "f4fe7d8dd4",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 498.52,
        "r": 504.0,
        "b": 457.25
      },
      "reference_id": "13f0118447",
      "reference_ressources": null,
      "content": null,
      "page_position": 10,
      "ref_position": 2,
      "type": "text",
      "text": "Docling provides an easy code interface to convert PDF documents from file system,"
    },
    {
      "id": "d52153ca30",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.75,
        "t": 448.91,
        "r": 423.45,
        "b": 441.44
      },
      "reference_id": "13f0118447",
      "reference_ressources": null,
      "content": null,
      "page_position": 11,
      "ref_position": 3,
      "type": "text",
      "text": "from docling.document_converter import DocumentConverter"
    },
    {
      "id": "1c4c53b791",
      "label": "code",
      "page_no": 2,
      "bbox": {
        "l": 108.78,
        "t": 428.99,
        "r": 491.34,
        "b": 381.67
      },
      "reference_id": "13f0118447",
      "reference_ressources": null,
      "content": "source = \"https://arxiv.org/pdf/2206.01062\" # PDF path or URL converter = DocumentConverter() result = converter.convert_single(source) print(result.render_as_markdown()) # output: \"## DocLayNet: A Large Human -Annotated Dataset for Document -Layout Analysis [...]\"",
      "page_position": 12,
      "ref_position": 4,
      "type": "resource",
      "text": "2 Getting Started"
    },
    {
      "id": "86122f2db2",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 367.84,
        "r": 504.0,
        "b": 315.65
      },
      "reference_id": "13f0118447",
      "reference_ressources": null,
      "content": null,
      "page_position": 13,
      "ref_position": 5,
      "type": "text",
      "text": "Optionally, you can configure custom pipeline features and runtime options, such as turning on or off features (e.g. OCR, table structure recognition), enforcing limits on the input document size, and defining the budget of CPU threads. Advanced usage examples and options are documented in the README file. Docling also provides a Dockerfile to demonstrate how to install and run it inside a container."
    },
    {
      "id": "f0f133b157",
      "label": "section_header",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 297.6,
        "r": 223.69,
        "b": 286.85
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 14,
      "ref_position": 10,
      "type": "text",
      "text": "3 Processing pipeline"
    },
    {
      "id": "1648385b63",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 272.75,
        "r": 504.0,
        "b": 176.92
      },
      "reference_id": "f0f133b157",
      "reference_ressources": null,
      "content": null,
      "page_position": 15,
      "ref_position": 1,
      "type": "text",
      "text": "Docling implements a linear pipeline of operations"
    },
    {
      "id": "bc91d32e33",
      "label": "section_header",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 161.43,
        "r": 192.03,
        "b": 152.48
      },
      "reference_id": "f0f133b157",
      "reference_ressources": null,
      "content": null,
      "page_position": 16,
      "ref_position": 2,
      "type": "text",
      "text": "3.1 PDF backends"
    },
    {
      "id": "2ccf3b74cc",
      "label": "text",
      "page_no": 2,
      "bbox": {
        "l": 108.0,
        "t": 141.07,
        "r": 504.0,
        "b": 88.88
      },
      "reference_id": "bc91d32e33",
      "reference_ressources": null,
      "content": null,
      "page_position": 17,
      "ref_position": 1,
      "type": "text",
      "text": "Two basic requirements to process PDF documents"
    },
    {
      "id": "825f57c6a1",
      "label": "footnote",
      "page_no": 2,
      "bbox": {
        "l": 120.65,
        "t": 79.7,
        "r": 276.46,
        "b": 70.14
      },
      "reference_id": "DOC",
      "reference_ressources": null,
      "content": null,
      "page_position": 18,
      "ref_position": 11,
      "type": "text",
      "text": "1 see huggingface.co/ds4sd/docling-models/"
    },
    {
      "id": "28b88acbd9",
      "label": "picture",
      "page_no": 3,
      "bbox": {
        "l": 109.06,
        "t": 720.87,
        "r": 502.19,
        "b": 581.8
      },
      "reference_id": "bc91d32e33",
      "reference_ressources": null,
      "content": null,
      "page_position": 1,
      "ref_position": 2,
      "type": "resource",
      "text": "Figure 1: Sketch of Docling's default processing pipeline. The inner part of the model pipeline is easily customizable and extensible."
    },
    {
      "id": "d506e3244d",
      "label": "caption",
      "page_no": 3,
      "bbox": {
        "l": 108.0,
        "t": 570.0,
        "r": 504.0,
        "b": 550.54
      },
      "reference_id": "28b88acbd9",
      "reference_ressources": "28b88acbd9",
      "content": null,
      "page_position": 2,
      "ref_position": 1,
      "type": "text",
      "text": "Figure 1: Sketch of Docling's default processing pipeline. The inner part of the model pipeline is easily customizable and extensible."
    }
  ]
}
```

Mettre à jour la documlentation avec les information ci dessus.