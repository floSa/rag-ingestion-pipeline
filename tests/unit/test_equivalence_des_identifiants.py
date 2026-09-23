"""LE CONTROLE NEGATIF DU HARNAIS D'EQUIVALENCE, et c'est lui qui coute.

Le harnais de `src/equivalence_des_identifiants.py` compare les `element_id`
obtenus en reextrayant un document par le chemin de production a ceux que le
graphe porte deja. Il rend un zero — « aucun identifiant n'a bouge » — et la
campagne de reingestion decidera sur ce zero.

**UN ZERO NON DOUBLE NE VAUT RIEN.** Un harnais qui compare mal, qui lit le
mauvais document ou dont la formule a ete recopiee de travers rend le meme zero
qu'un harnais juste. Ce qui distingue les deux est ici : quatre mutations, une
par entree de `compute_id`, qui prouvent que la sonde SAIT dire « different ».

**ET LE PIEGE EST DANS LA MUTATION ELLE-MEME.** Le lot qui a ecrit ce harnais
dans son scratchpad a d'abord mute `page_no` sur la condition `page_no >= 12` —
or un chapitre HTML est TOUT ENTIER en page 1, donc la mutation ne mutait RIEN
et le controle negatif passait au vert en ne controlant rien. C'est exactement
le defaut que ce fichier traque, retourne contre l'instrument qui le traque.
D'ou :func:`test_une_mutation_qui_ne_mute_rien_leve`, et d'ou le fait que
:class:`~src.equivalence_des_identifiants.MutationNulleError` existe dans le
PRODUCTEUR et non dans ce test : une mutation nulle doit etre impossible a
executer, pas seulement impossible a ecrire ici.

Ce fichier ne demande ni graphe, ni ChromaDB, ni Docling : les mutations et la
comparaison sont PURES, et c'est ce qui les rend gardables dans la porte
qualite. Ce que le harnais fait des stores est garde ailleurs — par le fait
qu'il ne peut rien y ecrire, lui aussi verifie ici.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.equivalence_des_identifiants import (
    MUTATIONS,
    MutationNulleError,
    appliquer_la_mutation,
    comparer,
    recalculer_les_identifiants,
)

CLE = "htms/Un ouvrage/3. Un chapitre.html"

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# `armer_les_barrieres` n'est PAS importe en tete de ce fichier : l'importer est
# sans effet, mais l'APPELER contamine la session pytest. Il n'est atteint que
# dans le sous-processus, ou le contaminer ne coute rien.
PREAMBULE = """
import json
from src.equivalence_des_identifiants import armer_les_barrieres, BarriereDEcritureError
"""


def _elements(nombre=60, page_no=1):
    """Un document d'essai : `nombre` elements, tous sur la meme page.

    TOUS SUR LA MEME PAGE, et c'est voulu : c'est la forme d'un chapitre HTML,
    donc celle sur laquelle la mutation `page_no` du lot precedent s'est
    revelee nulle. Un jeu d'essai etale sur douze pages aurait cache le defaut.
    """
    return [
        {
            "label": "text",
            "page_no": page_no,
            "page_position": rang,
            "text50": f"Le passage numero {rang} de ce chapitre.",
        }
        for rang in range(nombre)
    ]


def _graphe_depuis(elements, cle=CLE):
    """Le graphe tel qu'il serait s'il avait ete ecrit depuis ces elements."""
    recalcules = recalculer_les_identifiants(elements, cle)
    return {
        element["id"]: {"page_no": element["page_no"], "text": element["text50"]}
        for element in recalcules
    }


class TestLaSondeVoitCeQuiNeBougePas:
    """LE TEMOIN, et il passe en premier."""

    def test_sans_mutation_aucun_identifiant_ne_bouge(self):
        """Sans mutation, la sonde doit rendre le zero. Sinon elle ment a l'envers.

        Un harnais qui rendrait « different » sur un document inchange ferait
        echouer la campagne pour rien, et personne ne saurait dire si le rouge
        vient du pipeline ou de l'instrument.
        """
        elements = _elements()
        graphe = _graphe_depuis(elements)

        rapport = comparer(recalculer_les_identifiants(elements, CLE), graphe)

        assert rapport.identiques == 60
        assert rapport.graphe_seul == 0
        assert rapport.recalcul_seul == 0

    def test_la_comparaison_ne_rend_pas_zero_sur_un_graphe_vide(self):
        """Le second temoin : une sonde qui ne lit rien rendrait aussi `graphe_seul = 0`.

        Sans ce test, un `comparer` dont la lecture du graphe est cassee — space
        vide, mauvais document, requete muette — passerait le test precedent ET
        les quatre mutations, parce que « rien contre rien » est toujours egal.
        """
        rapport = comparer(recalculer_les_identifiants(_elements(), CLE), {})

        assert rapport.identiques == 0
        assert rapport.recalcul_seul == 60


class TestLeControleNegatif:
    """Les quatre entrees de `compute_id`, mutees une par une."""

    @pytest.mark.parametrize("nom", sorted(MUTATIONS))
    def test_la_sonde_voit_la_mutation(self, nom):
        """Chacune des quatre mutations DOIT faire bouger des identifiants.

        C'est la propriete qui donne sa valeur au zero : si muter `page_no` ne
        change rien a ce que la sonde rend, alors le zero qu'elle rend sur le
        document reel ne dit rien de `page_no`.
        """
        elements = _elements()
        graphe = _graphe_depuis(elements)

        cle_mutee, elements_mutes = appliquer_la_mutation(MUTATIONS[nom], elements, CLE)
        rapport = comparer(recalculer_les_identifiants(elements_mutes, cle_mutee), graphe)

        assert rapport.graphe_seul > 0, (
            f"la mutation « {nom} » n'a fait bouger aucun identifiant : la sonde "
            "ne garde pas cette entree de la formule"
        )
        assert rapport.identiques < 60

    @pytest.mark.parametrize("nom", sorted(MUTATIONS))
    def test_la_mutation_est_semantique_et_non_un_plantage(self, nom):
        """Une mutation doit produire un document COMPLET, et pas moins d'elements.

        Une mutation qui ferait disparaitre des elements — une exception avalee,
        une liste tronquee — ferait aussi bouger le compte, et le test precedent
        passerait pour la mauvaise raison.
        """
        elements = _elements()

        _cle, elements_mutes = appliquer_la_mutation(MUTATIONS[nom], elements, CLE)

        assert len(elements_mutes) == len(elements)
        assert all(element["text50"] for element in elements_mutes)

    def test_la_mutation_ne_touche_pas_les_elements_d_origine(self):
        """Muter ne doit pas alterer le releve source : les quatre passes le partagent."""
        elements = _elements()
        avant = [dict(element) for element in elements]

        for mutation in MUTATIONS.values():
            appliquer_la_mutation(mutation, elements, CLE)

        assert elements == avant


class TestUneMutationNulleEstImpossible:
    """LE DEFAUT DU LOT PRECEDENT, converti en garde."""

    def test_une_mutation_qui_ne_mute_rien_leve(self):
        """Une mutation sans effet doit LEVER, jamais rendre un vert.

        `page_no >= 12` sur un chapitre entierement en page 1 : la condition ne
        se declenche sur aucun element, aucun identifiant ne bouge, et le
        controle negatif se declare satisfait. Le harnais REFUSE desormais ce
        cas, au lieu de le compter comme une preuve.
        """
        from src.equivalence_des_identifiants import Mutation

        inerte = Mutation(
            nom="page_no (la version fausse du lot precedent)",
            terme="page_no",
            cle=lambda cle: cle,
            element=lambda element: (
                element.__setitem__("page_no", element["page_no"] + 1)
                if element["page_no"] >= 12
                else None
            ),
        )

        with pytest.raises(MutationNulleError, match="n'a deplace aucun"):
            appliquer_la_mutation(inerte, _elements(page_no=1), CLE)

    def test_la_meme_mutation_sur_un_document_pagine_ne_leve_pas(self):
        """Le temoin de la garde : elle refuse la mutation NULLE, pas la mutation.

        Sans ce second cas, un `appliquer_la_mutation` qui leverait TOUJOURS
        passerait le test precedent.
        """
        from src.equivalence_des_identifiants import Mutation

        inerte = Mutation(
            nom="page_no",
            terme="page_no",
            cle=lambda cle: cle,
            element=lambda element: (
                element.__setitem__("page_no", element["page_no"] + 1)
                if element["page_no"] >= 12
                else None
            ),
        )

        _cle, mutes = appliquer_la_mutation(inerte, _elements(page_no=12), CLE)

        assert mutes[0]["page_no"] == 13


class TestLeHarnaisNePeutRienEcrire:
    """« Ce qui ecrirait leve », et non « j'ai fait attention ».

    **L'ARMEMENT SE FAIT EN SOUS-PROCESSUS, et ce n'est pas une precaution de
    style.** `armer_les_barrieres` remplace les portes d'ecriture DANS les
    modules de production, et l'effet survit dans `sys.modules` pour tout le
    reste de la session pytest. `mesure` : arme dans le processus de pytest, il
    fait tomber SIX tests de `tests/unit/test_storage.py`, qui tourne apres ce
    fichier dans l'ordre alphabetique — un rouge sans rapport avec ce qu'il
    garde, et qui n'apparaitrait pas si ce fichier etait lance seul.

    L'irreversibilite est VOULUE dans le harnais : desarmer serait offrir le
    moyen d'ecrire. C'est donc au test de s'isoler, pas au producteur de
    s'affaiblir. Le geste est celui de `test_importabilite_cote_hote.py`.
    """

    def _armer_en_sous_processus(self, code):
        """Execute `code` dans un processus neuf, barrieres armees, et rend son JSON."""
        acheve = subprocess.run(
            [sys.executable, "-c", PREAMBULE + code],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        return json.loads(acheve.stdout.strip().splitlines()[-1])

    def test_les_barrieres_font_lever_toute_ecriture(self):
        """Apres armement, chaque porte d'ecriture des stores LEVE.

        Le harnais reextrait par le chemin de PRODUCTION : les fonctions qui
        ecrivent sont a portee d'appel, et une ligne oubliee ecrirait pour de
        bon dans le graphe, dans ChromaDB ou dans MinIO. Une relecture ne suffit
        pas a le garantir ; une exception, si.
        """
        releve = self._armer_en_sous_processus(
            """
armees = armer_les_barrieres()
resultat = {}
for nom, porte in sorted(armees.items()):
    try:
        porte()
    except BarriereDEcritureError as exc:
        resultat[nom] = str(exc)
    else:
        resultat[nom] = None
print(json.dumps(resultat))
"""
        )

        assert releve, "aucune barriere armee : le harnais ecrirait"
        for nom, message in sorted(releve.items()):
            assert message is not None, f"{nom} n'a pas leve : cette porte ecrit encore"
            assert nom.split(".")[-1] in message, (
                f"la barriere de {nom} leve sans se nommer : un run casse ne "
                f"dirait pas QUELLE ecriture a ete tentee ({message})"
            )

    def test_les_barrieres_sont_bien_posees_dans_les_modules_de_production(self):
        """LE TEMOIN. Une barriere rendue mais non posee ne garde rien.

        Sans ce test, un `armer_les_barrieres` qui construirait les levees et
        oublierait le `setattr` passerait le test precedent : les portes
        rendues leveraient, et la production ecrirait quand meme.
        """
        releve = self._armer_en_sous_processus(
            """
from src.docling_service import images, storage, vectors

avant = storage.persist
armer_les_barrieres()
leve = {}
for nom, fonction in (
    ("storage.persist", lambda: storage.persist(None, None, None, None)),
    ("vectors.write_elements", lambda: vectors.write_elements(None, None)),
    ("images.upload_file", lambda: images.upload_file(None, None, 0)),
):
    try:
        fonction()
    except BarriereDEcritureError:
        leve[nom] = True
    except Exception as exc:
        leve[nom] = f"AUTRE: {type(exc).__name__}"
    else:
        leve[nom] = False
leve["persist_remplace"] = storage.persist is not avant
print(json.dumps(leve))
"""
        )

        assert releve["persist_remplace"] is True, (
            "`storage.persist` n'a pas ete remplace dans le module : les "
            "barrieres sont construites mais pas posees"
        )
        for nom in ("storage.persist", "vectors.write_elements", "images.upload_file"):
            assert releve[nom] is True, f"{nom} appele par son module ne leve pas : {releve[nom]}"

    def test_les_barrieres_couvrent_les_trois_stores(self):
        """Une barriere par store, nommee. Un store oublie est un store qui ecrit."""
        releve = self._armer_en_sous_processus("print(json.dumps(sorted(armer_les_barrieres())))")
        armees = set(releve)

        assert any("nebula" in nom or "storage" in nom for nom in armees), armees
        assert any("vectors" in nom for nom in armees), armees
        assert any("images" in nom for nom in armees), armees
