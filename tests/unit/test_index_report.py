"""Tests de l'instrument de troncature.

Ces tests verifient quel texte est mesure : le texte encode (prefixe du titre
de section), et non le texte stocke. Ils construisent des chunks que le seul
prefixe de titre fait depasser la fenetre du modele (registre 3.4).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.index_report import FenetreMesuree, compter_les_documents, mesurer_la_fenetre

RACINE = Path(__file__).resolve().parents[2]


def compter_les_mots(texte: str) -> int:
    """Tokeniseur de substitution : un mot, un token.

    Le vrai tokeniseur demande le modele, donc torch. Ces tests ne portent pas
    sur la tokenisation, mais sur le texte qui lui est donne.
    """
    return len(texte.split())


class TestMesurerLaFenetre:
    # Deux chunks de 3 mots. Le premier porte un titre de 3 mots : prefixe, il
    # en fait 6 et franchit une fenetre de 5. Le second n'a pas de titre.
    DOCUMENTS = ["alpha beta gamma", "delta epsilon zeta"]
    METADATAS = [{"section_title": "un titre long"}, {"section_title": ""}]
    LIMITE = 5

    def test_the_title_prefix_is_counted(self):
        """Le chunk ne depasse la fenetre qu'avec son prefixe de titre."""
        mesure = mesurer_la_fenetre(
            self.DOCUMENTS, self.METADATAS, compter_les_mots, self.LIMITE, True
        )
        assert mesure.depassements == 1
        assert mesure.maximum == 6

    def test_the_stored_text_alone_would_count_nothing(self):
        """Sans prefixe, la mesure rend les chiffres du texte stocke.

        Ce cas prouve que le test precedent distingue les deux textes.
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
        """La borne est stricte : le modele tronque au-dela de sa fenetre."""
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
    """Le module s'importe sans `chromadb` ni le modele d'embedding.

    Ces dependances sont chargees dans ``main`` : le module est testable sans
    l'image d'extraction (10,4 Go).

    La verification passe par un sous-processus : lire ``sys.modules`` dans
    l'interpreteur courant dependrait de ce que d'autres tests ont importe.
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
    """L'identite d'un document est `source_path`, jamais `filename` seul.

    Exigence 3 du contrat. `mesure` le 31 aout 2026 sur l'index complet : 22
    `filename` distincts contre 23 `source_path`, la seule collision etant
    `Preface` (le corpus contient deux `Preface.html`, un par ouvrage).
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
        """Sur ce cas, les deux comptes different, et de combien."""
        assert len({m["filename"] for m in self.LES_DEUX_PREFACES}) == 1

    def test_two_chunks_of_the_same_document_count_once(self):
        metas = [{"filename": "a", "source_path": "livre/a.html"}] * 5
        assert compter_les_documents(metas) == 1

    def test_a_missing_source_path_does_not_crash_and_counts_as_one_unknown(self):
        """Un chunk sans chemin est deja une anomalie de `verify_contract`.

        Ce compteur ne doit ni lever ni compter chaque inconnu separement, ce
        qui surestimerait le nombre de documents.
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


class TestLaFenetreRapporteeEstCelleDuModele:
    """La fenetre rapportee est lue sur le modele, pas ecrite dans le code.

    La fenetre est le denominateur de toute mesure de troncature. Ce n'est pas
    un reglage : elle est lue a l'execution (`modele.max_seq_length`) et vaut
    128 tokens pour le modele du contrat (registre 6.2).

    `main()` est lance en sous-processus, avec un modele bouchonne dont la
    fenetre vaut une valeur qu'aucun defaut du depot ne porte : une valeur
    ecrite en dur (`128`, `256`) ne peut pas la reproduire.

    `chromadb` et `sentence_transformers` sont bouchonnes par de vrais paquets
    en tete de PYTHONPATH, et non dans `sys.modules`, pour ne pas laisser de
    bouchons aux tests suivants. Meme montage que `test_verify_data.py` et
    `test_wipe_stores.py`.
    """

    FENETRE_BOUCHON = 777

    BOUCHONS = {
        "chromadb.py": """
class _Collection:
    def get(self, include=None):
        return {
            "documents": ["un texte de chunk assez long pour compter"],
            "metadatas": [{"source_path": "htms/Livre/Chapitre.html", "block_size": 1}],
        }


class _Client:
    def get_or_create_collection(self, name):
        return _Collection()


def HttpClient(host=None, port=None):
    return _Client()
""",
        "sentence_transformers.py": """
class _Tokenizer:
    def encode(self, texte, add_special_tokens=True):
        # Un token par caractere : le compte n'a pas d'importance ici, seule la
        # LIMITE rapportee est sous test.
        return list(texte)


class SentenceTransformer:
    def __init__(self, *a, **k):
        self.max_seq_length = FENETRE
        self.tokenizer = _Tokenizer()

    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, *a, **k):
        return []
""",
    }

    def _rapport(self, tmp_path: Path, fenetre: int) -> str:
        """Lance `python -m src.index_report` pour de bon, stores bouchonnes."""
        bouchons = tmp_path / "bouchons"
        bouchons.mkdir()
        for nom, source in self.BOUCHONS.items():
            (bouchons / nom).write_text(source.replace("FENETRE", str(fenetre)), encoding="utf-8")

        environnement = dict(os.environ)
        environnement["PYTHONPATH"] = os.pathsep.join([str(bouchons), str(RACINE)])
        acheve = subprocess.run(
            [sys.executable, "-m", "src.index_report"],
            cwd=tmp_path,
            env=environnement,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        return acheve.stdout

    def test_le_montage_a_bien_atteint_le_rapport(self, tmp_path):
        """Le rapport a bien tourne jusqu'a la section de la fenetre.

        Sans ce test, un sous-processus sorti en 0 sans rien imprimer pourrait
        rendre les assertions suivantes vraies.
        """
        sortie = self._rapport(tmp_path, self.FENETRE_BOUCHON)

        assert "=== Fenetre du modele d'embedding ===" in sortie, sortie
        assert "chunks indexes            : 1" in sortie, sortie

    def test_la_limite_rapportee_est_celle_du_modele(self, tmp_path):
        """La fenetre affichee est celle du modele bouchonne."""
        sortie = self._rapport(tmp_path, self.FENETRE_BOUCHON)

        assert f"limite                    : {self.FENETRE_BOUCHON} tokens" in sortie, (
            f"la fenetre rapportee n'est pas celle du modele "
            f"({self.FENETRE_BOUCHON}) : l'instrument annonce un chiffre "
            f"fabrique, et son taux de troncature avec lui\n{sortie}"
        )

    def test_la_limite_suit_le_modele_et_n_est_pas_figee(self, tmp_path):
        """Deux modeles differents rendent deux fenetres differentes.

        Sans ce test, une limite ecrite en dur a la valeur du bouchon passerait
        le test precedent.
        """
        autre = 512

        sortie = self._rapport(tmp_path, autre)

        assert f"limite                    : {autre} tokens" in sortie, sortie
        assert str(self.FENETRE_BOUCHON) not in sortie, (
            f"la fenetre est figee a {self.FENETRE_BOUCHON} : elle ne suit pas le modele\n{sortie}"
        )
