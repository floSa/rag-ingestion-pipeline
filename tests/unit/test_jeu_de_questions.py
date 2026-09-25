"""Tests du jeu de questions de la premiere campagne de reference.

Ce que ces tests verifient : la **forme** du jeu, contre la specification du
registre (section 1, « La conception du jeu d'evaluation ») — les cinq strates
et leurs effectifs, le format des `element_id`, et les trois proprietes sans
lesquelles une strate ne mesure pas ce que son nom annonce. Les effectifs
attendus sont ecrits en litteraux, volontairement : les deriver du fichier
rendrait chaque assertion vraie par construction.

Ce qu'ils ne verifient pas :

- la **justesse** de la carte `ancrages`, par exemple que l'`element_id`
  `4b1d79b83a` designe bien la section « Embedding window considerations » du
  chapitre 7. Elle se verifie contre l'index vivant, et `chromadb` n'est pas
  dans l'environnement virtuel du depot. C'est le role de
  `scripts/campagne/verifier-le-jeu-de-questions.py`, qui relit chaque ancrage
  dans le store et sort en 1 au premier desaccord. Les deux controles sont
  complementaires ;
- la **qualite** des questions (difficulte, justesse des reponses attendues),
  qui demande une relecture humaine. C'est pourquoi la specification reporte
  les questions pieges au second tour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# Chemin du jeu, construit depuis `__file__` et relatif a la racine du depot.
# Un chemin lu dans un reglage laisserait passer la disparition du jeu.
#
# Le jeu est en YAML et non en JSON : `detect-secrets` prend l'empreinte du
# corpus pour une « Hex High Entropy String » et refuse le commit tant que le
# faux positif n'est pas declare sur place par un commentaire pragma, ce que
# JSON ne permet pas (registre section 3.6 bis, meme choix que
# `tests/fixtures/arbres_docling.yaml`).
JEU = (
    Path(__file__).resolve().parents[2]
    / "documentation"
    / "campagnes"
    / "2026-09-02-jeu-de-questions.yaml"
)

# Effectifs de la specification (registre, section 1), en litteraux : douze
# multi-passages (rendent l'ablation lisible), huit simples (plancher de
# controle), quatre sans reponse (testent l'abstention), quatre de suivi, deux
# reformulees (echantillon). Trente au total.
EFFECTIFS_ATTENDUS = {
    "multi_passages": 12,
    "simple": 8,
    "sans_reponse": 4,
    "de_suivi": 4,
    "reformulee": 2,
}
TOTAL_ATTENDU = 30

# Contrat, exigence 2 : `element_id` est deterministe, derive du contenu, dix
# caracteres hexadecimaux. Un identifiant hors format ne designe rien.
FORMAT_ELEMENT_ID = re.compile(r"^[a-f0-9]{10}$")


@pytest.fixture(scope="module")
def jeu() -> dict[str, Any]:
    return yaml.safe_load(JEU.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def questions(jeu: dict[str, Any]) -> list[dict[str, Any]]:
    return jeu["questions"]


class TestLeJeuExisteEtEstLisible:
    """Le jeu n'est pas vide : sans ce controle, les autres classes passeraient sur un jeu vide.

    Une liste vide satisfait tout `for q in questions: assert ...`.
    """

    def test_le_fichier_existe(self):
        assert JEU.is_file(), f"jeu de questions introuvable : {JEU}"

    def test_il_porte_trente_questions(self, questions):
        assert len(questions) == TOTAL_ATTENDU

    def test_les_identifiants_de_question_sont_uniques(self, questions):
        ids = [q["id"] for q in questions]
        assert len(set(ids)) == len(ids)


class TestLesCinqStratesEtLeursEffectifs:
    """Verifie les effectifs par strate de la table du registre, section 1."""

    def test_les_effectifs_sont_ceux_de_la_specification(self, questions):
        compte: dict[str, int] = {}
        for q in questions:
            compte[q["strate"]] = compte.get(q["strate"], 0) + 1
        assert compte == EFFECTIFS_ATTENDUS

    def test_aucune_strate_hors_specification(self, questions):
        assert {q["strate"] for q in questions} == set(EFFECTIFS_ATTENDUS)

    def test_le_total_declare_dans_le_jeu_ne_derive_pas_des_litteraux(self, jeu):
        """Les effectifs declares par le jeu coincident avec la specification.

        Sans cette assertion, modifier a la fois les questions et leur
        declaration laisserait le fichier coherent avec lui-meme mais faux
        par rapport a la specification.
        """
        assert jeu["strates_attendues"] == EFFECTIFS_ATTENDUS


class TestLesElementIdDesignentQuelqueChose:
    """Exigence 2 du contrat, appliquee au jeu qui s'en sert."""

    def test_tous_les_element_id_ont_le_format_du_contrat(self, questions):
        fautifs = [
            (q["id"], e)
            for q in questions
            for e in q["element_ids"]
            if not FORMAT_ELEMENT_ID.match(e)
        ]
        assert fautifs == []

    def test_une_question_ne_cite_jamais_deux_fois_le_meme_element(self, questions):
        """Un doublon gonflerait le denominateur du rappel sans rien ajouter."""
        fautifs = [
            q["id"] for q in questions if len(set(q["element_ids"])) != len(q["element_ids"])
        ]
        assert fautifs == []

    def test_chaque_element_cite_a_son_ancrage_mesure(self, jeu, questions):
        cites = {e for q in questions for e in q["element_ids"]}
        assert cites == set(jeu["ancrages"])

    def test_aucun_ancrage_ne_traine_sans_question_qui_le_cite(self, jeu, questions):
        """Complete le precedent dans l'autre sens.

        Une carte d'ancrages plus large que les citations passerait si la
        comparaison ne portait que dans un sens.
        """
        cites = {e for q in questions for e in q["element_ids"]}
        orphelins = sorted(set(jeu["ancrages"]) - cites)
        assert orphelins == []


class TestCeQuiRendChaqueStrateMesurable:
    """Trois proprietes, une par strate qui en depend.

    Chacune est la condition sans laquelle la strate ne mesure pas ce que son
    nom annonce.
    """

    def test_toute_question_a_reponse_porte_au_moins_un_ancrage(self, questions):
        """« Sans quoi le rappel n'est plus calculable » — registre, section 1.

        C'est la seule contrainte que la specification declare non negociable.
        """
        fautifs = [
            q["id"] for q in questions if q["strate"] != "sans_reponse" and not q["element_ids"]
        ]
        assert fautifs == []

    def test_les_questions_sans_reponse_n_en_portent_aucun(self, questions):
        """Une question « sans reponse » ne cite aucun passage.

        Sinon, elle penaliserait une abstention correcte et mesurerait l'inverse
        de ce qu'elle annonce.
        """
        fautifs = [q["id"] for q in questions if q["strate"] == "sans_reponse" and q["element_ids"]]
        assert fautifs == []

    def test_les_multi_passages_couvrent_au_moins_deux_sections_distinctes(self, jeu, questions):
        """« 2 ou 3 sections differentes » — c'est ce qui rend l'ablation lisible.

        Deux ancrages de la meme section ne demandent aucune reconstruction :
        la question serait un `simple` deguise, et l'ablation du graphe
        conclurait a tort qu'il ne sert a rien. Le compte porte donc sur les
        sections, pas sur les ancrages.
        """
        ancrages = jeu["ancrages"]
        fautifs = []
        for q in questions:
            if q["strate"] != "multi_passages":
                continue
            sections = {
                (ancrages[e]["source_path"], ancrages[e]["section_title"]) for e in q["element_ids"]
            }
            if len(sections) < 2:
                fautifs.append((q["id"], len(sections)))
        assert fautifs == []

    def test_les_questions_de_suivi_portent_un_historique_non_vide(self, questions):
        """Sans `chat_history`, une question de suivi est une question tronquee.

        Une question de suivi sans historique ne teste pas le suivi de
        conversation.
        """
        fautifs = [
            q["id"] for q in questions if q["strate"] == "de_suivi" and not q.get("chat_history")
        ]
        assert fautifs == []

    def test_un_historique_de_suivi_alterne_utilisateur_et_assistant(self, questions):
        """Complete le precedent : une liste non vide ne suffit pas.

        Un historique qui ne porterait que des tours `user` ne donnerait aucun
        antecedent a resoudre, et la question resterait tronquee.
        """
        for q in questions:
            if q["strate"] != "de_suivi":
                continue
            roles = [tour["role"] for tour in q["chat_history"]]
            assert "user" in roles and "assistant" in roles, q["id"]


class TestLesProprietesQueLaCAMPAGNEDoitPouvoirLIRE:
    """Ce que le rapport de campagne cite du jeu doit exister dans le jeu."""

    def test_chaque_question_porte_sa_langue(self, questions):
        assert all(q["langue"] in {"en", "fr"} for q in questions)

    def test_la_moitie_survivante_de_la_mesure_translinguistique_est_echantillonnee(
        self, questions
    ):
        """Le corpus est entierement anglais : seul l'axe « question fr ->
        document en » existe. Le jeu doit donc porter au moins une question
        francaise, sans quoi cet axe n'est pas echantillonne.

        Un `>= 1` et non une egalite : ajouter des questions francaises est une
        amelioration, et une egalite l'interdirait.
        """
        assert sum(1 for q in questions if q["langue"] == "fr") >= 1

    def test_chaque_question_porte_une_reponse_attendue_non_vide(self, questions):
        vides = [q["id"] for q in questions if not (q.get("reponse_attendue") or "").strip()]
        assert vides == []

    def test_les_chapitres_echantillonnes_sont_ceux_que_les_ancrages_citent(self, jeu):
        """Le perimetre declare et le perimetre reel doivent coincider.

        Un jeu qui declare echantillonner quatre chapitres et en cite un
        cinquieme annonce un perimetre faux, et le principe « on echantillonne
        les questions, jamais le corpus » deviendrait inverifiable.
        """
        declares = set(jeu["echantillonnage"]["chapitres"])
        declares.add(jeu["echantillonnage"]["pages_pdf"]["source_path"])
        reels = {a["source_path"] for a in jeu["ancrages"].values()}
        assert reels == declares
