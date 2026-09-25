"""Tests du harnais d'equivalence : il doit savoir rendre un rouge.

Le harnais de `src/equivalence_des_identifiants.py` fige, avant la campagne de
reingestion, un instantane des `element_id` emis par la production, puis dit
apres la campagne lesquels ont bouge.

Deux faux verts sont tenus par :class:`TestLesFauxVertsDeLAudit` :

- relance apres la campagne, un harnais qui reextrait avec le code du jour le
  compare au graphe ecrit par ce meme code : trois `ListItem` qui recoivent du
  texte rendraient `OK`, rc=0 ;
- un harnais qui recalcule l'identifiant au lieu de garder celui qui est emis ne
  voit pas une production qui calcule sur `.cleaned/<cle>`.

Les deux scenarios passent par le vrai `_extract_flat`, seul le convertisseur
Docling etant remplace : c'est le chemin que le harnais emprunte en campagne.

Une mutation qui ne mute rien rendrait le controle negatif vert sans rien
prouver (par exemple `page_no >= 12` sur un chapitre tout entier en page 1) :
:class:`TestUneMutationNulleEstImpossible` le verifie.

Les tests qui arment les barrieres tournent en sous-processus : l'armement
survit dans `sys.modules` et ferait tomber six tests de
`tests/unit/test_storage.py`.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.equivalence_des_identifiants import (
    EMPREINTES_ATTENDUES,
    MUTATIONS,
    PORTES,
    SITE_D_APPEL,
    TEMOIN_DU_STOCKAGE,
    BarriereDEcritureError,
    Bilan,
    Controle,
    DossierHorsCampagneError,
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
    dossier_de_campagne,
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

    Tous sur la meme page : c'est la forme d'un chapitre HTML, sur laquelle une
    mutation conditionnee par `page_no >= 12` serait nulle.
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
    """Un releve dont des elements partagent leur `text50`.

    Des lignes blanches de code (`""`), une virgule repetee : la forme reelle
    d'un chapitre technique. Une attribution par heuristique (rapprochement de
    textes identiques) y imputerait la mutation `filename` a `position_in_page`.
    """
    textes = ["", "", ",", "Texte A", "", ",", "Texte B", ""] * 8
    return _lignes(nombre=len(textes), textes=textes)


# ─── Le releve ──────────────────────────────────────────────────────────────


class TestLeReleveEstCeluiDeLEmission:
    def test_l_identifiant_emis_est_garde_tel_quel(self):
        """L'identifiant du releve est `element["id"]`, jamais un recalcul.

        Un harnais qui recalcule ne voit pas une production qui calcule sur
        autre chose que ce qu'elle declare.
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

    @pytest.mark.parametrize("champ", ["id", "page_no", "page_position", "text", "label"])
    def test_chaque_champ_du_contrat_leve_quand_il_disparait(self, champ):
        """Mutation M31 : les cinq champs sont lus par indexation.

        Avec `.get`, un champ disparu du contrat deviendrait un zero, une chaine
        vide ou un `None` qui se compare. `self_ref` n'y est pas : il est lu par
        `.get` a dessein, un element sans `self_ref` etant licite (l'appariement
        le rend "").
        """
        emis = {
            "id": "0123456789",
            "page_no": 1,
            "page_position": 0,
            "text": "Un passage",
            "label": "text",
            "self_ref": "#/texts/0",
        }
        del emis[champ]

        with pytest.raises(KeyError):
            releve_de_l_emission([emis], CLE)

    def test_un_self_ref_absent_est_licite_et_devient_vide(self):
        """La contrepartie du test ci-dessus : `self_ref` seul tolere l'absence."""
        emis = {"id": "x", "page_no": 1, "page_position": 0, "text": "a", "label": "text"}

        (ligne,) = releve_de_l_emission([emis], CLE)

        assert ligne["self_ref"] == ""

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
        # Une ligne de donnees, et non l'entete : alterer l'entete rougirait par
        # le controle d'entete, et ne testerait pas l'empreinte.
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
        """Ecraser l'instantane serait perdre l'avant, la seule chose qu'il garde."""
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})

        with pytest.raises(FileExistsError):
            ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-25"})

    def test_le_nombre_de_lignes_annonce_par_le_manifeste_est_verifie(self, tmp_path):
        """Mutation M14 : le manifeste annonce `elements`, et la relecture recompte.

        Sans ce controle, un releve tronque passerait pour complet, et ses
        elements manquants deviendraient des deplacements invisibles. Le SHA-256
        du releve est recalcule ici pour que ce soit le compte qui rougisse, et
        non l'empreinte : sinon le test ne dirait rien du compte.
        """
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})
        (releve,) = [f for f in tmp_path.iterdir() if f.name.startswith("htms__")]
        rangs = releve.read_text(encoding="utf-8").split("\n")
        releve.write_text("\n".join(rangs[:-2]) + "\n", encoding="utf-8")
        manifeste = tmp_path / "MANIFESTE.tsv"
        ancienne = hashlib.sha256("\n".join(rangs).encode("utf-8")).hexdigest()
        nouvelle = hashlib.sha256(releve.read_bytes()).hexdigest()
        manifeste.write_text(
            manifeste.read_text(encoding="utf-8").replace(ancienne, nouvelle), encoding="utf-8"
        )

        with pytest.raises(InstantaneCorrompuError, match="lignes"):
            lire_l_instantane(tmp_path)

    def test_le_format_de_l_instantane_est_verifie(self, tmp_path):
        """Mutation M28 : un instantane d'un autre format ne se relit pas en silence."""
        ecrire_l_instantane(tmp_path, [_emission()], {"date": "2026-09-24"})
        manifeste = tmp_path / "MANIFESTE.tsv"
        manifeste.write_text(
            manifeste.read_text(encoding="utf-8").replace(
                "instantane-des-identifiants/1", "instantane-des-identifiants/0"
            ),
            encoding="utf-8",
        )

        with pytest.raises(InstantaneCorrompuError, match="format"):
            lire_l_instantane(tmp_path)

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

        # La premiere occurrence de `#/texts/1` : un appariement qui oublierait le
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
        """Cas observe : une ligne de code vide herite de l'identifiant d'une puce.

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

    def test_identiques_retranche_les_reassignes(self):
        """Mutation M34 : `identiques` ne compte que les elements qui n'ont pas bouge.

        Un identifiant reassigne est present des deux cotes, donc compte par la
        difference d'ensembles, mais il designe un autre element : il ne doit
        pas etre compte parmi les identiques.
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

        ids_communs = {l["element_id"] for l in avant} & {l["element_id"] for l in apres}
        assert len(bilan.reassignes) == 1
        assert bilan.identiques == len(ids_communs) - 1

    def test_la_confrontation_ne_rend_pas_zero_sur_un_graphe_vide(self):
        """Une sonde qui ne lit rien rendrait aussi « graphe seul = 0 »."""
        confrontation = confronter_au_graphe(_lignes(), set())

        assert confrontation.identiques == 0
        assert confrontation.emis_seul == 60


def _bilan(lignes, apres):
    return comparer_les_releves("p", lignes, apres)


class TestLeVerdict:
    """rc=0 si et seulement si l'ensemble deplace egale l'ensemble declare."""

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
        """Sinon une reparation non appliquee passerait en silence."""
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

    def test_une_cle_apparue_sans_deplacement_declare_est_rouge(self):
        """Mutation M22 : la branche « apparue », en plus de « disparue ».

        Une cle d'objet apparue que la declaration n'explique pas est un crop de
        plus dans le bucket, sous un identifiant que personne n'a annonce.
        """
        raisons = raisons_des_cles_d_objet(
            "p",
            avant=["images/L/0123456789_picture.png"],
            apres=["images/L/0123456789_picture.png", "images/L/abcdef0123_picture.png"],
            bilan=Bilan("p", identiques=1, deplaces=[], apparus_sans_contrepartie=[]),
            declares=set(),
        )

        assert raisons and "apparue" in raisons[0], raisons

    def test_une_cle_apparue_sous_un_deplacement_declare_est_verte(self):
        """La contrepartie : sans elle, le test ci-dessus passerait sur un `True` nu."""
        avant = _lignes(nombre=1)
        apres = _reparer(avant, [0])
        bilan = comparer_les_releves("p", avant, apres)

        raisons = raisons_des_cles_d_objet(
            "p",
            avant=[f"images/L/{avant[0]['element_id']}_picture.png"],
            apres=[f"images/L/{apres[0]['element_id']}_picture.png"],
            bilan=bilan,
            declares={avant[0]["element_id"]},
        )

        assert raisons == []

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
        """Vue en totalite, et imputee a son terme, `text50` partages compris."""
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
    """Une mutation sans effet est refusee, pas comptee comme un succes."""

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
        """Contre-epreuve : seule la mutation nulle est refusee, pas la mutation."""
        mutes = appliquer_la_mutation(self._inerte(), _lignes(page_no=12))

        assert mutes[0]["page_no"] == 13

    def test_le_controle_rend_une_mutation_nulle_comme_un_echec(self):
        """Un releve vide ne laisse rien muter : chaque controle doit etre rouge."""
        assert not any(c.ok for c in controle_negatif("p", []))


class TestLeControleSeJuge:
    def test_une_mutation_mal_attribuee_n_est_pas_un_controle_vert(self):
        """Voir sans savoir pourquoi est un rouge."""
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


def _une_emission(partition_key="htms/L/1. Ch.html", lignes=None, empreinte="e" * 64):
    return Emission(
        partition_key=partition_key,
        cle=CLE,
        lignes=_lignes(nombre=12) if lignes is None else lignes,
        cles_d_objet=["images/L/0123456789_picture.png"],
        empreinte_de_l_entree=empreinte,
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

    def test_des_cles_d_objet_que_le_stockage_ne_liste_pas_sont_rouges(self, tmp_path):
        emission = _une_emission()
        monde = _monde([emission], objets={emission.partition_key: set()})

        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 1

    def test_une_barriere_touchee_est_rouge_meme_avalee(self, tmp_path):
        journal = ["vectors.get_collection"]

        assert (
            figer(_monde([_une_emission()]), tmp_path, {"date": "t"}, journal, lambda _: None) == 1
        )

    def test_un_graphe_qui_porte_deja_les_identifiants_mutes_est_rouge(self, tmp_path):
        """Tient la clause `vu_du_graphe > 0` du controle negatif.

        Un graphe qui porterait deja les identifiants mutes rendrait
        `emis_seul == 0` : la mutation serait « vue » par le seul appariement,
        sans confirmation par le graphe.

        Ici, le graphe porte l'emission et tous les identifiants qu'une mutation
        produirait. La raison attendue est celle de la mutation ; sans la
        clause, elle disparait.
        """
        emission = _une_emission()
        mutes = {
            identifiant_par_la_formule(ligne)
            for mutation in MUTATIONS.values()
            for ligne in appliquer_la_mutation(mutation, emission.lignes)
        }
        graphe = {emission.cle: {ligne["element_id"] for ligne in emission.lignes} | mutes}
        sortie = []

        rc = figer(_monde([emission], graphe=graphe), tmp_path, {"date": "t"}, [], sortie.append)

        assert rc == 1
        assert any("0 hors graphe — NON VU" in ligne for ligne in sortie), sortie
        assert any("mutation filename : NON VU" in ligne for ligne in sortie), sortie


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
        """Un corpus vide ne rend pas vert (registre 4.38.a).

        Il faut que `_ecarts_de_couverture` compare chaque paire dans les deux
        sens (`instantane - corpus` compris), et que `comparer` compte comme
        raison tout document absent du corpus. Le corpus arrive par un montage :
        un montage vide ou partiel est un cas reel.
        """
        sortie = []
        instantane = self._fige(tmp_path, [_une_emission()])

        monde = _monde([_une_emission()], corpus=[])

        rc = comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert rc == 1
        assert any("DOCUMENTS COMPARES 0 / 1" in ligne for ligne in sortie), sortie

    def test_un_seul_document_sorti_du_corpus_est_rouge(self, tmp_path):
        """Meme cas, en plus discret : l'instantane en porte 2, le corpus 1."""
        emissions = [_une_emission(), _une_emission("htms/L/2. Ch.html")]
        instantane = self._fige(tmp_path, emissions)
        sortie = []

        monde = _monde(emissions, corpus=["htms/L/1. Ch.html"])

        rc = comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert rc == 1
        assert any("DOCUMENTS COMPARES 1 / 2" in ligne for ligne in sortie), sortie
        # La raison explicite, et non le seul rc : l'ecart de couverture rougit
        # deja, donc sans cette assertion le retrait de `non_compares` passerait
        # (mutation A1-b).
        assert any("n'ont PAS ete compares" in ligne for ligne in sortie), sortie

    def test_un_graphe_vide_avec_un_corpus_intact_est_rouge(self, tmp_path):
        """Rouge par le sens `corpus - graphe` de la couverture."""
        instantane = self._fige(tmp_path, [_une_emission()])

        monde = _monde([_une_emission()], documents_du_graphe=[])

        assert comparer(monde, instantane, instantane.empreinte, set(), [], lambda _: None) == 1

    def test_chaque_paire_est_comparee_dans_les_deux_sens(self):
        """La docstring promet « un document manquant d'un cote est un rouge »."""
        raisons = _ecarts_de_couverture(corpus=set(), graphe={"d"}, instantane={"d"})

        assert any("dans graphe et pas dans corpus" in r for r in raisons), raisons
        assert any("dans instantane et pas dans corpus" in r for r in raisons), raisons

    def test_un_journal_de_barrieres_non_vide_rougit_comparer(self, tmp_path):
        """Mutation A2 : le journal non vide rougit aussi `comparer`.

        Pendant, cote `comparer`, de `test_une_barriere_touchee_est_rouge_meme_avalee`.
        """
        instantane = self._fige(tmp_path, [_une_emission()])
        journal = ["vectors.get_collection"]

        monde = _monde([_une_emission()])

        rc = comparer(monde, instantane, instantane.empreinte, set(), journal, lambda _: None)

        assert rc == 1


class TestCeQuiSeDitDevantUnRouge:
    """Mutation M35 : devant un rouge, la sortie dit si l'entree a change."""

    def _fige(self, tmp_path, emissions):
        figer(_monde(emissions), tmp_path, {"date": "t"}, [], lambda _: None)
        return lire_l_instantane(tmp_path)

    def test_une_entree_convertie_qui_change_est_signalee(self, tmp_path):
        instantane = self._fige(tmp_path, [_une_emission(empreinte="a" * 64)])
        sortie = []
        monde = _monde([_une_emission(empreinte="b" * 64)])

        comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert any("L'ENTREE CONVERTIE A CHANGE" in ligne for ligne in sortie), sortie

    def test_une_entree_inchangee_ne_se_signale_pas(self, tmp_path):
        """Sans ce pendant, un `dire` inconditionnel passerait le test ci-dessus."""
        instantane = self._fige(tmp_path, [_une_emission(empreinte="a" * 64)])
        sortie = []
        monde = _monde([_une_emission(empreinte="a" * 64)])

        comparer(monde, instantane, instantane.empreinte, set(), [], sortie.append)

        assert not any("L'ENTREE CONVERTIE A CHANGE" in ligne for ligne in sortie), sortie


class TestLeHarnaisNeSeRetautologisePas:
    """Refiger apres la campagne ne doit pas rendre vert (mutations A3, registre 4.39.a).

    L'empreinte attendue et le repertoire de campagne sont a des sites fixes,
    resolus depuis l'emplacement du module : aucun argument ne peut les
    deplacer.
    """

    def test_l_empreinte_attendue_ne_depend_plus_de_l_argument(self, tmp_path):
        """Une table voisine, ecrite a la main, n'authentifie rien."""
        dossier = tmp_path / "2026-09-24-instantane-des-identifiants"
        dossier.mkdir()
        (tmp_path / "empreintes-des-instantanes.tsv").write_text(
            "dossier\tempreinte\n2026-09-24-instantane-des-identifiants\tabc123\n",
            encoding="utf-8",
        )

        with pytest.raises(DossierHorsCampagneError, match="hors du repertoire de campagne"):
            empreinte_attendue(dossier)

    def test_le_repertoire_de_campagne_est_resolu_depuis_le_module(self):
        """Il ne vient ni de l'argument, ni du repertoire courant, ni d'une variable."""
        from src import equivalence_des_identifiants as module

        assert (
            Path(module.__file__).resolve().parents[1] / "documentation/campagnes"
        ) == module.REPERTOIRE_DE_CAMPAGNE
        assert module.REPERTOIRE_DE_CAMPAGNE.is_dir()

    def test_un_enfant_indirect_du_repertoire_est_refuse(self, tmp_path):
        """`…/campagnes/bis/instantane` n'est pas un enfant direct : refuse."""
        repertoire = tmp_path / "campagnes"
        (repertoire / "bis" / "instantane").mkdir(parents=True)

        with pytest.raises(DossierHorsCampagneError):
            dossier_de_campagne(repertoire / "bis" / "instantane", repertoire)

    def test_un_enfant_direct_du_repertoire_est_accepte_et_resolu(self, tmp_path):
        repertoire = tmp_path / "campagnes"
        (repertoire / "instantane").mkdir(parents=True)

        assert (
            dossier_de_campagne(repertoire / "." / "instantane", repertoire)
            == (repertoire / "instantane").resolve()
        )

    def test_l_empreinte_de_l_instantane_du_depot_est_celle_de_la_constante(self):
        """La constante du module, contre l'instantane versionne du depot."""
        nom = "2026-09-24-instantane-des-identifiants"
        dossier = RACINE_DEPOT / "documentation/campagnes" / nom

        assert EMPREINTES_ATTENDUES[nom] == lire_l_instantane(dossier).empreinte

    def test_un_instantane_refige_porte_une_autre_empreinte_et_rougit(self, tmp_path):
        """Refiger apres la campagne puis comparer contre ce nouvel instantane rougit."""
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

    def test_un_dossier_absent_de_la_constante_est_refuse(self):
        """Un second instantane, ecrit dans le repertoire mais inscrit nulle part."""
        dossier = RACINE_DEPOT / "documentation/campagnes/refige-en-douce"

        with pytest.raises(EmpreinteInattendueError, match="n'est pas dans EMPREINTES_ATTENDUES"):
            empreinte_attendue(dossier)

    def test_refiger_dans_le_meme_dossier_rend_un_rc_1_et_non_une_trace(self, tmp_path):
        """`FileExistsError` devient rc=1 : une trace d'appel n'est pas un verdict."""
        monde = _monde([_une_emission()])
        sortie = []
        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 0

        rc = figer(monde, tmp_path, {"date": "t"}, [], sortie.append)

        assert rc == 1
        assert any("ne s'ecrase pas" in ligne for ligne in sortie), sortie

    def test_le_site_reel_du_depot_s_authentifie(self):
        """`empreinte_attendue` authentifie l'instantane versionne du depot."""
        dossier = RACINE_DEPOT / "documentation/campagnes/2026-09-24-instantane-des-identifiants"

        assert empreinte_attendue(dossier) == lire_l_instantane(dossier).empreinte


class TestLesTroisGestesDeLAudit:
    """Le script lui-meme, par son code de sortie : figer ou comparer ailleurs rend 1.

    Scenario : `figer <ailleurs>`, une table d'empreintes ecrite a cote, puis
    `comparer <ailleurs>`. Les deux gestes refusent avant tout armement et
    toute connexion, donc le test tourne sur l'hote, sans store.
    """

    SCRIPT = "scripts/campagne/verifier-l-equivalence-des-identifiants.py"

    def _lancer(self, *arguments, cwd):
        return subprocess.run(
            [sys.executable, self.SCRIPT, *arguments],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT), "HOME": str(cwd)},
        )

    def test_figer_hors_du_repertoire_de_campagne_rend_1(self, tmp_path):
        cible = tmp_path / "bis" / "2026-09-24-instantane-des-identifiants"

        acheve = self._lancer("figer", str(cible), cwd=tmp_path)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "hors du repertoire de campagne" in acheve.stdout, acheve.stdout
        assert not cible.exists(), "rien n'a ete ecrit hors du repertoire"

    def test_comparer_hors_du_repertoire_de_campagne_rend_1_malgre_la_table_voisine(self, tmp_path):
        """Une table d'empreintes posee a cote du dossier n'est pas lue."""
        cible = tmp_path / "bis" / "2026-09-24-instantane-des-identifiants"
        cible.mkdir(parents=True)
        (cible.parent / "empreintes-des-instantanes.tsv").write_text(
            f"dossier\tempreinte\n{cible.name}\tdeadbeef\n", encoding="utf-8"
        )

        acheve = self._lancer("comparer", str(cible), cwd=tmp_path)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "hors du repertoire de campagne" in acheve.stdout, acheve.stdout

    def test_comparer_un_instantane_non_inscrit_rend_un_verdict_avant_toute_connexion(self):
        """Mutation N4 : un instantane non inscrit est refuse avant la connexion.

        Le dossier vise est un enfant direct du repertoire de campagne, donc il
        passe le premier refus, et il n'est pas dans `EMPREINTES_ATTENDUES`. Il
        n'existe pas : ce test n'ecrit rien dans le depot.

        `nebula3` n'est pas installe sur l'hote : un refus trop tardif rendrait
        aussi 1, mais sur un `ModuleNotFoundError` leve par `Graphe`. Les
        assertions portent donc sur le message et sur l'absence de trace
        d'appel, pas sur le seul rc (registre 4.40.g).
        """
        cible = RACINE_DEPOT / "documentation/campagnes/2099-01-01-instantane-non-inscrit"
        assert not cible.exists(), "ce test n'ecrit rien dans le depot"

        acheve = self._lancer("comparer", str(cible), cwd=RACINE_DEPOT)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "n'est pas dans EMPREINTES_ATTENDUES" in acheve.stdout, acheve.stdout
        assert "Traceback" not in acheve.stderr, acheve.stderr
        # Avant toute connexion et tout armement : ni l'un ni l'autre n'a imprime.
        assert "barrieres d'ecriture armees" not in acheve.stdout, acheve.stdout
        assert "nebula3" not in acheve.stderr, acheve.stderr


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


# ─── Barrieres a l'execution ────────────────────────────────────────────────
#
# La preuve de non-ecriture se fait a l'execution, sur les constructeurs des SDK
# (:func:`~src.equivalence_des_identifiants.barrer_les_sdk_de_store`), et non par
# une analyse statique des porteurs de client : un alias d'import, un `getattr`,
# un client construit au niveau du module ou une porte hors du dossier balaye
# echappent a toute analyse statique (registre 4.39.b). `TestLesPortesNeuvesLevent`
# mesure ces chemins un par un.
#
# Restent verifiees ci-dessous : les barrieres par site, seconde couche, sur la
# liste declaree des portes (sans pretention d'exhaustivite), et la
# classification des dependances tierces, lue depuis `src/` et non depuis une
# copie locale (voir `test_toute_dependance_tierce_de_src_est_classee`).

PARCOURS_DES_SITES = """
import importlib, json, sys

declarees = json.loads(sys.argv[1])
originaux = {}
for qualifie in declarees:
    module_court, attribut = qualifie.split(".")
    module = importlib.import_module(f"src.docling_service.{module_court}")
    originaux[f"src.docling_service.{qualifie}"] = getattr(module, attribut)
from src.equivalence_des_identifiants import armer_les_barrieres
armement = armer_les_barrieres()
encore_lies = []
for nom_mod, mod in sorted(sys.modules.items()):
    if mod is None or not nom_mod.startswith("src"):
        continue
    for attribut, valeur in vars(mod).items():
        for qualifie, original in originaux.items():
            if valeur is original:
                encore_lies.append([f"{nom_mod}.{attribut}", qualifie])
print(json.dumps({"originaux": sorted(originaux), "encore_lies": encore_lies,
                  "sites": armement.sites, "sdk_barres": armement.sdk.barres,
                  "sdk_absents": armement.sdk.absents}))
"""

DECLAREES = json.dumps([*PORTES, TEMOIN_DU_STOCKAGE])

# Sept chemins de construction d'un client. Chacun est un programme complet : il
# arme, puis tente de construire un client par un chemin different. Le verdict
# attendu est le meme partout : `BarriereDEcritureError`.
#
# Ils portent sur `minio`, seul des trois SDK installe sur l'hote, donc seul
# testable sans conteneur. Les trois SDK sont mesures ensemble dans l'image
# d'extraction (registre 4.39.b).
_ARMER = """
from src.equivalence_des_identifiants import armer_les_barrieres

armement = armer_les_barrieres()
"""
PORTES_NEUVES = {
    "alias d'import APRES armement": _ARMER
    + """
from minio import Minio as _M

_M("h", access_key="a", secret_key="b")
""",
    "alias d'import AVANT armement": """
from minio import Minio as _M
"""
    + _ARMER
    + """
_M("h", access_key="a", secret_key="b")
""",
    "getattr sur le module du SDK": _ARMER
    + """
import minio

getattr(minio, "Minio")("h", access_key="a", secret_key="b")
""",
    "client construit au NIVEAU DU MODULE": _ARMER
    + """
import pathlib
import sys
import tempfile

dossier = tempfile.mkdtemp()
sys.path.insert(0, dossier)
pathlib.Path(dossier, "porte_neuve.py").write_text(
    chr(10).join(
        [
            "from minio import Minio",
            "",
            'CLIENT = Minio("h", access_key="a", secret_key="b")',
        ]
    )
)
import porte_neuve
""",
    "porte deposee dans src/pipeline": _ARMER
    + """
import os

for nom, valeur in (
    ("S3_ENDPOINT", "h"),
    ("S3_ACCESS_KEY", "a"),
    ("S3_SECRET_KEY", "b"),
    ("S3_BUCKET", "seau"),
):
    os.environ.setdefault(nom, valeur)
from src.pipeline.media import ExportateurDImages

ExportateurDImages("un-document")._get_client()
""",
    "client d'ADMINISTRATION du SDK": _ARMER
    + """
from minio import MinioAdmin

MinioAdmin("h")
""",
    "import du SDK POSTERIEUR a l'armement": _ARMER
    + """
import importlib

importlib.import_module("minio").Minio("h", access_key="a", secret_key="b")
""",
}

# Huit chemins qui ne passent par aucun nom (registre 4.40.f). Le rebondage des
# noms ne les prend pas ; ils passent tous par la classe, dont l'`__init__` leve
# (`_barrer_la_classe`).
#
# Chacun capture le constructeur avant l'armement : au moment ou la barriere se
# pose, le nom `Minio` n'est plus le seul chemin vers la classe. Une capture
# posterieure serait prise par le rebondage, donc ne prouverait rien de neuf.
_CAPTURES = """
import functools

from minio import Minio


class SousClasse(Minio):
    pass


class Porteuse:
    fabrique = Minio


DICO = {"minio": Minio}


def par_defaut(fabrique=Minio):
    return fabrique("h", access_key="a", secret_key="b")


def fermeture():
    capture = Minio
    return lambda: capture("h", access_key="a", secret_key="b")


FERMETURE = fermeture()
PARTIEL = functools.partial(Minio, "h", access_key="a", secret_key="b")
LECTEUR = Minio("h", access_key="a", secret_key="b")
"""

CHEMINS_DE_CLASSE = {
    "sous-classe capturee avant l'armement": _CAPTURES
    + _ARMER
    + '\nSousClasse("h", access_key="a", secret_key="b")\n',
    "dictionnaire rempli avant l'armement": _CAPTURES
    + _ARMER
    + '\nDICO["minio"]("h", access_key="a", secret_key="b")\n',
    "attribut de classe": _CAPTURES
    + _ARMER
    + '\nPorteuse.fabrique("h", access_key="a", secret_key="b")\n',
    "argument par defaut": _CAPTURES + _ARMER + "\npar_defaut()\n",
    "fermeture capturee avant l'armement": _CAPTURES + _ARMER + "\nFERMETURE()\n",
    "partial capture avant l'armement": _CAPTURES + _ARMER + "\nPARTIEL()\n",
    "type(client) d'un client construit avant": _CAPTURES
    + _ARMER
    + '\ntype(LECTEUR)("h", access_key="a", secret_key="b")\n',
    "client.__class__ d'un client construit avant": _CAPTURES
    + _ARMER
    + '\nLECTEUR.__class__("h", access_key="a", secret_key="b")\n',
}

# Contre-epreuve : barrer la classe ne doit pas casser les clients de lecture du
# harnais, construits avant l'armement (leur `__init__` a deja tourne). Une
# barriere qui les casserait rendrait le harnais inutilisable.
CLIENT_DE_LECTURE_SURVIT = (
    _CAPTURES
    + _ARMER
    + """
import json

# Une methode de LECTURE sur le client construit avant l'armement. L'appel part
# sur le reseau et echoue — il n'y a aucun serveur en face — et c'est le verdict
# recherche : la BARRIERE ne s'est pas interposee.
from src.equivalence_des_identifiants import BarriereDEcritureError

try:
    LECTEUR.list_buckets()
    verdict = "lit"
except BarriereDEcritureError:
    verdict = "CASSE PAR LA BARRIERE"
except Exception as exc:
    verdict = "lit" if "Barriere" not in type(exc).__name__ else "CASSE PAR LA BARRIERE"
print(json.dumps(verdict))
"""
)


@contextlib.contextmanager
def _modules_temporaires(**modules):
    """Pose des modules factices dans `sys.modules`, et les retire a coup sur.

    Le balayage de `_barrer_le_constructeur` parcourt `sys.modules` : un module
    factice qu'on y laisserait serait vu par tous les tests suivants.
    """
    for nom, module in modules.items():
        assert nom not in sys.modules, nom
        sys.modules[nom] = module
    try:
        yield
    finally:
        for nom in modules:
            del sys.modules[nom]


class TestLaBarriereDescendALaClasse:
    """Les huit chemins qui ne passent par aucun nom levent (registre 4.40.f).

    Ils portent sur `minio`, seul des trois SDK installe sur l'hote, donc seul
    testable sans conteneur. Les trois SDK sont mesures ensemble dans l'image.
    """

    def _lancer(self, code):
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        )

    @pytest.mark.parametrize("nom", sorted(CHEMINS_DE_CLASSE))
    def test_un_chemin_qui_ne_passe_par_aucun_nom_leve(self, nom):
        acheve = self._lancer(CHEMINS_DE_CLASSE[nom])

        assert acheve.returncode != 0, f"{nom} : rc=0, le chemin a construit son client"
        assert "BarriereDEcritureError" in acheve.stderr, (nom, acheve.stdout, acheve.stderr)

    def test_le_client_de_lecture_construit_avant_l_armement_lit_toujours(self):
        """Contre-epreuve : la barriere ne casse pas le client de lecture du harnais."""
        assert _executer(CLIENT_DE_LECTURE_SURVIT) == "lit"

    def test_l_armement_rend_ce_qu_il_a_pris_a_la_classe_et_ce_qu_il_n_a_pas_pu(self):
        """Ce que la premiere couche n'a pas pu prendre est rendu, pas tu."""
        releve = _executer(
            """
import json
from src.equivalence_des_identifiants import armer_les_barrieres

armement = armer_les_barrieres()
print(json.dumps({"classes": armement.sdk.classes, "sans_classe": armement.sdk.sans_classe}))
"""
        )

        assert releve["classes"] == ["minio.Minio", "minio.MinioAdmin"], releve
        assert releve["sans_classe"] == {}, releve

    def test_le_rebondage_des_noms_relie_les_modules_deja_charges(self):
        """La seconde couche, sur ce qu'elle seule tient : une fabrique.

        Les deux constructeurs de `minio` sont des classes, donc pris par la
        premiere couche. Le rebondage des noms tient seul les constructeurs qui
        ne sont pas des classes, comme les 7 fabriques de `chromadb`, absentes
        de l'hote.

        Ce test utilise une fabrique factice et verifie qu'un
        `from module import fabrique` deja execute ailleurs est re-lie
        (mutation A6-b).
        """
        import types

        from src.equivalence_des_identifiants import _barrer_le_constructeur

        def fabrique_de_client(*_a, **_k):  # pragma: no cover - doit etre remplacee
            raise AssertionError("appelee")

        sdk = types.ModuleType("faux_sdk_de_store")
        sdk.HttpClient = fabrique_de_client
        # Un consommateur qui a deja fait son `from faux_sdk import HttpClient`,
        # sous un alias : le cas que le balayage doit prendre.
        consommateur = types.ModuleType("faux_consommateur")
        consommateur.client_http = fabrique_de_client
        journal: list[str] = []

        with _modules_temporaires(faux_sdk_de_store=sdk, faux_consommateur=consommateur):
            sites = _barrer_le_constructeur(sdk, "HttpClient", "faux_sdk.HttpClient", journal)

            assert "faux_consommateur.client_http" in sites, sites
            assert sdk.HttpClient is not fabrique_de_client
            assert consommateur.client_http is not fabrique_de_client
            for porteur, attribut in ((sdk, "HttpClient"), (consommateur, "client_http")):
                with pytest.raises(BarriereDEcritureError):
                    getattr(porteur, attribut)("h")
        assert journal == ["faux_sdk.HttpClient", "faux_sdk.HttpClient"]

    def test_une_fabrique_n_est_pas_prise_par_la_classe_et_la_raison_est_rendue(self):
        """Limite de la premiere couche : le cas de `chromadb`.

        Ses constructeurs sont des fonctions (`HttpClient`, `PersistentClient`,
        `EphemeralClient`…) : pas d'`__init__` a barrer. Le rebondage des noms
        est leur seule couche, et la raison entre dans `sans_classe` pour que le
        script l'imprime.

        `chromadb` n'est pas installe sur l'hote, et les deux constructeurs de
        `minio` sont des classes : le cas se teste donc directement sur la
        fonction.
        """
        from src.equivalence_des_identifiants import _barrer_la_classe

        def fabrique_de_client(*_a, **_k):  # pragma: no cover - jamais appelee
            raise AssertionError("appelee")

        journal: list[str] = []

        raison = _barrer_la_classe(fabrique_de_client, "chromadb.HttpClient", journal)

        assert raison is not None, "une fabrique n'a pas d'`__init__` : la raison est RENDUE"
        assert "n'est pas une classe" in raison, raison
        assert journal == []

    def test_une_classe_est_prise_et_le_deja_construit_survit(self):
        """L'autre sens, a la meme fonction : une classe est prise."""
        from src.equivalence_des_identifiants import BarriereDEcritureError, _barrer_la_classe

        class Client:
            def __init__(self, hote):
                self.hote = hote

            def lire(self):
                return f"lu sur {self.hote}"

        deja_construit = Client("h")
        journal: list[str] = []

        assert _barrer_la_classe(Client, "faux.Client", journal) is None

        with pytest.raises(BarriereDEcritureError):
            Client("h")
        assert journal == ["faux.Client.__init__"]
        # Contre-epreuve : l'instance anterieure reste utilisable.
        assert deja_construit.lire() == "lu sur h"


class TestLesPortesNeuvesLevent:
    """La preuve est a l'execution, et ne depend d'aucune lecture du code.

    Chaque chemin de `PORTES_NEUVES` tourne dans un processus neuf (armer est
    irreversible) et doit lever `BarriereDEcritureError`.
    """

    def _lancer(self, code):
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=RACINE_DEPOT,
            env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        )

    @pytest.mark.parametrize("nom", sorted(PORTES_NEUVES))
    def test_une_porte_neuve_leve_quel_que_soit_son_chemin(self, nom):
        acheve = self._lancer(PORTES_NEUVES[nom])

        assert acheve.returncode != 0, f"{nom} : rc=0, la porte a construit son client"
        assert "BarriereDEcritureError" in acheve.stderr, (nom, acheve.stdout, acheve.stderr)

    def test_le_constructeur_touche_entre_au_journal(self):
        """Une levee avalee par un `except Exception` laisse quand meme sa trace."""
        releve = _executer(
            """
import json
from src.equivalence_des_identifiants import armer_les_barrieres

armement = armer_les_barrieres()
try:
    from minio import Minio

    Minio("h", access_key="a", secret_key="b")
except Exception:
    pass
print(json.dumps(armement.journal))
"""
        )

        assert releve == ["minio.Minio"], releve


class TestLEnumerationDesConstructeurs:
    """Les constructeurs sont enumeres depuis le SDK installe, pas d'une liste."""

    def test_la_regle_de_minio_rend_les_clients_et_aucune_erreur(self):
        import minio

        from src.equivalence_des_identifiants import _classes_hors_exception

        noms = _classes_hors_exception(minio)

        assert "Minio" in noms and "MinioAdmin" in noms
        assert not [n for n in noms if n.endswith("Error")], noms

    def test_une_classe_publique_neuve_du_sdk_est_prise_sans_toucher_au_code(self):
        """Un client neuf publie par le SDK est pris sans modifier le code."""
        import types

        from src.equivalence_des_identifiants import _classes_hors_exception

        faux = types.ModuleType("faux_sdk")
        faux.ClientNeuf = type("ClientNeuf", (), {})
        faux.ErreurDuSdk = type("ErreurDuSdk", (Exception,), {})
        faux._Prive = type("_Prive", (), {})

        assert _classes_hors_exception(faux) == ["ClientNeuf"]

    def test_la_regle_de_chromadb_ne_retient_que_les_fabriques(self):
        """Les noms en `Client` qui sont des classes sont les interfaces abstraites."""
        import types

        from src.equivalence_des_identifiants import _fabriques_de_client

        faux = types.ModuleType("faux_chroma")
        faux.HttpClient = lambda: None
        faux.ClientAPI = type("ClientAPI", (), {})
        faux.Collection = type("Collection", (), {})

        assert _fabriques_de_client(faux) == ["HttpClient"]

    def test_la_regle_de_nebula3_prend_les_pools_et_la_connexion(self):
        import types

        from src.equivalence_des_identifiants import _pools_et_connexions

        faux = types.ModuleType("faux_nebula")
        faux.ConnectionPool = type("ConnectionPool", (), {})
        faux.SessionPool = type("SessionPool", (), {})
        faux.Connection = type("Connection", (), {})
        faux.Session = type("Session", (), {})

        assert _pools_et_connexions(faux) == ["Connection", "ConnectionPool", "SessionPool"]

    def test_minio_est_bien_barre_et_les_sdk_absents_sont_nommes(self):
        """Un SDK absent du processus n'y construit rien, et son absence est rendue."""
        releve = _executer(PARCOURS_DES_SITES, DECLAREES)

        assert releve["sdk_barres"]["minio"] == ["minio.Minio", "minio.MinioAdmin"]
        # Sur l'hote, `nebula3` et `chromadb` ne s'importent pas : ils doivent
        # etre nommes absents, pas sautes en silence.
        assert set(releve["sdk_barres"]) | set(releve["sdk_absents"]) == {
            "minio",
            "nebula3.gclient.net",
            "nebula3.gclient.net.SessionPool",
            "chromadb",
            # `chromadb` en deux sites : ses noms publics sont des fabriques, et
            # les classes concretes qu'elles construisent vivent dans
            # `chromadb.api.client`.
            "chromadb.api.client",
        }, releve

    def test_toute_dependance_tierce_de_src_est_classee(self):
        """Chaque module tiers importe par `src/` est classe : liste d'autorisation.

        Le test enumere tous les modules tiers de premier niveau importes par
        `src/` (hors bibliotheque standard, hors `src`) et exige que chacun
        figure dans la classification importee depuis `src/`, jamais une copie
        locale (registre 4.40.b ; mutations S1, un `import boto3` ajoute, et S2,
        la constante videe). Une dependance nouvelle rougit sans que personne ait
        eu a y penser ; une dependance classee qui disparait rougit aussi, pour
        que la classification ne garde pas de nom mort.
        """
        from src.equivalence_des_identifiants import PAS_UN_STORE, SDK_DE_STORE

        importe: set[str] = set()
        for chemin in sorted((RACINE_DEPOT / "src").rglob("*.py")):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    importe |= {alias.name.split(".")[0] for alias in noeud.names}
                # `level == 0` : un `from .ngql import` est un import relatif,
                # dont le module n'est pas une dependance tierce.
                elif isinstance(noeud, ast.ImportFrom) and noeud.level == 0 and noeud.module:
                    importe.add(noeud.module.split(".")[0])
        tiers = {m for m in importe if m != "src" and m not in sys.stdlib_module_names}

        classes = set(SDK_DE_STORE) | set(PAS_UN_STORE)
        non_classees = sorted(tiers - classes)
        assert not non_classees, (
            f"dependance(s) tierce(s) de `src/` que la classification ne connait pas : "
            f"{non_classees}. Chacune doit entrer dans SDK_DE_STORE — et alors dans "
            "CONSTRUCTEURS_DES_SDK, qui la barre — ou dans PAS_UN_STORE, avec la raison "
            "ecrite au site. Un nom qu'on ne classe pas est un store qu'on ne barre pas."
        )
        disparues = sorted(classes - tiers)
        assert not disparues, (
            f"la classification garde des noms que `src/` n'importe plus : {disparues}. "
            "Une classification qui survit a sa dependance ne decrit plus rien."
        )
        # Les deux classes sont disjointes : un nom des deux cotes rendrait le
        # verdict de chaque assertion insensible a l'autre.
        assert not set(SDK_DE_STORE) & set(PAS_UN_STORE)

    def test_les_sdk_classes_stores_sont_ceux_que_la_barriere_couvre(self):
        """Classer un SDK « de store » sans le barrer ne garderait rien.

        Le lien entre les deux constantes est verifie ici, pas laisse a la
        relecture : chaque nom de `SDK_DE_STORE` doit etre le premier segment
        d'au moins un chemin de `CONSTRUCTEURS_DES_SDK`, et reciproquement.
        """
        from src.equivalence_des_identifiants import CONSTRUCTEURS_DES_SDK, SDK_DE_STORE

        barres = {chemin.split(".")[0] for chemin, _ in CONSTRUCTEURS_DES_SDK}

        assert barres == set(SDK_DE_STORE), (barres, SDK_DE_STORE)


class TestLesClientsDeLectureDuHarnais:
    """Les seuls clients permis sont de lecture, et chacun porte sa limite."""

    def test_l_enveloppe_ne_laisse_passer_que_les_methodes_nommees(self):
        from src.equivalence_des_identifiants import BarriereDEcritureError, LectureSeule

        class Faux:
            def list_objects(self, *_a, **_k):
                return ["vu"]

            def remove_object(self, *_a, **_k):  # pragma: no cover - doit lever avant
                raise AssertionError("appele")

        journal: list[str] = []
        enveloppe = LectureSeule(Faux(), {"list_objects"}, "stockage objet du harnais", journal)

        assert enveloppe.list_objects("seau") == ["vu"]
        with pytest.raises(BarriereDEcritureError, match="LECTURE SEULE"):
            enveloppe.remove_object("seau", "cle")

    def test_l_enveloppe_ecrit_dans_le_journal_partage(self):
        """Le journal de l'enveloppe est celui de l'armement (mutation N2).

        `figer` et `comparer` rougissent sur ce journal : une ecriture refusee
        ici puis avalee par la production y reste visible.
        """
        from src.equivalence_des_identifiants import BarriereDEcritureError, LectureSeule

        journal: list[str] = ["une entree qui precede"]
        enveloppe = LectureSeule(object(), {"list_objects"}, "stockage objet du harnais", journal)

        with contextlib.suppress(BarriereDEcritureError):
            enveloppe.remove_object("seau", "cle")

        assert journal == ["une entree qui precede", "stockage objet du harnais.remove_object"]

    @pytest.mark.parametrize(
        "requete",
        [
            "INSERT VERTEX Document() VALUES 'x':();",
            "USE rag_space; DELETE VERTEX 'x';",
            "DROP SPACE rag_space;",
            "UPDATE VERTEX ON Document 'x' SET a = 1;",
            "SUBMIT JOB STATS;",
        ],
    )
    def test_la_session_refuse_toute_requete_qui_n_est_pas_une_lecture(self, requete):
        from src.equivalence_des_identifiants import BarriereDEcritureError, SessionEnLecture

        class Fausse:
            def execute(self, _requete):  # pragma: no cover - doit lever avant
                raise AssertionError("la requete est passee")

        journal = []
        session = SessionEnLecture(Fausse(), journal)

        with pytest.raises(BarriereDEcritureError, match="n'est pas un verbe de lecture"):
            session.execute(requete)
        assert journal, "une requete refusee entre au journal"

    # Ecritures composees (registre 4.40.c) : nGQL compose par `;`, par le tube
    # `|`, qui passe le resultat d'une lecture a une ecriture, et par le saut de
    # ligne. Chacune de ces formes commence par un verbe de lecture et ecrit.
    ECRITURES_COMPOSEES = [
        'GO FROM "v" OVER PARENT_OF YIELD dst(edge) AS d | DELETE VERTEX $-.d;',
        "SHOW SPACES | DROP SPACE $-.Name;",
        'YIELD "x" AS d | DELETE VERTEX $-.d;',
        "MATCH (d:Document) RETURN id(d) AS i | DELETE VERTEX $-.i;",
        "LOOKUP ON Document YIELD id(vertex) AS i | DELETE VERTEX $-.i;",
        'FETCH PROP ON Document "v" YIELD id(vertex) AS i | DELETE VERTEX $-.i;',
        'GO FROM "v" OVER PARENT_OF YIELD dst(edge) AS d | DELETE EDGE PARENT_OF "a" -> $-.d;',
        "USE rag_space | DROP SPACE rag_space;",
        "SHOW TAGS | DROP TAG $-.Name;",
        "DESCRIBE SPACE rag_space | DROP SPACE rag_space;",
        # Les deux formes par saut de ligne : c'est pourquoi le saut de ligne est
        # parmi les separateurs.
        'GO FROM "v" OVER PARENT_OF YIELD dst(edge) AS d\nDELETE VERTEX $-.d;',
        "MATCH (d:Document) RETURN d\nDROP SPACE rag_space;",
    ]

    @pytest.mark.parametrize("requete", ECRITURES_COMPOSEES)
    def test_une_ecriture_composee_par_un_tube_ou_un_saut_de_ligne_est_refusee(self, requete):
        """Le premier mot lit, et la requete ecrit : elle doit etre refusee."""
        from src.equivalence_des_identifiants import BarriereDEcritureError, SessionEnLecture

        class Fausse:
            def execute(self, _requete):  # pragma: no cover - doit lever avant
                raise AssertionError("la requete est passee")

        journal: list[str] = []

        with pytest.raises(BarriereDEcritureError, match="n'est pas un verbe de lecture"):
            SessionEnLecture(Fausse(), journal).execute(requete)
        assert journal, "une requete refusee entre au journal"

    def test_un_separateur_cite_fait_refuser_plus_jamais_moins(self):
        """Limite du decoupage : un separateur cite fait refuser une lecture.

        Un separateur a l'interieur d'une chaine citee compte pour un
        separateur : la requete ci-dessous ne fait que lire, et elle est
        pourtant refusee. C'est le sens sans danger : decouper ne fait
        qu'ajouter des fragments, donc des exigences. L'autre sens est tenu par
        les 12 formes ci-dessus.
        """
        from src.equivalence_des_identifiants import BarriereDEcritureError, SessionEnLecture

        class Fausse:
            def execute(self, _requete):  # pragma: no cover - doit lever avant
                raise AssertionError("la requete est passee")

        with pytest.raises(BarriereDEcritureError, match="n'est pas un verbe de lecture"):
            SessionEnLecture(Fausse(), []).execute(
                'MATCH (d:Document) WHERE d.Document.source_path == "a|b" RETURN d;'
            )

    @pytest.mark.parametrize(
        "requete",
        [
            "USE rag_space;",
            "MATCH (d:Document) RETURN d.Document.source_path AS s;",
            'GO FROM "x" OVER PARENT_OF YIELD dst(edge) AS d;',
        ],
    )
    def test_les_requetes_du_harnais_passent(self, requete):
        """Contre-epreuve : un controle qui refuserait tout ne prouverait rien."""
        from src.equivalence_des_identifiants import SessionEnLecture

        class Fausse:
            def execute(self, requete):
                return f"passe: {requete}"

        assert SessionEnLecture(Fausse(), []).execute(requete).startswith("passe")

    def test_les_trois_requetes_reelles_de_la_classe_graphe_passent(self):
        """Les requetes du harnais telles que `Graphe` les forme, et non recopiees.

        La contre-epreuve precedente porte sur des requetes ecrites a la main
        dans ce fichier : elles pourraient diverger de celles que le script
        envoie. Celles-ci sont formees par les memes constantes et les memes
        f-strings que `Graphe.__init__`, `Graphe.documents` et `Graphe.ids`, sur
        des `element_id` reels lus dans l'instantane versionne.
        """
        from src.docling_service.ngql import SPACE, document_vid
        from src.equivalence_des_identifiants import SessionEnLecture

        class Fausse:
            def execute(self, requete):
                return f"passe: {requete}"

        session = SessionEnLecture(Fausse(), [])
        versionne = RACINE_DEPOT / "documentation/campagnes/2026-09-24-instantane-des-identifiants"
        lot = [document_vid(cle) for cle in sorted(lire_l_instantane(versionne).documents)]
        liste = ", ".join('"' + vid.replace('"', '\\"') + '"' for vid in lot)

        for requete in (
            f"USE {SPACE};",
            "MATCH (d:Document) RETURN d.Document.source_path AS s;",
            f"GO FROM {liste} OVER PARENT_OF YIELD dst(edge) AS d;",
        ):
            assert session.execute(requete).startswith("passe"), requete


class TestLesBarrieres:
    """Ce qui ecrirait leve, a tous les sites ou la porte est liee."""

    def test_aucune_porte_declaree_ne_reste_liee_dans_un_module_src(self):
        """Aucune porte declaree ne reste liee a son original (registre 4.37.d).

        Apres armement, le test parcourt chaque module `src.*` charge et rougit
        si un attribut y est encore la fonction d'origine d'une porte declaree.
        `storage` et `extraction` importent `get_writer` par nom : barrer
        `nebula.get_writer` dans `nebula` seul le laisserait vivant a deux
        sites. Le test rougit si `nebula.get_writer` ou `vectors.get_collection`
        est retire de `PORTES`.

        Il ne prouve que cela : les portes nommees dans `PORTES` sont deliees
        partout. Les portes non nommees relevent de la barriere des SDK
        (`TestLesPortesNeuvesLevent`).
        """
        releve = _executer(PARCOURS_DES_SITES, DECLAREES)

        assert releve["originaux"], "aucune porte n'a ete relevee : le parcours ne parcourt rien"
        assert not releve["encore_lies"], (
            "des portes d'ecriture restent liees a leur original apres armement : "
            f"{releve['encore_lies']}"
        )

    def test_get_writer_est_barre_a_ses_trois_sites(self):
        """Le site par nom de `storage` et d'`extraction`, en plus de `nebula`."""
        releve = _executer(PARCOURS_DES_SITES, DECLAREES)

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
        """Une barriere rendue mais non posee ne garde rien : on appelle par le module."""
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

    def test_la_capture_valide_les_elements_comme_persist(self):
        """Mutation M32 : la capture valide les elements comme `persist`.

        Sans la validation, un element hors contrat entrerait dans l'instantane
        sans que rien ne le dise, alors que tout le reste est compare a
        l'instantane.
        """
        releve = _executer(
            """
import json
from src.equivalence_des_identifiants import armer_les_barrieres, installer_la_capture
armer_les_barrieres()
lots = installer_la_capture()
from src.docling_service import storage
bon = {"id": "0123456789", "page_no": 1, "page_position": 0, "text": "x",
       "label": "text", "self_ref": "#/texts/0"}
resultat = {}
try:
    storage.persist([bon], None, None)
except Exception as exc:
    resultat["bon"] = f"{type(exc).__name__}: {exc}"
else:
    resultat["bon"] = None
resultat["capture"] = len(lots)
try:
    storage.persist([{"pas": "un element"}], None, None)
except Exception as exc:
    resultat["mauvais"] = type(exc).__name__
else:
    resultat["mauvais"] = None
resultat["capture_apres"] = len(lots)
print(json.dumps(resultat))
"""
        )

        assert releve["bon"] is None, releve
        assert releve["capture"] == 1, "un element valide doit etre capture"
        assert releve["mauvais"] is not None, (
            "un element hors contrat est entre dans la capture sans un mot"
        )
        assert releve["capture_apres"] == 1, "l'element invalide ne doit pas etre capture"

    def test_le_temoin_du_stockage_enregistre_l_envoi_et_refuse_le_reste(self):
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


# Un chapitre de 30 elements, dont trois `ListItem` vides aux rangs 5, 11 et 17 :
# la forme observee des puces de Docling. Le convertisseur est remplace ; tout le
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
    """Les deux faux verts a eviter, par le vrai `_extract_flat`."""

    def test_trois_listitem_qui_recoivent_du_texte_rougissent_apres_la_campagne(self, tmp_path):
        """Faux vert n° 1 : comparer le code du jour au graphe qu'il a ecrit.

        Le graphe d'apres est ecrit par le code repare ; reextraire avec ce meme
        code ne verrait rien. Le harnais compare a l'instantane fige avant, et
        doit dire les trois deplaces, imputes a `text50`.
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
        """Faux vert n° 2 : un harnais qui recalcule ne voit pas une derive au site d'appel.

        Le graphe est celui de la production saine. La production derive
        ensuite au site d'appel : elle calcule sur `.cleaned/<cle>`. Aucun
        identifiant emis n'est plus celui du graphe, et `figer` doit refuser
        d'ecrire.
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
        # La raison, et pas seulement le rc : le controle negatif rougit aussi ce
        # scenario, par ricochet (la mutation `site_d_appel` y devient nulle), et
        # masquerait une confrontation au graphe devenue inoperante.
        assert any("l'emission n'est pas le graphe" in ligne for ligne in resultat["sortie"])
