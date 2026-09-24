"""Rejoue la table des mutations du lot 11 : chacune DOIT rougir son test.

**POURQUOI CE SCRIPT EXISTE.** Le premier passage du lot 11 a annonce 33
mutations sans les versionner, et l'audit n'a pas pu les rejouer — le defaut
nomme au registre 4.35.e. Une mutation qu'on ne peut pas rejouer n'est pas une
mesure, c'est une affirmation.

**IL N'ECRIT PLUS JAMAIS DANS L'ARBRE DE TRAVAIL, et c'est la reparation du
troisieme audit (registre 4.39.c).** La version precedente mutait les fichiers
de PRODUCTION en place et les restaurait dans un `finally`. Trois defauts
mesures : `extraction.py` restait mute 0,66 s sur disque a chaque passage ;
apres un `SIGKILL`, le fichier restait mute et le lancement suivant le prenait
pour l'origine, donc le « restaurait » MUTE ; et le controle final par `git
diff` (arbre contre index) laissait passer un residu INDEXE, tout en rendant un
faux « ECHEC » devant une modification legitime non commitee.

Le rejeu se fait desormais sur une COPIE JETABLE, dans un repertoire temporaire.
`mesure` du 24 septembre 2026 : 0,018 s pour `src/`, `tests/`, `scripts/`,
`documentation/campagnes/` et `pyproject.toml`, contre 0,335 s pour un
`git worktree add --detach`. La copie est retiree a la sortie ; apres un
`SIGKILL` elle SURVIT dans le repertoire temporaire du systeme — et c'est sans
consequence, puisqu'elle ne partage aucun fichier avec l'arbre. Le script le
verifie lui-meme : il releve les `mtime` de `src/` et de `tests/` avant et
apres, et rougit si l'un d'eux a bouge.

Pour chaque entree de `tests/mutations/table-des-mutations.json` :

1. applique la mutation au fichier de PRODUCTION nomme, DANS LA COPIE ;
2. lance le test vise dans la copie, et exige un ECHEC de TEST ;
3. rend la copie a son etat d'origine pour l'entree suivante.

Un motif qui n'apparait pas EXACTEMENT UNE FOIS fait echouer le rejeu : une
mutation qui ne mute rien ressemble a un garde qui ne voit rien. Et le verdict
exige `failed` dans la derniere ligne avec `rc=1` : une mutation qui CASSE LA
SYNTAXE ressemble a un garde qui voit, et passait pour « ROUGE » (registre
4.39.c).

Le code de sortie EST le comportement : 0 si toutes les mutations rougissent,
1 si l'une d'elles survit, 2 sur une table invalide.

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

# CE QUE LA COPIE PORTE. `src/` et `tests/` sont l'objet du rejeu ; `scripts/` et
# `documentation/campagnes/` parce que des tests les LISENT — le repertoire de
# campagne est resolu depuis l'emplacement du module, donc depuis la COPIE.
# `pyproject.toml` parce qu'il porte `addopts` et `testpaths`.
COPIES: tuple[str, ...] = ("src", "tests", "scripts", "documentation/campagnes")
FICHIERS: tuple[str, ...] = ("pyproject.toml",)
# Les arbres dont on releve les `mtime` : ceux que l'ancienne version mutait.
SURVEILLES: tuple[str, ...] = ("src", "tests")


class TableInvalideError(ValueError):
    """La table decrit une mutation qu'on ne peut pas appliquer telle quelle."""


def empreinte_des_mtime(racine: Path) -> dict[str, float]:
    """Le `mtime` de chaque fichier des arbres surveilles. La sonde du script."""
    releve: dict[str, float] = {}
    for arbre in SURVEILLES:
        for chemin in sorted((racine / arbre).rglob("*")):
            if chemin.is_file():
                releve[str(chemin.relative_to(racine))] = chemin.stat().st_mtime
    return releve


def ce_qui_a_bouge(avant: dict[str, float], apres: dict[str, float]) -> list[str]:
    """Les fichiers surveilles dont le `mtime` a change, ou qui ont disparu.

    C'est la sonde qui remplace le `git diff` de la version precedente : celui-ci
    comparait l'arbre a l'INDEX, donc laissait passer un residu indexe et
    rougissait devant une modification legitime non commitee.
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


def verdict(code_de_retour: int, derniere: str) -> tuple[bool, str]:
    """Le test a-t-il ECHOUE, et pas seulement casse ?

    **UN rc NON NUL NE SUFFIT PAS.** `mesure` du troisieme audit : une mutation
    qui casse la SYNTAXE du fichier de production empeche la collecte, rend un rc
    non nul et une derniere ligne en `error` — et passait pour « ROUGE ». Un
    garde qui ne voit rien et un fichier qu'on ne peut plus lire se ressemblent
    au seul rc.

    Le verdict exige donc les trois : `rc == 1`, `failed` dans la derniere ligne,
    et AUCUN `error`.
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

    Une entree peut nommer SON fichier de test (`fichier_de_test`) : les gardes
    d'un script de campagne ne vivent pas dans le fichier de test du harnais.
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
    # UNE AUTRE TABLE, pour que les tests de ce script puissent lui en donner une
    # qui casse la syntaxe : le verdict ne se verifie pas sur la table du depot,
    # qui est verte par construction.
    analyseur.add_argument("--table", type=Path, default=TABLE)
    arguments = analyseur.parse_args()

    table = json.loads(arguments.table.read_text(encoding="utf-8"))["mutations"]
    if arguments.id:
        table = [m for m in table if m["id"] == arguments.id]
        if not table:
            print(f"aucune mutation « {arguments.id} » dans {arguments.table}")
            return 2

    # LA SONDE, avant toute chose : l'arbre d'ou l'on lance ne doit pas bouger.
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

    # LA SONDE SE RELIT : le rejeu n'ecrit plus rien dans l'arbre de travail, et
    # c'est MESURE et non promis. `git diff` ne pouvait pas le dire — il compare
    # l'arbre a l'index, donc laisse passer un residu indexe et rougit devant
    # une modification legitime non commitee.
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
