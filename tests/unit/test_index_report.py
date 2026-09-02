"""Tests de l'instrument de troncature.

Un test « l'instrument rend un nombre » est vert des DEUX cotes du defaut :
tokeniser le texte stocke rend un nombre, tokeniser le texte encode aussi. Ces
tests font donc regresser le CHOIX du texte, en construisant des chunks que le
seul prefixe de titre fait franchir la fenetre — le cas exact que l'instrument
ne comptait pas (registre 3.4).
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


class TestLaFenetreRapporteeEstCelleDuModele:
    """LE GARDE QUI MANQUAIT, et son jumeau documentaire etait FAUX deux fois.

    La fenetre du modele est le denominateur de tout ce que cet instrument dit
    sur la troncature. Elle **n'est pas un reglage** : aucun `settings.py` ne la
    porte, elle est lue au runtime sur le modele du contrat
    (`modele.max_seq_length`). Or `services/chromadb.md` et
    `llm_integration_plan.md` l'annoncaient tous deux a **256** tokens quand elle
    vaut **128** (registre 6.2), et `extraction_donnees.md` batissait un
    pourcentage dessus.

    `mesure` le 2 septembre 2026 sur le code livre par le lot 4 : remplacer
    `limite = int(model.max_seq_length)` par `limite = 256` — le nombre meme qui
    etait faux dans la documentation — laisse la suite ENTIEREMENT VERTE, 834
    tests. L'instrument pouvait donc rapporter une fenetre fabriquee, et le
    chiffre de troncature avec elle, sans que rien ne bronche.

    Le garde asserte **depuis le cote qui produit** : `main()` est lance pour de
    bon en sous-processus, avec un modele bouchonne dont la fenetre vaut une
    valeur qu'aucun defaut du depot ne porte. Un `128` ou un `256` en dur ne peut
    pas la reproduire par hasard.

    Le montage bouchonne `chromadb` et `sentence_transformers` **comme de vrais
    paquets en tete de PYTHONPATH**, et non dans `sys.modules` : sinon les
    bouchons survivraient au test et l'ordre des tests deviendrait significatif.
    C'est le montage de `test_verify_data.py` et de `test_wipe_stores.py`.
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
        """LE TEMOIN, ET IL PASSE EN PREMIER.

        Un sous-processus qui sortirait en 0 sans rien imprimer rendrait les
        assertions suivantes vraies d'un rapport qui n'a jamais tourne.
        """
        sortie = self._rapport(tmp_path, self.FENETRE_BOUCHON)

        assert "=== Fenetre du modele d'embedding ===" in sortie, sortie
        assert "chunks indexes            : 1" in sortie, sortie

    def test_la_limite_rapportee_est_celle_du_modele(self, tmp_path):
        """LE GARDE. Une fenetre en dur rougit ici."""
        sortie = self._rapport(tmp_path, self.FENETRE_BOUCHON)

        assert f"limite                    : {self.FENETRE_BOUCHON} tokens" in sortie, (
            f"la fenetre rapportee n'est pas celle du modele "
            f"({self.FENETRE_BOUCHON}) : l'instrument annonce un chiffre "
            f"fabrique, et son taux de troncature avec lui\n{sortie}"
        )

    def test_la_limite_suit_le_modele_et_n_est_pas_figee(self, tmp_path):
        """Le second temoin, et il est le plus important.

        Sans lui, une limite ecrite en dur a la valeur du bouchon passerait le
        garde ci-dessus. Deux modeles differents doivent rendre deux fenetres
        differentes — c'est cela, « lue sur le modele ».
        """
        autre = 512

        sortie = self._rapport(tmp_path, autre)

        assert f"limite                    : {autre} tokens" in sortie, sortie
        assert str(self.FENETRE_BOUCHON) not in sortie, (
            f"la fenetre est figee a {self.FENETRE_BOUCHON} : elle ne suit pas le modele\n{sortie}"
        )
