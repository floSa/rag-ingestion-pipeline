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

from src.wipe_stores import (
    CibleHorsRacineError,
    purge_bucket,
    purge_cleaned,
    purge_collection,
    purge_space,
)


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
_TRACE = """
import os


def trace(ligne):
    with open(os.environ["WIPE_TRACE"], "a", encoding="utf-8") as fichier:
        fichier.write(ligne + "\\n")


def doit_echouer(store):
    return store in os.environ.get("WIPE_ECHECS", "").split(",")
"""

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


def _purger(
    tmp_path: Path,
    echecs: str = "",
    source_dir: str = "",
    cleaned_subdir: str | None = None,
):
    """Lance ``python -m src.wipe_stores`` pour de bon, stores bouchonnes.

    Args:
        tmp_path: Repertoire de travail du sous-processus. Il n'y a pas de
            ``.env`` dedans, donc les reglages sont ceux du code et non ceux du
            poste.
        echecs: Stores qui doivent echouer, separes par des virgules, parmi
            ``chroma``, ``minio`` et ``nebula``.
        source_dir: Racine des donnees, dont le sous-repertoire ``.cleaned`` est
            purge. Par defaut un chemin inexistant sous ``tmp_path``, pour
            qu'aucun test ne touche au corpus du poste.
        cleaned_subdir: Valeur de ``CLEANED_SUBDIR``. ``None`` laisse le defaut
            du code. C'EST LE RECLAGE QUI DECIDE DE LA CIBLE DU ``rmtree``, et il
            est annonce a l'operateur dans ``.env.example`` : le harnais devait
            pouvoir le poser, sans quoi aucun test n'observait ce que ce module
            supprime quand on le configure.

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
    # Jamais le `Datas/` du poste : ce sous-processus SUPPRIME un repertoire.
    environnement["SOURCE_DIR"] = source_dir or str(tmp_path / "datas_absent")
    if cleaned_subdir is not None:
        environnement["CLEANED_SUBDIR"] = cleaned_subdir
    else:
        environnement.pop("CLEANED_SUBDIR", None)

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


class TestLeHtmlNettoyeEstPurgeAussi:
    """Registre 4.28.b — LE PIEGE DE CE LOT, et il ne se voit pas.

    `wipe_stores` purgeait les trois stores et laissait `Datas/.cleaned/`. Or le
    HTML nettoye porte les URL MinIO des images (`cleaning.py` reecrit les
    `img src`), et l'asset Dagster `cleaned_html` ne se rematerialise pas si son
    fichier de sortie existe deja.

    La consequence, `mesure` le 1er septembre 2026 : le bucket porte **13**
    objets, tous des crops du PDF, et `Datas/.cleaned/` reference **199** URL
    `http://minio:9000/...` d'objets qui n'existent PAS. Une purge suivie d'une
    reingestion repart donc du HTML nettoye PERIME, et pointe 199 objets absents.
    **Reextraire ne suffit pas** — seule une execution de `cleaned_html` les
    restaure, en re-televersant les images depuis les captures.

    Purger `Datas/.cleaned/` est ce qui rend `wipe_stores` idempotent avec la
    chaine d'images : la purge devient « repartir de zero » et non « repartir de
    zero sauf le HTML ».
    """

    def test_le_repertoire_nettoye_est_supprime(self, tmp_path):
        nettoye = tmp_path / ".cleaned"
        (nettoye / "htms" / "livre").mkdir(parents=True)
        (nettoye / "htms" / "livre" / "chapitre.html").write_text("<html/>", encoding="utf-8")

        supprimes = purge_cleaned(nettoye, tmp_path)

        assert supprimes == 1
        assert not nettoye.exists()

    def test_un_repertoire_absent_ne_leve_pas_et_ne_compte_rien(self, tmp_path):
        """Le cas nominal d'une pile neuve : il n'y a rien a purger."""
        assert purge_cleaned(tmp_path / "jamais_cree", tmp_path) == 0

    def test_le_compte_est_celui_des_fichiers_reellement_retires(self, tmp_path):
        """Le compteur la ou il y a perte : une purge muette ne dit pas si elle a
        retire un fichier ou vingt-deux."""
        nettoye = tmp_path / ".cleaned"
        (nettoye / "a").mkdir(parents=True)
        for i in range(5):
            (nettoye / "a" / f"c{i}.html").write_text("<html/>", encoding="utf-8")

        assert purge_cleaned(nettoye, tmp_path) == 5

    def test_le_corpus_source_n_est_jamais_touche(self, tmp_path):
        """LE TEMOIN, et c'est le plus important du fichier.

        `Datas/.cleaned/` est un SOUS-REPERTOIRE de `Datas/`, qui porte le corpus
        versionne. Une purge qui viserait `Datas/` detruirait les 25 fichiers du
        corpus — 57 Mo — et le contenu entre dans le calcul d'`element_id`
        (contrat, exigences 2 et 3). Aucun garde-fou git ne s'y opposerait :
        `Datas/.cleaned/` est ignore, le corpus non, mais un `rmtree` ne lit pas
        `.gitignore`.
        """
        datas = tmp_path / "Datas"
        (datas / "htms" / "livre").mkdir(parents=True)
        source = datas / "htms" / "livre" / "chapitre.html"
        source.write_text("<html>le corpus</html>", encoding="utf-8")
        nettoye = datas / ".cleaned"
        (nettoye / "htms").mkdir(parents=True)
        (nettoye / "htms" / "chapitre.html").write_text("<html/>", encoding="utf-8")

        purge_cleaned(nettoye, datas)

        assert source.exists(), "le corpus source a ete detruit par la purge"
        assert source.read_text(encoding="utf-8") == "<html>le corpus</html>"
        assert datas.exists()


class TestMainPurgeAussiLeHtmlNettoye:
    """La purge du HTML nettoye est atteinte par `main()`, pas seulement offerte."""

    def test_main_annonce_le_html_nettoye_purge(self, tmp_path):
        acheve, _ = _purger(tmp_path)

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "HTML nettoye" in acheve.stdout, acheve.stdout

    def test_un_echec_de_purge_du_html_fait_sortir_en_un(self, tmp_path):
        """Un HTML nettoye qui survit est une reingestion qui repart du perime :
        c'est une purge INCOMPLETE, et elle doit sortir en 1 comme les autres.

        L'echec est reproduit par un repertoire parent en lecture seule, et ce
        n'est pas un cas de laboratoire : c'est exactement la panne que ce depot
        a deja rencontree sur `Datas/database/postgres`, ecrit par Docker en
        `root` et impossible a copier (mandat §7.1).
        """
        datas = tmp_path / "datas_protege"
        (datas / ".cleaned" / "htms").mkdir(parents=True)
        (datas / ".cleaned" / "htms" / "c.html").write_text("<html/>", encoding="utf-8")
        datas.chmod(0o500)
        try:
            acheve, _ = _purger(tmp_path, source_dir=str(datas))
        finally:
            datas.chmod(0o700)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "PURGE INCOMPLETE" in acheve.stdout
        assert "HTML nettoye" in acheve.stdout.split("PURGE INCOMPLETE")[1]

    def test_main_ne_purge_que_le_sous_repertoire_nettoye(self, tmp_path):
        """LE GARDE LE PLUS DANGEREUX DU FICHIER, et il manquait.

        `mesure` : faire viser `Path(source_dir)` au lieu de
        `Path(source_dir) / cleaned_subdir` laissait la suite ENTIEREMENT VERTE.
        Or cette mutation SUPPRIME `Datas/` — les 25 fichiers et 57 Mo du corpus
        versionne — et le contenu entre dans le calcul d'`element_id` (contrat,
        exigences 2 et 3). Aucun garde-fou git ne s'y opposerait : `rmtree` ne lit
        pas `.gitignore`.

        Le test precedent eprouvait `purge_cleaned` avec un chemin QU'IL
        FOURNISSAIT ; rien n'observait le chemin que `main()` CALCULE. C'est la
        lecon « mute le producteur, pas le consommateur ».
        """
        datas = tmp_path / "datas"
        corpus = datas / "htms" / "livre"
        corpus.mkdir(parents=True)
        (corpus / "chapitre.html").write_text("<html>le corpus</html>", encoding="utf-8")
        (datas / "pdfs").mkdir()
        (datas / "pdfs" / "livre.pdf").write_bytes(b"%PDF-1.4 le corpus")
        nettoye = datas / ".cleaned" / "htms"
        nettoye.mkdir(parents=True)
        (nettoye / "chapitre.html").write_text("<html/>", encoding="utf-8")

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert not (datas / ".cleaned").exists(), "le HTML nettoye devait etre purge"
        assert (corpus / "chapitre.html").read_text(encoding="utf-8") == "<html>le corpus</html>", (
            "LE CORPUS A ETE DETRUIT par la purge"
        )
        assert (datas / "pdfs" / "livre.pdf").exists(), "LE CORPUS A ETE DETRUIT"
        assert datas.exists()

    def test_le_html_nettoye_est_reellement_retire_par_main(self, tmp_path):
        """LE TEMOIN : `main()` ATTEINT la purge, il ne l'annonce pas seulement.

        Sans lui, un `print` sans appel passerait le premier test de cette
        classe — c'est la forme du defaut que ce chantier traque.
        """
        datas = tmp_path / "datas"
        nettoye = datas / ".cleaned" / "htms"
        nettoye.mkdir(parents=True)
        (nettoye / "c.html").write_text("<html/>", encoding="utf-8")

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert not (datas / ".cleaned").exists(), acheve.stdout
        assert "1 fichiers retires" in acheve.stdout


class TestUneCibleHorsDeLaRacineEstREFUSEE:
    """LE SEUL BLOQUANT DE CE LOT QUI DETRUIT QUELQUE CHOSE.

    `main()` calcule `Path(reglages.source_dir) / reglages.cleaned_subdir` et
    passe le resultat a `purge_cleaned`, qui faisait `shutil.rmtree` sans aucun
    controle de containment. Or `CLEANED_SUBDIR` **est un reglage annonce a
    l'operateur**, `.env.example:54`, et trois de ses valeurs font viser la
    RACINE ou au-dessus (`mesure` le 1er septembre 2026) :

        CLEANED_SUBDIR=""   ->  Path("/x/Datas") / ""   ==  /x/Datas
        CLEANED_SUBDIR="."  ->  idem, apres resolution
        CLEANED_SUBDIR="/etc" ->  un chemin ABSOLU REMPLACE la base
        CLEANED_SUBDIR=".." ->  /x/Datas/..  ->  /x

    Sur ce poste, `Datas/` porte le corpus VERSIONNE — 25 fichiers, 57 381 999
    octets — dont le contenu entre dans le calcul d'`element_id` (contrat,
    exigences 2 et 3), **et** `Datas/database/`, les bind mounts de ChromaDB,
    Nebula, MinIO et Postgres, c'est-a-dire l'antecedent mesure du chantier.
    `rmtree` ne lit pas `.gitignore` : aucun garde-fou git ne s'y opposerait.

    **LE REFUS EST DUR, ET C'EST UNE DECISION.** Pas un avertissement, pas un
    repli sur le defaut : un echec, verse aux `echecs`, code de sortie 1. Une
    purge qui ne sait pas ce qu'elle vise ne purge pas — et un repli silencieux
    sur `.cleaned` serait pire, l'operateur croyant avoir configure une cible
    que le code aurait discretement remplacee.

    Ces tests s'eprouvent sur un FAUX corpus jetable sous `tmp_path`, jamais sur
    le `Datas/` du poste. C'est le harnais qui le garantit : `SOURCE_DIR` pointe
    par defaut un chemin inexistant sous `tmp_path`.
    """

    @staticmethod
    def _faux_corpus(tmp_path: Path) -> Path:
        """Un `Datas/` jetable : du corpus, un `database/`, et du HTML nettoye."""
        datas = tmp_path / "datas"
        (datas / "htms" / "livre").mkdir(parents=True)
        (datas / "htms" / "livre" / "chapitre.html").write_text("<html>corpus</html>", "utf-8")
        (datas / "pdfs").mkdir()
        (datas / "pdfs" / "livre.pdf").write_bytes(b"%PDF-1.4 corpus")
        (datas / "database" / "chroma").mkdir(parents=True)
        (datas / "database" / "chroma" / "index.bin").write_bytes(b"des vecteurs")
        (datas / ".cleaned" / "htms").mkdir(parents=True)
        (datas / ".cleaned" / "htms" / "chapitre.html").write_text("<html/>", "utf-8")
        return datas

    def _corpus_intact(self, datas: Path) -> None:
        """Le corpus ET l'index vivant sont encore la, a l'octet."""
        chapitre = datas / "htms" / "livre" / "chapitre.html"
        assert chapitre.exists(), "LE CORPUS A ETE DETRUIT par la purge"
        assert chapitre.read_text(encoding="utf-8") == "<html>corpus</html>"
        assert (datas / "pdfs" / "livre.pdf").read_bytes() == b"%PDF-1.4 corpus"
        index = datas / "database" / "chroma" / "index.bin"
        assert index.exists(), "L'INDEX VIVANT A ETE DETRUIT par la purge"
        assert index.read_bytes() == b"des vecteurs"

    @pytest.mark.parametrize(
        ("sous_repertoire", "ce_qu_il_vise"),
        [
            ("", "la racine elle-meme : Path(base) / '' vaut base"),
            (".", "la racine elle-meme, apres resolution"),
            ("..", "le PARENT de la racine"),
        ],
    )
    def test_une_cible_qui_vise_la_racine_ou_au_dessus_fait_sortir_en_un(
        self, tmp_path: Path, sous_repertoire: str, ce_qu_il_vise: str
    ) -> None:
        """Les trois valeurs de reglage qui font viser la racine ou au-dessus.

        Le refus porte sur le CONTAINMENT STRICT, et rien de plus large. Ce qu'il
        ne couvre pas — une cible bien contenue mais fausse, `CLEANED_SUBDIR=htms`
        — est consigne au registre et NON traite : ce serait etendre une decision
        prise, pas la tenir.
        """
        datas = self._faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas), cleaned_subdir=sous_repertoire)

        self._corpus_intact(datas)
        assert acheve.returncode == 1, (
            f"CLEANED_SUBDIR={sous_repertoire!r} vise {ce_qu_il_vise} et la purge "
            f"a rendu 0 : elle ne sait pas ce qu'elle vise\n{acheve.stdout}{acheve.stderr}"
        )
        assert "PURGE INCOMPLETE" in acheve.stdout, acheve.stdout
        assert "HTML nettoye" in acheve.stdout.split("PURGE INCOMPLETE")[1], acheve.stdout

    def test_un_sous_repertoire_absolu_remplace_la_racine_et_est_refuse(
        self, tmp_path: Path
    ) -> None:
        """Le cas le plus large : un chemin ABSOLU fait oublier la base entiere.

        `Path("/x/Datas") / "/autre"` vaut `/autre`. La cible designee ici est un
        repertoire jetable HORS de la racine : elle doit survivre, et le refus
        doit venir du containment et non d'une absence de droits.
        """
        datas = self._faux_corpus(tmp_path)
        ailleurs = tmp_path / "ailleurs"
        ailleurs.mkdir()
        (ailleurs / "temoin.txt").write_text("hors de la racine", encoding="utf-8")

        acheve, _ = _purger(tmp_path, source_dir=str(datas), cleaned_subdir=str(ailleurs))

        self._corpus_intact(datas)
        assert (ailleurs / "temoin.txt").exists(), (
            "un chemin ABSOLU a remplace la racine et la purge a suivi"
        )
        assert acheve.returncode == 1, acheve.stdout + acheve.stderr

    def test_le_refus_nomme_la_cible_la_racine_et_le_reglage(self, tmp_path: Path) -> None:
        """Un refus sans cause probable envoie l'operateur lire le code.

        Il doit nommer les trois choses qu'il faut pour agir : ce qui a ete vise,
        ce dans quoi cela devait tenir, et le REGLAGE qui en a decide.
        """
        datas = self._faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas), cleaned_subdir="")

        assert "CLEANED_SUBDIR" in acheve.stdout, acheve.stdout
        assert str(datas) in acheve.stdout, acheve.stdout

    def test_les_trois_stores_sont_purges_quand_meme(self, tmp_path: Path) -> None:
        """Le refus ne condamne pas le reste, comme aucun des quatre echecs.

        Un refus qui arreterait la purge des stores laisserait l'etat exact que
        ce script existe pour eviter : des stores peuples qu'on croit vides.
        """
        datas = self._faux_corpus(tmp_path)

        acheve, gestes = _purger(tmp_path, source_dir=str(datas), cleaned_subdir="")

        assert acheve.returncode == 1
        assert any("chroma delete_collection" in geste for geste in gestes), gestes
        assert any("minio remove_object" in geste for geste in gestes), gestes
        assert any("DROP SPACE" in geste for geste in gestes), gestes

    def test_le_sous_repertoire_livre_est_accepte_et_purge(self, tmp_path: Path) -> None:
        """LE TEMOIN, et sans lui le refus serait vrai d'un module qui refuse tout.

        `.cleaned` est strictement contenu dans la racine : il doit passer, et
        etre reellement retire. Un garde qui refuserait aussi la cible nominale
        rendrait `wipe_stores` inutilisable en sortant en 1 a chaque purge — et
        les cinq tests ci-dessus resteraient verts.
        """
        datas = self._faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas), cleaned_subdir=".cleaned")

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert not (datas / ".cleaned").exists(), "le HTML nettoye devait etre purge"
        self._corpus_intact(datas)

    def test_le_defaut_du_code_est_accepte_quand_le_reglage_est_absent(
        self, tmp_path: Path
    ) -> None:
        """Le second temoin : un poste dont le `.env` est muet purge normalement.

        Sans lui, le garde pourrait n'accepter que la valeur explicite et casser
        tout poste qui ne declare pas `CLEANED_SUBDIR` — c'est-a-dire le cas
        nominal, `.env.example` la donnant en commentaire.
        """
        datas = self._faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert not (datas / ".cleaned").exists(), acheve.stdout


class TestLeContainmentEstDecideParPurgeCleaned:
    """La decision vit dans `purge_cleaned`, pas dans son appelant.

    La poser dans `main()` l'aurait laissee hors de portee de tout appelant
    futur — et `purge_cleaned` est une fonction publique du module. C'est la
    lecon « asserte depuis le cote qui PRODUIT le comportement ».
    """

    def test_la_racine_elle_meme_est_refusee(self, tmp_path: Path) -> None:
        datas = tmp_path / "datas"
        (datas / "htms").mkdir(parents=True)
        with pytest.raises(CibleHorsRacineError):
            purge_cleaned(datas, datas)

    def test_le_parent_de_la_racine_est_refuse(self, tmp_path: Path) -> None:
        datas = tmp_path / "datas"
        datas.mkdir()
        with pytest.raises(CibleHorsRacineError):
            purge_cleaned(datas / "..", datas)

    def test_un_lien_symbolique_qui_sort_de_la_racine_est_refuse(self, tmp_path: Path) -> None:
        """La comparaison porte sur le chemin RESOLU, et c'est ce qui compte ici.

        Un `.cleaned` qui serait un lien vers l'exterieur passerait toute
        comparaison textuelle, et `rmtree` suivrait le lien.
        """
        datas = tmp_path / "datas"
        datas.mkdir()
        dehors = tmp_path / "dehors"
        (dehors / "sous").mkdir(parents=True)
        (dehors / "sous" / "temoin.txt").write_text("hors de la racine", encoding="utf-8")
        (datas / ".cleaned").symlink_to(dehors / "sous", target_is_directory=True)

        with pytest.raises(CibleHorsRacineError):
            purge_cleaned(datas / ".cleaned", datas)
        assert (dehors / "sous" / "temoin.txt").exists(), "le lien a ete suivi et la cible detruite"

    def test_une_cible_contenue_mais_absente_ne_leve_pas_et_ne_compte_rien(
        self, tmp_path: Path
    ) -> None:
        """Le cas nominal d'une pile neuve reste distinct du refus.

        Une cible bien placee qui n'existe pas encore n'est pas une erreur de
        configuration : il n'y a rien a purger. Confondre les deux ferait sortir
        en 1 toute premiere purge.
        """
        datas = tmp_path / "datas"
        datas.mkdir()
        assert purge_cleaned(datas / ".cleaned", datas) == 0

    def test_une_cible_profondement_contenue_est_acceptee(self, tmp_path: Path) -> None:
        """Le temoin : le controle est un containment, pas une egalite de nom.

        Un garde ecrit `cible.parent == racine` refuserait
        `Datas/.cleaned/htms`, qui est une cible legitime si le reglage la
        designe. Le contrat est « strictement contenu », rien de plus etroit.
        """
        datas = tmp_path / "datas"
        cible = datas / "a" / "b" / "c"
        cible.mkdir(parents=True)
        (cible / "f.html").write_text("<html/>", encoding="utf-8")

        assert purge_cleaned(cible, datas) == 1
        assert not cible.exists()
        assert (datas / "a" / "b").exists(), "seule la cible devait partir"
