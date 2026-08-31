"""Capture l'arbre de titres que Docling rend sur des chapitres reels.

POURQUOI CE SCRIPT EXISTE, et pourquoi le test ne convertit pas lui-meme.

Le test de non-platitude doit distinguer « Docling imbrique » de « CE
chapitre-la imbrique » : c'est ce que l'audit du lot 1 reclame, apres que le
chantier a failli supprimer un lot entier sur un antecedent jamais mesure. Il
lui faut donc des captures REELLES, pas un arbre fabrique a la main — le
reproche exact fait a ``test_hierarchie_bout_en_bout.py``.

Mais convertir dans ``make test`` demanderait Docling cote hote, et c'est
mesure : ``uv pip install docling==2.117.0`` ajoute **85 paquets**, dont torch
et quinze paquets NVIDIA CUDA, et retrograde ``websockets`` — sur une chaine
dont le ``pyproject.toml`` dit que « les deps lourdes d'extraction vivent dans
Dockerfile.docling », et qui tourne sur processeur.

D'ou ce partage : la conversion est capturee ICI, une fois, dans l'image
d'extraction ; le test rejoue le code de rang sur la capture. Ce que le test ne
voit pas est un changement de comportement de Docling — et c'est ce script qui
le verra, puisqu'il est rejouable et que ``--verifier`` compare au lieu
d'ecrire. ``docling`` est epingle a 2.117.0 ; le jour ou cette version bouge,
rejouer ce script fait partie du geste.

Le nettoyage et la conversion vivent dans DEUX images differentes — c'est la
chaine reelle : Dagster nettoie, Docling convertit — donc l'image d'extraction
n'a pas ``trafilatura``. Il faut l'ajouter au conteneur jetable :

    docker run --rm --network rag_network -v "$PWD":/travail -w /travail \\
      --env-file .env -e HOME=/tmp -e PYTHONPATH=/travail \\
      rag-ingestion-pipeline-docling-service sh -c \\
      'pip install --quiet trafilatura==2.2.0 readability-lxml==0.8.4.1 \\
         beautifulsoup4==4.15.0 && \\
       python /travail/scripts/capturer-larbre-docling.py [--verifier]'

Les versions sont celles du ``pyproject.toml`` : le test recalcule l'empreinte
du HTML nettoye sur l'hote, et une version differente la ferait diverger.
`mesure` le 31 aout 2026 : les empreintes produites dans l'image et sur l'hote
sont identiques.

Sans argument, il ECRIT ``tests/fixtures/arbres_docling.yaml``. Avec
``--verifier``, il compare et sort en 1 si la capture a bouge.

Le format est du YAML et non du JSON pour une raison precise : les deux
empreintes SHA-256 sont lues par ``detect-secrets`` comme des « Hex High
Entropy String ». Le depot declare ses faux positifs AU SITE, par un
``pragma: allowlist secret`` justifie — et JSON n'admet pas de commentaire. Le
YAML porte donc la justification a cote de la valeur, ou un relecteur la voit,
et ``check-yaml`` le controle.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

# En-tete du fichier genere : il doit dire ce qu'il est et comment le refaire,
# parce qu'un fichier de 150 Ko genere se relit comme une donnee d'entree.
ENTETE = """# Arbres de titres rendus par Docling sur deux chapitres reels du corpus.
#
# GENERE — ne pas editer a la main. Pour le refaire, ou verifier qu'il est
# encore juste, voir scripts/capturer-larbre-docling.py.
#
# Les deux empreintes portent « pragma: allowlist secret » : ce sont des
# SHA-256 de fichiers du corpus, que detect-secrets lit comme des chaines
# hexadecimales a haute entropie. Elles scellent la capture au corpus
# versionne — si le HTML bouge, tests/unit/test_non_platitude.py rougit.
"""

RACINE_CORPUS = Path("Datas/htms")
FIXTURE = Path("tests/fixtures/arbres_docling.yaml")

# Les deux chapitres, et le pourquoi de chacun. Le second est le SEUL chapitre
# retenu du corpus sans aucune balise <h2> (`mesure` par l'audit du lot 1 sur
# les 22) : son graphe est reellement plat, et un test qui ne couvrirait que le
# premier lirait la platitude comme un defaut.
CHAPITRES = {
    "imbrique": "MLOps with Databricks/7. Foundation Models and Context Engineering.html",
    "plat": (
        "Practical MLflow for Generative AI on Databricks/"
        "10. Unifying GenAI Systems with MLflow.html"
    ),
}


def empreinte(texte: str) -> str:
    """SHA-256 hexadecimal d'un texte."""
    return hashlib.sha256(texte.encode()).hexdigest()


def capturer(chemin: Path) -> dict[str, Any]:
    """Nettoie puis convertit un chapitre, et rend son arbre d'items.

    Les images ne sont PAS exportees : ce qui est capture est la STRUCTURE des
    titres. Le test nettoie dans les memes conditions, sans quoi les empreintes
    divergeraient sans que rien ne soit casse.

    Args:
        chemin: Chapitre HTML brut, versionne sous ``Datas/htms/``.

    Returns:
        Les deux empreintes et l'arbre : pour chaque item, son label et la
        reference de son parent, tels que Docling les rend.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from docling.document_converter import DocumentConverter

    from src.pipeline.cleaning import clean_html
    from src.pipeline.sources import CleaningOptions

    brut = chemin.read_text(encoding="utf-8", errors="ignore")
    nettoye, _ = clean_html(brut, CleaningOptions())

    temporaire = Path("/tmp") / chemin.name
    temporaire.write_text(nettoye, encoding="utf-8")
    document = DocumentConverter().convert(str(temporaire)).document

    items: dict[str, dict[str, str]] = {}
    ordre: list[str] = []
    for item, _ in document.iterate_items():
        reference = str(getattr(item, "self_ref", ""))
        parent = getattr(item, "parent", None)
        items[reference] = {
            "label": str(getattr(item, "label", "")),
            "parent": str(getattr(parent, "cref", "")) if parent is not None else "",
        }
        ordre.append(reference)

    return {
        "source": str(chemin).replace("\\", "/"),
        "sha256_brut": empreinte(brut),
        "sha256_nettoye": empreinte(nettoye),
        "ordre": ordre,
        "items": items,
    }


def main() -> int:
    """Ecrit la capture, ou la compare si ``--verifier`` est passe."""
    import yaml

    verifier = "--verifier" in sys.argv
    capture = {nom: capturer(RACINE_CORPUS / rel) for nom, rel in CHAPITRES.items()}
    rendu = ENTETE + yaml.safe_dump(capture, allow_unicode=True, sort_keys=True)
    # Le pragma va sur la LIGNE de chaque empreinte : c'est la que
    # `detect-secrets` le cherche, et c'est la qu'un relecteur le lit.
    rendu = "\n".join(
        f"{ligne}  # pragma: allowlist secret"
        if ligne.lstrip().startswith(("sha256_brut:", "sha256_nettoye:"))
        else ligne
        for ligne in rendu.split("\n")
    )

    if not verifier:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(rendu, encoding="utf-8")
        for nom, contenu in capture.items():
            print(f"{nom:10s} {len(contenu['items']):4d} items  {contenu['source']}")
        print(f"ecrit : {FIXTURE}")
        return 0

    if not FIXTURE.exists():
        print(f"{FIXTURE} absent : rejouer ce script sans --verifier.")
        return 1
    if FIXTURE.read_text(encoding="utf-8") == rendu:
        print("la capture est inchangee.")
        return 0
    print(
        f"LA CAPTURE A BOUGE. Docling ou le corpus a change depuis {FIXTURE}. "
        "Relire le diff avant de regenerer : c'est exactement ce que le test "
        "ne peut pas voir seul."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
