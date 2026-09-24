"""LE COMPTAGE DE L'EMIETTEMENT NE VAUT QUE SI SES BORNES SONT EXACTES.

`scripts/campagne/compter-l-emiettement.py` compte, depuis l'instantane
VERSIONNE et lui seul, les elements que Docling rend emiettes — un `<li>` ou un
paragraphe mis en forme decoupe en plusieurs elements, parfois d'un seul
caractere. Chacun coute une place de fenetre et un marqueur au prompt de
l'agent (registre 4.38.g).

**CE QUE CES TESTS TIENNENT** : que les tranches ne se recouvrent ni ne laissent
de trou, que « vide » soit compte A PART des textes courts, et que le script
tourne bel et bien sur l'instantane du depot — pas sur un tmp_path complaisant.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parents[2]
INSTANTANE = RACINE_DEPOT / "documentation/campagnes/2026-09-24-instantane-des-identifiants"
SCRIPT = "scripts/campagne/compter-l-emiettement.py"


def _executer():
    acheve = subprocess.run(
        [sys.executable, SCRIPT, str(INSTANTANE)],
        capture_output=True,
        text=True,
        cwd=RACINE_DEPOT,
        env={"PYTHONPATH": str(RACINE_DEPOT), "PATH": "/usr/bin:/bin"},
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    return acheve.stdout


class TestLesBornesDesTranches:
    def test_les_tranches_ne_se_recouvrent_ni_ne_laissent_de_trou(self):
        """1, 2-5, 6-20, 21-49 doivent couvrir 1 a 49 exactement une fois."""
        import importlib.util

        # Le fichier porte des tirets, comme les autres scripts de campagne : il
        # se charge par son CHEMIN, et non par un nom de module qui n'existe pas.
        specification = importlib.util.spec_from_file_location(
            "compter_l_emiettement", RACINE_DEPOT / SCRIPT
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        couvert = [0] * 50
        for _, bas, haut in module.TRANCHES:
            for longueur in range(bas, haut + 1):
                couvert[longueur] += 1

        assert couvert[0] == 0, "le vide se compte A PART, jamais dans une tranche"
        assert all(n == 1 for n in couvert[1:50]), couvert


class TestLeComptageTourneSurLInstantaneDuDepot:
    def test_le_script_rend_zero_et_nomme_l_empreinte_versionnee(self):
        from src.equivalence_des_identifiants import empreinte_attendue

        sortie = _executer()

        assert empreinte_attendue(INSTANTANE) in sortie

    def test_les_tranches_somment_au_total_des_elements(self):
        """Le piege du comptage : une tranche oubliee ne se voit pas dans un tableau."""
        sortie = _executer()
        rangs = {
            ligne.split()[0]: ligne.split()
            for ligne in sortie.splitlines()
            if ligne and not ligne.startswith((" ", "-", "="))
        }
        total_elements = int(
            next(ligne.split()[1] for ligne in sortie.splitlines() if ligne.startswith("ELEMENTS "))
        )
        vide = int(rangs["vide"][-1])
        moins_de_50 = int(rangs["<"][-1])
        tronques = int(
            next(ligne.rsplit(":", 1)[1] for ligne in sortie.splitlines() if "TRONQUES" in ligne)
        )

        assert vide + moins_de_50 + tronques == total_elements, (vide, moins_de_50, tronques)
