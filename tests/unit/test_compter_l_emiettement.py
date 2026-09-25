"""Tests du comptage de l'emiettement (`scripts/campagne/compter-l-emiettement.py`).

Le script compte, depuis l'instantane versionne, les elements que Docling rend
emiettes : un `<li>` ou un paragraphe mis en forme decoupe en plusieurs
elements, parfois d'un seul caractere (registre 4.38.g).

Ces tests verifient que les tranches de longueur ne se recouvrent pas et ne
laissent pas de trou, que « vide » est compte a part des textes courts, que le
format se lit a la derniere extension, et que le script tourne sur
l'instantane reel du depot.
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

        # Le nom du fichier porte des tirets, comme les autres scripts de
        # campagne : il se charge par son chemin, pas par un nom de module.
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
        """Une tranche oubliee ne se verrait pas dans un tableau : elle est testee."""
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


# ─── Sur un jeu d'essai, dont les chiffres sont connus a l'avance ────────────

# Un jeu d'essai, en plus de l'instantane du depot, permet de verifier chaque
# chiffre par format et par label. Il detecte deux mutations (registre 4.39.d,
# A10-a et A10-b) : `lo == 0` devenu `lo <= 1`, qui compterait les elements d'un
# caractere parmi les vides, et `rsplit` devenu `split` sur l'extension, qui se
# tromperait de format des qu'un dossier porte un point.
#
# Propriete du jeu : aucun element vide ni d'un seul caractere dans les PDF. La
# colonne PDF vaut donc 0 sur ces deux lignes, ce que `lo <= 1` casserait. Le
# PDF est range sous `livres/v1.2/`, un dossier avec un point : `split` y lirait
# le format « 2/UN LIVRE.PDF ».
PDF_DU_JEU = "livres/v1.2/Un livre.pdf"
HTML_DU_JEU = "htms/Un ouvrage/1. Un chapitre.html"


def _ligne(cle, rang, texte, label="text"):
    from src.equivalence_des_identifiants import identifiant_par_la_formule

    ligne = {
        "cle": cle,
        "page_no": 1,
        "position_in_page": rang,
        "self_ref": f"#/texts/{rang}",
        "text50": texte,
        "label": label,
    }
    ligne["element_id"] = identifiant_par_la_formule(ligne)
    return ligne


def _jeu_d_essai(dossier):
    """Un instantane a deux documents, dont un PDF sans vide ni caractere seul."""
    from src.equivalence_des_identifiants import Emission, ecrire_l_instantane

    pdf = Emission(
        partition_key=PDF_DU_JEU,
        cle="livres__Un livre",
        lignes=[
            _ligne("livres__Un livre", 0, "ab"),
            _ligne("livres__Un livre", 1, "un texte de plus de vingt caracteres"),
        ],
    )
    html = Emission(
        partition_key=HTML_DU_JEU,
        cle="htms__Un ouvrage__1. Un chapitre",
        lignes=[
            _ligne("htms__Un ouvrage__1. Un chapitre", 0, "", label="list_item"),
            _ligne("htms__Un ouvrage__1. Un chapitre", 1, ")", label="list_item"),
            _ligne("htms__Un ouvrage__1. Un chapitre", 2, "quatre mots ici encore"),
        ],
    )
    ecrire_l_instantane(dossier, [pdf, html], {"date": "jeu d'essai"})
    return dossier


def _table_par_format(sortie):
    """Rend `{tranche: {colonne: valeur}}` du premier tableau, celui par format."""
    lignes = sortie.split("=== TRANCHES DE LONGUEUR, PAR FORMAT")[1].splitlines()
    entetes = lignes[1].split()
    table = {}
    for ligne in lignes[3:]:
        if not ligne.strip():
            break
        champs = ligne.split()
        # « < 50 (1-49) » et « 1 car. » portent des espaces : on recolle par la fin.
        valeurs = champs[-len(entetes) + 1 :]
        table[" ".join(champs[: len(champs) - len(valeurs)])] = dict(
            zip(entetes[1:], valeurs, strict=True)
        )
    return table


class TestLesChiffresParFormatSontTenus:
    def test_aucun_element_vide_ni_d_un_caractere_dans_les_pdf(self, tmp_path):
        """Aucun vide ni caractere seul dans le PDF (detecte la mutation A10-a).

        Le HTML porte un vide ET un element d'un caractere ; le PDF n'a ni l'un
        ni l'autre. Un `lo <= 1` ferait passer la case « vide » du HTML de 1 a 2.
        """
        acheve = subprocess.run(
            [sys.executable, SCRIPT, str(_jeu_d_essai(tmp_path / "jeu"))],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={"PYTHONPATH": str(RACINE_DEPOT), "PATH": "/usr/bin:/bin"},
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr

        table = _table_par_format(acheve.stdout)

        assert table["vide"] == {"HTML": "1", "PDF": "0", "total": "1"}, table
        assert table["1 car."] == {"HTML": "1", "PDF": "0", "total": "1"}, table
        assert table["TOUS"] == {"HTML": "3", "PDF": "2", "total": "5"}, table

    def test_le_format_se_lit_a_la_derniere_extension(self, tmp_path):
        """`livres/v1.2/Un livre.pdf` est un PDF (detecte la mutation A10-b).

        Un `split(".", 1)[-1]` y rendrait « 2/UN LIVRE.PDF ». Les deux colonnes
        du tableau sont donc verifiees par leur nom, pas seulement comptees.
        """
        acheve = subprocess.run(
            [sys.executable, SCRIPT, str(_jeu_d_essai(tmp_path / "jeu"))],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={"PYTHONPATH": str(RACINE_DEPOT), "PATH": "/usr/bin:/bin"},
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr

        assert sorted(_table_par_format(acheve.stdout)["TOUS"]) == ["HTML", "PDF", "total"]
