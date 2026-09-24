"""Rejoue la table des mutations du lot 11 : chacune DOIT rougir son test.

**POURQUOI CE SCRIPT EXISTE.** Le premier passage du lot 11 a annonce 33
mutations sans les versionner, et l'audit n'a pas pu les rejouer — le defaut
nomme au registre 4.35.e. Une mutation qu'on ne peut pas rejouer n'est pas une
mesure, c'est une affirmation.

Il lit `tests/mutations/table-des-mutations.json`, et pour chaque entree :

1. applique la mutation au fichier de PRODUCTION nomme — jamais au test ;
2. lance le test vise, et exige un ECHEC ;
3. restaure le fichier, quoi qu'il arrive.

Un motif qui n'apparait pas EXACTEMENT UNE FOIS fait echouer le rejeu : une
mutation qui ne mute rien ressemble a un garde qui ne voit rien.

Le code de sortie EST le comportement : 0 si toutes les mutations rougissent,
1 si l'une d'elles survit, 2 sur une table invalide.

    uv run python scripts/rejouer-les-mutations.py [--id A1-a] [--fichier-de-test <chemin>]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
TABLE = RACINE / "tests/mutations/table-des-mutations.json"
TESTS = "tests/unit/test_equivalence_des_identifiants.py"


class TableInvalideError(ValueError):
    """La table decrit une mutation qu'on ne peut pas appliquer telle quelle."""


def _muter(chemin: Path, mutation: dict[str, str]) -> str:
    """Applique la mutation et rend le texte d'ORIGINE, pour la restauration."""
    origine = chemin.read_text(encoding="utf-8")
    if "ajouter" in mutation:
        chemin.write_text(origine + mutation["ajouter"], encoding="utf-8")
        return origine
    motif = mutation["motif"]
    vus = origine.count(motif)
    if vus != 1:
        raise TableInvalideError(
            f"{mutation['id']} : le motif apparait {vus} fois dans {mutation['fichier']}, "
            "attendu exactement 1. Une mutation qui ne mute rien ne prouve rien."
        )
    chemin.write_text(origine.replace(motif, mutation["remplacement"]), encoding="utf-8")
    return origine


def rejouer(mutation: dict[str, str], fichier_de_test: str) -> tuple[bool, str]:
    """Rend `(rougit, detail)` : le test vise a-t-il bien echoue sous la mutation ?"""
    chemin = RACINE / mutation["fichier"]
    origine = _muter(chemin, mutation)
    try:
        acheve = subprocess.run(
            [sys.executable, "-m", "pytest", fichier_de_test, "-k", mutation["test"], "--tb=no"],
            capture_output=True,
            text=True,
            cwd=RACINE,
        )
    finally:
        chemin.write_text(origine, encoding="utf-8")
    derniere = acheve.stdout.strip().splitlines()[-1] if acheve.stdout.strip() else "(muet)"
    if (
        " no tests ran" in derniere
        or "deselected" in derniere
        and "passed" not in derniere
        and ("failed" not in derniere)
    ):
        return False, f"aucun test ne correspond a « {mutation['test']} » — {derniere}"
    return acheve.returncode != 0, derniere


def main() -> int:
    """Point d'entree."""
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--id", default=None, help="ne rejouer qu'une mutation")
    analyseur.add_argument("--fichier-de-test", default=TESTS)
    arguments = analyseur.parse_args()

    table = json.loads(TABLE.read_text(encoding="utf-8"))["mutations"]
    if arguments.id:
        table = [m for m in table if m["id"] == arguments.id]
        if not table:
            print(f"aucune mutation « {arguments.id} » dans {TABLE}")
            return 2

    survivants: list[str] = []
    depart = time.monotonic()
    for mutation in table:
        try:
            rougit, detail = rejouer(mutation, arguments.fichier_de_test)
        except TableInvalideError as exc:
            print(f"  {mutation['id']:6s} TABLE INVALIDE — {exc}")
            return 2
        etat = "ROUGE" if rougit else "SURVIT"
        print(f"  {mutation['id']:6s} {etat:6s} {mutation['test'][:46]:46s} {detail}")
        if not rougit:
            survivants.append(f"{mutation['id']} ({mutation['constat']})")

    duree = time.monotonic() - depart
    print(f"\n{len(table)} mutation(s) rejouee(s) en {duree:.1f} s")

    # LA RESTAURATION SE MESURE, elle ne se presume pas : ce script ecrit dans
    # des fichiers de production, et un `finally` ne survit pas a un SIGKILL.
    # Laisser un fichier mute derriere soi, c'est livrer la mutation.
    touches = sorted({m["fichier"] for m in table})
    reste = subprocess.run(
        ["git", "diff", "--name-only", "--", *touches],
        capture_output=True,
        text=True,
        cwd=RACINE,
    ).stdout.split()
    if reste:
        print(f"ECHEC : des fichiers restent MUTES apres le rejeu : {reste}")
        return 1
    print(f"fichiers restaures, verifie par git diff : {len(touches)} fichier(s)")
    if survivants:
        print(f"ECHEC — {len(survivants)} mutation(s) SURVIVENT, donc non tenues :")
        for survivant in survivants:
            print(f"  - {survivant}")
        return 1
    print("OK : chaque mutation rougit le test qui la tient.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
