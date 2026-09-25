"""Rejoue la table des mutations du lot 11 : chacune DOIT rougir son test.

Une mutation est une modification volontaire d'une ligne de code de
production. Elle est « rouge » quand le test qui la vise echoue, « survivante »
sinon : un test qui passe sous la mutation ne protege pas la ligne. La table
versionnee permet de rejouer chaque mutation (registre 4.35.e).

Pour chaque entree de `tests/mutations/table-des-mutations.json` :

1. applique la mutation au fichier de production nomme, dans une copie ;
2. lance le test vise dans la copie, et exige un echec de test ;
3. rend la copie a son etat d'origine pour l'entree suivante.

Le rejeu n'ecrit jamais dans l'arbre de travail (registre 4.39.c). Il travaille
sur une copie jetable dans un repertoire temporaire : `src/`, `tests/`,
`scripts/`, `documentation/campagnes/` et `pyproject.toml` (0,018 s, `mesure`
le 24 septembre 2026, contre 0,335 s pour un `git worktree add --detach`). Apres
un `SIGKILL`, la copie reste dans le repertoire temporaire du systeme, sans
effet sur l'arbre. Le script releve les `mtime` des sources de `src/` et
`tests/` avant et apres, et echoue si l'un d'eux a change.

Un motif qui n'apparait pas exactement une fois fait echouer le rejeu : une
mutation qui ne modifie rien ne prouve rien. Le verdict exige `rc=1` et
`failed` dans la derniere ligne de pytest : une mutation qui casse la syntaxe
fait aussi echouer pytest, mais ce n'est pas un echec de test.

Code de sortie : 0 si toutes les mutations sont rouges, 1 si l'une survit ou si
l'arbre de travail a change, 2 sur une table invalide.

    uv run python scripts/rejouer-les-mutations.py [--id A1-a] [--table <chemin>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
TABLE = RACINE / "tests/mutations/table-des-mutations.json"
TESTS = "tests/unit/test_equivalence_des_identifiants.py"

# Contenu de la copie. `src/` et `tests/` sont l'objet du rejeu ; `scripts/` et
# `documentation/campagnes/` parce que des tests les lisent (le repertoire de
# campagne est resolu depuis l'emplacement du module, donc dans la copie).
# `pyproject.toml` parce qu'il porte `addopts` et `testpaths`.
COPIES: tuple[str, ...] = ("src", "tests", "scripts", "documentation/campagnes")
FICHIERS: tuple[str, ...] = ("pyproject.toml",)
# Les arbres dont les `mtime` sont releves : ceux ou vivent les fichiers mutes.
SURVEILLES: tuple[str, ...] = ("src", "tests")


class TableInvalideError(ValueError):
    """La table decrit une mutation qu'on ne peut pas appliquer telle quelle."""


def empreinte_des_mtime(racine: Path) -> dict[str, float]:
    """Le `mtime` de chaque fichier source des arbres surveilles.

    `__pycache__` est exclu (defaut B1) : l'interpreteur reecrit les `.pyc` des
    qu'un module est importe. Un simple `python -c "import src.index_report"`
    lance depuis l'arbre pendant `make mutations` faisait sinon echouer le
    rejeu a tort. Seules les sources peuvent etre mutees.
    """
    releve: dict[str, float] = {}
    for arbre in SURVEILLES:
        for chemin in sorted((racine / arbre).rglob("*")):
            if chemin.is_file() and "__pycache__" not in chemin.parts:
                releve[str(chemin.relative_to(racine))] = chemin.stat().st_mtime
    return releve


def ce_qui_a_bouge(avant: dict[str, float], apres: dict[str, float]) -> list[str]:
    """Les fichiers surveilles dont le `mtime` a change, ou qui ont disparu.

    Cette comparaison remplace un `git diff`, qui compare l'arbre a l'index :
    il laisserait passer un residu indexe et echouerait devant une modification
    legitime non commitee.
    """
    return sorted(
        [nom for nom, t in apres.items() if avant.get(nom) != t] + list(set(avant) - set(apres))
    )


def preparer_la_copie(destination: Path) -> None:
    """Copie dans `destination` tout ce dont le rejeu a besoin, et rien d'autre."""
    for nom in COPIES:
        shutil.copytree(RACINE / nom, destination / nom)
    for nom in FICHIERS:
        (destination / nom).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / nom, destination / nom)


def _muter(chemin: Path, mutation: dict[str, str]) -> str:
    """Applique la mutation et rend le texte d'origine, pour la restauration."""
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


def verdict(code_de_retour: int, derniere: str) -> tuple[bool, str]:
    """Le test a-t-il echoue, et pas seulement casse ?

    Un rc non nul ne suffit pas : une mutation qui casse la syntaxe du fichier
    de production empeche la collecte, et rend un rc non nul avec une derniere
    ligne en `error` (registre 4.39.c).

    Le verdict exige donc `rc == 1`, `failed` dans la derniere ligne, et aucun
    `error`.
    """
    if " no tests ran" in derniere or (
        "deselected" in derniere and "passed" not in derniere and "failed" not in derniere
    ):
        return False, f"aucun test ne correspond a ce `-k` — {derniere}"
    if "error" in derniere.lower():
        return False, f"ERREUR et non echec de test (syntaxe ? collecte ?) — {derniere}"
    if code_de_retour != 1:
        return False, f"rc={code_de_retour}, attendu 1 pour un echec de test — {derniere}"
    if "failed" not in derniere:
        return False, f"aucun `failed` dans la derniere ligne — {derniere}"
    return True, derniere


def rejouer(copie: Path, mutation: dict[str, str], fichier_de_test: str) -> tuple[bool, str]:
    """Rend `(rougit, detail)` : le test vise a-t-il bien echoue sous la mutation ?

    Une entree peut nommer son propre fichier de test (`fichier_de_test`) : les
    tests d'un script de campagne ne sont pas dans le fichier de test par
    defaut.
    """
    chemin = copie / mutation["fichier"]
    fichier = mutation.get("fichier_de_test", fichier_de_test)
    origine = _muter(chemin, mutation)
    try:
        acheve = subprocess.run(
            [sys.executable, "-m", "pytest", fichier, "-k", mutation["test"], "--tb=no"],
            capture_output=True,
            text=True,
            cwd=copie,
        )
    finally:
        chemin.write_text(origine, encoding="utf-8")
    derniere = acheve.stdout.strip().splitlines()[-1] if acheve.stdout.strip() else "(muet)"
    return verdict(acheve.returncode, derniere)


def main() -> int:
    """Point d'entree."""
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--id", default=None, help="ne rejouer qu'une mutation")
    analyseur.add_argument("--fichier-de-test", default=TESTS)
    # Une autre table, pour que les tests de ce script puissent lui en donner
    # une qui casse la syntaxe : la table du depot ne contient que des
    # mutations rouges.
    analyseur.add_argument("--table", type=Path, default=TABLE)
    arguments = analyseur.parse_args()

    table = json.loads(arguments.table.read_text(encoding="utf-8"))["mutations"]
    if arguments.id:
        table = [m for m in table if m["id"] == arguments.id]
        if not table:
            print(f"aucune mutation « {arguments.id} » dans {arguments.table}")
            return 2

    # Releve des `mtime` avant tout : l'arbre de travail ne doit pas changer.
    avant = empreinte_des_mtime(RACINE)

    survivants: list[str] = []
    depart = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rejeu-des-mutations-") as temporaire:
        copie = Path(temporaire) / "copie"
        preparer_la_copie(copie)
        print(f"rejeu sur une COPIE JETABLE : {copie}")
        for mutation in table:
            try:
                rougit, detail = rejouer(copie, mutation, arguments.fichier_de_test)
            except TableInvalideError as exc:
                print(f"  {mutation['id']:6s} TABLE INVALIDE — {exc}")
                return 2
            etat = "ROUGE" if rougit else "SURVIT"
            print(f"  {mutation['id']:6s} {etat:6s} {mutation['test'][:46]:46s} {detail}")
            if not rougit:
                survivants.append(f"{mutation['id']} ({mutation['constat']})")

    duree = time.monotonic() - depart
    print(f"\n{len(table)} mutation(s) rejouee(s) en {duree:.1f} s")

    # Second releve : verifie que le rejeu n'a rien ecrit dans l'arbre de
    # travail (voir `ce_qui_a_bouge` pour le choix face a `git diff`).
    bouges = ce_qui_a_bouge(avant, empreinte_des_mtime(RACINE))
    if bouges:
        print(f"ECHEC : le rejeu a TOUCHE l'arbre de travail : {bouges[:8]}")
        return 1
    print(f"arbre de travail intact, {len(avant)} fichier(s) surveille(s) par leur mtime")
    if survivants:
        print(f"ECHEC — {len(survivants)} mutation(s) SURVIVENT, donc non tenues :")
        for survivant in survivants:
            print(f"  - {survivant}")
        return 1
    print("OK : chaque mutation rougit le test qui la tient.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
