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
    TEMOIN_MINIO,
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

    @pytest.mark.parametrize("champ", ["id", "page_no", "page_position", "text", "label"])
    def test_chaque_champ_du_contrat_leve_quand_il_disparait(self, champ):
        """M31 : les CINQ champs lus par indexation, pas seulement `page_position`.

        Le mutant de l'audit remplacait l'indexation par `.get` : un champ
        disparu du contrat devenait alors un zero, une chaine vide ou un `None`
        qui se compare — et le seul test qui tenait cette garantie portait sur
        `page_position`. `self_ref` n'y est pas : il est lu par `.get` A DESSEIN,
        un element sans `self_ref` etant licite (l'appariement le rend "").
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

    def test_le_nombre_de_lignes_annonce_par_le_manifeste_est_verifie(self, tmp_path):
        """M14 : le manifeste annonce `elements`, et la relecture DOIT recompter.

        Sans ce garde, un releve tronque passait pour complet — et un releve
        tronque, c'est « ces elements n'existent plus », donc des deplacements
        invisibles. Le SHA-256 du releve est recalcule ici pour que ce soit le
        COMPTE qui rougisse, et non l'empreinte : un garde qu'un autre garde
        couvre n'est pas tenu.
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
        """M28 : un instantane d'un AUTRE format ne se relit pas en silence."""
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

    def test_identiques_retranche_les_reassignes(self):
        """M34 : `identiques` ANNONCE les elements qui n'ont pas bouge.

        Un identifiant REASSIGNE est present des deux cotes, donc compte par la
        difference d'ensembles — mais il designe un autre element. Le compter
        parmi les identiques, c'est annoncer une stabilite qu'on vient de nier
        deux lignes plus bas. Le mutant de l'audit retirait la soustraction ;
        aucun test ne le voyait.
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

    def test_une_cle_apparue_sans_deplacement_declare_est_rouge(self):
        """M22 : seule la branche « disparue » etait tenue.

        Une cle d'objet APPARUE que la declaration n'explique pas, c'est un crop
        de plus dans MinIO sous un identifiant que personne n'a annonce.
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

    def test_des_cles_d_objet_que_minio_ne_liste_pas_sont_rouges(self, tmp_path):
        emission = _une_emission()
        monde = _monde([emission], objets={emission.partition_key: set()})

        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 1

    def test_une_barriere_touchee_est_rouge_meme_avalee(self, tmp_path):
        journal = ["vectors.get_collection"]

        assert (
            figer(_monde([_une_emission()]), tmp_path, {"date": "t"}, journal, lambda _: None) == 1
        )

    def test_un_graphe_qui_porte_deja_les_identifiants_mutes_est_rouge(self, tmp_path):
        """LE CONTROLE NEGATIF DU CONTROLE NEGATIF : la clause `vu_du_graphe > 0`.

        Le troisieme audit l'a montree SANS TEST : elle survivait a son retrait.
        Ce qu'elle garde : un graphe qui porterait DEJA les identifiants mutes
        rendrait `emis_seul == 0`, et la mutation serait « vue » par le seul
        appariement, sans qu'aucune confrontation au graphe l'ait confirmee.

        Le monde est construit ainsi : le graphe porte l'emission ET tous les
        identifiants qu'une mutation produirait. La raison attendue est celle
        de la MUTATION ; sans la clause, elle disparait.
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
        # LA RAISON EXPLICITE, et non le seul rc : sans cette assertion, le
        # garde `non_compares` survivait a son propre retrait (mutation A1-b),
        # l'ecart de couverture rougissant deja. Une garantie qu'un autre garde
        # couvre n'est pas tenue — c'est tout le motif de la table des mutations.
        assert any("n'ont PAS ete compares" in ligne for ligne in sortie), sortie

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


class TestCeQuiSeDitDevantUnRouge:
    """M35 : une garantie ECRITE — « elle dit, devant un rouge, si c'est l'entree
    ou le code qui a change » — que le mutant de l'audit retirait sans qu'un
    test bouge."""

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
    """A3 : refiger apres la campagne rendait rc=0 des deux cotes (`mesure` de l'audit).

    A3 posait l'empreinte attendue dans le PARENT du dossier — donc a un site
    que l'appelant DESIGNAIT. Le troisieme audit l'a retourne : trois gestes, un
    `printf` au milieu, et rc=0. Les deux sites sont desormais FIXES, resolus
    depuis l'emplacement du module (§4.39.a).
    """

    def test_l_empreinte_attendue_ne_depend_plus_de_l_argument(self, tmp_path):
        """LE GESTE DE L'AUDIT : une table voisine, ecrite a la main, n'authentifie plus rien."""
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
        """`…/campagnes/bis/instantane` porterait sa propre table voisine."""
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
        """LE SITE CANONIQUE : la constante du module, contre l'instantane du depot."""
        nom = "2026-09-24-instantane-des-identifiants"
        dossier = RACINE_DEPOT / "documentation/campagnes" / nom

        assert EMPREINTES_ATTENDUES[nom] == lire_l_instantane(dossier).empreinte

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

    def test_un_dossier_absent_de_la_constante_est_refuse(self):
        """Le cas exact du second instantane : ecrit DANS le repertoire, inscrit nulle part."""
        dossier = RACINE_DEPOT / "documentation/campagnes/refige-en-douce"

        with pytest.raises(EmpreinteInattendueError, match="n'est pas dans EMPREINTES_ATTENDUES"):
            empreinte_attendue(dossier)

    def test_refiger_dans_le_meme_dossier_rend_un_rc_1_et_non_une_trace(self, tmp_path):
        """`FileExistsError` remontait nue : une trace d'appel n'est pas un verdict."""
        monde = _monde([_une_emission()])
        sortie = []
        assert figer(monde, tmp_path, {"date": "t"}, [], lambda _: None) == 0

        rc = figer(monde, tmp_path, {"date": "t"}, [], sortie.append)

        assert rc == 1
        assert any("ne s'ecrase pas" in ligne for ligne in sortie), sortie

    def test_le_site_reel_du_depot_s_authentifie(self):
        """LE SITE REEL : la constante du module, contre l'instantane du depot."""
        dossier = RACINE_DEPOT / "documentation/campagnes/2026-09-24-instantane-des-identifiants"

        assert empreinte_attendue(dossier) == lire_l_instantane(dossier).empreinte


class TestLesTroisGestesDeLAudit:
    """LE SCENARIO EXACT du troisieme audit, contre le SCRIPT, rc du PROCESSUS.

    `figer <ailleurs>` ; un `printf` dans la table voisine ; `comparer
    <ailleurs>`. Les deux gestes du script rendent desormais 1, avant tout
    armement et toute connexion — donc mesurables sur l'hote, sans store.
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
        """LE `printf` DE L'AUDIT : la table voisine n'est meme plus lue."""
        cible = tmp_path / "bis" / "2026-09-24-instantane-des-identifiants"
        cible.mkdir(parents=True)
        (cible.parent / "empreintes-des-instantanes.tsv").write_text(
            f"dossier\tempreinte\n{cible.name}\tdeadbeef\n", encoding="utf-8"
        )

        acheve = self._lancer("comparer", str(cible), cwd=tmp_path)

        assert acheve.returncode == 1, acheve.stdout + acheve.stderr
        assert "hors du repertoire de campagne" in acheve.stdout, acheve.stdout


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


# ─── CE QUI A REMPLACE LA DERIVATION AST, ET POURQUOI ELLE EST RETIREE ───────
#
# La version precedente DERIVAIT, par lecture AST de `src/docling_service/*.py`,
# les fonctions porteuses d'un client de store, a partir de trois « semences »
# (`Minio`, `ConnectionPool`, `HttpClient`) et d'un point fixe ; chaque porteur
# devait ensuite etre une porte barree, un `NON_ECRIVAINS` motive ou un
# `HORS_PROCESSUS` motive, « sans quatrieme cas ».
#
# LE TROISIEME AUDIT DU LOT 11 LUI A FAIT PASSER QUATRE PORTES NEUVES SUR CINQ :
# un alias d'import (`from minio import Minio as _M`), un `getattr(minio,
# "Minio")(...)`, un client construit au niveau du module, et une porte deposee
# dans `src/pipeline/` — hors du dossier balaye. Et ses quatre `HORS_PROCESSUS`
# ne reposaient que sur l'absence de `fastapi` SUR L'HOTE : dans l'image
# d'extraction, `src.docling_service.main` s'importe et les quatre ressortaient
# LIES. Deux de ses classements etaient en outre FAUX, et personne ne l'avait
# vu : `images.ensure_bucket` appelle `make_bucket`, donc ecrit ; les raisons
# d'`extract` et de `_extract_pdf` omettaient `storage.forget_document`.
#
# DECISION DU PILOTE, 24 septembre 2026 : on ne rafistole pas l'analyse
# statique — `getattr` suffit a tromper n'importe laquelle, et chaque audit
# trouverait le trou suivant. La derivation, `NON_ECRIVAINS` et `HORS_PROCESSUS`
# sont RETIRES : ils promettaient une exhaustivite qu'ils ne tenaient pas, et
# leurs classements faux n'etaient plus corriges par personne. La preuve est
# passee a l'EXECUTION, sur les constructeurs des SDK
# (:func:`~src.equivalence_des_identifiants.barrer_les_sdk_de_store`), et les
# tests de `TestLesPortesNeuvesLevent` la mesurent porte par porte.
#
# CE QUI RESTE DE L'ANCIEN DISPOSITIF, et ce qu'il prouve exactement : les
# barrieres par SITE, seconde couche, verifiees ci-dessous sur la liste
# DECLAREE des portes — plus aucune derivation, donc plus aucune phrase
# d'exhaustivite. Et la CLASSIFICATION des dependances tierces, qui se lit
# depuis `src/` et jamais d'une copie locale : ce fichier en portait une en dur,
# donc vider la constante de production ne rougissait rien (mutant `S2` du
# quatrieme audit). Voir `test_toute_dependance_tierce_de_src_est_classee`.

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

DECLAREES = json.dumps([*PORTES, TEMOIN_MINIO])

# LES CINQ PORTES NEUVES DE L'AUDIT, plus les deux variantes de ce lot. Chacune
# est un programme COMPLET : il arme, puis tente de construire un client par un
# chemin different. Le verdict attendu est le meme partout — `BarriereDEcriture`.
#
# Elles portent sur `minio`, et c'est un choix de MESURE : c'est le seul des
# trois SDK installe sur l'hote, donc le seul dont la porte qualite puisse
# rougir sans conteneur. Les trois SDK sont mesures ensemble DANS L'IMAGE
# d'extraction, et le releve est au registre (§4.39.b).
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
    ("MINIO_ENDPOINT", "h"),
    ("MINIO_ROOT_USER", "a"),
    ("MINIO_ROOT_PASSWORD", "b"),
    ("MINIO_BUCKET", "seau"),
):
    os.environ.setdefault(nom, valeur)
from src.pipeline.media import MinioImageExporter

MinioImageExporter("un-document")._get_client()
""",
    "client d'ADMINISTRATION MinIO": _ARMER
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


class TestLesPortesNeuvesLevent:
    """LA PREUVE EST A L'EXECUTION, et elle ne depend d'aucune lecture du code.

    Les cinq portes que la derivation AST a laissees passer, plus deux
    variantes de ce lot. Chacune tourne dans un processus NEUF — armer est
    irreversible — et doit lever `BarriereDEcritureError`.
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
    """LES CONSTRUCTEURS SONT ENUMERES DEPUIS LE SDK INSTALLE, jamais de memoire."""

    def test_la_regle_de_minio_rend_les_clients_et_aucune_erreur(self):
        import minio

        from src.equivalence_des_identifiants import _classes_hors_exception

        noms = _classes_hors_exception(minio)

        assert "Minio" in noms and "MinioAdmin" in noms
        assert not [n for n in noms if n.endswith("Error")], noms

    def test_une_classe_publique_neuve_du_sdk_est_prise_sans_toucher_au_code(self):
        """LE PIEGE « une liste en dur se trompe en silence », a un cran de profondeur."""
        import types

        from src.equivalence_des_identifiants import _classes_hors_exception

        faux = types.ModuleType("faux_sdk")
        faux.ClientNeuf = type("ClientNeuf", (), {})
        faux.ErreurDuSdk = type("ErreurDuSdk", (Exception,), {})
        faux._Prive = type("_Prive", (), {})

        assert _classes_hors_exception(faux) == ["ClientNeuf"]

    def test_la_regle_de_chromadb_ne_retient_que_les_fabriques(self):
        """Les noms en `Client` qui sont des CLASSES sont les interfaces abstraites."""
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
        """Un SDK absent du processus n'y construit rien : c'est RENDU, jamais tu."""
        releve = _executer(PARCOURS_DES_SITES, DECLAREES)

        assert releve["sdk_barres"]["minio"] == ["minio.Minio", "minio.MinioAdmin"]
        # Sur l'hote, `nebula3` et `chromadb` ne s'importent pas : ils doivent
        # etre NOMMES absents, et jamais sautes en silence.
        assert set(releve["sdk_barres"]) | set(releve["sdk_absents"]) == {
            "minio",
            "nebula3.gclient.net",
            "nebula3.gclient.net.SessionPool",
            "chromadb",
        }, releve

    def test_toute_dependance_tierce_de_src_est_classee(self):
        """CHAQUE module tiers importe par `src/` est classe, et c'est une AUTORISATION.

        **LA POLARITE EST LE GARDE, et c'est la reparation B2 du quatrieme
        audit.** Ce test portait sa propre copie en dur de `SDK_DE_STORE` et
        ecrivait `importe & set(SDK_DE_STORE) ^ set(SDK_DE_STORE)`, qui vaut
        `SDK_DE_STORE - importe` parce que `&` lie plus fort que `^`. Il ne
        pouvait voir qu'un SDK DISPARU. Deux mutants y survivaient : `S1`, un
        `import boto3` ajoute a `storage.py`, et `S2`, la constante videe.

        Il enumere desormais TOUS les modules tiers de premier niveau importes
        par `src/` — hors bibliotheque standard, hors `src` — et exige que
        chacun figure dans une classification IMPORTEE DEPUIS `src/`. Une
        dependance tierce nouvelle rougit sans que personne ait eu a penser a
        elle ; une dependance classee qui disparait rougit aussi, pour que la
        classification ne conserve pas de nom mort.
        """
        from src.equivalence_des_identifiants import PAS_UN_STORE, SDK_DE_STORE

        importe: set[str] = set()
        for chemin in sorted((RACINE_DEPOT / "src").rglob("*.py")):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    importe |= {alias.name.split(".")[0] for alias in noeud.names}
                # `level == 0` : un `from .ngql import` est un import RELATIF,
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
        # Les deux classes sont DISJOINTES : un nom des deux cotes rendrait le
        # verdict de chaque assertion insensible a l'autre.
        assert not set(SDK_DE_STORE) & set(PAS_UN_STORE)

    def test_les_sdk_classes_stores_sont_ceux_que_la_barriere_couvre(self):
        """Classer un SDK « de store » sans le barrer ne garderait rien.

        Le lien entre les deux constantes est fait ICI, et pas laisse a la
        relecture : chaque nom de `SDK_DE_STORE` doit etre le premier segment
        d'au moins un chemin de `CONSTRUCTEURS_DES_SDK`, et reciproquement.
        """
        from src.equivalence_des_identifiants import CONSTRUCTEURS_DES_SDK, SDK_DE_STORE

        barres = {chemin.split(".")[0] for chemin, _ in CONSTRUCTEURS_DES_SDK}

        assert barres == set(SDK_DE_STORE), (barres, SDK_DE_STORE)


class TestLesClientsDeLectureDuHarnais:
    """LES SEULS CLIENTS PERMIS SONT CEUX DE LECTURE, et ils portent leur borne."""

    def test_l_enveloppe_ne_laisse_passer_que_les_methodes_nommees(self):
        from src.equivalence_des_identifiants import BarriereDEcritureError, LectureSeule

        class Faux:
            def list_objects(self, *_a, **_k):
                return ["vu"]

            def remove_object(self, *_a, **_k):  # pragma: no cover - doit lever avant
                raise AssertionError("appele")

        enveloppe = LectureSeule(Faux(), {"list_objects"}, "minio du harnais")

        assert enveloppe.list_objects("seau") == ["vu"]
        with pytest.raises(BarriereDEcritureError, match="LECTURE SEULE"):
            enveloppe.remove_object("seau", "cle")

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

    @pytest.mark.parametrize(
        "requete",
        [
            "USE rag_space;",
            "MATCH (d:Document) RETURN d.Document.source_path AS s;",
            'GO FROM "x" OVER PARENT_OF YIELD dst(edge) AS d;',
        ],
    )
    def test_les_requetes_du_harnais_passent(self, requete):
        """Le controle negatif : un garde qui refuse tout ne garde rien."""
        from src.equivalence_des_identifiants import SessionEnLecture

        class Fausse:
            def execute(self, requete):
                return f"passe: {requete}"

        assert SessionEnLecture(Fausse(), []).execute(requete).startswith("passe")


class TestLesBarrieres:
    """« Ce qui ecrirait leve », a TOUS les sites ou la porte est liee."""

    def test_aucune_porte_declaree_ne_reste_liee_dans_un_module_src(self):
        """LE TEST QUE L'AUDIT A MONTRE MANQUANT : deux mutants lui survivaient.

        Apres armement, il parcourt chaque module `src.*` charge et rougit si un
        attribut y EST ENCORE la fonction d'origine d'une porte DECLAREE.
        `storage` et `extraction` importent `get_writer` PAR NOM : barrer
        `nebula.get_writer` dans `nebula` seul le laisse vivant a deux sites.
        `mesure` : il rougit au retrait de `nebula.get_writer` comme de
        `vectors.get_collection` de la liste du producteur (registre 4.37.d).

        **CE QU'IL PROUVE, ET RIEN DE PLUS** : que les portes NOMMEES dans
        `PORTES` sont bien deliees partout. Il ne dit rien des portes qu'on
        n'aurait pas nommees — c'est la barriere d'execution sur les SDK qui
        tient celles-la (`TestLesPortesNeuvesLevent`).
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

    def test_la_capture_valide_les_elements_comme_persist(self):
        """M32 : « la capture valide les elements comme `persist` », ecrit et non tenu.

        Sans la validation, un element hors contrat entrerait dans l'instantane
        sans que rien ne le dise — et l'instantane est ce a quoi on compare
        TOUT le reste. Le mutant de l'audit retirait l'appel a
        `validate_elements` ; 55 tests restaient verts.
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
