"""Tests de la purge des stores.

Le bucket d'objets demande le plus d'attention : une purge qui laisse des
objets derriere elle ne leve rien, ne journalise rien, et rend un compte
plausible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.docling_service.elements import CLEANED_SUBDIR
from src.wipe_stores import (
    CibleHorsDuNettoyeError,
    CibleHorsRacineError,
    purge_bucket,
    purge_cleaned,
    purge_collection,
    purge_space,
)


class ObjetDuBucket:
    """Objet minimal, tel que ``list_objects`` le rend."""

    def __init__(self, object_name: str) -> None:
        self.object_name = object_name


class FauxClientS3:
    """Client S3 qui reproduit la difference entre listage plat et recursif.

    ``list_objects(recursive=False)`` ne rend que les prefixes de premier niveau
    (``images/``), jamais les objets qu'ils contiennent. Une purge fondee sur ce
    listage supprimerait zero objet sans erreur.
    """

    def __init__(self, objets: list[str], existe: bool = True) -> None:
        self.objets = list(objets)
        self.existe = existe
        self.supprimes: list[str] = []

    def bucket_exists(self, bucket: str) -> bool:
        return self.existe

    def list_objects(self, bucket: str, recursive: bool = False):
        if recursive:
            return [ObjetDuBucket(nom) for nom in self.objets]
        prefixes = sorted({nom.split("/", 1)[0] + "/" for nom in self.objets if "/" in nom})
        return [ObjetDuBucket(prefixe) for prefixe in prefixes]

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
        client = FauxClientS3(OBJETS)
        assert purge_bucket(client, "documents") == len(OBJETS)
        assert client.objets == []

    def test_le_bucket_est_reellement_vide_apres(self):
        # Asserte l'etat du store, pas la valeur de retour : un compte juste
        # sur un bucket encore plein serait vert.
        client = FauxClientS3(OBJETS)
        purge_bucket(client, "documents")
        assert list(client.list_objects("documents", recursive=True)) == []

    def test_descend_dans_les_prefixes(self):
        # Un listage plat ne voit que « images/ », et la
        # purge laisse tout le contenu derriere elle.
        client = FauxClientS3(OBJETS)
        purge_bucket(client, "documents")
        assert all(nom in client.supprimes for nom in OBJETS)

    def test_bucket_absent_ne_leve_pas(self):
        client = FauxClientS3([], existe=False)
        assert purge_bucket(client, "documents") == 0

    def test_bucket_deja_vide(self):
        client = FauxClientS3([])
        assert purge_bucket(client, "documents") == 0

    def test_un_echec_de_suppression_remonte(self):
        # Une purge partielle est pire qu'une purge absente : on croit repartir
        # propre et on re-ingere par-dessus des restes.
        client = FauxClientS3(OBJETS)

        def refuse(bucket: str, nom: str) -> None:
            raise OSError("stockage objet injoignable")

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
# Les tests ci-dessous portent sur main() : il doit purger aussi le bucket
# d'objets, et sortir en 1 sur une purge partielle. Ils echouent si sys.exit(1)
# devient sys.exit(0), si le bloc du stockage objet disparait, ou s'il n'est
# plus compte parmi les echecs.
#
# main() est lance dans un sous-processus, et non importe :
#
#   - le comportement teste est le code de sortie du processus, celui que lit
#     un `&&` dans une procedure de purge. Un import ne verrait qu'un
#     SystemExit leve ;
#   - main() importe chromadb et nebula3, absents de l'environnement de
#     developpement. Les bouchonner dans sys.modules du processus de test
#     laisserait ces bouchons en place pour les autres fichiers de la suite.
#
# Les bouchons sont de vrais paquets, ecrits sur disque et places en tete de
# PYTHONPATH. Ils remplacent aussi `minio`, la bibliotheque cliente S3, presente
# mais dont le client ouvrirait une connexion reseau.

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# Journal partage par les trois bouchons. Chaque operation effectuee sur un
# store y laisse une ligne : on distingue ainsi « la purge a eu lieu » de « le
# script est alle jusqu'au bout ».
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
        if doit_echouer("stockage"):
            raise RuntimeError("stockage objet injoignable")
        return True

    def list_objects(self, bucket, recursive=False):
        trace("stockage list_objects recursive=%s" % recursive)
        return [_Objet("images/livre/1.png"), _Objet("images/livre/2.png")]

    def remove_object(self, bucket, nom):
        trace("stockage remove_object " + nom)
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
    reglages: dict[str, str] | None = None,
    attendre_des_gestes: bool = True,
):
    """Lance ``python -m src.wipe_stores`` pour de bon, stores bouchonnes.

    Args:
        tmp_path: Repertoire de travail du sous-processus. Il n'y a pas de
            ``.env`` dedans, donc les reglages sont ceux du code et non ceux du
            poste.
        echecs: Stores qui doivent echouer, separes par des virgules, parmi
            ``chroma``, ``stockage`` et ``nebula``.
        source_dir: Racine des donnees, dont le sous-repertoire ``.cleaned`` est
            purge. Par defaut un chemin inexistant sous ``tmp_path``, pour
            qu'aucun test ne touche au corpus du poste.
        reglages: Variables d'environnement supplementaires posees pour le
            sous-processus, appliquees en dernier. Une valeur vide declare la
            variable vide, ce qui n'est pas la meme chose que l'omettre.
        attendre_des_gestes: Faux quand le cas teste est qu'aucun store n'est
            touche. Le harnais ne le verifie pas lui-meme (c'est au test de le
            faire), mais le parametre documente l'intention.
        cleaned_subdir: Valeur posee dans ``CLEANED_SUBDIR``. ``None`` retire la
            variable de l'environnement herite. Ce n'est plus un reglage : le
            sous-repertoire est une constante du code (registre 4.29.a). Poser
            la variable permet de verifier qu'elle est sans effet.

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
    # Jamais le `Datas/` du poste : ce sous-processus supprime un repertoire.
    environnement["SOURCE_DIR"] = source_dir or str(tmp_path / "datas_absent")
    if cleaned_subdir is not None:
        environnement["CLEANED_SUBDIR"] = cleaned_subdir
    else:
        environnement.pop("CLEANED_SUBDIR", None)
    environnement.update(reglages or {})

    processus = subprocess.run(
        [sys.executable, "-m", "src.wipe_stores"],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return processus, trace.read_text(encoding="utf-8").splitlines()


def _faux_corpus(tmp_path: Path) -> Path:
    """Un `Datas/` jetable : du corpus, un `database/`, et du HTML nettoye.

    Jamais le `Datas/` du poste : ces tests lancent un sous-processus qui
    supprime un repertoire, et le corpus reel est versionne (son contenu entre
    dans le calcul d'`element_id`, contrat, exigences 2 et 3).
    """
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


def _corpus_intact(datas: Path) -> None:
    """Le corpus et l'index vivant sont encore la, a l'octet."""
    chapitre = datas / "htms" / "livre" / "chapitre.html"
    assert chapitre.exists(), "LE CORPUS A ETE DETRUIT par la purge"
    assert chapitre.read_text(encoding="utf-8") == "<html>corpus</html>"
    assert (datas / "pdfs" / "livre.pdf").read_bytes() == b"%PDF-1.4 corpus"
    index = datas / "database" / "chroma" / "index.bin"
    assert index.exists(), "L'INDEX VIVANT A ETE DETRUIT par la purge"
    assert index.read_bytes() == b"des vecteurs"


class TestLesBouchonsFonctionnent:
    """Verifie que les bouchons ont trace des operations (harnais bien cable)."""

    def test_le_sous_processus_a_bien_charge_les_bouchons(self, tmp_path):
        processus, gestes = _purger(tmp_path)
        assert processus.returncode == 0, processus.stderr
        assert gestes, f"aucun geste trace — les bouchons n'ont pas ete atteints : {processus}"


class TestMainPurgeLesTroisStores:
    def test_chromadb_est_purge(self, tmp_path):
        _, gestes = _purger(tmp_path)
        assert "chroma delete_collection rag_documents" in gestes

    def test_le_bucket_dobjets_est_purge(self, tmp_path):
        # main() purge aussi le bucket : ce test echoue si le bloc du stockage
        # objet disparait.
        _, gestes = _purger(tmp_path)
        assert "stockage list_objects recursive=True" in gestes
        assert [geste for geste in gestes if geste.startswith("stockage remove_object")] == [
            "stockage remove_object images/livre/1.png",
            "stockage remove_object images/livre/2.png",
        ]

    def test_la_purge_annonce_l_adresse_du_stockage_qu_elle_vide(self, tmp_path):
        """L'en-tete du stockage objet affiche l'adresse visee, avant le compte.

        Un nom de produit ecrit dans le code s'afficherait a l'identique quel
        que soit le serveur vide.
        """
        processus, _ = _purger(tmp_path, reglages={"S3_ENDPOINT": "un-autre-stockage:9999"})

        assert processus.returncode == 0, processus.stdout + processus.stderr
        assert "--- Stockage objet (un-autre-stockage:9999) ---" in processus.stdout

    def test_sans_s3_endpoint_la_purge_ne_commence_pas(self, tmp_path):
        """Sans `S3_ENDPOINT`, la purge echoue avant toute suppression.

        Cette commande vide le bucket designe. Une adresse par defaut lui ferait
        vider un serveur non choisi en rendant compte d'une purge reussie. Sans
        valeur par defaut, la construction des reglages leve avant que le
        premier client ne soit construit.
        """
        processus, gestes = _purger(
            tmp_path, reglages={"S3_ENDPOINT": ""}, attendre_des_gestes=False
        )

        assert processus.returncode != 0
        assert "S3_ENDPOINT" in processus.stdout + processus.stderr
        assert gestes == [], f"un store a ete touche malgre l'absence d'adresse : {gestes}"

    def test_le_space_nebula_est_supprime(self, tmp_path):
        _, gestes = _purger(tmp_path)
        assert any("DROP SPACE IF EXISTS" in geste for geste in gestes)

    def test_une_purge_complete_sort_en_zero(self, tmp_path):
        processus, _ = _purger(tmp_path)
        assert processus.returncode == 0
        assert "PURGE INCOMPLETE" not in processus.stdout


class TestUnePurgePartielleEchoue:
    """Une purge partielle sort en 1.

    Le code de sortie est ce qu'un `&&` lit dans une procedure de purge. Une
    purge partielle qui sortirait en 0 ferait reingerer par-dessus des restes.
    """

    def test_un_bucket_dobjets_en_echec_fait_sortir_en_un(self, tmp_path):
        # Le store le plus recemment ajoute a main(), donc celui dont
        # l'oubli dans la liste des echecs se verrait le moins.
        processus, _ = _purger(tmp_path, echecs="stockage")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : Stockage objet" in processus.stdout

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
        assert "stockage list_objects recursive=True" in gestes
        assert any("DROP SPACE IF EXISTS" in geste for geste in gestes)

    def test_tous_les_stores_en_echec_sont_nommes(self, tmp_path):
        processus, _ = _purger(tmp_path, echecs="chroma,stockage,nebula")
        assert processus.returncode == 1
        assert "PURGE INCOMPLETE : ChromaDB, Stockage objet, NebulaGraph" in processus.stdout


class TestLeHtmlNettoyeEstPurgeAussi:
    """La purge du HTML nettoye (registre 4.28.b, 4.33.a).

    L'asset `cleaned_html` reecrit sa destination a chaque materialisation
    (`mesure` le 22 septembre 2026, teste dans
    `tests/unit/test_factory.py::TestCeQueLaPurgeDuNettoyeRetireVRAIMENT`).
    Cette purge retire donc surtout les copies nettoyees des documents sortis
    du corpus : rien d'autre ne les efface, et apres la purge du bucket elles
    pointent des objets supprimes.

    Les tests ci-dessous portent sur ce que la purge retire, ce qu'elle compte
    et ce qu'elle ne touche pas.
    """

    def test_le_repertoire_nettoye_est_supprime(self, tmp_path):
        nettoye = tmp_path / ".cleaned"
        (nettoye / "htms" / "livre").mkdir(parents=True)
        (nettoye / "htms" / "livre" / "chapitre.html").write_text("<html/>", encoding="utf-8")

        supprimes = purge_cleaned(nettoye, tmp_path)

        assert supprimes == 1
        assert not nettoye.exists()

    def test_un_repertoire_absent_ne_leve_pas_et_ne_compte_rien(self, tmp_path):
        """Le cas nominal d'une pile neuve : il n'y a rien a purger.

        La cible est celle que `main()` compose, `racine/CLEANED_SUBDIR`, et
        non un nom quelconque.
        """
        assert purge_cleaned(tmp_path / CLEANED_SUBDIR, tmp_path) == 0

    def test_le_compte_est_celui_des_fichiers_reellement_retires(self, tmp_path):
        """La purge rend le nombre de fichiers retires."""
        nettoye = tmp_path / ".cleaned"
        (nettoye / "a").mkdir(parents=True)
        for i in range(5):
            (nettoye / "a" / f"c{i}.html").write_text("<html/>", encoding="utf-8")

        assert purge_cleaned(nettoye, tmp_path) == 5

    def test_le_corpus_source_n_est_jamais_touche(self, tmp_path):
        """La purge ne touche pas au corpus voisin.

        `Datas/.cleaned/` est un sous-repertoire de `Datas/`, qui porte le corpus
        versionne (son contenu entre dans le calcul d'`element_id`, contrat,
        exigences 2 et 3). `rmtree` ne lit pas `.gitignore` : git ne protegerait
        rien.
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
    """`main()` appelle effectivement la purge du HTML nettoye."""

    def test_main_annonce_le_html_nettoye_purge(self, tmp_path):
        acheve, _ = _purger(tmp_path)

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "HTML nettoye" in acheve.stdout, acheve.stdout

    def test_un_echec_de_purge_du_html_fait_sortir_en_un(self, tmp_path):
        """Un echec de la purge du HTML nettoye sort en 1, comme les autres.

        L'echec est reproduit par un repertoire parent en lecture seule. Le cas
        existe sur ce depot : `Datas/database/postgres`, ecrit par Docker en
        `root`, ne se copie pas (mandat §7.1).
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
        """`main()` vise `.cleaned`, et non la racine des donnees.

        Si `main()` visait `Path(source_dir)`, il supprimerait `Datas/`, donc le
        corpus versionne ; `rmtree` ne lit pas `.gitignore`. Les tests de
        `purge_cleaned` fournissent eux-memes le chemin : celui-ci observe le
        chemin que `main()` calcule.
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
        """`main()` execute la purge, il ne se contente pas de l'annoncer.

        Sans ce test, un `print` sans appel passerait le premier test de cette
        classe.
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
    """Le containment de la purge : une cible hors de la racine est refusee.

    Sous `Datas/` vivent le corpus versionne (son contenu entre dans le calcul
    d'`element_id`, contrat, exigences 2 et 3) et `Datas/database/`, les bind
    mounts de ChromaDB, Nebula, du stockage objet et de Postgres. `rmtree` ne
    lit pas `.gitignore`.

    Le refus est un echec, compte dans `echecs` (code de sortie 1), sans repli
    sur une cible par defaut.

    `CLEANED_SUBDIR` n'est plus un reglage (voir
    `TestLeSousRepertoireNettoyeNEstPlusUnReglage`) ; `source_dir` en reste un.
    Les cas dangereux sont donc testes par la racine via `main()`, et
    directement sur `purge_cleaned` pour ceux qu'aucun `source_dir` ne produit.

    Tous ces tests portent sur un faux corpus sous `tmp_path` : par defaut,
    le harnais fait pointer `SOURCE_DIR` vers un chemin inexistant sous
    `tmp_path`.
    """

    def test_un_cleaned_qui_sort_de_la_racine_fait_sortir_en_un(self, tmp_path: Path) -> None:
        """Un `.cleaned` lie vers l'exterieur de la racine est refuse par `main()`.

        Un lien symbolique passerait une comparaison textuelle, et `rmtree`
        suivrait le lien. La comparaison porte sur les chemins resolus des deux
        cotes. Un tel lien peut apparaitre sur ce depot, ou des stores ont deja
        ete deplaces et remplaces (registre 4.26).
        """
        datas = _faux_corpus(tmp_path)
        # Le `.cleaned` du faux corpus est un vrai repertoire : on le remplace
        # par un lien vers un repertoire de controle situe hors de la racine.
        shutil.rmtree(datas / ".cleaned")
        dehors = tmp_path / "dehors"
        dehors.mkdir()
        (dehors / "temoin.txt").write_text("hors de la racine", encoding="utf-8")
        (datas / ".cleaned").symlink_to(dehors, target_is_directory=True)

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        _corpus_intact(datas)
        assert (dehors / "temoin.txt").exists(), (
            "le lien a ete suivi et la cible HORS de la racine detruite"
        )
        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "PURGE INCOMPLETE" in acheve.stdout, acheve.stdout
        assert "HTML nettoye" in acheve.stdout.split("PURGE INCOMPLETE")[1], acheve.stdout

    def test_le_refus_nomme_la_cible_la_racine_et_le_reglage_qui_reste(
        self, tmp_path: Path
    ) -> None:
        """Le message de refus dit quoi corriger.

        Il nomme ce qui a ete vise, ce dans quoi cela devait tenir, et le
        reglage qui en decide : `SOURCE_DIR` (et non plus `CLEANED_SUBDIR`).
        """
        datas = _faux_corpus(tmp_path)
        shutil.rmtree(datas / ".cleaned")
        dehors = tmp_path / "dehors"
        dehors.mkdir()
        (datas / ".cleaned").symlink_to(dehors, target_is_directory=True)

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        assert "SOURCE_DIR" in acheve.stdout, acheve.stdout
        assert "CLEANED_SUBDIR" not in acheve.stdout, (
            "le refus nomme un reglage qui n'existe plus : l'operateur "
            f"chercherait une variable absente de .env.example\n{acheve.stdout}"
        )
        assert str(datas) in acheve.stdout, acheve.stdout

    def test_les_trois_stores_sont_purges_quand_meme(self, tmp_path: Path) -> None:
        """Le refus n'empeche pas la purge des trois stores.

        S'il l'empechait, les stores resteraient peuples alors qu'on les croit
        vides.
        """
        datas = _faux_corpus(tmp_path)
        shutil.rmtree(datas / ".cleaned")
        dehors = tmp_path / "dehors"
        dehors.mkdir()
        (datas / ".cleaned").symlink_to(dehors, target_is_directory=True)

        acheve, gestes = _purger(tmp_path, source_dir=str(datas))

        assert acheve.returncode == 1
        assert any("chroma delete_collection" in geste for geste in gestes), gestes
        assert any("stockage remove_object" in geste for geste in gestes), gestes
        assert any("DROP SPACE" in geste for geste in gestes), gestes

    def test_le_sous_repertoire_livre_est_accepte_et_purge(self, tmp_path: Path) -> None:
        """La cible nominale est acceptee et retiree.

        Un controle qui refuserait aussi `.cleaned` ferait sortir `wipe_stores`
        en 1 a chaque purge, et les tests de refus passeraient quand meme.
        """
        datas = _faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas))

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert not (datas / ".cleaned").exists(), "le HTML nettoye devait etre purge"
        _corpus_intact(datas)


class TestLeContainmentEstDecideParPurgeCleaned:
    """La decision vit dans `purge_cleaned`, pas dans son appelant.

    `purge_cleaned` est publique : un controle pose dans `main()` ne
    protegerait pas les autres appelants. Ces tests appellent donc
    `purge_cleaned` directement.
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
        """La comparaison porte sur le chemin resolu.

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

    def test_un_descendant_du_nettoye_est_accepte(self, tmp_path: Path) -> None:
        """Un descendant du repertoire nettoye est accepte.

        `Datas/.cleaned/htms` est dans le repertoire nettoye. Un controle qui
        refuserait toute cible rendrait `wipe_stores` inutilisable, et les deux
        tests suivants passeraient quand meme.
        """
        datas = tmp_path / "datas"
        cible = datas / CLEANED_SUBDIR / "htms" / "livre"
        cible.mkdir(parents=True)
        (cible / "f.html").write_text("<html/>", encoding="utf-8")

        assert purge_cleaned(cible, datas) == 1
        assert not cible.exists()
        assert (datas / CLEANED_SUBDIR / "htms").exists(), "seule la cible devait partir"

    @pytest.mark.parametrize(
        ("sous_chemin", "ce_qu_elle_emportait"),
        [
            ("htms", "24 des 25 fichiers du corpus versionne"),
            ("database", "les cinq stores de Datas/database/"),
            ("pdfs", "le PDF du corpus"),
        ],
    )
    def test_une_cible_contenue_mais_hors_du_nettoye_est_refusee(
        self, tmp_path: Path, sous_chemin: str, ce_qu_elle_emportait: str
    ) -> None:
        """Une cible contenue dans la racine mais hors du nettoye est refusee.

        Ces trois cibles passent le containment (registre 4.29.a : le
        1er septembre 2026, `CLEANED_SUBDIR=htms` faisait detruire le corpus).
        Le test verifie aussi que le contenu vise est intact apres le refus :
        un refus leve apres le `rmtree` passerait sinon sur l'exception.
        """
        datas = tmp_path / "datas"
        cible = datas / sous_chemin
        cible.mkdir(parents=True)
        temoin = cible / "precieux.bin"
        temoin.write_text("le corpus", encoding="utf-8")

        with pytest.raises(CibleHorsDuNettoyeError):
            purge_cleaned(cible, datas)

        assert temoin.exists(), f"{sous_chemin} a ete detruit : {ce_qu_elle_emportait}"
        assert temoin.read_text(encoding="utf-8") == "le corpus"

    def test_le_refus_hors_du_nettoye_nomme_la_cible_et_le_nettoye(self, tmp_path: Path) -> None:
        """Le message nomme ce qui a ete vise et ce qui etait attendu.

        Le premier refus designe `SOURCE_DIR` ; celui-ci designe l'argument.
        """
        datas = tmp_path / "datas"
        (datas / "htms").mkdir(parents=True)

        with pytest.raises(CibleHorsDuNettoyeError) as refus:
            purge_cleaned(datas / "htms", datas)

        message = str(refus.value)
        assert str(datas / "htms") in message, message
        assert str(datas / CLEANED_SUBDIR) in message, message

    def test_un_lien_du_nettoye_vers_le_corpus_est_refuse(self, tmp_path: Path) -> None:
        """Un `.cleaned` lie vers `Datas/htms` est refuse.

        La cible resolue est contenue dans la racine, donc le containment
        l'accepte. Si `cleaned_root(racine)` etait resolu lui aussi, la
        comparaison opposerait `Datas/htms` a `Datas/htms` et accepterait la
        cible. La forme nominale est donc comparee non resolue. Le test verifie
        le contenu du corpus, pas seulement l'exception.
        """
        datas = tmp_path / "datas"
        corpus = datas / "htms"
        corpus.mkdir(parents=True)
        temoin = corpus / "chapitre.html"
        temoin.write_text("<html>le corpus</html>", encoding="utf-8")
        (datas / CLEANED_SUBDIR).symlink_to(corpus, target_is_directory=True)

        with pytest.raises(CibleHorsDuNettoyeError):
            purge_cleaned(datas / CLEANED_SUBDIR, datas)

        assert temoin.exists(), "le lien a ete suivi et le corpus detruit"
        assert temoin.read_text(encoding="utf-8") == "<html>le corpus</html>"


class TestLeSousRepertoireNettoyeNEstPlusUnReglage:
    """`CLEANED_SUBDIR` n'est plus un reglage (registre 4.29.a).

    Le containment refuse `""`, `"."`, `".."` et un chemin absolu, mais une
    valeur contenue et fausse le passait : `mesure` le 1er septembre 2026 sur
    un faux corpus, `CLEANED_SUBDIR=htms` faisait detruire `Datas/htms/`, et
    `=database` les stores. Le sous-repertoire est donc devenu une constante.
    Ces tests posent ces valeurs dans l'environnement et verifient qu'elles
    sont sans effet.
    """

    @pytest.mark.parametrize(
        ("valeur", "ce_qu_elle_faisait_avant"),
        [
            ("htms", "detruisait 24 des 25 fichiers du corpus versionne"),
            ("database", "detruisait les cinq stores de Datas/database/"),
            ("", "visait la racine elle-meme : Path(base) / '' vaut base"),
            (".", "visait la racine elle-meme, apres resolution"),
            ("..", "visait le PARENT de la racine"),
            ("/", "un chemin ABSOLU remplacait la base entiere"),
            (".propre", "deplacait le depot du HTML nettoye, et avec lui les element_id"),
        ],
    )
    def test_la_variable_d_environnement_est_inerte(
        self, tmp_path: Path, valeur: str, ce_qu_elle_faisait_avant: str
    ) -> None:
        """Aucune de ces valeurs ne change la cible du `rmtree`."""
        datas = _faux_corpus(tmp_path)

        acheve, _ = _purger(tmp_path, source_dir=str(datas), cleaned_subdir=valeur)

        _corpus_intact(datas)
        assert acheve.returncode == 0, (
            f"CLEANED_SUBDIR={valeur!r} ({ce_qu_elle_faisait_avant}) n'est plus "
            f"un reglage : la purge devait se derouler normalement\n"
            f"{acheve.stdout}{acheve.stderr}"
        )
        assert not (datas / ".cleaned").exists(), (
            f"CLEANED_SUBDIR={valeur!r} a detourne la purge : le sous-repertoire "
            f"livre n'a pas ete retire\n{acheve.stdout}"
        )
        assert (datas / "htms" / "livre" / "chapitre.html").exists()
        assert (datas / "database" / "chroma" / "index.bin").exists()

    def test_le_champ_a_disparu_des_reglages_du_pipeline(self) -> None:
        """Les reglages ne portent plus de champ `cleaned_subdir`.

        Un champ renomme ou une variable d'environnement renommee ferait
        passer le test precedent sans que le reglage ait disparu.
        """
        from src.pipeline.settings import PipelineSettings

        assert "cleaned_subdir" not in PipelineSettings.model_fields, (
            "`cleaned_subdir` est revenu dans PipelineSettings. Personne ne "
            "configure ou l'etape de nettoyage ecrit : c'est un detail "
            "d'implementation, et c'etait le seul chemin par lequel un outil de "
            "ce depot pouvait viser le corpus versionne (registre 4.29.a)"
        )
