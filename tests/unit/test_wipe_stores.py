"""Tests de la purge des stores.

Le test qui compte est celui du bucket MinIO : c'est le store que la purge
oubliait, et le mode d'echec est silencieux. Une purge qui laisse des objets
derriere elle ne leve rien, ne journalise rien, et rend un compte qui a l'air
juste — c'est en cela qu'elle ressemble aux autres pannes de cette chaine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.wipe_stores import purge_bucket, purge_collection, purge_space


class ObjetMinio:
    """Objet MinIO minimal, tel que ``list_objects`` le rend."""

    def __init__(self, object_name: str) -> None:
        self.object_name = object_name


class FauxMinio:
    """Client MinIO qui reproduit la difference entre listage plat et recursif.

    C'est le point du test : ``list_objects(recursive=False)`` ne rend que les
    prefixes de premier niveau — ``images/`` — et jamais les objets qu'ils
    contiennent. Une purge batie dessus supprime zero objet en croyant avoir
    fini.
    """

    def __init__(self, objets: list[str], existe: bool = True) -> None:
        self.objets = list(objets)
        self.existe = existe
        self.supprimes: list[str] = []

    def bucket_exists(self, bucket: str) -> bool:
        return self.existe

    def list_objects(self, bucket: str, recursive: bool = False):
        if recursive:
            return [ObjetMinio(nom) for nom in self.objets]
        prefixes = sorted({nom.split("/", 1)[0] + "/" for nom in self.objets if "/" in nom})
        return [ObjetMinio(prefixe) for prefixe in prefixes]

    def remove_object(self, bucket: str, nom: str) -> None:
        if nom not in self.objets:
            raise KeyError(f"objet inexistant : {nom}")
        self.objets.remove(nom)
        self.supprimes.append(nom)


# Un bucket comme le pipeline le remplit : des crops sous images/{stem}/.
OBJETS = [
    "images/docling_paper/58363088aa_picture.png",
    "images/docling_paper/bcbe047fc2_table.png",
    "images/Practical MLOps/a56855cfa7_picture.png",
    "images/Practical MLOps/6b85633a11_picture.png",
    "images/Livre A/Preface/03795dc837_picture.png",
]


class TestPurgeBucket:
    def test_supprime_tous_les_objets(self):
        client = FauxMinio(OBJETS)
        assert purge_bucket(client, "documents") == len(OBJETS)
        assert client.objets == []

    def test_le_bucket_est_reellement_vide_apres(self):
        # Asserte l'etat du store, pas la valeur de retour : un compte juste
        # sur un bucket encore plein serait vert.
        client = FauxMinio(OBJETS)
        purge_bucket(client, "documents")
        assert list(client.list_objects("documents", recursive=True)) == []

    def test_descend_dans_les_prefixes(self):
        # Le defaut historique : un listage plat ne voit que « images/ » et la
        # purge laisse tout le contenu derriere elle.
        client = FauxMinio(OBJETS)
        purge_bucket(client, "documents")
        assert all(nom in client.supprimes for nom in OBJETS)

    def test_bucket_absent_ne_leve_pas(self):
        client = FauxMinio([], existe=False)
        assert purge_bucket(client, "documents") == 0

    def test_bucket_deja_vide(self):
        client = FauxMinio([])
        assert purge_bucket(client, "documents") == 0

    def test_un_echec_de_suppression_remonte(self):
        # Une purge partielle est pire qu'une purge absente : on croit repartir
        # propre et on re-ingere par-dessus des restes.
        client = FauxMinio(OBJETS)

        def refuse(bucket: str, nom: str) -> None:
            raise OSError("MinIO injoignable")

        client.remove_object = refuse
        with pytest.raises(OSError):
            purge_bucket(client, "documents")


class FausseReponse:
    def __init__(self, ok: bool, erreur: str = "") -> None:
        self.ok = ok
        self.erreur = erreur

    def is_succeeded(self) -> bool:
        return self.ok

    def error_msg(self) -> str:
        return self.erreur


class FausseSession:
    def __init__(self, reponse: FausseReponse) -> None:
        self.reponse = reponse
        self.requetes: list[str] = []

    def execute(self, requete: str) -> FausseReponse:
        self.requetes.append(requete)
        return self.reponse


class TestPurgeSpace:
    def test_drop_space_emis(self):
        session = FausseSession(FausseReponse(True))
        purge_space(session, "rag_space")
        assert session.requetes == ["DROP SPACE IF EXISTS rag_space;"]

    def test_succes_rapporte(self):
        session = FausseSession(FausseReponse(True))
        assert "supprime" in purge_space(session, "rag_space")

    def test_echec_rapporte_le_message(self):
        session = FausseSession(FausseReponse(False, "permission refusee"))
        assert "permission refusee" in purge_space(session, "rag_space")


class FauxChroma:
    def __init__(self) -> None:
        self.supprimees: list[str] = []

    def delete_collection(self, nom: str) -> None:
        self.supprimees.append(nom)


class TestPurgeCollection:
    def test_supprime_la_collection_nommee(self):
        client = FauxChroma()
        purge_collection(client, "rag_documents")
        assert client.supprimees == ["rag_documents"]


# ── Le point d'entree lui-meme ───────────────────────────────────────────────
#
# Les tests ci-dessus exercent les trois fonctions de purge. Ils ne touchent pas
# a main(), qui porte pourtant les deux moities du titre du commit 7d587b0 :
# « purger AUSSI le bucket MinIO » et « ECHOUER sur une purge partielle ». Trois
# mutations y survivaient : remplacer sys.exit(1) par sys.exit(0), retirer le
# bloc MinIO, ou ne plus ajouter « MinIO » a la liste des echecs.
#
# On teste ce point d'entree dans un SOUS-PROCESSUS, et non par import. Deux
# raisons, la premiere seule suffirait :
#
#   - le comportement en cause EST le code de sortie du processus. C'est ce
#     qu'un operateur voit, c'est ce qu'un `docker compose exec` remonte, et
#     c'est ce qu'un `&&` dans une procedure de purge lit. Un import laisse
#     attraper SystemExit et lire son attribut, ce qui prouve qu'un objet a ete
#     leve, pas que la commande echoue ;
#   - main() importe chromadb et nebula3, absents de l'environnement de
#     developpement. Les bouchonner dans sys.modules du processus de test
#     laisserait ces bouchons derriere lui pour les autres fichiers de la suite,
#     et l'ordre des tests deviendrait significatif.
#
# Les bouchons sont donc de vrais paquets, ecrits sur disque et places en tete
# de PYTHONPATH. Ils shuntent aussi `minio`, present lui, mais dont le client
# ouvrirait une connexion reseau.

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# Journal partage par les trois bouchons. Chaque geste effectivement pratique
# sur un store y laisse une ligne : c'est ce qui distingue « la purge a eu
# lieu » de « le script est alle jusqu'au bout ».
_TRACE = '''
import os


def trace(ligne):
    with open(os.environ["WIPE_TRACE"], "a", encoding="utf-8") as fichier:
        fichier.write(ligne + "\\n")


def doit_echouer(store):
    return store in os.environ.get("WIPE_ECHECS", "").split(",")
'''

BOUCHONS = {
    "_bouchon_commun.py": _TRACE,
    "chromadb/__init__.py": """
from _bouchon_commun import doit_echouer, trace


class HttpClient:
    def __init__(self, host=None, port=None):
        if doit_echouer("chroma"):
            raise RuntimeError("chromadb injoignable")

    def delete_collection(self, nom):
        trace("chroma delete_collection " + nom)
""",
    "minio/__init__.py": """
from _bouchon_commun import doit_echouer, trace


class _Objet:
    def __init__(self, nom):
        self.object_name = nom


class Minio:
    def __init__(self, endpoint, access_key=None, secret_key=None, secure=False):
        pass

    def bucket_exists(self, bucket):
        if doit_echouer("minio"):
            raise RuntimeError("minio injoignable")
        return True

    def list_objects(self, bucket, recursive=False):
        trace("minio list_objects recursive=%s" % recursive)
        return [_Objet("images/livre/1.png"), _Objet("images/livre/2.png")]

    def remove_object(self, bucket, nom):
        trace("minio remove_object " + nom)
""",
    "minio/error.py": """
class S3Error(Exception):
    pass
""",
    "nebula3/__init__.py": "",
    "nebula3/Config.py": """
class Config:
    pass
""",
    "nebula3/gclient/__init__.py": "",
    "nebula3/gclient/net.py": """
from _bouchon_commun import doit_echouer, trace


class _Reponse:
    def is_succeeded(self):
        return True

    def error_msg(self):
        return ""


class _Session:
    def execute(self, requete):
        trace("nebula execute " + requete)
        return _Reponse()

    def release(self):
        pass


class ConnectionPool:
    def init(self, adresses, config):
        if doit_echouer("nebula"):
            raise RuntimeError("graphd injoignable")
        return True

    def get_session(self, utilisateur, mot_de_passe):
        return _Session()

    def close(self):
        pass
""",
}


def _purger(tmp_path: Path, echecs: str = ""):
    """Lance ``python -m src.wipe_stores`` pour de bon, stores bouchonnes.

    Args:
        tmp_path: Repertoire de travail du sous-processus. Il n'y a pas de
            ``.env`` dedans, donc les reglages sont ceux du code et non ceux du
            poste.
        echecs: Stores qui doivent echouer, separes par des virgules, parmi
            ``chroma``, ``minio`` et ``nebula``.

    Returns:
        Le processus termine, et la liste des gestes tracee par les bouchons.
    """
    bouchons = tmp_path / "bouchons"
    for chemin, source in BOUCHONS.items():
        cible = bouchons / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(source, encoding="utf-8")

    trace = tmp_path / "trace.txt"
    trace.write_text("", encoding="utf-8")

    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = os.pathsep.join([str(bouchons), str(RACINE_DEPOT)])
    environnement["WIPE_TRACE"] = str(trace)
    environnement["WIPE_ECHECS"] = echecs
    # Sans quoi un graphd injoignable serait retente quinze fois, cinq secondes
    # d'attente entre chaque.
    environnement["NEBULA_MAX_ATTEMPTS"] = "1"
    environnement["NEBULA_RETRY_SECONDS"] = "0"

    processus = subprocess.run(
        [sys.executable, "-m", "src.wipe_stores"],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return processus, trace.read_text(encoding="utf-8").splitlines()


class TestLesBouchonsFonctionnent:
    """Sans ceci, « la purge a tout fait » serait vrai pour la mauvaise raison."""

    def test_le_sous_processus_a_bien_charge_les_bouchons(self, tmp_path):
        processus, gestes = _purger(tmp_path)
        assert processus.returncode == 0, processus.stderr
        assert gestes, f"aucun geste trace — les bouchons n'ont pas ete atteints : {processus}"


class TestMainPurgeLesTroisStores:
    def test_chromadb_est_purge(self, tmp_path):
        _, gestes = _purger(tmp_path)
        assert "chroma delete_collection rag_documents" in gestes

    def test_le_bucket_minio_est_purge(self, tmp_path):
        # La moitie du titre de 7d587b0 : « purger AUSSI le bucket MinIO ».
        # Retirer le bloc MinIO de main() laissait la suite verte.
        _, gestes = _purger(tmp_path)
        assert "minio list_objects recursive=True" in gestes
        assert [geste for geste in gestes if geste.startswith("minio remove_object")] == [
            "minio remove_object images/livre/1.png",
            "minio remove_object images/livre/2.png",
        ]

    def test_le_space_nebula_est_supprime(self, tmp_path):
        _, gestes = _purger(tmp_path)
        assert any("DROP SPACE IF EXISTS" in geste for geste in gestes)

    def test_une_purge_complete_sort_en_zero(self, tmp_path):
        processus, _ = _purger(tmp_path)
        assert processus.returncode == 0
        assert "PURGE INCOMPLETE" not in processus.stdout


class TestUnePurgePartielleEchoue:
    """L'autre moitie du titre : « ECHOUER sur une purge partielle ».

    Le code de sortie est le comportement lui-meme, pas son temoin : c'est ce
    qu'un `&&` lit dans une procedure de purge. Remplacer sys.exit(1) par
    sys.exit(0) laissait la suite verte, et une purge partielle passait alors
    pour une purge reussie — on croit repartir propre et on re-ingere par-dessus
    des restes.
    """

    def test_un_bucket_minio_en_echec_fait_sortir_en_un(self, tmp_path):
        # Le store le plus recemment ajoute a main(), donc celui dont
        # l'oubli dans la liste des echecs se verrait le moins.
        processus, _ = _purger(tmp_path, echecs="minio")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : MinIO" in processus.stdout

    def test_chromadb_en_echec_fait_sortir_en_un(self, tmp_path):
        processus, _ = _purger(tmp_path, echecs="chroma")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : ChromaDB" in processus.stdout

    def test_le_graphe_en_echec_fait_sortir_en_un(self, tmp_path):
        processus, _ = _purger(tmp_path, echecs="nebula")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : NebulaGraph" in processus.stdout

    def test_les_stores_encore_debout_sont_purges_quand_meme(self, tmp_path):
        # Une purge partielle sort en 1, elle ne s'arrete pas au premier echec :
        # les deux autres stores doivent bien avoir ete vides.
        processus, gestes = _purger(tmp_path, echecs="chroma")
        assert processus.returncode == 1
        assert "minio list_objects recursive=True" in gestes
        assert any("DROP SPACE IF EXISTS" in geste for geste in gestes)

    def test_tous_les_stores_en_echec_sont_nommes(self, tmp_path):
        processus, _ = _purger(tmp_path, echecs="chroma,minio,nebula")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : ChromaDB, MinIO, NebulaGraph" in processus.stdout
