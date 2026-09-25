"""LE REJEU NE TOUCHE PLUS L'ARBRE DE TRAVAIL, ET SON VERDICT DISTINGUE ROUGE ET CASSE.

Deux defauts mesures par le troisieme audit du lot 11 (registre 4.39.c) :

- le rejeu mutait les fichiers de PRODUCTION en place. `extraction.py` restait
  mute 0,66 s sur disque a chaque passage ; apres un `SIGKILL` il restait mute,
  et le lancement suivant le prenait pour l'origine. Le controle final par
  `git diff` comparait l'arbre a l'INDEX, donc laissait passer un residu indexe
  et rougissait devant une modification legitime non commitee ;
- une mutation qui CASSAIT LA SYNTAXE passait pour « ROUGE » : un rc non nul
  suffisait au verdict, et un fichier illisible ressemble a un garde qui voit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE_DEPOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/rejouer-les-mutations.py"


def _module():
    """Le script se charge par son CHEMIN : son nom porte des tirets."""
    specification = importlib.util.spec_from_file_location(
        "rejouer_les_mutations", RACINE_DEPOT / SCRIPT
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _lancer(*arguments):
    return subprocess.run(
        [sys.executable, SCRIPT, *arguments],
        capture_output=True,
        text=True,
        cwd=RACINE_DEPOT,
        env={"PYTHONPATH": str(RACINE_DEPOT), "PATH": "/usr/bin:/bin"},
    )


class TestLeVerdict:
    """`failed` dans la derniere ligne, `rc == 1`, et aucun `error`. Les trois."""

    @pytest.mark.parametrize(
        ("code", "derniere"),
        [
            (2, "1 error in 0.10s"),
            (1, "1 error in 0.10s"),
            # LE CAS QUI TIENT LA CLAUSE `error` A ELLE SEULE : un rc=1 avec un
            # `failed` — tout ce que les deux autres clauses demandent — mais une
            # collecte cassee a cote. C'est la mutation de syntaxe.
            (1, "1 failed, 1 error in 0.10s"),
            (0, "1 passed in 0.10s"),
            (1, "82 deselected in 0.02s"),
            # LE CAS QUI TIENT LA CLAUSE `failed` A ELLE SEULE : rc=1, ni erreur
            # ni deselection, mais rien n'a rougi.
            (1, "5 passed in 0.10s"),
            (4, "1 failed in 0.10s"),
            (2, "no tests ran in 0.01s"),
        ],
    )
    def test_ce_qui_n_est_pas_un_echec_de_test_ne_passe_pas_pour_un_rouge(self, code, derniere):
        rougit, detail = _module().verdict(code, derniere)

        assert not rougit, detail

    def test_un_echec_de_test_est_un_rouge(self):
        rougit, detail = _module().verdict(1, "1 failed, 82 deselected in 0.04s")

        assert rougit, detail


class TestUneMutationQuiCasseLaSyntaxeFaitEchouerLeRejeu:
    """LE PIEGE : « une mutation qui casse la syntaxe ressemble a un garde qui voit »."""

    def test_une_entree_qui_casse_la_syntaxe_ne_passe_pas_pour_rouge(self, tmp_path):
        table = tmp_path / "table.json"
        table.write_text(
            json.dumps(
                {
                    "mutations": [
                        {
                            "id": "SYNTAXE",
                            "constat": "cette entree casse la syntaxe au lieu de muter un garde",
                            "fichier": "src/equivalence_des_identifiants.py",
                            "motif": "def empreinte_attendue(dossier: Path) -> str:",
                            "remplacement": "def empreinte_attendue(dossier: Path) -> str",
                            "test": "test_le_site_reel_du_depot_s_authentifie",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        acheve = _lancer("--table", str(table))

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "SURVIT" in acheve.stdout, acheve.stdout
        assert "ERREUR et non echec de test" in acheve.stdout, acheve.stdout


class TestLArbreDeTravailNeBougePas:
    """Le rejeu tourne sur une COPIE JETABLE : la sonde des `mtime` le mesure."""

    def test_un_mtime_qui_bouge_est_vu(self):
        """LA SONDE ELLE-MEME : sans ce test, la rendre aveugle ne rougirait rien."""
        module = _module()

        assert module.ce_qui_a_bouge({"src/a.py": 1.0}, {"src/a.py": 2.0}) == ["src/a.py"]
        assert module.ce_qui_a_bouge({"src/a.py": 1.0}, {}) == ["src/a.py"]
        assert module.ce_qui_a_bouge({}, {"src/b.py": 1.0}) == ["src/b.py"]
        assert module.ce_qui_a_bouge({"src/a.py": 1.0}, {"src/a.py": 1.0}) == []

    def test_un_rejeu_complet_ne_touche_aucun_mtime_de_src_ni_de_tests(self, tmp_path):
        module = _module()
        table = tmp_path / "table.json"
        table.write_text(
            json.dumps(
                {
                    "mutations": [
                        {
                            "id": "M28",
                            "constat": "le format de l'instantane n'etait plus verifie",
                            "fichier": "src/equivalence_des_identifiants.py",
                            "motif": '    if entete.get("format") != FORMAT:',
                            "remplacement": "    if False:",
                            "test": "test_le_format_de_l_instantane_est_verifie",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        avant = module.empreinte_des_mtime(RACINE_DEPOT)

        acheve = _lancer("--table", str(table))

        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert "COPIE JETABLE" in acheve.stdout, acheve.stdout
        assert module.empreinte_des_mtime(RACINE_DEPOT) == avant

    def test_la_sonde_ignore_les_pycache_et_voit_toujours_les_sources(self, tmp_path):
        """LA SONDE NE SURVEILLE QUE LES SOURCES, et c'est le defaut B1 du quatrieme audit.

        Elle faisait `rglob("*")` sans exclusion : elle surveillait les `.pyc`,
        que l'interpreteur REECRIT tout seul. `mesure` de l'audit : un
        `python -c "import src.index_report"` lance depuis l'arbre pendant
        `make mutations` faisait rendre 2 a `make` — le rejeu etait declare
        « ECHEC : le rejeu a TOUCHE l'arbre de travail » alors qu'il n'avait
        rien touche. Un garde qui rougit sur ce qu'il ne garde pas finit par
        etre desarme.

        Les deux sens sont tenus ici : le `.pyc` ne rougit plus, le `.py` rougit
        toujours. Le second est ce qui empeche de « reparer » le premier en
        rendant la sonde aveugle.
        """
        module = _module()
        (tmp_path / "src/__pycache__").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "src/production.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "tests/test_production.py").write_text("y = 2", encoding="utf-8")
        cache = tmp_path / "src/__pycache__/production.cpython-311.pyc"
        cache.write_bytes(b"compile")

        avant = module.empreinte_des_mtime(tmp_path)

        assert set(avant) == {"src/production.py", "tests/test_production.py"}, avant

        # Le `.pyc` REECRIT ne rougit plus...
        os.utime(cache, (1_000_000, 1_000_000))
        assert module.ce_qui_a_bouge(avant, module.empreinte_des_mtime(tmp_path)) == []

        # ...et la source touchee rougit toujours.
        os.utime(tmp_path / "src/production.py", (1_000_000, 1_000_000))
        assert module.ce_qui_a_bouge(avant, module.empreinte_des_mtime(tmp_path)) == [
            "src/production.py"
        ]

    def test_la_copie_porte_ce_dont_les_tests_ont_besoin(self, tmp_path):
        """`documentation/campagnes` en fait partie : le repertoire fixe s'y resout."""
        module = _module()
        copie = tmp_path / "copie"

        module.preparer_la_copie(copie)

        for nom in (*module.COPIES, *module.FICHIERS):
            assert (copie / nom).exists(), nom
        assert (copie / "documentation/campagnes/2026-09-24-instantane-des-identifiants").is_dir()
