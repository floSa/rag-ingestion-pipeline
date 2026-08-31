"""Tests de l'instrument de troncature.

Un test « l'instrument rend un nombre » est vert des DEUX cotes du defaut :
tokeniser le texte stocke rend un nombre, tokeniser le texte encode aussi. Ces
tests font donc regresser le CHOIX du texte, en construisant des chunks que le
seul prefixe de titre fait franchir la fenetre — le cas exact que l'instrument
ne comptait pas (registre 3.4).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.index_report import FenetreMesuree, mesurer_la_fenetre


def compter_les_mots(texte: str) -> int:
    """Tokeniseur de substitution : un mot, un token.

    Le vrai tokeniseur demande le modele, donc torch. Ce qui est mesure ici
    n'est pas la tokenisation — c'est QUEL TEXTE lui est donne.
    """
    return len(texte.split())


class TestMesurerLaFenetre:
    # Deux chunks de 3 mots. Le premier porte un titre de 3 mots : prefixe, il
    # en fait 6 et franchit une fenetre de 5. Le second n'a pas de titre.
    DOCUMENTS = ["alpha beta gamma", "delta epsilon zeta"]
    METADATAS = [{"section_title": "un titre long"}, {"section_title": ""}]
    LIMITE = 5

    def test_the_title_prefix_is_counted(self):
        """Sans le prefixe, ce chunk ne compte pas : c'est tout le defaut."""
        mesure = mesurer_la_fenetre(
            self.DOCUMENTS, self.METADATAS, compter_les_mots, self.LIMITE, True
        )
        assert mesure.depassements == 1
        assert mesure.maximum == 6

    def test_the_stored_text_alone_would_count_nothing(self):
        """Le contre-cas, qui prouve que le test distingue les deux textes.

        Si la mesure retombait sur le texte stocke, elle rendrait ces
        chiffres-ci — et le test ci-dessus rougirait.
        """
        mesure = mesurer_la_fenetre(
            self.DOCUMENTS, self.METADATAS, compter_les_mots, self.LIMITE, False
        )
        assert mesure.depassements == 0
        assert mesure.maximum == 3

    def test_the_measure_says_which_text_it_measured(self):
        """Le reglage a deux positions et le rapport doit dire laquelle il lit."""
        assert mesurer_la_fenetre([""], [{}], compter_les_mots, 5, True).prefixe_du_titre
        assert not mesurer_la_fenetre([""], [{}], compter_les_mots, 5, False).prefixe_du_titre

    def test_a_chunk_exactly_at_the_limit_is_not_truncated(self):
        """La borne est stricte : le modele tronque AU-DELA de sa fenetre."""
        mesure = mesurer_la_fenetre(["un deux trois quatre cinq"], [{}], compter_les_mots, 5, True)
        assert mesure.depassements == 0

    def test_the_totals_describe_every_chunk(self):
        mesure = mesurer_la_fenetre(
            self.DOCUMENTS, self.METADATAS, compter_les_mots, self.LIMITE, True
        )
        assert mesure.total == 2
        assert isinstance(mesure, FenetreMesuree)

    def test_a_misaligned_input_is_refused_and_not_measured_wrong(self):
        with pytest.raises(ValueError):
            mesurer_la_fenetre(["a", "b"], [{}], compter_les_mots, 5, True)


class TestLeModuleResteImportableSansModele:
    """Ce qu'un test n'importe pas, il ne teste pas.

    L'instrument n'etait garde par aucun test parce qu'il importait `chromadb`
    et le modele d'embedding au niveau du module : personne ne pouvait
    l'importer sans l'image d'extraction, qui pese 10,4 Go. Ces dependances sont
    desormais chargees dans ``main``.

    La verification passe par un SOUS-PROCESSUS : mesurer ``sys.modules`` dans
    l'interpreteur courant rendrait le verdict dependant de ce qu'un autre test
    a importe avant.
    """

    SONDE = (
        "import sys, src.index_report;"
        "sys.exit(1 if {'chromadb', 'sentence_transformers'} & set(sys.modules) else 0)"
    )

    def test_importing_the_module_pulls_no_heavy_dependency(self):
        acheve = subprocess.run(
            [sys.executable, "-c", self.SONDE],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
