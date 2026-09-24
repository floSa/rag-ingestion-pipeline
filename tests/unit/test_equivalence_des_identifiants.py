"""LE HARNAIS D'EQUIVALENCE DOIT SAVOIR DIRE NON, et ce fichier le lui fait dire.

Le harnais de `src/equivalence_des_identifiants.py` fige, avant la campagne de
reingestion, un instantane des `element_id` EMIS par la production, puis dit
apres la campagne lesquels ont bouge. Sa valeur est sa capacite a rendre un
rouge.

**LA VERSION PRECEDENTE RENDAIT DEUX FAUX VERTS**, trouves par l'audit du lot 11
et devenus ici des tests de la porte qualite (:class:`TestLesFauxVertsDeLAudit`) :

- relancee APRES la campagne, elle comparait le code X au graphe ecrit par ce
  meme code X — trois `ListItem` qui recoivent du texte rendaient `OK`, rc=0 ;
- elle jetait l'identifiant EMIS pour le recalculer — une production qui calcule
  sur `.cleaned/<cle>` rendait `IDENTIQUES 30`, rc=0.

Les deux scenarios passent par le VRAI `_extract_flat`, seul le convertisseur
Docling etant remplace : c'est le chemin que le harnais emprunte en campagne.

**ET LE PIEGE EST DANS LA MUTATION ELLE-MEME.** Une mutation qui ne mute rien
rend le controle negatif vert sans rien prouver : le lot qui a ecrit la
premiere version a mute `page_no` sur `page_no >= 12`, sur un chapitre tout
entier en page 1. :class:`TestUneMutationNulleEstImpossible` le garde.

Ce qui arme les barrieres tourne en SOUS-PROCESSUS : l'armement survit dans
`sys.modules` et ferait tomber six tests de `tests/unit/test_storage.py`.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.equivalence_des_identifiants import (
    EMPREINTES,
    MUTATIONS,
    PORTES,
    SITE_D_APPEL,
    TEMOIN_MINIO,
    Bilan,
    Controle,
    Emission,
    EmpreinteInattendueError,
    InstantaneCorrompuError,
    Monde,
    Mutation,
    MutationNulleError,
    _ecarts_de_couverture,
    appliquer_la_mutation,
    comparer,
    comparer_les_releves,
    confronter_au_graphe,
    controle_negatif,
    ecrire_l_instantane,
    empreinte_attendue,
    figer,
    identifiant_par_la_formule,
    lire_l_instantane,
    lire_les_deplacements_annonces,
    raisons_des_cles_d_objet,
    releve_de_l_emission,
    trancher,
)

CLE = "htms/Un ouvrage/3. Un chapitre"
RACINE_DEPOT = Path(__file__).resolve().parents[2]


def _lignes(nombre=60, page_no=1, textes=None):
    """Un releve d'essai coherent : chaque identifiant est celui de la formule.

    TOUS SUR LA MEME PAGE, et c'est voulu : c'est la forme d'un chapitre HTML,
    celle sur laquelle la mutation historique `page_no >= 12` etait nulle.
    """
    lignes = []
    for rang in range(nombre):
        ligne = {
            "cle": CLE,
            "page_no": page_no,
            "position_in_page": rang,
            "self_ref": f"#/texts/{rang}",
            "text50": textes[rang] if textes else f"Le passage numero {rang} de ce chapitre.",
            "label": "text",
        }
        ligne["element_id"] = identifiant_par_la_formule(ligne)
        lignes.append(ligne)
    return lignes


def _lignes_a_jumeaux():
    """Le cas qui trompait l'ancienne attribution : des `text50` partages.

    Des lignes blanches de code (`""`), une virgule repetee — la forme reelle
    d'un chapitre technique. L'ancienne heuristique par jumeaux attribuait la
    mutation `filename` a `position_in_page` 60 fois sur 60.
    """
    textes = ["", "", ",", "Texte A", "", ",", "Texte B", ""] * 8
    return _lignes(nombre=len(textes), textes=textes)


# ─── Le releve ──────────────────────────────────────────────────────────────


class TestLeReleveEstCeluiDeLEmission:
    def test_l_identifiant_emis_est_garde_tel_quel(self):
        """L'identifiant du releve est `element["id"]`, JAMAIS un recalcul.

        C'est le second faux vert de l'audit : un harnais qui recalcule ne voit
        pas une production qui calcule sur autre chose que ce qu'elle declare.
        """
        emis = {
            "id": "0123456789",
            "page_no": 1,
            "page_position": 0,
            "text": "Un passage",
            "label": "text",
            "self_ref": "#/texts/0",
        }

        (ligne,) = releve_de_l_emission([emis], CLE)

        assert ligne["element_id"] == "0123456789"
        assert ligne["element_id"] != identifiant_par_la_formule(ligne)

    def test_un_champ_disparu_du_contrat_leve(self):
        """Un champ manquant doit lever, pas devenir un zero qui se compare."""
        with pytest.raises(KeyError):
            releve_de_l_emission(
                [{"id": "0123456789", "page_no": 1, "text": "", "label": "x"}], CLE
            )

    def test_text50_est_la_troncature_de_la_formule(self):
        emis = {"id": "x", "page_no": 1, "page_position": 0, "text": "a" * 80, "label": "text"}

        (ligne,) = releve_de_l_emission([emis], CLE)

        assert ligne["text50"] == "a" * 50


# ─── L'instantane ───────────────────────────────────────────────────────────


TEXTES_HOSTILES = [
    "tab\tulation",
    "retour\na la ligne",
    "chariot\rseul",
    'guillemets "doubles" et \\ contre-oblique',
    "espace finale ",
    "separateur\u2028unicode",
    "accentue : ete, noel, cafe",
    "",
]


def _emission(partition_key="htms/Un ouvrage/3. Un chapitre.html", textes=TEXTES_HOSTILES):
    return Emission(
        partition_key=partition_key,
        cle=CLE,
        lignes=_lignes(nombre=len(textes), textes=textes),
        cles_d_objet=["images/x/0123456789_picture.png"],
        empreinte_de_l_entree="e" * 64,
    )


class TestLInstantane:
    def test_l_aller_retour_est_exact_meme_sur_des_textes_hostiles(self, tmp_path):
        """Tabulation, retours, separateur Unicode : rien ne doit couper une ligne."""
        emission = _emission()

        ecrire_l_instantane(tmp_path, [emission], {"date": "2026-09-24"})
        relu = lire_l_instantane(tmp_path)

        document = relu.documents[emission.partition_key]
        assert document.lignes == emission.lignes
        assert document.cles_d_objet == emission.cles_d_objet
        assert document.empreinte_de_l_entree == emission.empreinte_de_l_entree

    def test_aucun_hook_ne_peut_reecrire_l_instantane(self, tmp_path):
        """ASCII, aucune fin de ligne blanche : `trailing-whitespace` n'y touche pas."""
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})

        for fichier in tmp_path.iterdir():
            octets = fichier.read_bytes()
            assert octets.isascii(), fichier.name
            for rang in octets.split(b"\n"):
                assert rang == rang.rstrip(), (fichier.name, rang)

    def test_l_empreinte_couvre_chaque_fichier(self, tmp_path):
        """Un octet change dans un releve fait lever la relecture."""
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})
        (releve,) = [f for f in tmp_path.iterdir() if f.name.startswith("htms__")]
        # Une ligne de DONNEES, et non l'entete : alterer l'entete rougirait par
        # le controle d'entete, et laisserait l'empreinte sans garde.
        releve.write_text(releve.read_text(encoding="utf-8").replace("\ttext\n", "\tcode\n", 1))

        with pytest.raises(InstantaneCorrompuError):
            lire_l_instantane(tmp_path)

    def test_l_empreinte_couvre_les_cles_d_objet(self, tmp_path):
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})
        fichier = tmp_path / "cles-d-objet.tsv"
        fichier.write_text(fichier.read_text(encoding="utf-8").replace("0123", "9123"))

        with pytest.raises(InstantaneCorrompuError):
            lire_l_instantane(tmp_path)

    def test_un_instantane_ne_s_ecrase_pas(self, tmp_path):
        """Ecraser l'instantane serait perdre l'avant — la seule chose qu'il garde."""
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})

        with pytest.raises(FileExistsError):
            ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-25"})

    def test_l_empreinte_est_deterministe(self, tmp_path):
        a = ecrire_l_instantane(tmp_path / "a", [_emission()], {"date": "2026-09-24"})
        b = ecrire_l_instantane(tmp_path / "b", [_emission()], {"date": "2026-09-24"})

        assert a == b
        assert a == lire_l_instantane(tmp_path / "a").empreinte


# ─── La comparaison et le verdict ───────────────────────────────────────────


def _reparer(lignes, rangs, texte="Texte recupere"):
    """Ce qu'une reparation d'`item_text` ferait : du texte, donc un autre identifiant."""
    apres = [dict(ligne) for ligne in lignes]
    for rang in rangs:
        apres[rang]["text50"] = f"{texte} {rang}"
        apres[rang]["element_id"] = identifiant_par_la_formule(apres[rang])
    return apres


class TestLaComparaison:
    def test_sans_changement_rien_ne_bouge(self):
        lignes = _lignes()

        bilan = comparer_les_releves("p", lignes, [dict(ligne) for ligne in lignes])

        assert bilan.identiques == 60
        assert bilan.deplaces == []
        assert bilan.apparus_sans_contrepartie == []

    def test_trois_textes_rendus_font_trois_deplaces_attribues_au_texte(self):
        lignes = _lignes_a_jumeaux()

        bilan = comparer_les_releves("p", lignes, _reparer(lignes, [0, 4, 7]))

        assert len(bilan.deplaces) == 3
        assert {d.termes for d in bilan.deplaces} == {("text50",)}
        assert bilan.apparus_sans_contrepartie == []

    def test_un_element_retire_decale_ses_voisins_et_le_dit(self):
        """Ne plus emettre un element decale `position_in_page` de ceux qui suivent."""
        lignes = _lignes(nombre=10)
        apres = [dict(ligne) for ligne in lignes if ligne["position_in_page"] != 3]
        for ligne in apres:
            if ligne["position_in_page"] > 3:
                ligne["position_in_page"] -= 1
                ligne["element_id"] = identifiant_par_la_formule(ligne)

        bilan = comparer_les_releves("p", lignes, apres)

        disparus = [d for d in bilan.deplaces if d.apres is None]
        decales = [d for d in bilan.deplaces if d.apres is not None]
        assert len(disparus) == 1 and disparus[0].termes == ()
        assert len(decales) == 6
        assert {d.termes for d in decales} == {("position_in_page",)}

    def test_un_pdf_repete_ses_self_ref_et_l_appariement_tient(self):
        """Un PDF converti par lots rend `#/texts/0` une fois par lot."""
        lignes = _lignes(nombre=6)
        for ligne in lignes:
            ligne["self_ref"] = f"#/texts/{ligne['position_in_page'] % 3}"

        # La PREMIERE occurrence de `#/texts/1` : un appariement qui oublierait le
        # rang d'occurrence la confondrait avec la derniere, rang 4.
        bilan = comparer_les_releves("p", lignes, _reparer(lignes, [1]))

        (deplace,) = bilan.deplaces
        assert deplace.apres is not None
        assert deplace.apres["position_in_page"] == deplace.avant["position_in_page"] == 1
        assert deplace.termes == ("text50",)

    def test_un_element_apparu_n_a_pas_de_contrepartie(self):
        lignes = _lignes(nombre=5)
        nouveau = dict(lignes[-1], self_ref="#/texts/99", position_in_page=5)
        nouveau["element_id"] = identifiant_par_la_formule(nouveau)

        bilan = comparer_les_releves("p", lignes, [*lignes, nouveau])

        assert bilan.deplaces == []
        assert [ligne["element_id"] for ligne in bilan.apparus_sans_contrepartie] == [
            nouveau["element_id"]
        ]

    def test_un_identifiant_repris_par_un_autre_element_est_reassigne(self):
        """Le cas MESURE de l'option d : une ligne de code vide herite de l'id d'une puce.

        Meme cle, meme page, meme rang, meme `text50` vide : meme identifiant.
        Une difference d'ensembles le dit identique ; il designe pourtant du code.
        """
        textes = ["Titre", "", "", "Suite"]
        avant = _lignes(nombre=4, textes=textes)
        avant[1]["label"] = "list_item"
        avant[2]["label"] = "code"
        apres = [
            dict(avant[0]),
            dict(avant[2], position_in_page=1),
            dict(avant[3], position_in_page=2),
        ]
        for ligne in apres:
            ligne["element_id"] = identifiant_par_la_formule(ligne)

        bilan = comparer_les_releves("p", avant, apres)

        assert [a["element_id"] for a, _ in bilan.reassignes] == [avant[1]["element_id"]]
        verdict = trancher([bilan], {d.avant["element_id"] for d in bilan.deplaces})
        assert not verdict.ok
        assert "REASSIGNE" in verdict.raisons[0]

    def test_la_confrontation_ne_rend_pas_zero_sur_un_graphe_vide(self):
        """Une sonde qui ne lit rien rendrait aussi « graphe seul = 0 »."""
        confrontation = confronter_au_graphe(_lignes(), set())

        assert confrontation.identiques == 0
        assert confrontation.emis_seul == 60


def _bilan(lignes, apres):
    return comparer_les_releves("p", lignes, apres)


class TestLeVerdict:
    """rc=0 si et seulement si l'ensemble deplace EGALE l'ensemble declare."""

    def test_rien_declare_rien_deplace_est_vert(self):
        lignes = _lignes()

        assert trancher([_bilan(lignes, lignes)], set()).ok

    def test_un_deplacement_non_declare_est_rouge(self):
        lignes = _lignes()

        verdict = trancher([_bilan(lignes, _reparer(lignes, [2]))], set())

        assert not verdict.ok
        assert "NON declare" in verdict.raisons[0]

    def test_la_declaration_exacte_est_verte(self):
        lignes = _lignes()
        declares = {lignes[2]["element_id"], lignes[9]["element_id"]}

        assert trancher([_bilan(lignes, _reparer(lignes, [2, 9]))], declares).ok

    def test_un_declare_qui_ne_bouge_pas_est_rouge(self):
        """Sinon une reparation NON APPLIQUEE passerait en silence."""
        lignes = _lignes()
        declares = {lignes[2]["element_id"], lignes[9]["element_id"]}

        verdict = trancher([_bilan(lignes, _reparer(lignes, [2]))], declares)

        assert not verdict.ok
        assert "PAS bouge" in verdict.raisons[0]
        assert lignes[9]["element_id"] in verdict.raisons[0]

    def test_une_reparation_declaree_et_non_appliquee_est_rouge(self):
        lignes = _lignes()

        verdict = trancher([_bilan(lignes, lignes)], {lignes[2]["element_id"]})

        assert not verdict.ok

    def test_un_apparu_sans_contrepartie_est_rouge(self):
        lignes = _lignes(nombre=5)
        nouveau = dict(lignes[-1], self_ref="#/texts/99", position_in_page=5)
        nouveau["element_id"] = identifiant_par_la_formule(nouveau)

        assert not trancher([_bilan(lignes, [*lignes, nouveau])], set()).ok

    def test_une_autre_raison_suffit_a_rougir(self):
        lignes = _lignes()

        assert not trancher([_bilan(lignes, lignes)], set(), ["le graphe ment"]).ok

    def test_la_declaration_se_lit_avec_ses_commentaires(self, tmp_path):
        fichier = tmp_path / "annonces.txt"
        fichier.write_text("# la reparation d'item_text\n269b2e32d8  # une puce\n\n5558e561d7\n")

        assert lire_les_deplacements_annonces(fichier) == {"269b2e32d8", "5558e561d7"}
        assert lire_les_deplacements_annonces(None) == set()


class TestLesClesDObjet:
    def test_une_cle_qui_suit_un_deplacement_declare_est_verte(self):
        lignes = _lignes(nombre=3)
        apres = _reparer(lignes, [1])
        bilan = _bilan(lignes, apres)
        avant = [f"images/x/{lignes[1]['element_id']}_picture.png"]
        nouvelles = [f"images/x/{apres[1]['element_id']}_picture.png"]

        assert (
            raisons_des_cles_d_objet("p", avant, nouvelles, bilan, {lignes[1]["element_id"]}) == []
        )

    def test_une_cle_disparue_sans_declaration_est_rouge(self):
        lignes = _lignes(nombre=3)
        bilan = Bilan(partition_key="p", identiques=3, deplaces=[], apparus_sans_contrepartie=[])

        raisons = raisons_des_cles_d_objet("p", ["images/x/0123456789_table.png"], [], bilan, set())

        assert raisons and "disparue" in raisons[0]
        assert lignes


# ─── Le controle negatif ────────────────────────────────────────────────────


class TestLeControleNegatif:
    """Cinq derives simulees : une par entree de la formule, plus le site d'appel."""

    @pytest.mark.parametrize("nom", sorted(MUTATIONS))
    def test_chaque_mutation_est_vue_et_attribuee_a_son_seul_terme(self, nom):
        """Vue EN TOTALITE, et imputee a SON terme — jumeaux de `text50` compris.

        C'est ce que l'ancienne attribution ne tenait pas : sur ce releve, elle
        imputait `filename` a `position_in_page` pour chaque element.
        """
        lignes = _lignes_a_jumeaux()

        bilan = comparer_les_releves("p", lignes, appliquer_la_mutation(MUTATIONS[nom], lignes))

        assert bilan.deplaces, f"la mutation « {nom} » n'a rien fait voir"
        assert {d.termes for d in bilan.deplaces} == {(MUTATIONS[nom].terme,)}

    def test_le_site_d_appel_est_nomme_quand_les_quatre_entrees_sont_intactes(self):
        lignes = _lignes()

        bilan = comparer_les_releves(
            "p", lignes, appliquer_la_mutation(MUTATIONS["site_d_appel"], lignes)
        )

        assert {d.termes for d in bilan.deplaces} == {(SITE_D_APPEL,)}

    def test_le_controle_complet_est_vert_sur_un_releve_a_jumeaux(self):
        """Les collisions de `position_in_page` sont comptees, pas prises pour un rouge."""
        controles = controle_negatif("p", _lignes_a_jumeaux())

        assert [c.nom for c in controles] == sorted(MUTATIONS)
        assert all(c.ok for c in controles), controles
        assert any(c.collisions for c in controles), "le releve a jumeaux ne collisionne plus"

    def test_la_mutation_ne_touche_pas_le_releve_d_origine(self):
        lignes = _lignes()
        avant = [dict(ligne) for ligne in lignes]

        for mutation in MUTATIONS.values():
            appliquer_la_mutation(mutation, lignes)

        assert lignes == avant


class TestUneMutationNulleEstImpossible:
    """LE DEFAUT DU LOT PRECEDENT, converti en garde."""

    @staticmethod
    def _inerte():
        def muter(ligne):
            if ligne["page_no"] >= 12:
                ligne["page_no"] += 1
                ligne["element_id"] = identifiant_par_la_formule(ligne)

        return Mutation("page_no (la version fausse du lot precedent)", "page_no", muter)

    def test_une_mutation_qui_ne_mute_rien_leve(self):
        with pytest.raises(MutationNulleError, match="n'a deplace aucun"):
            appliquer_la_mutation(self._inerte(), _lignes(page_no=1))

    def test_la_meme_mutation_sur_un_document_pagine_ne_leve_pas(self):
        """Le temoin de la garde : elle refuse la mutation NULLE, pas la mutation."""
        mutes = appliquer_la_mutation(self._inerte(), _lignes(page_no=12))

        assert mutes[0]["page_no"] == 13

    def test_le_controle_rend_une_mutation_nulle_comme_un_echec(self):
        """Un releve vide ne laisse rien muter : chaque controle doit etre ROUGE."""
        assert not any(c.ok for c in controle_negatif("p", []))


class TestLeControleSeJuge:
    def test_une_mutation_mal_attribuee_n_est_pas_un_controle_vert(self):
        """Voir sans savoir POURQUOI est un rouge : c'est l'ancienne attribution."""
        assert not Controle("x", mutes=3, collisions=0, vus=3, mal_attribues=1).ok

    def test_une_mutation_vue_en_partie_n_est_pas_un_controle_vert(self):
        assert not Controle("x", mutes=3, collisions=0, vus=2, mal_attribues=0).ok

    def test_une_mutation_vue_et_bien_attribuee_est_verte(self):
        assert Controle("x", mutes=3, collisions=1, vus=3, mal_attribues=0).ok


# ─── L'orchestration, sur un monde factice ──────────────────────────────────


def _monde(emissions, graphe=None, corpus=None, documents_du_graphe=None, objets=None):
    """Un monde ou le graphe porte exactement ce que la production emet."""
    par_cle = {e.partition_key: e for e in emissions}
    ids = (
        graphe
        if graphe is not None
        else {e.cle: {ligne["element_id"] for ligne in e.lignes} for e in emissions}
    )
    return Monde(
        documents_du_corpus=lambda: list(corpus if corpus is not None else par_cle),
        documents_du_graphe=lambda: list(
            documents_du_graphe if documents_du_graphe is not None else par_cle
        ),
        ids_du_graphe=lambda cle: set(ids.get(cle, set())),
        objets_listes=lambda partition_key: (objets or {}).get(partition_key),
        reextraire=lambda partition_key: par_cle[partition_key],
    )


def _une_emission(partition_key="htms/L/1. Ch.html", lignes=None):
    return Emission(
        partition_key=partition_key,
        cle=CLE,
        lignes=_lignes(nombre=12) if lignes is None else lignes,
        cles_d_objet=["images/L/0123456789_picture.png"],
    )


class TestFigerRefuseDEcrireUnInstantaneFaux:
    def test_le_monde_sain_est_fige(self, tmp_path):
        rc = figer(_monde([_une_emission()]), tmp_path, {"date": "t"}, [], lambda _: None)

        assert rc == 0
        assert lire_l_instantane(tmp_path).documents

    def test_un_document_du_corpus_absent_du_graphe_est_rouge(self, tmp_path):
        """Aucune liste en dur : le corpus et le graphe se controlent l'un l'autre."""
        emissions = [_une_emission(), _une_emission("htms/L/2. Ch.html")]
        monde = _monde(emissions, documents_du_graphe=["htms/L/1. Ch.html"])

        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 1
        assert not (tmp_path / "MANIFESTE.tsv").exists()

    def test_une_emission_vide_contre_un_graphe_vide_est_rouge(self, tmp_path):
        """« Rien contre rien » est toujours egal : c'est un rouge, pas un zero.

        La confrontation au graphe le laisse passer ; c'est le controle negatif
        qui le rougit, toutes ses mutations etant nulles sur un releve vide.
        """
        sortie = []
        monde = _monde([_une_emission(lignes=[])], graphe={})

        assert figer(monde, tmp_path, {"date": "t"}, [], sortie.append) == 1
        assert any("mutation filename : NON VU" in ligne for ligne in sortie), sortie

    def test_des_cles_d_objet_que_minio_ne_liste_pas_sont_rouges(self, tmp_path):
        emission = _une_emission()
        monde = _monde([emission], objets={emission.partition_key: set()})

        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 1

    def test_une_barriere_touchee_est_rouge_meme_avalee(self, tmp_path):
        journal = ["vectors.get_collection"]

        assert (
            figer(_monde([_une_emission()]), tmp_path, {"date": "t"}, journal, lambda _: None) == 1
        )


class TestComparerAlInstantane:
    def _fige(self, tmp_path, emissions):
        figer(_monde(emissions), tmp_path, {"date": "t"}, [], lambda _: None)
        return lire_l_instantane(tmp_path)

    def test_rien_ne_bouge_est_vert(self, tmp_path):
        instantane = self._fige(tmp_path, [_une_emission()])

        monde = _monde([_une_emission()])

        assert comparer(monde, instantane, instantane.empreinte, set(), [], lambda _: None) == 0

    def test_un_document_absent_de_l_instantane_est_rouge(self, tmp_path):
        instantane = self._fige(tmp_path, [_une_emission()])
        apres = [_une_emission(), _une_emission("htms/L/2. Ch.html")]

        monde = _monde(apres)

        assert comparer(monde, instantane, instantane.empreinte, set(), [], lambda _: None) == 1

    def test_un_graphe_qui_n_est_pas_l_emission_est_rouge(self, tmp_path):
        """Apres la campagne, le graphe doit etre ce que le code emet."""
        instantane = self._fige(tmp_path, [_une_emission()])
        monde = _monde([_une_emission()], graphe={CLE: set()})

        assert comparer(monde, instantane, instantane.empreinte, set(), [], lambda _: None) == 1

    def test_un_corpus_vide_est_rouge_et_ne_compare_rien(self, tmp_path):
        """LE FAUX VERT BLOQUANT DU SECOND AUDIT : rc=0 et OK sur un corpus VIDE.

        `_ecarts_de_couverture` ne comparait chaque paire que dans UN sens
        (`a - b` pour `a < b` en ordre alphabetique) : `instantane - corpus`
        n'etait jamais calcule. Puis `comparer` sautait par `continue` tout
        document absent du corpus. Le corpus arrive par un MONTAGE, et ce depot
        a deja connu une purge qui emportait 24 fichiers sur 25.
        """
        sortie = []
        instantane = self._fige(tmp_path, [_une_emission()])

        monde = _monde([_une_emission()], corpus=[])

        rc = comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert rc == 1
        assert any("DOCUMENTS COMPARES 0 / 1" in ligne for ligne in sortie), sortie

    def test_un_seul_document_sorti_du_corpus_est_rouge(self, tmp_path):
        """Le meme trou, en plus discret : l'instantane en porte 2, le corpus 1."""
        emissions = [_une_emission(), _une_emission("htms/L/2. Ch.html")]
        instantane = self._fige(tmp_path, emissions)
        sortie = []

        monde = _monde(emissions, corpus=["htms/L/1. Ch.html"])

        rc = comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert rc == 1
        assert any("DOCUMENTS COMPARES 1 / 2" in ligne for ligne in sortie), sortie

    def test_un_graphe_vide_avec_un_corpus_intact_est_rouge(self, tmp_path):
        """Deja rouge sur `3625816` par le sens `corpus - graphe` ; tenu ici contre son retrait."""
        instantane = self._fige(tmp_path, [_une_emission()])

        monde = _monde([_une_emission()], documents_du_graphe=[])

        assert comparer(monde, instantane, instantane.empreinte, set(), [], lambda _: None) == 1

    def test_chaque_paire_est_comparee_dans_les_deux_sens(self):
        """La docstring promet « un document manquant d'un cote est un rouge »."""
        raisons = _ecarts_de_couverture(corpus=set(), graphe={"d"}, instantane={"d"})

        assert any("dans graphe et pas dans corpus" in r for r in raisons), raisons
        assert any("dans instantane et pas dans corpus" in r for r in raisons), raisons

    def test_un_journal_de_barrieres_non_vide_rougit_comparer(self, tmp_path):
        """A2 : le garde existait dans `comparer`, aucun test ne le tenait.

        Mutation de l'audit — retrait du garde dans `comparer` SEUL : 55 tests
        verts. Le pendant de `test_une_barriere_touchee_est_rouge_meme_avalee`,
        cote `comparer`.
        """
        instantane = self._fige(tmp_path, [_une_emission()])
        journal = ["vectors.get_collection"]

        monde = _monde([_une_emission()])

        rc = comparer(monde, instantane, instantane.empreinte, set(), journal, lambda _: None)

        assert rc == 1


class TestLeHarnaisNeSeRetautologisePas:
    """A3 : refiger apres la campagne rendait rc=0 des deux cotes (`mesure` de l'audit).

    La confrontation de l'empreinte est desormais une MESURE et non une
    consigne : elle est prise a un site VERSIONNE, le parent du dossier, parce
    qu'un second instantane porterait son propre manifeste — donc sa propre
    empreinte — et se signerait lui-meme.
    """

    def _table(self, dossier, lignes):
        (dossier.parent / EMPREINTES).write_text(
            "# essai\ndossier\tempreinte\n" + "".join(lignes), encoding="utf-8"
        )

    def test_l_empreinte_attendue_se_lit_dans_le_parent(self, tmp_path):
        dossier = tmp_path / "2026-09-24-instantane"
        self._table(dossier, [f"{dossier.name}\tabc123\n"])

        assert empreinte_attendue(dossier) == "abc123"

    def test_un_instantane_refige_porte_une_autre_empreinte_et_rougit(self, tmp_path):
        """LE SCENARIO DE L'AUDIT : on refige apres la campagne, on compare contre lui."""
        dossier = tmp_path / "instantane"
        figer(_monde([_une_emission()]), dossier, {"date": "t"}, [], lambda _: None)
        refige = tmp_path / "refige"
        figer(_monde([_une_emission()]), refige, {"date": "AUTRE DATE"}, [], lambda _: None)
        sortie = []
        monde = _monde([_une_emission()])

        rc = comparer(
            monde,
            lire_l_instantane(refige),
            lire_l_instantane(dossier).empreinte,
            set(),
            [],
            sortie.append,
        )

        assert rc == 1
        assert any("EMPREINTE INATTENDUE" in ligne for ligne in sortie), sortie

    def test_un_dossier_absent_de_la_table_est_refuse(self, tmp_path):
        """Le cas exact du second instantane : ecrit, mais inscrit nulle part."""
        dossier = tmp_path / "refige-en-douce"
        self._table(dossier, ["2026-09-24-instantane-des-identifiants\tabc123\n"])

        with pytest.raises(EmpreinteInattendueError, match="n'est pas dans"):
            empreinte_attendue(dossier)

    def test_une_table_absente_n_authentifie_rien(self, tmp_path):
        with pytest.raises(EmpreinteInattendueError, match="manque"):
            empreinte_attendue(tmp_path / "instantane")

    def test_refiger_dans_le_meme_dossier_rend_un_rc_1_et_non_une_trace(self, tmp_path):
        """`FileExistsError` remontait nue : une trace d'appel n'est pas un verdict."""
        monde = _monde([_une_emission()])
        sortie = []
        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 0

        rc = figer(monde, tmp_path, {"date": "t"}, [], sortie.append)

        assert rc == 1
        assert any("ne s'ecrase pas" in ligne for ligne in sortie), sortie

    def test_la_table_versionnee_du_depot_attend_l_instantane_de_la_campagne(self):
        """LE SITE REEL : la table du depot, contre l'instantane du depot."""
        dossier = RACINE_DEPOT / "documentation/campagnes/2026-09-24-instantane-des-identifiants"

        assert empreinte_attendue(dossier) == lire_l_instantane(dossier).empreinte


# ─── En sous-processus : les barrieres, et les deux faux verts de l'audit ───


def _executer(code, *arguments):
    """Execute `code` dans un processus neuf et rend la derniere ligne, en JSON."""
    acheve = subprocess.run(
        [sys.executable, "-c", code, *arguments],
        capture_output=True,
        text=True,
        cwd=RACINE_DEPOT,
        env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    return json.loads(acheve.stdout.strip().splitlines()[-1])


# Les PORTEURS de client de store qui N'ECRIVENT PAS EUX-MEMES, chacun avec sa
# raison. CE N'EST PAS LA LISTE DES PORTES DU PRODUCTEUR, et c'est tout
# l'interet : tout porteur absent d'ici est TENU pour une porte, donc doit etre
# barre a tous ses sites. Le test DERIVE les porteurs du code (voir
# `PARCOURS_DES_SITES`) : une porte neuve deposee dans n'importe quel
# `src/docling_service/*.py` rougit ce test tant que quelqu'un ne l'a pas classee.
NON_ECRIVAINS = {
    "src.docling_service.images.ensure_bucket": "ne parle a MinIO que par get_client, le temoin",
    "src.docling_service.images.crop_and_upload": (
        "la production tourne ; son envoi passe par get_client"
    ),
    "src.docling_service.extraction.extract": (
        "l'orchestrateur : n'atteint les stores que par persist et get_writer, barres"
    ),
    "src.docling_service.extraction._extract_flat": (
        "le chemin HTML que le harnais APPELLE ; ses ecritures passent par persist, barre"
    ),
    "src.docling_service.extraction._extract_pdf": (
        "le chemin PDF que le harnais APPELLE ; idem, plus crop_and_upload sur le temoin"
    ),
    "src.docling_service.extraction._convert_batch": "convertit ; aucun client de store en propre",
    "src.docling_service.extraction._prepared_source": (
        "prepare l'entree sur disque ; n'atteint MinIO que par _upload_markdown_images"
    ),
    "src.docling_service.extraction._upload_markdown_images": (
        "envoie par upload_file, qui est une porte barree"
    ),
    "src.docling_service.extraction._already_ingested": (
        "LIT le graphe par get_writer().find_duplicate ; get_writer est barre, donc leve"
    ),
}

# Les porteurs dont le module ne s'importe PAS sur l'hote, chacun avec sa raison.
# Ils ne sont pas sautes en silence : le test exige qu'ils soient classes ici, et
# qu'aucun d'eux ne soit charge dans le processus du harnais apres armement.
HORS_PROCESSUS = {
    "main._init_graph": "le service FastAPI, jamais importe par le harnais",
    "main._init_objects": "le service FastAPI, jamais importe par le harnais",
    "main._run_extraction": "le service FastAPI, jamais importe par le harnais",
    "main.lifespan": "le service FastAPI, jamais importe par le harnais",
}

# Les constructeurs de clients de store, et eux seuls : le reste est DERIVE.
# Cette liste-ci ne peut pas se tromper en silence comme une liste de modules,
# parce qu'un client de store ne se construit pas autrement — et parce que
# `test_les_semences_construisent_bien_un_client_de_store` la confronte au code.
SEMENCES = ("Minio", "ConnectionPool", "HttpClient")
# Les SDK de store que `src/docling_service` importe. Une semence protege contre une
# porte neuve ; CECI protege contre un client neuf : un SDK de plus, ou une autre
# classe de client du meme SDK, rougit tant que les semences ne le couvrent pas.
SDK_DE_STORE = ("minio", "nebula3", "chromadb")

PARCOURS_DES_SITES = """
import ast, json, pathlib, sys

# ─── LA DERIVATION, et c'est le point : AUCUNE LISTE DE MODULES EN DUR. ──────
# La version precedente bornait le balayage a `MODULES = (nebula, vectors,
# storage, images)`, une seconde liste en dur, non defendue : `mesure` du second
# audit du lot 11 — une porte ecrivante neuve deposee dans `extraction.py`
# passait, rc=0, 5 tests verts. On derive ici, par le texte du code, TOUTE
# fonction qui construit ou RECOIT un client de store, dans tout
# `src/docling_service/*.py`, par point fixe : une fonction qui en appelle une
# autre deja porteuse l'est a son tour.
SEMENCES = set(json.loads(sys.argv[1]))

def _noms_cites(noeud):
    vus = set()
    for n in ast.walk(noeud):
        if isinstance(n, ast.Name):
            vus.add(n.id)
        elif isinstance(n, ast.Attribute):
            vus.add(n.attr)
    return vus

corps = {}
for fichier in sorted(pathlib.Path("src/docling_service").glob("*.py")):
    for noeud in ast.parse(fichier.read_text(encoding="utf-8")).body:
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            corps[f"{fichier.stem}.{noeud.name}"] = _noms_cites(noeud)

porteurs = set()
change = True
while change:
    change = False
    simples = {p.split(".")[1] for p in porteurs}
    for qualifie, cites in corps.items():
        if qualifie not in porteurs and cites & (SEMENCES | simples):
            porteurs.add(qualifie)
            change = True

import importlib
originaux = {}
hors_processus = []
for qualifie in sorted(porteurs):
    module_court, attribut = qualifie.split(".")
    try:
        module = importlib.import_module(f"src.docling_service.{module_court}")
    except Exception as exc:
        # PAS UN SAUT SILENCIEUX : le module est rendu, et le test exige qu'il
        # soit classe. `main` tire `fastapi`, qui n'est pas une dependance de
        # l'hote — c'est aussi la preuve qu'il n'est pas dans le processus du
        # harnais, donc qu'il n'y a aucun site a barrer.
        hors_processus.append([qualifie, f"{type(exc).__name__}: {exc}"])
        continue
    originaux[f"src.docling_service.{qualifie}"] = getattr(module, attribut)
from src.equivalence_des_identifiants import armer_les_barrieres
armement = armer_les_barrieres()
charges_apres_armement = sorted(m for m in sys.modules if m.startswith("src.docling_service."))
encore_lies = []
for nom_mod, mod in sorted(sys.modules.items()):
    if mod is None or not nom_mod.startswith("src"):
        continue
    for attribut, valeur in vars(mod).items():
        for qualifie, original in originaux.items():
            if valeur is original:
                encore_lies.append([f"{nom_mod}.{attribut}", qualifie])
print(json.dumps({"originaux": sorted(originaux), "encore_lies": encore_lies,
                  "sites": armement.sites, "porteurs": sorted(porteurs),
                  "hors_processus": sorted(hors_processus),
                  "charges": charges_apres_armement}))
"""


class TestLesBarrieres:
    """« Ce qui ecrirait leve », a TOUS les sites ou la porte est liee."""

    def test_aucune_porte_d_origine_ne_reste_liee_dans_un_module_src(self):
        """LE TEST QUE L'AUDIT A MONTRE MANQUANT : deux mutants lui survivaient.

        Apres armement, il parcourt chaque module `src.*` charge et rougit si un
        attribut y EST ENCORE une fonction d'origine des modules de stores, hors
        de la liste `NON_ECRIVAINS` que ce test tient lui-meme. `storage` et
        `extraction` importent `get_writer` PAR NOM : barrer `nebula.get_writer`
        dans `nebula` seul le laisse vivant a deux sites. `mesure` : il rougit au
        retrait de `nebula.get_writer` comme de `vectors.get_collection` de la
        liste du producteur (registre 4.37.d).
        """
        releve = _executer(PARCOURS_DES_SITES, json.dumps(SEMENCES))

        inconnus = sorted(set(NON_ECRIVAINS) - set(releve["originaux"]))
        assert not inconnus, f"NON_ECRIVAINS nomme des fonctions qui n'existent plus : {inconnus}"
        portes_vivantes = [
            [site, qualifie]
            for site, qualifie in releve["encore_lies"]
            if qualifie not in NON_ECRIVAINS
        ]
        assert not portes_vivantes, (
            "des portes d'ecriture restent liees a leur original apres armement : "
            f"{portes_vivantes}"
        )

    def test_les_porteurs_derives_sont_tous_barres_ou_classes(self):
        """A4 : plus de liste de MODULES en dur, et rien n'est saute en silence.

        `mesure` du second audit du lot 11 : le balayage etait borne a
        `MODULES = (nebula, vectors, storage, images)`, et une porte ecrivante
        neuve deposee dans `extraction.py` passait, rc=0, 5 tests verts. Les
        porteurs sont desormais DERIVES par point fixe sur tout
        `src/docling_service/*.py` ; chacun est une porte barree, un
        `NON_ECRIVAINS` motive, ou un `HORS_PROCESSUS` motive. Aucun quatrieme cas.
        """
        releve = _executer(PARCOURS_DES_SITES, json.dumps(SEMENCES))

        barres = {f"src.docling_service.{nom}" for nom in list(PORTES) + [TEMOIN_MINIO]}
        classes = (
            barres | set(NON_ECRIVAINS) | {f"src.docling_service.{nom}" for nom in HORS_PROCESSUS}
        )
        inclasses = sorted(f"src.docling_service.{p}" for p in releve["porteurs"])
        assert not [p for p in inclasses if p not in classes], (
            "des porteurs de client de store ne sont ni barres ni classes : "
            f"{[p for p in inclasses if p not in classes]}"
        )

    def test_les_hors_processus_ne_sont_pas_dans_le_processus_du_harnais(self):
        """Leur raison d'etre classes EST qu'ils n'y sont pas : on le mesure."""
        releve = _executer(PARCOURS_DES_SITES, json.dumps(SEMENCES))

        assert sorted(nom for nom, _ in releve["hors_processus"]) == sorted(HORS_PROCESSUS)
        modules = {nom.split(".")[0] for nom in HORS_PROCESSUS}
        assert not [m for m in modules if f"src.docling_service.{m}" in releve["charges"]], releve[
            "charges"
        ]

    def test_les_semences_construisent_bien_un_client_de_store(self):
        """Une semence qui ne seme rien laisserait la derivation vide et MUETTE.

        Le piege « une mutation qui ne mute rien » : on exige que chaque semence
        soit citee par le code des stores, et que la derivation rende au moins
        les portes deja declarees.
        """
        releve = _executer(PARCOURS_DES_SITES, json.dumps(SEMENCES))
        source = "".join(
            chemin.read_text(encoding="utf-8")
            for chemin in sorted((RACINE_DEPOT / "src/docling_service").glob("*.py"))
        )

        muettes = [semence for semence in SEMENCES if f"{semence}(" not in source]
        assert not muettes, f"des semences ne construisent aucun client : {muettes}"
        assert set(releve["porteurs"]) >= set(PORTES) | {TEMOIN_MINIO}

    def test_aucun_sdk_de_store_n_entre_sans_sa_semence(self):
        """UNE SEMENCE PROTEGE D'UNE PORTE NEUVE ; ceci protege d'un CLIENT neuf.

        Sans ce garde, passer `chromadb.HttpClient` a `chromadb.PersistentClient`
        rendrait la derivation aveugle EN SILENCE — le piege « une liste en dur se
        trompe en silence », a un cran de profondeur.
        """
        importe = set()
        for chemin in sorted((RACINE_DEPOT / "src/docling_service").glob("*.py")):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    importe |= {alias.name.split(".")[0] for alias in noeud.names}
                elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                    importe.add(noeud.module.split(".")[0])

        inconnus = sorted(importe & set(SDK_DE_STORE) ^ set(SDK_DE_STORE))
        assert not inconnus, (
            f"SDK_DE_STORE ne decrit plus les imports reels : {inconnus}. "
            "Un SDK de store nouveau ou disparu exige de revoir SEMENCES."
        )

    def test_get_writer_est_barre_a_ses_trois_sites(self):
        """Le site par nom de `storage` et d'`extraction`, en plus de `nebula`."""
        releve = _executer(PARCOURS_DES_SITES, json.dumps(SEMENCES))

        assert set(releve["sites"]["nebula.get_writer"]) >= {
            "src.docling_service.nebula.get_writer",
            "src.docling_service.storage.get_writer",
            "src.docling_service.extraction.get_writer",
        }

    def test_chaque_barriere_leve_se_nomme_et_se_journalise(self):
        """Meme avalee par un `except Exception`, une porte touchee reste au journal."""
        releve = _executer(
            """
import json
from src.equivalence_des_identifiants import armer_les_barrieres, BarriereDEcritureError
armement = armer_les_barrieres()
resultat = {}
for nom, porte in sorted(armement.barrieres.items()):
    try:
        porte()
    except BarriereDEcritureError as exc:
        resultat[nom] = str(exc)
    else:
        resultat[nom] = None
print(json.dumps({"leves": resultat, "journal": armement.journal}))
"""
        )

        assert releve["leves"], "aucune barriere armee : le harnais ecrirait"
        for nom, message in releve["leves"].items():
            assert message is not None and nom in message, (nom, message)
        assert sorted(releve["journal"]) == sorted(releve["leves"])

    def test_la_production_appelee_par_son_module_leve(self):
        """Une barriere rendue mais non POSEE ne garde rien : on appelle par le module."""
        releve = _executer(
            """
import json
from src.equivalence_des_identifiants import armer_les_barrieres, BarriereDEcritureError
armement = armer_les_barrieres()
from src.docling_service import storage, vectors, images, extraction
leve = {}
for nom, fonction in (
    ("storage.persist", lambda: storage.persist([{}], None, None)),
    ("storage.get_writer", lambda: storage.get_writer()),
    ("extraction.get_writer", lambda: extraction.get_writer()),
    ("vectors.get_collection", lambda: vectors.get_collection()),
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
print(json.dumps(leve))
"""
        )

        assert all(v is True for v in releve.values()), releve

    def test_le_temoin_minio_enregistre_l_envoi_et_refuse_le_reste(self):
        """`crop_and_upload` reste celui de la production ; seul l'envoi est remplace."""
        releve = _executer(
            """
import io, json
from src.docling_service import images
production = images.crop_and_upload
from src.equivalence_des_identifiants import armer_les_barrieres, BarriereDEcritureError
armement = armer_les_barrieres()
client = images.get_client()
client.put_object("bucket", "images/x/0123456789_picture.png", io.BytesIO(b""), length=0)
try:
    client.remove_object("bucket", "images/x/0123456789_picture.png")
    refuse = False
except BarriereDEcritureError:
    refuse = True
print(json.dumps({
    "production_intacte": images.crop_and_upload is production,
    "cles": armement.temoin.cles,
    "refuse": refuse,
    "journal": armement.journal,
}))
"""
        )

        assert releve["production_intacte"] is True
        assert releve["cles"] == ["images/x/0123456789_picture.png"]
        assert releve["refuse"] is True
        assert releve["journal"] == ["images.get_client().remove_object"]


# Un chapitre de 30 elements, dont trois `ListItem` VIDES aux rangs 5, 11 et 17 —
# la forme mesuree des puces de Docling. Le convertisseur est remplace ; tout le
# reste est le chemin de production, `_extract_flat` et `DocumentAccumulator`
# compris.
SCENARIO = """
import json, sys
from pathlib import Path
from types import SimpleNamespace as NS
from src import equivalence_des_identifiants as eq

RACINE = Path(sys.argv[1])
CLE = "htms/L/1. Ch.html"
for sous in ("corpus", "cleaned"):
    (RACINE / sous / "htms/L").mkdir(parents=True, exist_ok=True)
    (RACINE / sous / CLE).write_text("<html><body><p>x</p></body></html>")

armement = eq.armer_les_barrieres()
lots = eq.installer_la_capture()
from src.docling_service import elements, extraction

VIDES = (5, 11, 17)
def items(repare):
    return [
        NS(label="list_item" if i in VIDES else "text",
           text=(f"Texte recupere de la puce {i}" if repare else "") if i in VIDES
           else f"Passage {i} du chapitre",
           prov=None, self_ref=f"#/texts/{i}")
        for i in range(30)
    ]
ETAT = {"repare": False}
class Document:
    def iterate_items(self):
        return [(item, 0) for item in items(ETAT["repare"])]
extraction.get_converter = lambda: NS(convert=lambda chemin: NS(document=Document()))

def reextraire(partition_key):
    return eq.reextraire(
        partition_key, RACINE / "corpus", RACINE / "cleaned", lots, armement.temoin
    )

GRAPHE = {"ids": set()}
def monde():
    return eq.Monde(
        documents_du_corpus=lambda: [CLE],
        documents_du_graphe=lambda: [CLE],
        ids_du_graphe=lambda cle: set(GRAPHE["ids"]),
        objets_listes=lambda partition_key: None,
        reextraire=reextraire,
    )
sortie = []
dire = sortie.append
"""


def _scenario(tmp_path, suite):
    return _executer(SCENARIO + suite + "\nprint(json.dumps(resultat))", str(tmp_path))


class TestLesFauxVertsDeLAudit:
    """Les deux scenarios de l'audit du lot 11, par le VRAI `_extract_flat`."""

    def test_trois_listitem_qui_recoivent_du_texte_rougissent_apres_la_campagne(self, tmp_path):
        """FAUX VERT N° 1 : relance apres la campagne, l'ancien harnais disait `OK`.

        Le graphe d'apres est ECRIT par le code repare, et l'ancien harnais
        reextrayait avec ce meme code : il comparait X a X. Le harnais compare
        desormais a l'instantane fige AVANT, et doit dire les trois deplaces,
        imputes a `text50`.
        """
        resultat = _scenario(
            tmp_path,
            """
GRAPHE["ids"] = {l["element_id"] for l in reextraire(CLE).lignes}
rc_figer = eq.figer(monde(), RACINE / "instantane", {"date": "essai"}, armement.journal, dire)
fige = eq.lire_l_instantane(RACINE / "instantane").documents[CLE].lignes
avant = {l["position_in_page"]: l["element_id"] for l in fige}
ETAT["repare"] = True                       # la campagne : le code repare reecrit le graphe
GRAPHE["ids"] = {l["element_id"] for l in reextraire(CLE).lignes}
instantane = eq.lire_l_instantane(RACINE / "instantane")
EMPREINTE = instantane.empreinte
rc_nu = eq.comparer(monde(), instantane, EMPREINTE, set(), armement.journal, dire)
declares = {avant[i] for i in VIDES}
rc_declare = eq.comparer(monde(), instantane, EMPREINTE, declares, armement.journal, dire)
rc_trop = eq.comparer(
    monde(), instantane, EMPREINTE, declares | {avant[0]}, armement.journal, dire)
resultat = {"rc_figer": rc_figer, "rc_nu": rc_nu, "rc_declare": rc_declare, "rc_trop": rc_trop,
            "sortie": sortie, "journal": armement.journal}
""",
        )

        assert resultat["rc_figer"] == 0, resultat["sortie"]
        assert resultat["rc_nu"] == 1, "trois ids disparus, et le harnais dit OK"
        assert any("DEPLACES 3, DECLARES 0" in ligne for ligne in resultat["sortie"])
        assert any("{'text50': 3}" in ligne for ligne in resultat["sortie"])
        assert resultat["rc_declare"] == 0, "la declaration exacte doit etre verte"
        assert resultat["rc_trop"] == 1, "un declare qui ne bouge pas doit rougir"
        assert resultat["journal"] == []

    def test_une_production_qui_calcule_sur_cleaned_rougit_avant_la_campagne(self, tmp_path):
        """FAUX VERT N° 2 : l'ancien harnais recalculait, et ne voyait pas la derive.

        Le graphe est celui de la production SAINE. La production derive ensuite
        au site d'appel : elle calcule sur `.cleaned/<cle>`. Aucun identifiant
        emis n'est plus celui du graphe, et `figer` doit REFUSER d'ecrire.
        """
        resultat = _scenario(
            tmp_path,
            """
GRAPHE["ids"] = {l["element_id"] for l in reextraire(CLE).lignes}
vrai = elements.compute_id
elements.compute_id = lambda f, p, pos, t: vrai(f".cleaned/{f}", p, pos, t)
emis = reextraire(CLE).lignes
rc = eq.figer(monde(), RACINE / "instantane", {"date": "essai"}, armement.journal, dire)
resultat = {"rc": rc, "conformes": sum(l["element_id"] in GRAPHE["ids"] for l in emis),
            "ecrit": (RACINE / "instantane" / "MANIFESTE.tsv").exists(), "sortie": sortie}
""",
        )

        assert resultat["conformes"] == 0, "le scenario ne derive plus : il ne prouve rien"
        assert resultat["rc"] == 1, "la production ne rend plus le graphe, et le harnais dit OK"
        assert resultat["ecrit"] is False, "un instantane faux a ete fige"
        assert any("emis seul / graphe seul  : 30 / 30" in ligne for ligne in resultat["sortie"])
        # LA RAISON, et pas seulement le rc : le controle negatif rougit aussi ce
        # scenario, par ricochet (la mutation `site_d_appel` y devient nulle), et
        # masquerait une confrontation au graphe qui ne garderait plus rien.
        assert any("l'emission n'est pas le graphe" in ligne for ligne in resultat["sortie"])
