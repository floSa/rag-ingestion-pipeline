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

import os
import subprocess
import sys
from pathlib import Path

from src.verify_data import report

RACINE = Path(__file__).resolve().parents[2]

# Bouchons des trois clients de stores, ecrits sur disque et places en tete de
# PYTHONPATH. Le meme montage que `test_wipe_stores.py`, et pour les memes deux
# raisons : `chromadb` et `nebula3` ne sont pas dans le venv du depot, et
# bouchonner `sys.modules` dans l'interpreteur courant laisserait les bouchons
# derriere soi — l'ordre des tests deviendrait significatif.
#
# `VD_ECHECS` nomme les stores qui doivent tomber en panne.
_COMMUN = """
import os


def doit_echouer(store):
    return store in os.environ.get("VD_ECHECS", "").split(",")
"""

BOUCHONS = {
    "_bouchon_vd.py": _COMMUN,
    "chromadb/__init__.py": """
from _bouchon_vd import doit_echouer


class _Collection:
    def count(self):
        return 4365


class HttpClient:
    def __init__(self, host=None, port=None):
        if doit_echouer("chroma"):
            raise RuntimeError("chromadb injoignable")

    def get_collection(self, nom):
        return _Collection()
""",
    "minio/__init__.py": """
from _bouchon_vd import doit_echouer


class Minio:
    def __init__(self, endpoint, access_key=None, secret_key=None, secure=False):
        if doit_echouer("minio"):
            raise RuntimeError("minio injoignable")

    def list_objects(self, bucket, recursive=False):
        return [object(), object()]
""",
    "nebula3/__init__.py": "",
    "nebula3/Config.py": """
class Config:
    pass
""",
    "nebula3/gclient/__init__.py": "",
    "nebula3/gclient/net.py": """
from _bouchon_vd import doit_echouer


class _Valeur:
    def get_iVal(self):
        return 15196


class _Ligne:
    values = [_Valeur()]


class _Reponse:
    def is_succeeded(self):
        return not doit_echouer("nebula_requete")

    def error_msg(self):
        return "requete rejetee"

    def rows(self):
        return [_Ligne()]


class _Session:
    def execute(self, requete):
        return _Reponse()

    def release(self):
        pass


class ConnectionPool:
    def init(self, adresses, config):
        return not doit_echouer("nebula")

    def get_session(self, utilisateur, mot_de_passe):
        # Les identifiants sont IMPRIMES : c'est ce qui permet d'asserter qu'ils
        # viennent des reglages et ne sont plus ecrits en dur (registre 4.3).
        print(f"IDENTIFIANTS={utilisateur}/{mot_de_passe}")
        return _Session()

    def close(self):
        pass
""",
}


def _controler(tmp_path: Path, echecs: str = "", reglages: dict[str, str] | None = None):
    """Lance `python -m src.verify_data` pour de bon, stores bouchonnes.

    Args:
        tmp_path: Repertoire de travail du sous-processus. Pas de `.env` dedans,
            donc les reglages sont ceux du code et non ceux du poste.
        echecs: Stores qui doivent echouer, separes par des virgules, parmi
            `chroma`, `minio`, `nebula` et `nebula_requete`.
        reglages: Variables d'environnement a poser pour le sous-processus.
            `NEBULA_USER` et `NEBULA_PASSWORD` sont d'abord RETIRES de
            l'environnement herite : sans ce retrait, un poste qui les declare
            rendrait le temoin des defauts vert ou rouge selon la machine.

    Returns:
        Le processus termine.
    """
    bouchons = tmp_path / "bouchons"
    for chemin, source in BOUCHONS.items():
        cible = bouchons / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(source, encoding="utf-8")

    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = os.pathsep.join([str(bouchons), str(RACINE)])
    environnement["VD_ECHECS"] = echecs
    for cle in ("NEBULA_USER", "NEBULA_PASSWORD"):
        environnement.pop(cle, None)
    environnement.update(reglages or {})
    return subprocess.run(
        [sys.executable, "-m", "src.verify_data"],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )


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


class TestLesBouchonsFonctionnent:
    """Sans ceci, « les trois stores repondent » serait vrai pour la mauvaise raison."""

    def test_le_sous_processus_a_bien_charge_les_bouchons(self, tmp_path):
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "--- ChromaDB ---" in acheve.stdout
        assert "--- MinIO ---" in acheve.stdout
        assert "--- NebulaGraph ---" in acheve.stdout
        # Les valeurs viennent bien des bouchons, donc les controles ont TOURNE.
        assert "4365" in acheve.stdout
        assert "15196" in acheve.stdout


class TestLesIdentifiantsDuGrapheViennentDesReglages:
    """Registre 4.3 : les identifiants du graphd etaient ecrits en dur.

    Le bouchon `nebula3` imprime ce qu'il recoit, si bien que la propriete est
    assertee de bout en bout — `python -m src.verify_data` lance pour de bon —
    et non sur une relecture du fichier.
    """

    def test_le_env_decide_des_identifiants(self, tmp_path):
        acheve = _controler(
            tmp_path,
            # `phrase` est la valeur d'essai que le bouchon doit rendre : ce
            # test existe pour prouver que le sous-processus la lit dans
            # l'environnement. Aucun store reel n'est joint.
            reglages={
                "NEBULA_USER": "lecteur",
                "NEBULA_PASSWORD": "phrase",  # pragma: allowlist secret
            },
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "IDENTIFIANTS=lecteur/phrase" in acheve.stdout

    def test_sans_variables_les_defauts_de_la_pile_valent(self, tmp_path):
        """LE TEMOIN : les defauts historiques sont conserves.

        Sans lui, exposer les reglages avec de mauvais defauts casserait tout
        poste dont le `.env` ne les declare pas, et le test ci-dessus resterait
        vert puisqu'il fournit les deux variables.
        """
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "IDENTIFIANTS=root/nebula" in acheve.stdout


class TestLeCodeDeSortieEstLeComportement:
    """M20 : `sys.exit(1)` de `main()` n'etait asserte NULLE PART.

    `mesure` : le remplacer par `sys.exit(0)` laissait 639 tests verts. C'est mot
    pour mot la leçon que le lot 0 a payee sur `wipe_stores` — « un code de sortie
    documente et justifie n'etait asserte nulle part » — et l'equivalent y est
    garde par cinq tests depuis `1c002f2`.

    Le code de sortie EST le comportement, pas son temoin : c'est ce qu'un
    `docker compose exec` remonte et ce qu'un `&&` lit dans une procedure
    d'avant-vol. Un `import` laisserait attraper `SystemExit` — prouver qu'un
    objet a ete leve, pas que la commande echoue. D'ou le sous-processus.
    """

    def test_les_trois_stores_debout_sortent_en_zero(self, tmp_path):
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0
        assert "Les trois stores repondent." in acheve.stdout

    def test_un_chromadb_injoignable_fait_sortir_en_un(self, tmp_path):
        acheve = _controler(tmp_path, echecs="chroma")
        assert acheve.returncode == 1
        assert "controle(s) en echec" in acheve.stdout

    def test_un_minio_injoignable_fait_sortir_en_un(self, tmp_path):
        acheve = _controler(tmp_path, echecs="minio")
        assert acheve.returncode == 1

    def test_un_graphd_injoignable_fait_sortir_en_un(self, tmp_path):
        """Le `pool.init` qui rend False, et non une exception : l'autre branche."""
        acheve = _controler(tmp_path, echecs="nebula")
        assert acheve.returncode == 1

    def test_une_requete_ngql_rejetee_fait_sortir_en_un(self, tmp_path):
        """Les stores repondent, mais le graphe refuse : un cas distinct des trois.

        Sans lui, un garde qui n'observerait que les connexions serait vert sur
        un graphd debout dont le space n'existe pas.
        """
        acheve = _controler(tmp_path, echecs="nebula_requete")
        assert acheve.returncode == 1

    def test_le_bilan_nomme_les_controles_en_echec(self, tmp_path):
        """Le code de sortie dit QU'il y a un probleme ; le bilan dit lequel."""
        acheve = _controler(tmp_path, echecs="chroma,minio")
        assert acheve.returncode == 1
        assert "2 controle(s) en echec" in acheve.stdout
        assert "connexion" in acheve.stdout
