"""Capture l'arbre de titres que Docling rend sur des chapitres reels.

Le test de non-platitude (``tests/unit/test_non_platitude.py``) verifie la
hierarchie de titres sur des captures reelles de Docling, et non sur un arbre
fabrique a la main : il doit distinguer « Docling imbrique » de « ce
chapitre-la imbrique ».

Le test ne convertit pas lui-meme : Docling cote hote ajouterait 85 paquets
(dont torch et quinze paquets NVIDIA CUDA) et retrograderait ``websockets``
(`mesure` avec ``uv pip install docling==2.117.0``). La conversion est donc
capturee ici, dans l'image d'extraction ; le test rejoue le code de rang sur la
capture. Un changement de comportement de Docling se voit en rejouant ce script
avec ``--verifier``. ``docling`` est epingle a 2.117.0 : a chaque changement de
version, rejouer ce script.

Le nettoyage (Dagster) et la conversion (Docling) vivent dans deux images
differentes, et l'image d'extraction n'a pas ``trafilatura``. Il faut
l'installer dans le conteneur jetable :

    docker compose run --rm --no-deps -T -v "$PWD":/travail -w /travail \\
      -e PYTHONPATH=/travail \\
      docling-service sh -c \\
      'pip install --quiet trafilatura==2.2.0 readability-lxml==0.8.4.1 \\
         beautifulsoup4==4.15.0 && \\
       python /travail/scripts/capturer-larbre-docling.py [--verifier]'

Les versions sont celles du ``pyproject.toml`` : le test recalcule l'empreinte
du HTML nettoye sur l'hote, et une version differente la ferait diverger.
`mesure` le 31 aout 2026 : les empreintes produites dans l'image et sur l'hote
sont identiques.

Sans argument, il ecrit ``tests/fixtures/arbres_docling.yaml``. Avec
``--verifier``, il compare et sort en 1 si la capture a change.

Le format est YAML et non JSON : ``detect-secrets`` lit les deux empreintes
SHA-256 comme des « Hex High Entropy String », et le depot declare ses faux
positifs sur la ligne meme, par un commentaire ``pragma: allowlist secret``.
JSON n'admet pas de commentaire. ``check-yaml`` controle le fichier.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

# En-tete du fichier genere : il dit ce qu'il est et comment le refaire, pour
# qu'un fichier genere de 150 Ko ne soit pas pris pour une donnee d'entree.
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

# Les deux chapitres. Le premier s'imbrique ; le second a un graphe reellement
# plat : aucun titre rendu sous le niveau de tete. Couvrir les deux evite de
# lire une platitude legitime comme un defaut.
#
# L'absence de balise <h2> ne suffit pas a caracteriser ce cas : trois des 22
# chapitres retenus n'en ont pas, et les deux `Preface.html` s'imbriquent quand
# meme ({0: 9, 1: 4} et {0: 8, 1: 4} sur le graphe). Registre 3.2.
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

    Les images ne sont pas exportees : seule la structure des titres est
    capturee. Le test nettoie dans les memes conditions, sans quoi les
    empreintes divergeraient.

    Args:
        chemin: Chapitre HTML brut, versionne sous ``Datas/htms/``.

    Returns:
        Les deux empreintes et l'arbre : pour chaque item, son label et la
        reference de son parent, tels que Docling les rend. Les noeuds de
        groupe en font partie : ils ne sont pas du contenu, mais ils sont sur
        le chemin des parents.
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
    # ``with_groups=True`` est indispensable. Par defaut, ``iterate_items`` ne
    # rend pas les noeuds de groupe (listes, blocs de mise en page, conteneurs
    # anonymes). Or ``ranking.docling_parent_rank`` remonte la chaine des parents
    # en franchissant ces conteneurs : sans eux, la chaine est coupee.
    #
    # `mesure` sur le chapitre imbrique : sans groupes, 1 130 references de
    # parent pointaient un noeud absent, dont deux titres (``#/texts/389`` ->
    # ``#/groups/79`` et ``#/texts/468`` -> ``#/groups/91``). Leur rang devenait
    # ``None`` et le test comptait 39 titres au lieu de 41. Sans noeud de groupe,
    # le test ne pourrait pas non plus verifier que les conteneurs anonymes ne
    # sont pas comptes comme des titres.
    for item, _ in document.iterate_items(with_groups=True):
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
    # Le pragma va sur la ligne de chaque empreinte : c'est la que
    # `detect-secrets` le cherche.
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
