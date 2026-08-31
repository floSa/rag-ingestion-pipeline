"""Le controle avant-vol ne doit rien faire tant qu'on ne l'appelle pas.

``verify_data`` n'avait pas de ``main`` : ouvrir une connexion ChromaDB, lister
un bucket MinIO et interroger NebulaGraph etaient des instructions de niveau
module. Un simple ``import`` declenchait les trois controles et pouvait appeler
``sys.exit(1)`` — et rien n'etait testable, puisqu'un test qui importe le module
aurait exige les trois stores debout (registre 4.5).

La verification passe par un SOUS-PROCESSUS : un import de plus dans
l'interpreteur courant ne rejouerait pas le module deja charge, donc le test
serait vert des deux cotes du defaut.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.verify_data import report

RACINE = Path(__file__).resolve().parents[2]


class TestLImportNeFaitRien:
    def test_importing_the_module_does_not_touch_any_store(self):
        """Il s'importe, il ne sort pas, et il ne charge aucun client de store."""
        acheve = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, src.verify_data;"
                "sys.exit(1 if {'chromadb', 'minio', 'nebula3'} & set(sys.modules) else 0)",
            ],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    def test_importing_the_module_prints_nothing(self):
        """Le module affichait « --- ChromaDB --- » a l'import."""
        acheve = subprocess.run(
            [sys.executable, "-c", "import src.verify_data"],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.stdout == "", f"l'import a affiche : {acheve.stdout!r}"


class TestReport:
    """Les echecs se notent dans une liste PASSEE, et non dans un etat de module.

    Un etat de module survit a l'appel : deux executions dans un meme processus
    cumuleraient leurs echecs, et la seconde sortirait en erreur pour ceux de la
    premiere.
    """

    def test_a_successful_check_notes_nothing(self):
        echecs: list[str] = []
        report("chunks", "4365", echecs)
        assert echecs == []

    def test_a_failed_check_is_noted_under_its_label(self):
        echecs: list[str] = []
        report("connexion", "refusee", echecs, ok=False)
        assert echecs == ["connexion"]

    def test_two_runs_do_not_share_their_failures(self):
        premier: list[str] = []
        report("connexion", "refusee", premier, ok=False)
        second: list[str] = []
        report("chunks", "0", second)
        assert second == []
