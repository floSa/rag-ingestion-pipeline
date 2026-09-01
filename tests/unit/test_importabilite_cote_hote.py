"""Tout module de ``src/`` s'importe cote hote, sauf UNE exception nommee.

**C'etait la cause mecanique de six angles morts du chantier** — registre §3.4,
§4.4, §4.5, §4.19, §4.28.d. Un module qui importe une dependance lourde au niveau
du module est inimportable dans le venv du depot, donc rien de ce qu'il decide
n'est testable : *ce qu'un test n'importe pas, il ne teste pas.* Six modules ont
ete deverrouilles aux lots 3, 3-repare et 4, en differant l'import dans la
fonction qui en a besoin.

**Et le README affirmait « Aucun module du depot n'est plus inimportable cote
hote ».** C'est une PHRASE D'EXHAUSTIVITE, la famille que le mandat §10 nomme
comme un defaut en attente — et elle etait fausse. `mesure` le 1er septembre
2026 : **33 modules sous `src/`, 1 inimportable**,
`src/docling_service/main.py`, sur `ModuleNotFoundError: No module named
'fastapi'`. Le tableau situe deux paragraphes plus haut dans le meme README dit
lui-meme que `main.py` est atteint par un BOUCHON et non par un import : le
document se contredisait dans sa propre section.

Ce fichier convertit la phrase en garde. Il rougirait le jour ou un septieme
module deviendrait inimportable — le motif exact qui a produit les six angles
morts — et il ne pretend pas que l'exception n'existe pas : il la nomme.

L'exploration se fait en SOUS-PROCESSUS, et pour deux raisons dont la premiere
suffirait :

- importer trente-trois modules dans le processus de pytest les laisserait dans
  `sys.modules` pour le reste de la suite, et l'ordre des tests deviendrait
  significatif ;
- ``PYTHONPATH`` est remis a la seule racine du depot. Sans cela, un bouchon pose
  par un autre fichier de tests — `test_main.py` pose un faux `fastapi` en tete
  de `PYTHONPATH` — rendrait `main.py` importable et le resultat dependrait de
  l'ordre des tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# L'exception CONNUE, et elle est nommee plutot que niee. `main.py` EST
# l'application FastAPI : differer cet import-la n'aurait aucun sens, et le
# module n'a AUCUNE ligne modifiee par le lot 4. Il est atteint par un bouchon
# `fastapi` pose comme un vrai paquet en tete de `PYTHONPATH`
# (`test_main.py`), ce qui n'est pas la meme chose qu'etre importable.
EXCEPTIONS_CONNUES = {"src.docling_service.main"}

# Un module dont l'import est deja prouve possible, pour que ce fichier ne
# puisse pas etre vert sur un balayage qui n'a rien importe.
TEMOIN = "src.docling_service.ngql"

_EXPLORATEUR = """
import importlib, json, pathlib, sys

racine = pathlib.Path(sys.argv[1])
modules = sorted(
    ".".join(chemin.relative_to(racine).with_suffix("").parts)
    for chemin in (racine / "src").rglob("*.py")
    if chemin.name != "__init__.py"
)
importes, inimportables = [], {}
for nom in modules:
    try:
        importlib.import_module(nom)
    except BaseException as exc:
        inimportables[nom] = f"{type(exc).__name__}: {exc}"
    else:
        importes.append(nom)
print(json.dumps({"modules": modules, "importes": importes, "inimportables": inimportables}))
"""


def _explorer() -> dict[str, object]:
    """Importe chaque module de ``src/`` dans un sous-processus propre."""
    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = str(RACINE_DEPOT)
    acheve = subprocess.run(
        [sys.executable, "-c", _EXPLORATEUR, str(RACINE_DEPOT)],
        cwd=RACINE_DEPOT,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    return json.loads(acheve.stdout.strip().splitlines()[-1])


class TestAucunModuleNeDevientInimportableSansQuOnLeDise:
    """La phrase du README, convertie en garde."""

    def test_le_balayage_a_bien_importe_quelque_chose(self) -> None:
        """LE TEMOIN, ET IL PASSE EN PREMIER.

        *Un test qui choisit lui-meme son cas doit prouver qu'il l'a atteint.*
        Un balayage qui ne trouverait aucun fichier rendrait « 0 inimportable »,
        et les tests suivants seraient verts sans rien garder. La borne est
        INFERIEURE et non une egalite : ajouter un module ne doit pas rougir.
        """
        releve = _explorer()

        modules = releve["modules"]
        assert isinstance(modules, list)
        assert len(modules) >= 30, f"le balayage n'a trouve que {len(modules)} modules"
        assert TEMOIN in releve["importes"], (  # type: ignore[operator]
            f"{TEMOIN} n'a pas ete importe : le balayage n'importe rien"
        )

    def test_seules_les_exceptions_connues_sont_inimportables(self) -> None:
        """LE GARDE. Un septieme module inimportable rougit ici.

        L'assertion est un SOUS-ENSEMBLE et non une egalite : le jour ou
        `fastapi` entre au venv, `main.py` devient importable, et ce n'est pas une
        regression — c'est la fin de l'exception.
        """
        releve = _explorer()
        inimportables = releve["inimportables"]
        assert isinstance(inimportables, dict)

        inattendus = set(inimportables) - EXCEPTIONS_CONNUES
        assert inattendus == set(), (
            "des modules sont inimportables cote hote sans etre declares : "
            f"{ {nom: inimportables[nom] for nom in sorted(inattendus)} }. "
            "C'etait la cause mecanique de six angles morts du chantier — rien "
            "de ce que ces modules decident n'est testable. Differe l'import "
            "dans la fonction qui en a besoin, ou declare l'exception ici."
        )

    def test_l_exception_connue_est_encore_la_pour_la_raison_annoncee(self) -> None:
        """Le second temoin : l'exception est NOMMEE, pas supposee.

        Sans lui, `EXCEPTIONS_CONNUES` pourrait grossir indefiniment sans que
        personne ne verifie que ses entrees decrivent encore quelque chose. Si
        `main.py` devient importable, ce test le dit — et il faudra retirer
        l'entree plutot que la garder « au cas ou ».
        """
        releve = _explorer()
        inimportables = releve["inimportables"]
        assert isinstance(inimportables, dict)

        if "src.docling_service.main" not in inimportables:
            raise AssertionError(
                "`src/docling_service/main.py` s'importe desormais cote hote : "
                "retire-le d'EXCEPTIONS_CONNUES et corrige la phrase du README, "
                "qui borne l'affirmation a ce module"
            )
        assert "fastapi" in inimportables["src.docling_service.main"], (
            "la cause a change : l'exception etait declaree pour `fastapi`, elle "
            f"vaut maintenant pour « {inimportables['src.docling_service.main']} »"
        )
