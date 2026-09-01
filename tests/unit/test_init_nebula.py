"""L'amorcage du cluster ne doit rien faire tant qu'on ne l'appelle pas, et il
doit declarer le meme space que le service.

Deux defauts fermes ici, tous deux de la famille « le montage a l'air du bon » :

1. **tout le corps etait au niveau du MODULE.** Un `import src.init_nebula`
   enregistrait un hote et creait un space. C'est mot pour mot le defaut 4.5 de
   `verify_data`, sur un cinquieme module ;
2. **il declarait `vid_type=FIXED_STRING(64)` en dur**, la ou le service declare
   `FIXED_STRING(VID_MAX_BYTES)`, soit 256. Les deux passent par
   `CREATE SPACE IF NOT EXISTS`, donc le premier a tourner gagne — et ce
   script-ci prescrit lui-meme d'etre lance avant le service. `mesure` le
   1er septembre 2026 sur un space jetable en `FIXED_STRING(64)` : l'insertion
   des deux documents reels du corpus est REFUSEE par le graphd, leurs
   identifiants faisant 65 et 67 octets. Nebula ne sait pas modifier un
   `vid_type` : la reparation coute une purge complete.

La verification passe par un SOUS-PROCESSUS, comme `test_verify_data.py` et pour
les memes deux raisons : `nebula3` n'est pas dans le venv du depot, et boucher
`sys.modules` dans l'interpreteur courant laisserait les bouchons derriere soi,
rendant l'ordre des tests significatif.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.docling_service.ngql import VID_MAX_BYTES

RACINE = Path(__file__).resolve().parents[2]

# Bouchon `nebula3` : il IMPRIME les requetes qu'il recoit, ce qui permet
# d'asserter ce que le script emet reellement plutot que de relire son source.
BOUCHONS = {
    "nebula3/__init__.py": "",
    "nebula3/Config.py": "class Config:\n    pass\n",
    "nebula3/gclient/__init__.py": "",
    "nebula3/gclient/net.py": """
import os


class _Reponse:
    def is_succeeded(self):
        return True

    def error_msg(self):
        return ""

    def rows(self):
        return []


class _Session:
    def execute(self, requete):
        print(f"REQUETE={requete}")
        return _Reponse()

    def release(self):
        pass


class ConnectionPool:
    def init(self, adresses, config):
        print(f"ADRESSES={adresses}")
        return os.environ.get("IN_INIT_ECHOUE") != "1"

    def get_session(self, utilisateur, mot_de_passe):
        print(f"IDENTIFIANTS={utilisateur}/{mot_de_passe}")
        return _Session()

    def close(self):
        pass
""",
}


def _amorcer(tmp_path: Path, reglages: dict[str, str] | None = None):
    """Lance `python -m src.init_nebula` pour de bon, `nebula3` bouchonne.

    Args:
        tmp_path: Repertoire de travail du sous-processus. Pas de `.env` dedans,
            donc les reglages sont ceux du code et non ceux du poste.
        reglages: Variables d'environnement a poser. Celles du graphe sont
            d'abord RETIREES de l'environnement herite : sans ce retrait, un
            poste qui les declare rendrait les temoins verts ou rouges selon la
            machine.

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
    for cle in ("NEBULA_HOST", "NEBULA_PORT", "NEBULA_USER", "NEBULA_PASSWORD"):
        environnement.pop(cle, None)
    environnement.pop("IN_INIT_ECHOUE", None)
    # Les pauses d'amorcage attendent le heartbeat du storaged. Il n'y a aucun
    # storaged ici : les annuler ne retire rien a ce qui est asserte, et les
    # payer coutait 76 s a une suite qui tient en 14 (`mesure`). C'est un
    # reglage du script et non une valeur en dur, ce qui est precisement ce qui
    # rend ce test possible.
    environnement["NEBULA_AMORCAGE_PAUSE_SECONDS"] = "0"
    environnement.update(reglages or {})
    return subprocess.run(
        [sys.executable, "-m", "src.init_nebula"],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestLImportNeFaitRien:
    """Le corps vivait au niveau du module : un import creait un space."""

    def test_importer_le_module_ne_touche_pas_au_graphe(self):
        acheve = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, src.init_nebula;sys.exit(1 if 'nebula3' in sys.modules else 0)",
            ],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    def test_importer_le_module_n_affiche_rien(self):
        """Le module affichait « Adding hosts... » a l'import."""
        acheve = subprocess.run(
            [sys.executable, "-c", "import src.init_nebula"],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.stdout == "", acheve.stdout
        assert acheve.returncode == 0, acheve.stderr


class TestLeSpaceCreeEstCeluiDuService:
    """Le garde du `vid_type` : un seul site, et il tient le corpus."""

    def test_le_create_space_declare_la_taille_du_code(self, tmp_path):
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        creations = [ligne for ligne in acheve.stdout.splitlines() if "CREATE SPACE" in ligne]
        assert creations, f"aucun CREATE SPACE emis parmi {acheve.stdout!r}"
        assert f"FIXED_STRING({VID_MAX_BYTES})" in creations[0]

    def test_le_create_space_ne_declare_plus_64(self, tmp_path):
        """LE TEMOIN, et c'est lui qui ferme le defaut mesure.

        Sans lui, le test precedent resterait vert si `VID_MAX_BYTES` retombait
        a 64 — et c'est precisement la valeur que ce script ecrivait en dur.
        """
        acheve = _amorcer(tmp_path)
        creations = [ligne for ligne in acheve.stdout.splitlines() if "CREATE SPACE" in ligne]
        assert creations, f"aucun CREATE SPACE emis parmi {acheve.stdout!r}"
        assert "FIXED_STRING(64)" not in creations[0], (
            "un space en FIXED_STRING(64) refuse les deux documents reels du "
            "corpus, dont les identifiants font 65 et 67 octets, et Nebula ne "
            "sait pas modifier un vid_type : la reparation coute une purge"
        )


class TestLesReglagesDecidentDeLaConnexion:
    """Le script codait `("graphd", 9669)` et `("root", "nebula")` en dur."""

    def test_l_adresse_et_les_identifiants_viennent_du_env(self, tmp_path):
        acheve = _amorcer(
            tmp_path,
            reglages={
                "NEBULA_HOST": "autre_graphd",
                "NEBULA_PORT": "9670",
                "NEBULA_USER": "amorceur",
                "NEBULA_PASSWORD": "phrase",  # pragma: allowlist secret
            },
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "ADRESSES=[('autre_graphd', 9670)]" in acheve.stdout
        assert "IDENTIFIANTS=amorceur/phrase" in acheve.stdout

    def test_sans_variables_les_defauts_de_la_pile_valent(self, tmp_path):
        """LE TEMOIN : de mauvais defauts casseraient tout poste au `.env` muet."""
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "ADRESSES=[('graphd', 9669)]" in acheve.stdout
        assert "IDENTIFIANTS=root/nebula" in acheve.stdout


class TestLeCodeDeSortieDitSiLaConnexionAEuLieu:
    """Le script faisait `exit(1)` depuis le niveau module, jamais asserte."""

    def test_une_connexion_refusee_sort_en_un(self, tmp_path):
        acheve = _amorcer(tmp_path, reglages={"IN_INIT_ECHOUE": "1"})
        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "Connexion impossible" in acheve.stdout
        assert "CREATE SPACE" not in acheve.stdout

    def test_une_connexion_ouverte_sort_en_zero(self, tmp_path):
        """LE TEMOIN du precedent : sans lui, un script qui sort toujours en 1
        passerait."""
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
