"""Gardes sur le jeu de questions de la premiere campagne de reference.

**CE QUE CES TESTS GARDENT, ET CE QU'ILS NE GARDENT PAS.** La distinction est le
sujet de ce fichier, et l'ecrire ici est ce qui l'empeche d'etre lu plus large
qu'il n'est.

Ils gardent la **FORME** du jeu contre la specification du registre, section 1
« La conception du jeu d'evaluation » : les cinq strates et leurs effectifs, le
format des `element_id`, et les trois proprietes sans lesquelles une strate ne
mesure pas ce que son nom annonce. Les effectifs attendus sont ecrits ici en
**litteraux**, et c'est delibere : les deriver du fichier rendrait chaque
assertion vraie par construction — le motif des treize gardes creux de ce
chantier, *le test observe ce qu'il a lui-meme fourni*.

Ils ne gardent **pas** la VERITE de la carte `ancrages`, c'est-a-dire que
l'`element_id` `4b1d79b83a` designe bien la section « Embedding window
considerations » du chapitre 7. Cette verite se mesure contre l'index vivant, ce
qu'aucun test de ce depot ne peut faire — `chromadb` n'est pas dans le venv du
depot. Le geste qui la garde est
`scripts/campagne/verifier-le-jeu-de-questions.py`, qui relit chaque ancrage
dans le store et sort en 1 au premier desaccord. **Les deux sont necessaires et
aucun ne remplace l'autre** : la forme rougit ici, la provenance rougit la-bas.

Ils ne gardent pas non plus la QUALITE des questions — qu'une question soit
dure, qu'une reponse attendue soit juste. Cela demande une relecture humaine, et
c'est precisement le motif pour lequel la specification reporte les questions
pieges au second tour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# Le chemin du jeu, relatif a la racine du depot. Un test qui construit son
# chemin depuis `__file__` survit a un deplacement du fichier de test ; un test
# qui le prend d'un reglage survivrait a la disparition du jeu.
#
# LE JEU EST EN YAML ET NON EN JSON, et le motif est mesure : `detect-secrets`
# lit l'empreinte du corpus comme une « Hex High Entropy String » et refuse le
# commit tant que le faux positif n'est pas declare AU SITE par un pragma
# justifie. JSON n'admet pas de commentaire. C'est l'arbitrage du registre
# section 3.6 bis, deja pris pour `tests/fixtures/arbres_docling.yaml`.
JEU = (
    Path(__file__).resolve().parents[2]
    / "documentation"
    / "campagnes"
    / "2026-09-02-jeu-de-questions.yaml"
)

# LES EFFECTIFS DE LA SPECIFICATION, EN LITTERAUX. Registre, section 1 : douze
# multi-passages « rend l'ablation lisible », huit simples « plancher de
# controle », quatre sans reponse « teste l'abstention », quatre de suivi « il
# n'y en avait AUCUNE », deux reformulees « echantillon ». Trente au total, et
# non trente-six : « a 4 par strate, aucune ne dit rien ».
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
    """Le temoin des autres classes : sans lui, un jeu vide les rendrait vertes.

    Une liste vide satisfait tout `for q in questions: assert ...`. C'est la
    forme la plus courante du garde creux, et elle se ferme par un compte.
    """

    def test_le_fichier_existe(self):
        assert JEU.is_file(), f"jeu de questions introuvable : {JEU}"

    def test_il_porte_trente_questions(self, questions):
        assert len(questions) == TOTAL_ATTENDU

    def test_les_identifiants_de_question_sont_uniques(self, questions):
        ids = [q["id"] for q in questions]
        assert len(set(ids)) == len(ids)


class TestLesCinqStratesEtLeursEffectifs:
    """Convertit en garde la table du registre section 1.

    Cette table est une **phrase d'exhaustivite** dans un document : elle clot
    une enumeration que personne ne rouvre. Le chantier en a paye assez pour ne
    plus en laisser une decider d'une mesure sans qu'un test la tienne.
    """

    def test_les_effectifs_sont_ceux_de_la_specification(self, questions):
        compte: dict[str, int] = {}
        for q in questions:
            compte[q["strate"]] = compte.get(q["strate"], 0) + 1
        assert compte == EFFECTIFS_ATTENDUS

    def test_aucune_strate_hors_specification(self, questions):
        assert {q["strate"] for q in questions} == set(EFFECTIFS_ATTENDUS)

    def test_le_total_declare_dans_le_jeu_ne_derive_pas_des_litteraux(self, jeu):
        """Le jeu declare ses propres effectifs : ils doivent coincider ICI.

        Sans cette assertion, editer les questions ET la declaration du meme
        geste laisserait le fichier coherent avec lui-meme et faux contre la
        specification. C'est le seul endroit ou les deux se rencontrent.
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
        """Le temoin du precedent, par l'autre bout.

        Une carte d'ancrages plus large que les citations resterait verte sur
        l'egalite si l'assertion ne portait que dans un sens.
        """
        cites = {e for q in questions for e in q["element_ids"]}
        orphelins = sorted(set(jeu["ancrages"]) - cites)
        assert orphelins == []


class TestCeQuiRendChaqueStrateMesurable:
    """Trois proprietes de SERRAGE, une par strate qui en depend.

    Ce ne sont pas des controles de forme : chacune est la condition sans
    laquelle la strate ne mesure pas ce que son nom annonce.
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
        """Le temoin du precedent, et il n'est pas symetrique.

        Une question « sans reponse » qui citerait un passage punirait une
        abstention CORRECTE : elle mesurerait l'inverse de ce qu'elle pretend.
        """
        fautifs = [q["id"] for q in questions if q["strate"] == "sans_reponse" and q["element_ids"]]
        assert fautifs == []

    def test_les_multi_passages_couvrent_au_moins_deux_sections_distinctes(self, jeu, questions):
        """« 2 ou 3 sections differentes » — c'est ce qui rend l'ablation lisible.

        Deux ancrages de la MEME section ne demandent aucune reconstruction :
        la question redeviendrait un `simple` deguise, et l'ablation du graphe
        conclurait « le graphe ne sert a rien » sur une population de questions
        incapable de le voir. Le compte porte sur les SECTIONS, pas sur les
        ancrages, et c'est la le serrage.
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

        La strate existe parce qu'« il n'y en avait AUCUNE » dans l'ancien jeu :
        livrer quatre questions de suivi sans historique la recreerait vide.
        """
        fautifs = [
            q["id"] for q in questions if q["strate"] == "de_suivi" and not q.get("chat_history")
        ]
        assert fautifs == []

    def test_un_historique_de_suivi_alterne_utilisateur_et_assistant(self, questions):
        """Le temoin du precedent : une liste non vide ne suffit pas.

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
        """Le corpus est ENTIEREMENT anglais : « question fr -> document en »
        reste possible, l'inverse disparait. Le jeu doit donc porter au moins une
        question francaise, sans quoi l'axe n'est pas echantillonne du tout.

        Un `>= 1` et non une egalite : ajouter des questions francaises est une
        amelioration, et une egalite l'interdirait.
        """
        assert sum(1 for q in questions if q["langue"] == "fr") >= 1

    def test_chaque_question_porte_une_reponse_attendue_non_vide(self, questions):
        vides = [q["id"] for q in questions if not (q.get("reponse_attendue") or "").strip()]
        assert vides == []

    def test_les_chapitres_echantillonnes_sont_ceux_que_les_ancrages_citent(self, jeu):
        """Le perimetre declare et le perimetre reel doivent coincider.

        Un jeu qui declare echantillonner quatre chapitres et cite un cinquieme
        ment sur son perimetre, et « on echantillonne les questions, jamais le
        corpus » deviendrait inverifiable.
        """
        declares = set(jeu["echantillonnage"]["chapitres"])
        declares.add(jeu["echantillonnage"]["pages_pdf"]["source_path"])
        reels = {a["source_path"] for a in jeu["ancrages"].values()}
        assert reels == declares
