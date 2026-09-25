"""L'amorcage du cluster ne doit rien faire tant qu'on ne l'appelle pas, et il
doit declarer le meme space que le service.

Deux proprietes :

1. importer `src.init_nebula` n'enregistre aucun hote et ne cree aucun space :
   le corps est dans `main` (meme regle que `verify_data`, registre 4.5) ;
2. le space est declare par `create_space_statement()`, donc avec
   `FIXED_STRING(VID_MAX_BYTES)`, comme le service. Le script et le service
   passent tous deux par `CREATE SPACE IF NOT EXISTS`, et le premier a tourner
   fixe le `vid_type`, que Nebula ne sait pas modifier ensuite. `mesure` le
   1er septembre 2026 sur un space jetable en `FIXED_STRING(64)` : le graphd
   refusait deux documents reels du corpus, dont les identifiants font 65 et
   67 octets.

La verification passe par un sous-processus, comme `test_verify_data.py` :
`nebula3` n'est pas dans le venv du depot, et bouchonner `sys.modules` dans
l'interpreteur courant laisserait les bouchons en place pour les tests suivants.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.docling_service.ngql import VID_MAX_BYTES

RACINE = Path(__file__).resolve().parents[2]

# Bouchon `nebula3` : il imprime les requetes qu'il recoit, ce qui permet
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
            d'abord retirees de l'environnement herite, pour que les tests des
            valeurs par defaut ne dependent pas de la machine.

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
    # Les pauses d'amorcage attendent le heartbeat du storaged, absent ici. Les
    # mettre a zero ne change rien a ce qui est verifie, et evite 76 s d'attente
    # (`mesure`). C'est possible parce que la pause est un reglage.
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
    """Importer le module ne cree aucun space."""

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
        """L'import n'affiche rien."""
        acheve = subprocess.run(
            [sys.executable, "-c", "import src.init_nebula"],
            cwd=RACINE,
            capture_output=True,
            text=True,
        )
        assert acheve.stdout == "", acheve.stdout
        assert acheve.returncode == 0, acheve.stderr


class TestLeSpaceCreeEstCeluiDuService:
    """Le `vid_type` vient de `create_space_statement`, et tient le corpus."""

    def test_le_create_space_declare_la_taille_du_code(self, tmp_path):
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        creations = [ligne for ligne in acheve.stdout.splitlines() if "CREATE SPACE" in ligne]
        assert creations, f"aucun CREATE SPACE emis parmi {acheve.stdout!r}"
        assert f"FIXED_STRING({VID_MAX_BYTES})" in creations[0]

    def test_le_create_space_ne_declare_plus_64(self, tmp_path):
        """Le space n'est pas declare en `FIXED_STRING(64)`.

        Le test precedent passerait si `VID_MAX_BYTES` valait 64 ; celui-ci non.
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
    """L'adresse et les identifiants du graphd viennent de l'environnement."""

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
        """Sans variables, les valeurs par defaut habituelles sont utilisees."""
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "ADRESSES=[('graphd', 9669)]" in acheve.stdout
        assert "IDENTIFIANTS=root/nebula" in acheve.stdout


class TestLeCodeDeSortieDitSiLaConnexionAEuLieu:
    """`main()` rend 1 quand la connexion est impossible."""

    def test_une_connexion_refusee_sort_en_un(self, tmp_path):
        acheve = _amorcer(tmp_path, reglages={"IN_INIT_ECHOUE": "1"})
        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "Connexion impossible" in acheve.stdout
        assert "CREATE SPACE" not in acheve.stdout

    def test_une_connexion_ouverte_sort_en_zero(self, tmp_path):
        """Une connexion reussie rend 0 (exclut un script qui sort toujours en 1)."""
        acheve = _amorcer(tmp_path)
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
