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

from src.index_report import FenetreMesuree, compter_les_documents, mesurer_la_fenetre


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


class TestCompterLesDocuments:
    """L'IDENTITE D'UN DOCUMENT EST `source_path`, JAMAIS `filename` SEUL.

    `index_report:116` comptait `{m.get("filename")}` et rendait **22** alors que
    le graphe porte **23** documents. `mesure` le 31 aout 2026 sur l'index
    complet : 22 `filename` distincts contre 23 `source_path`, et la seule
    collision est `Preface` — le corpus contient deux `Preface.html`, un par
    ouvrage.

    C'est EXACTEMENT le cas d'ecole que l'exigence 3 du contrat cite comme sa
    preuve, dans un fichier que le lot 3 a reecrit. Et deux lignes plus bas, le
    bloc « Profondeur de hierarchie » du meme fichier utilisait correctement
    `source_path` : les deux identites cohabitaient dans la meme fonction.
    """

    # Le cas reel, reduit a l'essentiel : deux ouvrages, un `Preface` chacun.
    LES_DEUX_PREFACES = [
        {
            "filename": "Preface",
            "source_path": ".cleaned/htms/MLOps with Databricks/Preface.html",
        },
        {
            "filename": "Preface",
            "source_path": (
                ".cleaned/htms/Practical MLflow for Generative AI on Databricks/Preface.html"
            ),
        },
    ]

    def test_two_prefaces_from_two_books_count_as_two_documents(self):
        assert compter_les_documents(self.LES_DEUX_PREFACES) == 2

    def test_counting_by_filename_would_have_said_one(self):
        """LE TEMOIN, et c'est lui le resultat.

        Sans lui, le test ci-dessus se lirait comme un truisme. Il montre que les
        deux comptes DIVERGENT sur ce cas precis, et de combien.
        """
        assert len({m["filename"] for m in self.LES_DEUX_PREFACES}) == 1

    def test_two_chunks_of_the_same_document_count_once(self):
        metas = [{"filename": "a", "source_path": "livre/a.html"}] * 5
        assert compter_les_documents(metas) == 1

    def test_a_missing_source_path_does_not_crash_and_counts_as_one_unknown(self):
        """Un chunk sans chemin est deja une anomalie de `verify_contract`.

        Ce compteur ne doit ni lever ni compter chaque inconnu separement : il
        rendrait un nombre de documents superieur au reel, ce qui est le defaut
        inverse et tout aussi silencieux.
        """
        metas = [{"filename": "a"}, {"filename": "b"}, {"source_path": "livre/c.html"}]
        assert compter_les_documents(metas) == 2

    def test_an_empty_index_counts_zero(self):
        assert compter_les_documents([]) == 0

    def test_the_same_stem_in_two_books_is_the_general_case_not_just_preface(self):
        """`Index.html` est ecarte par le capteur, `Preface.html` non — mais la
        propriete ne depend pas du nom : elle vaut pour tout fichier homonyme."""
        metas = [
            {"filename": "1. Introduction", "source_path": "htms/livre A/1. Introduction.html"},
            {"filename": "1. Introduction", "source_path": "htms/livre B/1. Introduction.html"},
        ]
        assert compter_les_documents(metas) == 2
