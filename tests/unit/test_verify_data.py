"""Le controle avant-vol ne doit rien faire tant qu'on ne l'appelle pas.

Importer ``verify_data`` ne doit ouvrir aucune connexion ni appeler
``sys.exit`` : les controles vivent dans ``main`` (registre 4.5).

La verification passe par un sous-processus : un second import dans
l'interpreteur courant ne reexecuterait pas un module deja charge.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.verify_data import report

RACINE = Path(__file__).resolve().parents[2]

# Bouchons des trois clients de stores, ecrits sur disque et places en tete de
# PYTHONPATH. Meme montage que `test_wipe_stores.py`, pour les memes raisons :
# `chromadb` et `nebula3` ne sont pas dans le venv du depot, et bouchonner
# `sys.modules` dans l'interpreteur courant laisserait les bouchons en place pour
# les tests suivants.
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
    # Le bouchon porte le nom du paquet, `minio` (la bibliotheque cliente S3).
    # La cle d'echec nomme le store, comme dans le reste de ce fichier.
    "minio/__init__.py": """
from _bouchon_vd import doit_echouer


class Minio:
    def __init__(self, endpoint, access_key=None, secret_key=None, secure=False):
        if doit_echouer("stockage"):
            raise RuntimeError("stockage objet injoignable")

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
            `chroma`, `stockage`, `nebula` et `nebula_requete`.
        reglages: Variables d'environnement a poser pour le sous-processus.
            `NEBULA_USER` et `NEBULA_PASSWORD` sont d'abord retires de
            l'environnement herite, pour que le test des valeurs par defaut ne
            depende pas de la machine.

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
        """L'import n'affiche rien (en particulier pas « --- ChromaDB --- »)."""
        acheve = subprocess.run(
            [sys.executable, "-c", "import src.verify_data"],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.stdout == "", f"l'import a affiche : {acheve.stdout!r}"


class TestReport:
    """Les echecs se notent dans une liste passee en argument, pas au niveau module.

    Un etat de module cumulerait les echecs de deux executions dans un meme
    processus, et la seconde sortirait en erreur pour ceux de la premiere.
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
    """Les controles s'executent reellement contre les bouchons."""

    def test_le_sous_processus_a_bien_charge_les_bouchons(self, tmp_path):
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "--- ChromaDB ---" in acheve.stdout
        assert "--- Stockage objet (" in acheve.stdout
        assert "--- NebulaGraph ---" in acheve.stdout
        # Les valeurs viennent des bouchons : les controles ont donc tourne.
        assert "4365" in acheve.stdout
        assert "15196" in acheve.stdout


class TestLeControleNommeLeServeurQuIlInterroge:
    """L'en-tete du stockage objet affiche `S3_ENDPOINT`.

    Un nom de produit ecrit dans le code s'afficherait a l'identique quel que
    soit le serveur interroge. `S3_ENDPOINT` designe le serveur reellement
    interroge.
    """

    def test_l_adresse_configuree_est_dans_la_sortie(self, tmp_path):
        acheve = _controler(tmp_path, reglages={"S3_ENDPOINT": "un-autre-stockage:9999"})

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "--- Stockage objet (un-autre-stockage:9999) ---" in acheve.stdout

    def test_sans_s3_endpoint_le_controle_ne_demarre_pas(self, tmp_path):
        """Sans `S3_ENDPOINT`, le processus echoue avant d'interroger un store.

        `S3_ENDPOINT` n'a pas de valeur par defaut : `get_settings()` leve
        avant le premier appel. Une adresse par defaut ferait conclure « les
        trois stores repondent » sur un serveur non choisi.
        """
        environnement = {"S3_ENDPOINT": ""}
        acheve = _controler(tmp_path, reglages=environnement)

        assert acheve.returncode != 0
        assert "S3_ENDPOINT" in acheve.stdout + acheve.stderr


class TestLesIdentifiantsDuGrapheViennentDesReglages:
    """Registre 4.3 : les identifiants du graphd viennent de l'environnement.

    Le bouchon `nebula3` imprime ce qu'il recoit : la propriete est verifiee de
    bout en bout, en lancant `python -m src.verify_data`, et non en relisant le
    fichier.
    """

    def test_le_env_decide_des_identifiants(self, tmp_path):
        acheve = _controler(
            tmp_path,
            # `phrase` est une valeur d'essai : le test verifie que le
            # sous-processus la lit dans l'environnement. Aucun store reel
            # n'est joint.
            reglages={
                "NEBULA_USER": "lecteur",
                "NEBULA_PASSWORD": "phrase",  # pragma: allowlist secret
            },
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "IDENTIFIANTS=lecteur/phrase" in acheve.stdout

    def test_sans_variables_les_defauts_de_la_pile_valent(self, tmp_path):
        """Sans variables, les valeurs par defaut habituelles sont utilisees.

        Le test precedent fournit les deux variables : il ne verrait pas un
        mauvais defaut, qui casserait tout poste dont le `.env` ne les declare
        pas.
        """
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "IDENTIFIANTS=root/nebula" in acheve.stdout


class TestLeCodeDeSortieEstLeComportement:
    """Mutation M20 : `main()` sort en 1 quand un controle echoue.

    Le code de sortie est ce qu'un `&&` lit dans une procedure d'avant-vol. Un
    `import` ne verrait qu'un `SystemExit` leve : d'ou le sous-processus. Ces
    tests echouent si `sys.exit(1)` devient `sys.exit(0)`.
    """

    def test_les_trois_stores_debout_sortent_en_zero(self, tmp_path):
        acheve = _controler(tmp_path)
        assert acheve.returncode == 0
        assert "Les trois stores repondent." in acheve.stdout

    def test_un_chromadb_injoignable_fait_sortir_en_un(self, tmp_path):
        acheve = _controler(tmp_path, echecs="chroma")
        assert acheve.returncode == 1
        assert "controle(s) en echec" in acheve.stdout

    def test_un_stockage_objet_injoignable_fait_sortir_en_un(self, tmp_path):
        acheve = _controler(tmp_path, echecs="stockage")
        assert acheve.returncode == 1

    def test_un_graphd_injoignable_fait_sortir_en_un(self, tmp_path):
        """Un `pool.init` qui rend False, sans lever, sort aussi en 1."""
        acheve = _controler(tmp_path, echecs="nebula")
        assert acheve.returncode == 1

    def test_une_requete_ngql_rejetee_fait_sortir_en_un(self, tmp_path):
        """Les stores repondent, mais une requete du graphe echoue : sortie en 1.

        Cas d'un graphd debout dont le space n'existe pas : un test limite aux
        connexions ne le verrait pas.
        """
        acheve = _controler(tmp_path, echecs="nebula_requete")
        assert acheve.returncode == 1

    def test_le_bilan_nomme_les_controles_en_echec(self, tmp_path):
        """Le code de sortie signale un probleme ; le bilan dit lequel."""
        acheve = _controler(tmp_path, echecs="chroma,stockage")
        assert acheve.returncode == 1
        assert "2 controle(s) en echec" in acheve.stdout
        assert "connexion" in acheve.stdout
