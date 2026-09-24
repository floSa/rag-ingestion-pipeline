"""Le harnais qui dit, avant et apres une reingestion, quels `element_id` ont bouge.

**SA VALEUR EST SA CAPACITE A DIRE NON.** La campagne a venir purgera les stores
puis reingerera tout le corpus. Le jeu de questions de l'agent pointe sur des
`element_id` : ce harnais doit prouver AVANT que la production redonne les
identifiants du graphe, et dire APRES exactement lesquels ont bouge.

**LA VERSION PRECEDENTE ETAIT TAUTOLOGIQUE APRES LA CAMPAGNE, et c'est le defaut
qui donne a ce module sa forme** (registre 4.37). Elle reextrayait avec le code X
et comparait au graphe... ecrit par ce meme code X. Elle ne pouvait voir qu'un
non-determinisme, jamais un deplacement : trois `ListItem` qui recoivent du
texte entre l'avant et l'apres rendaient `IDENTIQUES 30`, `OK`, rc=0. Et elle
JETAIT l'`element["id"]` emis par la production pour le recalculer avec
`compute_id` : une derive au site d'appel lui etait invisible.

D'ou les trois gestes de ce module :

1. :func:`ecrire_l_instantane` FIGE, avant la campagne, un releve par element —
   l'identifiant EMIS, la cle du document, `page_no`, `position_in_page`,
   `text50`, le label, et `self_ref` pour l'appariement. Le graphe ne stocke pas
   `position_in_page` : l'instantane vient donc de l'EMISSION de la production,
   et il n'est ecrit qu'apres que :func:`confronter_au_graphe` a prouve que ces
   identifiants emis sont ceux du graphe ;
2. apres la campagne, :func:`comparer_les_releves` confronte le nouveau releve a
   CET instantane, jamais a une reextraction du meme code. Les identifiants
   compares sont ceux que la production passe a `persist` ; `compute_id` ne sert
   plus qu'a l'attribution ;
3. :func:`trancher` rend vert si et seulement si l'ensemble deplace EGALE
   l'ensemble DECLARE — vide par defaut. Un identifiant declare qui ne bouge pas
   est rouge : sinon une reparation non appliquee passerait en silence.

**L'ATTRIBUTION EST EXACTE, et l'heuristique par jumeaux est supprimee.** Chaque
element d'avant est apparie a son homologue d'apres par `self_ref` (rang
d'occurrence compris : un PDF converti par lots repete les memes `self_ref` d'un
lot a l'autre), puis les QUATRE entrees de la formule sont comparees
directement. L'ancienne heuristique attribuait la mutation `filename` a
`position_in_page` 60 fois sur 60. **Sa borne reelle** : l'attribution est aussi
juste que l'appariement, et l'appariement par `self_ref` suppose que Docling
rend le meme arbre — meme version, meme entree. Si Docling change, le COMPTE
reste exact (c'est une comparaison d'ensembles) et l'attribution peut designer
des termes qui n'ont pas bouge.

**CE MODULE N'ECRIT DANS AUCUN STORE, et ce n'est pas une intention.**
:func:`armer_les_barrieres` remplace chaque porte d'ecriture A TOUS LES SITES OU
ELLE EST LIEE, imports par nom compris : la version precedente ne barrait
`nebula.get_writer` que dans `nebula`, alors que `storage` et `extraction`
l'importent par nom. Toute porte touchee est en outre JOURNALISEE, parce qu'une
levee peut etre avalee par un `except Exception` de la production.

Ce module s'importe cote hote : aucune dependance lourde au niveau du module,
les modules de production sont importes dans la fonction qui en a besoin.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.docling_service.elements import compute_id

# Une ligne de releve : les QUATRE entrees de `compute_id` telles que la
# production les a passees, l'identifiant qu'elle a EMIS, et deux champs qui
# n'entrent pas dans la formule — le label, pour lire un rouge, et `self_ref`,
# pour apparier l'avant et l'apres. `text50` et non `text` : c'est la troncature
# qui entre dans la formule.
Ligne = dict[str, Any]

# Les quatre entrees de `compute_id(filename, page_no, position_in_page, text)`.
# `filename` est le nom du parametre ; la production y passe `identity.key`, la
# cle du document, que la ligne porte sous `cle`.
TERMES: tuple[str, ...] = ("filename", "page_no", "position_in_page", "text50")
_CHAMP_DU_TERME = {
    "filename": "cle",
    "page_no": "page_no",
    "position_in_page": "position_in_page",
    "text50": "text50",
}

# Le terme impute quand l'identifiant emis change ALORS QUE les quatre entrees
# sont identiques : la production a calcule sur autre chose que ce qu'elle
# declare. C'est le scenario `.cleaned/<cle>` de l'audit du lot 11.
SITE_D_APPEL = "site d'appel"

FORMAT = "instantane-des-identifiants/1"
MANIFESTE = "MANIFESTE.tsv"
CLES_D_OBJET = "cles-d-objet.tsv"
# ─── LE SITE DE L'EMPREINTE ATTENDUE, ET POURQUOI IL NE DEPEND PLUS DE L'ARGUMENT

# LA VERSION PRECEDENTE LISAIT LA TABLE A COTE DU DOSSIER QU'ON LUI DESIGNAIT,
# donc a un site que l'appelant choisissait. `mesure` du troisieme audit du lot
# 11, contre les vrais stores : `figer /sp/bis/2026-09-24-instantane-des-identifiants`
# (rc=0), un `sha256sum` et un `printf` dans `/sp/bis/empreintes-des-instantanes.tsv`,
# puis `comparer /sp/bis/…` — rc=0, `OK`, 23 / 23. Le harnais etait de nouveau
# TAUTOLOGIQUE : il comparait le code du jour a un instantane ecrit par le code
# du jour, authentifie par une table ecrite par la meme main.
#
# DEUX SITES FIXES LE REMPLACENT, et aucun des deux ne se deduit de l'argument :
#
# 1. `REPERTOIRE_DE_CAMPAGNE`, resolu depuis L'EMPLACEMENT DE CE MODULE. Tout
#    dossier hors de lui est REFUSE, pour `figer` comme pour `comparer` : les
#    trois gestes de l'audit rendent desormais 1. `src/` est monte en LECTURE
#    SEULE dans l'image d'extraction, et c'est lui qui EST le harnais ;
# 2. `EMPREINTES_ATTENDUES`, une constante de ce module — plus un fichier voisin.
#    LA BORNE RESTANTE S'ECRIT ICI, ET ELLE EST VOULUE : modifier une empreinte
#    exige un commit sur `src/`, revu comme du code. Un instantane refige sans
#    commit porte une autre empreinte, donc rougit.
REPERTOIRE_DE_CAMPAGNE = Path(__file__).resolve().parents[1] / "documentation/campagnes"
#
# LE `pragma` CI-DESSOUS EST UN FAUX POSITIF MOTIVE, et il suit la doctrine du
# depot (`.pre-commit-config.yaml`, hook `detect-secrets`) : un faux positif se
# declare AU SITE, avec sa raison, jamais dans une baseline. Cette chaine est le
# SHA-256 d'un manifeste VERSIONNE, recalculable par `sha256sum` sur un fichier
# que tout le monde lit — c'est l'exact contraire d'un secret : sa valeur EST sa
# publicite. Le hook ne voit qu'une chaine hexadecimale de haute entropie.
EMPREINTES_ATTENDUES: dict[str, str] = {}
# L'AFFECTATION EST SEPAREE POUR UNE SEULE RAISON : le `pragma` doit tenir sur la
# ligne de la chaine, et cette ligne-ci fait exactement 100 caracteres. Dans le
# dictionnaire, l'indentation en ajoutait quatre et `ruff` rougissait (E501).
EMPREINTES_ATTENDUES["2026-09-24-instantane-des-identifiants"] = (
    "e945893b1021e2f1aa3809434a889443ed9fb85e2a7e290cd329f077687f0f6d"  # pragma: allowlist secret
)
# `label` EN DERNIER, et ce n'est pas cosmetique : il n'est jamais vide, donc
# aucune ligne ne finit par une espace que le hook `trailing-whitespace`
# retirerait au commit en alterant l'instantane.
COLONNES: tuple[str, ...] = (
    "element_id",
    "page_no",
    "position_in_page",
    "self_ref",
    "text50",
    "label",
)


class MutationNulleError(AssertionError):
    """Une mutation du controle negatif n'a deplace aucun identifiant.

    C'est un ECHEC DU CONTROLE, pas un resultat : une mutation qui ne mute rien
    rend le controle negatif vert sans rien prouver.
    """


class BarriereDEcritureError(AssertionError):
    """Le harnais a tente d'ecrire dans un store."""


class InstantaneCorrompuError(ValueError):
    """Un fichier de l'instantane ne correspond plus a l'empreinte du manifeste."""


@dataclass
class Emission:
    """Ce que la production a emis pour UN document, lu a la frontiere des stores.

    Attributes:
        partition_key: Chemin du document relatif a `Datas/`.
        cle: `identity.key`, la cle qui entre dans la formule.
        lignes: Un releve par element, dans l'ordre d'emission.
        cles_d_objet: Les cles MinIO que la production aurait ecrites.
        empreinte_de_l_entree: SHA-256 du fichier REELLEMENT converti — la copie
            nettoyee pour un HTML. Elle dit, devant un rouge, si c'est l'entree
            ou le code qui a change.
    """

    partition_key: str
    cle: str
    lignes: list[Ligne]
    cles_d_objet: list[str] = field(default_factory=list)
    empreinte_de_l_entree: str = ""


def releve_de_l_emission(elements: Iterable[Mapping[str, Any]], cle: str) -> list[Ligne]:
    """Rend le releve des elements que la production passe a `persist`.

    **L'identifiant est celui qu'elle a EMIS**, `element["id"]`, et non un
    recalcul : c'est ce qui rend visible une derive au site d'appel de
    `compute_id`. Les champs sont lus par indexation, pas par `.get` : un champ
    disparu du contrat doit lever, pas devenir un zero.

    Args:
        elements: Elements tels que `persist` les recoit.
        cle: Cle du document.

    Returns:
        Un releve par element, dans l'ordre d'emission.
    """
    return [
        {
            "element_id": str(element["id"]),
            "cle": cle,
            "page_no": int(element["page_no"]),
            "position_in_page": int(element["page_position"]),
            "self_ref": str(element.get("self_ref") or ""),
            "text50": str(element["text"] or "")[:50],
            "label": str(element["label"]),
        }
        for element in elements
    ]


def identifiant_par_la_formule(ligne: Mapping[str, Any]) -> str:
    """L'identifiant que `compute_id` donne aux entrees DECLAREES par la ligne.

    Sert a l'attribution et a elle seule : la comparaison porte sur
    l'identifiant emis.
    """
    return compute_id(
        str(ligne["cle"]),
        int(ligne["page_no"]),
        int(ligne["position_in_page"]),
        str(ligne["text50"]),
    )


# ─── L'instantane ───────────────────────────────────────────────────────────
#
# Un fichier TSV par document, un manifeste, un fichier de cles d'objet. Le choix
# du format est justifie au registre (4.37.a) ; en deux mots : un fichier par
# document tient sous le plafond de 500 Ko du hook `check-added-large-files`, et
# des colonnes SANS guillemets ne declenchent pas `detect-secrets`, qui voit un
# secret dans tout hexadecimal entre guillemets (`mesure` au commit).


def _echapper(valeur: str) -> str:
    """Rend une chaine sur une seule ligne, en ASCII, sans tabulation.

    `json.dumps` en ASCII : les controles, la tabulation, les retours et tout
    caractere non ASCII sortent en sequences `\\uXXXX`, donc une ligne de releve
    ne se coupe jamais et aucun hook ne peut la reecrire.
    """
    return json.dumps(valeur, ensure_ascii=True)[1:-1]


def _desechapper(valeur: str) -> str:
    """Inverse exact de :func:`_echapper`."""
    return str(json.loads(f'"{valeur}"'))


def _sha256(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _nom_du_fichier(cle: str) -> str:
    return cle.replace("/", "__") + ".tsv"


def ecrire_le_releve(lignes: Sequence[Ligne]) -> str:
    """Serialise un releve en TSV, entete compris."""
    rangs = ["\t".join(COLONNES)]
    for ligne in lignes:
        rangs.append("\t".join(_echapper(str(ligne[colonne])) for colonne in COLONNES))
    return "\n".join(rangs) + "\n"


def lire_le_releve(texte: str, cle: str) -> list[Ligne]:
    """Relit un releve ecrit par :func:`ecrire_le_releve`.

    `split("\\n")` et non `splitlines()` : ce dernier coupe aussi sur des
    separateurs Unicode, que l'echappement neutralise mais qu'une lecture ne
    doit pas presumer.
    """
    rangs = texte.split("\n")
    if rangs[0] != "\t".join(COLONNES):
        raise InstantaneCorrompuError(f"entete inattendu : {rangs[0]!r}")
    lignes: list[Ligne] = []
    for rang in rangs[1:]:
        if not rang:
            continue
        valeurs = [_desechapper(v) for v in rang.split("\t")]
        brut = dict(zip(COLONNES, valeurs, strict=True))
        lignes.append(
            {
                "element_id": brut["element_id"],
                "cle": cle,
                "page_no": int(brut["page_no"]),
                "position_in_page": int(brut["position_in_page"]),
                "self_ref": brut["self_ref"],
                "text50": brut["text50"],
                "label": brut["label"],
            }
        )
    return lignes


def ecrire_l_instantane(
    dossier: Path, emissions: Sequence[Emission], entete: Mapping[str, str]
) -> str:
    """Ecrit l'instantane et rend son EMPREINTE, le SHA-256 du manifeste.

    Le manifeste porte le SHA-256 de chaque autre fichier : son empreinte les
    couvre donc tous, et c'est elle que le registre consigne.

    Args:
        dossier: Dossier de sortie, cree s'il manque. Il doit etre vide :
            ecraser un instantane serait perdre l'avant.
        emissions: Une emission par document.
        entete: Lignes `cle: valeur` du manifeste — date, commit, commande.

    Returns:
        L'empreinte de l'instantane.

    Raises:
        FileExistsError: Si le dossier contient deja un manifeste.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    if (dossier / MANIFESTE).exists():
        raise FileExistsError(f"{dossier / MANIFESTE} existe : un instantane ne s'ecrase pas")

    rangs = [f"# format\t{FORMAT}"]
    rangs += [f"# {nom}\t{_echapper(valeur)}" for nom, valeur in entete.items()]
    rangs.append("partition_key\tcle\tfichier\telements\tsha256_du_releve\tsha256_de_l_entree")
    objets = ["partition_key\tcle_d_objet"]
    for emission in sorted(emissions, key=lambda e: e.partition_key):
        texte = ecrire_le_releve(emission.lignes)
        nom = _nom_du_fichier(emission.cle)
        (dossier / nom).write_text(texte, encoding="utf-8")
        rangs.append(
            "\t".join(
                (
                    _echapper(emission.partition_key),
                    _echapper(emission.cle),
                    _echapper(nom),
                    str(len(emission.lignes)),
                    _sha256(texte),
                    emission.empreinte_de_l_entree,
                )
            )
        )
        objets += [
            f"{_echapper(emission.partition_key)}\t{_echapper(cle)}"
            for cle in sorted(emission.cles_d_objet)
        ]
    texte_objets = "\n".join(objets) + "\n"
    (dossier / CLES_D_OBJET).write_text(texte_objets, encoding="utf-8")
    rangs.append(f"# sha256 de {CLES_D_OBJET}\t{_sha256(texte_objets)}")
    manifeste = "\n".join(rangs) + "\n"
    (dossier / MANIFESTE).write_text(manifeste, encoding="utf-8")
    return _sha256(manifeste)


@dataclass
class Instantane:
    """Un instantane relu et verifie."""

    empreinte: str
    entete: dict[str, str]
    documents: dict[str, Emission]


class EmpreinteInattendueError(ValueError):
    """L'instantane n'est pas celui que la table versionnee attend."""


class DossierHorsCampagneError(ValueError):
    """Le dossier designe n'est pas dans le repertoire de campagne FIXE."""


def dossier_de_campagne(dossier: Path, repertoire: Path = REPERTOIRE_DE_CAMPAGNE) -> Path:
    """Rend le dossier RESOLU, et refuse tout ce qui est hors du repertoire fixe.

    **C'EST LA REPARATION DU TROISIEME AUDIT**, et elle porte sur les DEUX
    gestes : `figer` dans un scratchpad puis `comparer` contre lui y rendait
    rc=0 des deux cotes. Le repertoire ne se deduit plus de l'argument : il est
    resolu depuis l'emplacement de CE module.

    Le parent doit etre le repertoire, et non un ancetre quelconque : un
    `…/campagnes/bis/instantane` porterait sa propre table voisine, ce que la
    version precedente acceptait.

    Raises:
        DossierHorsCampagneError: Si le dossier n'est pas un enfant direct du
            repertoire de campagne.
    """
    resolu = Path(dossier).resolve()
    if resolu.parent != repertoire.resolve():
        raise DossierHorsCampagneError(
            f"{resolu} est hors du repertoire de campagne {repertoire} : un instantane "
            "ecrit ou compare ailleurs n'est authentifie par rien. Le harnais refuse."
        )
    return resolu


def empreinte_attendue(dossier: Path) -> str:
    """L'empreinte que CE MODULE attend pour ce dossier d'instantane.

    Prise dans :data:`EMPREINTES_ATTENDUES`, une constante de ce module, et non
    plus dans un fichier voisin du dossier designe : l'argument ne peut plus
    deplacer la table qui l'authentifie.

    Un dossier ABSENT de la constante est refuse : c'est exactement le cas du
    second instantane qu'on vient d'ecrire.

    Raises:
        DossierHorsCampagneError: Si le dossier est hors du repertoire fixe.
        EmpreinteInattendueError: Si ce dossier n'est pas dans la constante.
    """
    resolu = dossier_de_campagne(dossier)
    if resolu.name not in EMPREINTES_ATTENDUES:
        raise EmpreinteInattendueError(
            f"{resolu.name} n'est pas dans EMPREINTES_ATTENDUES de "
            f"{Path(__file__).name} : un instantane non inscrit n'est pas comparable "
            "— un second instantane porte sa propre empreinte. L'inscrire exige un commit."
        )
    return EMPREINTES_ATTENDUES[resolu.name]


def lire_l_instantane(dossier: Path) -> Instantane:
    """Relit un instantane et VERIFIE chaque fichier contre le manifeste.

    Raises:
        InstantaneCorrompuError: Si un fichier a change depuis l'ecriture, ou si
            le format n'est pas celui de ce module.
    """
    manifeste = (dossier / MANIFESTE).read_text(encoding="utf-8")
    entete: dict[str, str] = {}
    documents: dict[str, Emission] = {}
    for rang in manifeste.split("\n"):
        if not rang or rang.startswith("partition_key\t"):
            continue
        if rang.startswith("# "):
            nom, _, valeur = rang[2:].partition("\t")
            entete[nom] = _desechapper(valeur)
            continue
        partition_key, cle, nom, nombre, empreinte, entree = rang.split("\t")
        texte = (dossier / _desechapper(nom)).read_text(encoding="utf-8")
        if _sha256(texte) != empreinte:
            raise InstantaneCorrompuError(f"{_desechapper(nom)} ne correspond plus au manifeste")
        lignes = lire_le_releve(texte, _desechapper(cle))
        if len(lignes) != int(nombre):
            raise InstantaneCorrompuError(
                f"{_desechapper(nom)} : {len(lignes)} lignes, {nombre} annoncees"
            )
        documents[_desechapper(partition_key)] = Emission(
            partition_key=_desechapper(partition_key),
            cle=_desechapper(cle),
            lignes=lignes,
            empreinte_de_l_entree=entree,
        )
    if entete.get("format") != FORMAT:
        raise InstantaneCorrompuError(f"format {entete.get('format')!r}, attendu {FORMAT!r}")

    texte_objets = (dossier / CLES_D_OBJET).read_text(encoding="utf-8")
    if _sha256(texte_objets) != entete.get(f"sha256 de {CLES_D_OBJET}"):
        raise InstantaneCorrompuError(f"{CLES_D_OBJET} ne correspond plus au manifeste")
    for rang in texte_objets.split("\n")[1:]:
        if rang:
            partition_key, cle_d_objet = rang.split("\t")
            documents[_desechapper(partition_key)].cles_d_objet.append(_desechapper(cle_d_objet))
    return Instantane(empreinte=_sha256(manifeste), entete=entete, documents=documents)


# ─── La comparaison ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Confrontation:
    """L'emission d'un document confrontee aux sommets du graphe."""

    identiques: int
    emis_seul: int
    graphe_seul: int


def confronter_au_graphe(lignes: Sequence[Ligne], ids_du_graphe: Iterable[str]) -> Confrontation:
    """Confronte les identifiants EMIS a ceux que le graphe porte."""
    emis = {str(ligne["element_id"]) for ligne in lignes}
    graphe = set(ids_du_graphe)
    return Confrontation(
        identiques=len(emis & graphe),
        emis_seul=len(emis - graphe),
        graphe_seul=len(graphe - emis),
    )


@dataclass(frozen=True)
class Deplacement:
    """Un identifiant de l'instantane absent de l'apres.

    Attributes:
        avant: La ligne de l'instantane.
        apres: Son homologue apparie, ou None si l'element a disparu.
        termes: Les entrees de la formule qui different entre les deux, ou
            ``(SITE_D_APPEL,)`` si aucune ne differe et que l'identifiant emis
            change quand meme ; ``()`` si l'element a disparu.
    """

    avant: Ligne
    apres: Ligne | None
    termes: tuple[str, ...]


@dataclass
class Bilan:
    """La comparaison d'un document entre l'instantane et l'apres."""

    partition_key: str
    identiques: int
    deplaces: list[Deplacement]
    apparus_sans_contrepartie: list[Ligne]
    reassignes: list[tuple[Ligne, Ligne]] = field(default_factory=list)


def _cle_d_appariement(lignes: Sequence[Ligne]) -> list[tuple[str, int]]:
    """`(self_ref, rang d'occurrence)` : un PDF repete ses `self_ref` d'un lot a l'autre."""
    vus: dict[str, int] = {}
    cles: list[tuple[str, int]] = []
    for ligne in lignes:
        ref = str(ligne["self_ref"])
        cles.append((ref, vus.get(ref, 0)))
        vus[ref] = vus.get(ref, 0) + 1
    return cles


def termes_changes(avant: Mapping[str, Any], apres: Mapping[str, Any]) -> tuple[str, ...]:
    """Les entrees de la formule qui different, comparees DIRECTEMENT.

    Aucune heuristique : les deux lignes portent les quatre entrees. Si aucune
    ne differe et que l'identifiant emis change, c'est le site d'appel.
    """
    termes = tuple(t for t in TERMES if avant[_CHAMP_DU_TERME[t]] != apres[_CHAMP_DU_TERME[t]])
    if not termes and avant["element_id"] != apres["element_id"]:
        return (SITE_D_APPEL,)
    return termes


def comparer_les_releves(
    partition_key: str, avant: Sequence[Ligne], apres: Sequence[Ligne]
) -> Bilan:
    """Compare l'apres a l'instantane, pour UN document.

    Le COMPTE est une difference d'ensembles d'identifiants emis ; l'appariement
    ne sert qu'a l'attribution.
    """
    ids_apres = {str(ligne["element_id"]) for ligne in apres}
    ids_avant = {str(ligne["element_id"]) for ligne in avant}
    homologue = dict(zip(_cle_d_appariement(apres), apres, strict=True))

    deplaces: list[Deplacement] = []
    contreparties: set[int] = set()
    for cle, ligne in zip(_cle_d_appariement(avant), avant, strict=True):
        if ligne["element_id"] in ids_apres:
            continue
        pendant = homologue.get(cle)
        if pendant is not None:
            contreparties.add(id(pendant))
        deplaces.append(
            Deplacement(
                avant=ligne,
                apres=pendant,
                termes=termes_changes(ligne, pendant) if pendant is not None else (),
            )
        )
    apparus = [
        ligne
        for ligne in apres
        if ligne["element_id"] not in ids_avant and id(ligne) not in contreparties
    ]
    # UN IDENTIFIANT PRESENT DES DEUX COTES PEUT DESIGNER UN AUTRE ELEMENT, et
    # une difference d'ensembles le compte comme identique. `mesure` au chantier
    # B de la reprise du lot 11 : ne plus emettre les puces vides decale leurs
    # voisins, et deux lignes de code VIDES tombent a la position de la puce
    # retiree — meme cle, meme page, meme rang, meme `text50` "", donc MEME
    # identifiant. Un ancrage sur cette puce designerait du code, sans un rouge.
    par_id_apres = {str(ligne["element_id"]): ligne for ligne in apres}
    reassignes = [
        (ligne, par_id_apres[str(ligne["element_id"])])
        for ligne in avant
        if str(ligne["element_id"]) in par_id_apres
        and (ligne["label"], ligne["self_ref"])
        != (
            par_id_apres[str(ligne["element_id"])]["label"],
            par_id_apres[str(ligne["element_id"])]["self_ref"],
        )
    ]
    return Bilan(
        partition_key=partition_key,
        identiques=len(ids_avant & ids_apres) - len(reassignes),
        deplaces=deplaces,
        apparus_sans_contrepartie=apparus,
        reassignes=reassignes,
    )


@dataclass
class Verdict:
    """Le verdict de la campagne : vert si et seulement si `raisons` est vide."""

    deplaces: set[str]
    declares: set[str]
    raisons: list[str]

    @property
    def ok(self) -> bool:
        return not self.raisons


def lire_les_deplacements_annonces(chemin: Path | None) -> set[str]:
    """Lit la liste DECLAREE des deplacements attendus : un identifiant par ligne.

    `#` ouvre un commentaire, pour que chaque identifiant porte sa raison. Sans
    fichier, la liste est VIDE : aucun deplacement n'est attendu.
    """
    if chemin is None:
        return set()
    declares: set[str] = set()
    for rang in chemin.read_text(encoding="utf-8").split("\n"):
        valeur = rang.split("#", 1)[0].strip()
        if valeur:
            declares.add(valeur)
    return declares


def trancher(
    bilans: Sequence[Bilan], declares: set[str], autres_raisons: Sequence[str] = ()
) -> Verdict:
    """Vert si et seulement si l'ensemble deplace EGALE l'ensemble declare.

    Quatre rouges, et aucun n'est tolerable :

    - un deplacement NON declare : l'agent perd un ancrage sans que personne l'ait
      decide ;
    - un identifiant declare qui NE BOUGE PAS : la reparation annoncee n'a pas ete
      appliquee, et un vert le cacherait ;
    - un identifiant APPARU sans contrepartie deplacee : un element que
      l'instantane ne connait pas. La declaration ne porte que sur l'instantane,
      donc un element ajoute n'est pas declarable ; c'est une borne voulue, qui
      force a refiger l'instantane plutot qu'a l'etendre en silence ;
    - un identifiant REASSIGNE : present des deux cotes, il designe apres un
      autre element (autre label ou autre `self_ref`). Non declarable non plus :
      un ancrage qui glisse en silence vers un autre passage est pire qu'un
      ancrage mort.
    """
    deplaces = {str(d.avant["element_id"]) for bilan in bilans for d in bilan.deplaces}
    raisons = list(autres_raisons)
    non_declares = sorted(deplaces - declares)
    non_deplaces = sorted(declares - deplaces)
    apparus = sorted(
        str(ligne["element_id"]) for b in bilans for ligne in b.apparus_sans_contrepartie
    )
    if non_declares:
        raisons.append(f"{len(non_declares)} deplace(s) NON declare(s) : {non_declares[:12]}")
    if non_deplaces:
        raisons.append(
            f"{len(non_deplaces)} declare(s) qui n'ont PAS bouge — la reparation "
            f"annoncee n'est pas appliquee : {non_deplaces[:12]}"
        )
    if apparus:
        raisons.append(f"{len(apparus)} apparu(s) sans contrepartie deplacee : {apparus[:12]}")
    reassignes = sorted(str(a["element_id"]) for b in bilans for a, _ in b.reassignes)
    if reassignes:
        raisons.append(
            f"{len(reassignes)} identifiant(s) REASSIGNE(S) a un autre element — "
            f"meme identifiant, autre label ou autre self_ref : {reassignes[:12]}"
        )
    return Verdict(deplaces=deplaces, declares=set(declares), raisons=raisons)


# ─── Le controle negatif ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Mutation:
    """Une derive simulee de la production, sur UNE entree de la formule.

    Attributes:
        nom: Le nom sous lequel le controle negatif la designe.
        terme: Le terme que l'attribution DOIT designer, et lui seul.
        ligne: Transforme une ligne EN PLACE — ses entrees, et son identifiant
            emis recalcule comme la production derivee le calculerait.
    """

    nom: str
    terme: str
    ligne: Callable[[Ligne], None]


def _reemettre(ligne: Ligne) -> None:
    ligne["element_id"] = identifiant_par_la_formule(ligne)


def _muter_filename(ligne: Ligne) -> None:
    # Le cas d'une identite de document qui change : la copie nettoyee entre dans
    # la cle.
    ligne["cle"] = f".cleaned/{ligne['cle']}"
    _reemettre(ligne)


def _muter_page_no(ligne: Ligne) -> None:
    # Aucune condition sur `page_no` : un chapitre HTML est tout entier en page 1,
    # et c'est ce qui avait rendu nulle la mutation historique `page_no >= 12`.
    if int(ligne["position_in_page"]) >= 1:
        ligne["page_no"] = int(ligne["page_no"]) + 1
        _reemettre(ligne)


def _muter_position(ligne: Ligne) -> None:
    # Le rang glisse d'un cran : un element insere ou retire en amont dans la page.
    ligne["position_in_page"] = int(ligne["position_in_page"]) + 1
    _reemettre(ligne)


def _muter_text50(ligne: Ligne) -> None:
    # Un caractere change, sur un element sur deux : une extraction qui rend un
    # texte legerement different.
    texte = str(ligne["text50"])
    if int(ligne["position_in_page"]) % 2 == 0 and texte and not texte.startswith("Z"):
        ligne["text50"] = "Z" + texte[1:]
        _reemettre(ligne)


def _muter_site_d_appel(ligne: Ligne) -> None:
    # LE SCENARIO DE L'AUDIT : la production calcule sur `.cleaned/<cle>` mais
    # declare la bonne cle. Les quatre entrees de la ligne sont intactes, seul
    # l'identifiant emis change — l'ancien harnais, qui recalculait, ne le voyait
    # pas.
    ligne["element_id"] = compute_id(
        f".cleaned/{ligne['cle']}",
        int(ligne["page_no"]),
        int(ligne["position_in_page"]),
        str(ligne["text50"]),
    )


MUTATIONS: dict[str, Mutation] = {
    "filename": Mutation("filename", "filename", _muter_filename),
    "page_no": Mutation("page_no", "page_no", _muter_page_no),
    "position_in_page": Mutation("position_in_page", "position_in_page", _muter_position),
    "text50": Mutation("text50", "text50", _muter_text50),
    "site_d_appel": Mutation("site_d_appel", SITE_D_APPEL, _muter_site_d_appel),
}


def appliquer_la_mutation(mutation: Mutation, lignes: Sequence[Ligne]) -> list[Ligne]:
    """Applique une mutation a une COPIE, et refuse une mutation sans effet.

    Raises:
        MutationNulleError: Si aucun identifiant ne bouge — le defaut du lot qui
            a ecrit la premiere version, `page_no >= 12` sur un document tout
            entier en page 1.
    """
    copies = [dict(ligne) for ligne in lignes]
    for ligne in copies:
        mutation.ligne(ligne)
    if all(a["element_id"] == b["element_id"] for a, b in zip(lignes, copies, strict=True)):
        raise MutationNulleError(
            f"la mutation « {mutation.nom} » n'a deplace aucun identifiant sur "
            f"{len(copies)} elements : elle ne prouve rien. Sa condition ne se "
            "declenche sur aucun element de ce document — resserre-la."
        )
    return copies


@dataclass(frozen=True)
class Controle:
    """Le resultat d'une mutation : combien la sonde a vu, et a quoi elle l'impute.

    Attributes:
        nom: La mutation.
        mutes: Identifiants d'avant ABSENTS du releve mute, recomptes ici par
            une difference d'ensembles nue, independante de
            :func:`comparer_les_releves`. **Ce n'est pas le nombre de lignes
            touchees** : muter `position_in_page` de +1 fait tomber l'identifiant
            d'un element sur celui de son voisin quand les deux partagent leur
            `text50` — les lignes blanches d'un bloc de code partagent `""`. Ces
            collisions sont comptees a part.
        collisions: Lignes dont l'identifiant a change vers un identifiant que
            l'avant portait deja.
        vus: Deplacements que :func:`comparer_les_releves` rend.
        mal_attribues: Deplacements imputes a autre chose que le terme mute.
    """

    nom: str
    mutes: int
    collisions: int
    vus: int
    mal_attribues: int

    @property
    def ok(self) -> bool:
        return self.vus == self.mutes > 0 and self.mal_attribues == 0


def controle_negatif(partition_key: str, lignes: Sequence[Ligne]) -> list[Controle]:
    """Chaque mutation doit etre VUE en totalite et attribuee a SON terme, seul.

    C'est ce qui prouve, sur le releve reel du jour, que la comparaison sait
    dire « different » et sait dire POURQUOI. Une mutation nulle est un echec du
    controle, rendu comme `mutes = 0`.
    """
    ids_avant = {str(ligne["element_id"]) for ligne in lignes}
    resultats: list[Controle] = []
    for nom, mutation in sorted(MUTATIONS.items()):
        try:
            mutes = appliquer_la_mutation(mutation, lignes)
        except MutationNulleError:
            resultats.append(Controle(nom=nom, mutes=0, collisions=0, vus=0, mal_attribues=0))
            continue
        absents = ids_avant - {str(ligne["element_id"]) for ligne in mutes}
        collisions = sum(
            a["element_id"] != b["element_id"] and b["element_id"] in ids_avant
            for a, b in zip(lignes, mutes, strict=True)
        )
        bilan = comparer_les_releves(partition_key, lignes, mutes)
        mal = sum(d.termes != (mutation.terme,) for d in bilan.deplaces)
        resultats.append(
            Controle(
                nom=nom,
                mutes=len(absents),
                collisions=collisions,
                vus=len(bilan.deplaces),
                mal_attribues=mal,
            )
        )
    return resultats


# ─── Les barrieres sur les SDK de store eux-memes ───────────────────────────

# **POURQUOI LA BARRIERE DESCEND AU SDK, ET POURQUOI LA DERIVATION AST EST PARTIE.**
#
# Les barrieres par SITE (plus bas) remplacent des fonctions NOMMEES de
# `src.docling_service`. Pour savoir lesquelles nommer, la version precedente
# derivait les porteurs de client par lecture AST de `src/docling_service/*.py`.
# Le troisieme audit du lot 11 lui a fait passer QUATRE portes neuves sur cinq :
# un alias d'import (`from minio import Minio as _M`), un `getattr(minio,
# "Minio")(...)`, un client construit au niveau du module, et une porte deposee
# dans `src/pipeline/`. AUCUNE analyse statique du Python n'est complete —
# `getattr` suffit a la tromper — et chaque audit trouverait le trou suivant.
#
# DECISION DU PILOTE, 24 septembre 2026 : on ne rafistole pas l'analyse
# statique. La barriere se pose sur les CONSTRUCTEURS des SDK eux-memes. Une
# fois armee, toute construction de client de store DANS CE PROCESSUS leve,
# quel que soit le chemin qui y mene — alias, `getattr`, niveau de module,
# n'importe quel paquet. Les barrieres par site restent, comme SECONDE COUCHE.
#
# LA BORNE RESTANTE S'ECRIT ICI : un client construit dans un SOUS-PROCESSUS,
# ou par une bibliotheque tierce qui parle a un store hors de ces trois SDK
# (un client S3 `boto3`, un driver HTTP ecrit a la main), n'est pas atteint.
# La barriere tient sur ce qui est IMPORTE dans le processus du harnais.

# Les SDK de store, et le module ou leurs constructeurs sont publies. Un SDK
# absent du processus n'y construit rien : son absence est RENDUE, jamais tue.
SDK_DE_STORE: tuple[str, ...] = ("minio", "nebula3", "chromadb")


def _classes_hors_exception(module: Any) -> list[str]:
    """Les classes publiques du module qui ne sont pas des exceptions.

    La regle de `minio` : le module publie `Minio` et `MinioAdmin` a cote de ses
    seules erreurs. Aucun nom en dur — un client de plus est pris tel quel.
    """
    return sorted(
        nom
        for nom, valeur in vars(module).items()
        if not nom.startswith("_")
        and inspect.isclass(valeur)
        and not issubclass(valeur, BaseException)
    )


def _pools_et_connexions(module: Any) -> list[str]:
    """Les classes publiques dont le nom finit par `Pool`, plus `Connection`.

    La regle de `nebula3.gclient.net` : on n'entre dans le graphe que par un
    pool — `ConnectionPool`, `SessionPool` — ou par la connexion nue. `Session`
    n'est pas la : elle ne se construit pas, elle se demande a un pool barre.
    """
    return sorted(
        nom
        for nom, valeur in vars(module).items()
        if not nom.startswith("_")
        and inspect.isclass(valeur)
        and (nom.endswith("Pool") or nom == "Connection")
    )


def _fabriques_de_client(module: Any) -> list[str]:
    """Les FONCTIONS publiques dont le nom finit par `Client`.

    La regle de `chromadb` : ses clients sont des fabriques (`HttpClient`,
    `Client`, `PersistentClient`, `EphemeralClient`, `CloudClient`,
    `AsyncHttpClient`, `AdminClient`), la ou les noms en `Client` qui sont des
    CLASSES sont les interfaces abstraites (`ClientAPI`, `AsyncClientCreator`),
    qu'on n'instancie pas. `mesure` le 24 septembre 2026, chromadb 0.6.3 :
    7 fabriques, 4 interfaces.
    """
    return sorted(
        nom
        for nom, valeur in vars(module).items()
        if not nom.startswith("_") and nom.endswith("Client") and inspect.isfunction(valeur)
    )


# Par SDK : le module a barrer, et la regle qui ENUMERE ses constructeurs depuis
# le SDK INSTALLE. Une liste de noms en dur se tromperait en silence le jour ou
# le SDK en publie un de plus ; une regle, non.
# `nebula3` en DEUX sites : `SessionPool` est une classe d'un SOUS-MODULE, que
# `nebula3.gclient.net` n'expose pas — l'y chercher ne rendrait que le module.
CONSTRUCTEURS_DES_SDK: tuple[tuple[str, Callable[[Any], list[str]]], ...] = (
    ("minio", _classes_hors_exception),
    ("nebula3.gclient.net", _pools_et_connexions),
    ("nebula3.gclient.net.SessionPool", _pools_et_connexions),
    ("chromadb", _fabriques_de_client),
)


@dataclass
class ArmementDesSdk:
    """Ce que :func:`barrer_les_sdk_de_store` a pose.

    Attributes:
        barres: Par module de SDK, les noms qualifies des constructeurs barres.
        sites: Par constructeur, les `module.attribut` re-lies — un
            `from minio import Minio` deja execute est un site de plus.
        absents: Par module absent du processus, la raison. Un SDK qui ne s'importe
            pas n'y construit aucun client : c'est une constatation, pas un saut.
    """

    barres: dict[str, list[str]]
    sites: dict[str, list[str]]
    absents: dict[str, str]


def barrer_les_sdk_de_store(journal: list[str]) -> ArmementDesSdk:
    """Fait LEVER tout constructeur de client de store, par quelque chemin que ce soit.

    Deux gestes pour chaque constructeur, et il faut les deux :

    1. l'attribut du module du SDK est remplace — ce qui prend tout import
       POSTERIEUR, tout `getattr(minio, "Minio")` et tout alias a venir ;
    2. tout module DEJA charge qui porte l'objet d'origine est re-lie — ce qui
       prend les `from minio import Minio` deja executes, alias compris, dans
       n'importe quel paquet.

    Irreversible dans le processus, comme :func:`armer_les_barrieres`.

    Args:
        journal: Rempli a chaque constructeur touche, meme si la production
            avale la levee. **Un journal non vide est un rouge.**
    """
    barres: dict[str, list[str]] = {}
    sites: dict[str, list[str]] = {}
    absents: dict[str, str] = {}
    for chemin, enumerer in CONSTRUCTEURS_DES_SDK:
        try:
            module = importlib.import_module(chemin)
        # UN `except Exception` LARGE, ET C'EST MOTIVE : l'absence d'un SDK se
        # manifeste par un `ModuleNotFoundError`, mais aussi par tout ce que son
        # import declenche chez un tiers. Elle est RENDUE dans `absents`, jamais
        # tue — et un SDK absent du processus n'y construit aucun client.
        except Exception as exc:
            absents[chemin] = f"{type(exc).__name__}: {exc}"
            continue
        noms = enumerer(module)
        barres[chemin] = [f"{chemin}.{nom}" for nom in noms]
        for nom in noms:
            qualifie = f"{chemin}.{nom}"
            sites[qualifie] = _barrer_le_constructeur(module, nom, qualifie, journal)
    return ArmementDesSdk(barres=barres, sites=sites, absents=absents)


def _barrer_le_constructeur(module: Any, nom: str, qualifie: str, journal: list[str]) -> list[str]:
    """Pose la levee sur `module.nom` ET sur tout module deja charge qui le porte."""
    original = getattr(module, nom)

    def _leve(*_args: Any, **_kwargs: Any) -> Any:
        journal.append(qualifie)
        raise BarriereDEcritureError(
            f"{qualifie} construit : le harnais d'equivalence ne construit aucun client "
            "de store apres armement. Ses clients de LECTURE sont construits avant."
        )

    _leve.__name__ = nom
    # LE BALAYAGE SUFFIT, ET C'EST UNE MUTATION QUI L'A MONTRE. Un
    # `setattr(module, nom, _leve)` explicite figurait ici ; le retirer ne
    # rougissait AUCUNE des sept portes neuves, parce que le module du SDK est
    # lui-meme dans `sys.modules` et que le balayage ci-dessous le re-lie comme
    # les autres. Une ligne qu'aucune mutation ne tient est une ligne qui ne
    # garde rien : elle est partie (registre 4.39.b).
    sites: list[str] = []
    for nom_du_module, charge in sorted(sys.modules.items()):
        if charge is None:
            continue
        try:
            attributs = list(vars(charge).items())
        except TypeError:  # pragma: no cover - un module sans __dict__
            continue
        for nom_d_attribut, valeur in attributs:
            if valeur is original:
                setattr(charge, nom_d_attribut, _leve)
                sites.append(f"{nom_du_module}.{nom_d_attribut}")
    return sorted(set(sites) | {qualifie})


# ─── Les clients de LECTURE, construits AVANT l'armement ────────────────────

# Les verbes nGQL qui LISENT. Tout le reste — `INSERT`, `UPDATE`, `UPSERT`,
# `DELETE`, `DROP`, `CREATE`, `REBUILD`, `SUBMIT` — est refuse.
#
# LA BORNE, ecrite au site : le controle porte sur le PREMIER MOT de chaque
# fragment separe par `;`, et un `;` a l'interieur d'une chaine citee compterait
# comme un separateur. Il refuserait alors plus que necessaire, JAMAIS moins :
# c'est le sens sur lequel un garde peut se tromper.
VERBES_DE_LECTURE: frozenset[str] = frozenset(
    {"USE", "MATCH", "GO", "LOOKUP", "FETCH", "SHOW", "DESCRIBE", "DESC", "RETURN", "YIELD"}
)


class SessionEnLecture:
    """Une session Nebula qui ne laisse passer que des requetes de LECTURE.

    Une enveloppe par methodes nommees ne suffirait pas ici : `execute` est une
    methode de lecture qui accepte n'importe quelle requete d'ecriture. Le
    controle porte donc sur la REQUETE, verbe par verbe.
    """

    def __init__(self, session: Any, journal: list[str]) -> None:
        self._session = session
        self._journal = journal

    def execute(self, requete: str) -> Any:
        """Execute la requete si, et seulement si, chacun de ses fragments LIT."""
        for fragment in requete.split(";"):
            mots = fragment.strip().split(None, 1)
            if mots and mots[0].upper() not in VERBES_DE_LECTURE:
                self._journal.append(f"nebula.execute({mots[0]})")
                raise BarriereDEcritureError(
                    f"requete nGQL refusee, « {mots[0]} » n'est pas un verbe de lecture : "
                    f"{sorted(VERBES_DE_LECTURE)}"
                )
        return self._session.execute(requete)

    def release(self) -> None:
        """Rend la session au pool. Ne lit ni n'ecrit."""
        self._session.release()


class LectureSeule:
    """Une enveloppe qui ne laisse passer que des methodes de LECTURE NOMMEES.

    Tout le reste leve et se journalise. C'est la preuve demandee pour les
    clients que le harnais construit AVANT l'armement : ils survivent a la
    barriere des SDK, donc ils doivent porter la leur.
    """

    def __init__(self, client: Any, methodes: Iterable[str], nom: str) -> None:
        self._client = client
        self._methodes = frozenset(methodes)
        self._nom = nom
        self._journal: list[str] = []

    def __getattr__(self, nom: str) -> Any:
        if nom not in self._methodes:
            self._journal.append(f"{self._nom}.{nom}")
            raise BarriereDEcritureError(
                f"{self._nom}.{nom} appele : ce client est enveloppe en LECTURE SEULE, "
                f"et ne laisse passer que {sorted(self._methodes)}"
            )
        return getattr(self._client, nom)


# ─── Les barrieres ──────────────────────────────────────────────────────────

# Les portes d'ecriture des trois stores, par nom qualifie sous
# `src.docling_service`. Le test de la porte qualite ne se fie PAS a cette liste :
# il classe lui-meme chaque fonction des modules de stores, et rougit sur toute
# porte qui reste liee quelque part a son original.
PORTES: tuple[str, ...] = (
    "storage.persist",
    "storage.forget_document",
    "vectors.write_elements",
    "vectors.delete_document",
    "vectors.get_collection",
    "nebula.get_writer",
    "nebula.NebulaWriter",
    "images.upload_file",
)
# `images.get_client` n'est pas une barriere mais un TEMOIN : voir TemoinMinio.
TEMOIN_MINIO = "images.get_client"

# Pour chaque porte, tout ce qui a ete pose a sa place, l'original en tete : un
# remplacement ulterieur (la capture de `persist` apres sa barriere) doit
# retrouver les sites de TOUS les objets precedents.
_POSES: dict[str, list[object]] = {}


def remplacer_partout(nom: str, remplacant: object) -> list[str]:
    """Remplace une porte a TOUS les sites ou elle est liee, et rend ces sites.

    Parcourt chaque module `src.*` charge et remplace tout attribut qui EST
    l'original ou un remplacant precedent — l'import par nom
    (`from src.docling_service.nebula import get_writer`) cree un second site, que
    remplacer dans `nebula` seul ne touche pas. Un module importe APRES lit la
    porte dans son module d'origine, donc le remplacant.

    Args:
        nom: Nom qualifie sous `src.docling_service`, comme `"nebula.get_writer"`.
        remplacant: L'objet a poser.

    Returns:
        Les sites remplaces, `module.attribut`.
    """
    module_nom, attribut = nom.split(".")
    module = sys.modules[f"src.docling_service.{module_nom}"]
    anciens = _POSES.setdefault(nom, [getattr(module, attribut)])
    sites: list[str] = []
    for nom_du_module, charge in sorted(sys.modules.items()):
        if charge is None or not (nom_du_module == "src" or nom_du_module.startswith("src.")):
            continue
        for nom_d_attribut, valeur in list(vars(charge).items()):
            if any(valeur is ancien for ancien in anciens):
                setattr(charge, nom_d_attribut, remplacant)
                sites.append(f"{nom_du_module}.{nom_d_attribut}")
    anciens.append(remplacant)
    return sites


class TemoinMinio:
    """Un client MinIO INERTE : il enregistre `put_object` et leve sur tout le reste.

    **POURQUOI UN TEMOIN ET NON UNE BARRIERE.** `crop_and_upload` est sur le
    chemin NOMINAL d'un PDF. La version precedente le remplacait par une copie
    qui calculait la cle — et l'enregistrait la ou la production aurait rendu
    None (zone vide, crop en echec), puisque la copie ne croppait pas. Ce temoin
    est pose UNE COUCHE PLUS BAS : `crop_and_upload` de PRODUCTION tourne en
    entier, crop compris, et ses cas None restent les siens. Seul l'envoi est
    remplace. Les cles enregistrees sont ensuite confrontees au listing de MinIO,
    en lecture seule, par le script.

    Toute autre methode leve et se journalise : `remove_object`, `make_bucket`...
    """

    def __init__(self, journal: list[str]) -> None:
        self.cles: list[str] = []
        self._journal = journal

    def put_object(self, _bucket: str, object_name: str, *_args: Any, **_kwargs: Any) -> None:
        self.cles.append(object_name)

    def __getattr__(self, nom: str) -> Any:
        self._journal.append(f"{TEMOIN_MINIO}().{nom}")
        raise BarriereDEcritureError(f"MinIO.{nom} appele : le harnais d'equivalence n'ecrit pas")


@dataclass
class Armement:
    """Ce que :func:`armer_les_barrieres` a pose.

    Attributes:
        barrieres: Par porte, la fonction qui leve.
        sites: Par porte, les `module.attribut` remplaces.
        temoin: Le client MinIO inerte.
        journal: Chaque porte touchee, meme si la production a avale la levee.
            **Un journal non vide est un rouge**, quel que soit le reste.
    """

    barrieres: dict[str, Callable[..., Any]]
    sites: dict[str, list[str]]
    temoin: TemoinMinio
    journal: list[str]
    sdk: ArmementDesSdk


def armer_les_barrieres(journal: list[str] | None = None) -> Armement:
    """Barre les CONSTRUCTEURS des SDK, puis chaque porte d'ecriture a tous ses sites.

    **« Ce qui ecrirait leve », et c'est la seule forme de preuve qui tienne
    ici.** DEUX COUCHES, et l'ordre compte :

    1. :func:`barrer_les_sdk_de_store`, la couche qui PORTE la preuve : apres
       elle, aucune construction de client de store ne passe dans ce processus,
       quel que soit le chemin — alias d'import, `getattr`, niveau de module,
       paquet quelconque. Elle est posee EN PREMIER, avant meme le chargement
       d'`extraction`, pour qu'un client construit a l'import leve a l'import ;
    2. les barrieres par SITE, seconde couche : elles nomment les portes de
       `src.docling_service` et rendent leur levee lisible au journal.

    Irreversible dans le processus : desarmer serait offrir le moyen d'ecrire.
    **CONSEQUENCE, ecrite ici pour que personne ne la decouvre :** l'effet
    survit dans `sys.modules`, donc armer dans un processus partage contamine
    tout ce qui suit — `mesure` par le lot 11 : arme dans le processus de pytest,
    six tests de `tests/unit/test_storage.py` tombent. Le harnais tourne dans un
    processus dedie, et la porte qualite arme en SOUS-PROCESSUS.

    Args:
        journal: Le journal a remplir. Celui d'un appelant qui a deja enveloppe
            ses clients de lecture, ou un neuf. **Un journal non vide est un
            rouge**, quel que soit le reste.
    """
    journal = [] if journal is None else journal
    sdk = barrer_les_sdk_de_store(journal)

    # Charger `extraction` APRES la barriere des SDK et AVANT de poser les
    # barrieres par site : c'est lui qui cree les sites par nom (`get_writer`
    # dans `extraction` et `storage`). Un module charge apres lirait la porte
    # deja remplacee dans son module d'origine.
    importlib.import_module("src.docling_service.extraction")

    def barriere(nom: str) -> Callable[..., Any]:
        def _leve(*_args: Any, **_kwargs: Any) -> Any:
            journal.append(nom)
            raise BarriereDEcritureError(
                f"{nom} appele : le harnais d'equivalence n'ecrit dans aucun store"
            )

        return _leve

    barrieres: dict[str, Callable[..., Any]] = {}
    sites: dict[str, list[str]] = {}
    for nom in PORTES:
        barrieres[nom] = barriere(nom)
        sites[nom] = remplacer_partout(nom, barrieres[nom])

    temoin = TemoinMinio(journal)
    sites[TEMOIN_MINIO] = remplacer_partout(TEMOIN_MINIO, lambda: temoin)
    return Armement(barrieres=barrieres, sites=sites, temoin=temoin, journal=journal, sdk=sdk)


def installer_la_capture() -> list[list[dict[str, Any]]]:
    """Remplace `storage.persist` par une CAPTURE, a tous ses sites.

    La capture valide les elements comme `persist` (c'est pur), les copie, et ne
    rappelle jamais la production.

    Returns:
        La liste des lots captures, remplie au fil des appels.
    """
    from src.docling_service import storage

    valider = storage.validate_elements
    lots: list[list[dict[str, Any]]] = []

    def capture(elements: Sequence[dict[str, Any]], *_args: Any, **_kwargs: Any) -> int:
        valider(elements)
        lots.append([dict(element) for element in elements])
        return 0

    remplacer_partout("storage.persist", capture)
    return lots


def reextraire(
    partition_key: str,
    corpus: Path,
    nettoyes: Path,
    lots: list[list[dict[str, Any]]],
    temoin: TemoinMinio,
) -> Emission:
    """Reextrait un document par le chemin de PRODUCTION et rend ce qu'il a EMIS.

    Les fonctions appelees sont `_extract_flat` et `_extract_pdf` : seule la
    frontiere des stores est remplacee. **Les identifiants rendus sont ceux que
    la production passe a `persist`.**

    Args:
        partition_key: Chemin du document relatif a `Datas/`.
        corpus: Racine du corpus, en lecture.
        nettoyes: Racine des copies nettoyees : c'est ELLE que le pipeline
            convertit pour un HTML, et non la source.
        lots: La liste que :func:`installer_la_capture` remplit, videe ici.
        temoin: Le client MinIO inerte, dont les cles sont videes ici.

    Raises:
        FileNotFoundError: Si la source, ou la copie nettoyee d'un HTML, manque.
    """
    from src.docling_service import extraction
    from src.docling_service.elements import document_identity

    lots.clear()
    temoin.cles.clear()
    identity = document_identity(partition_key)
    source = corpus / partition_key
    if not source.exists():
        raise FileNotFoundError(f"introuvable dans le corpus : {source}")

    if partition_key.lower().endswith(".html"):
        entree = nettoyes / partition_key
        if not entree.exists():
            raise FileNotFoundError(
                f"copie nettoyee manquante : {entree}. C'est elle que le pipeline "
                "convertit, et non la source."
            )
        empreinte = extraction.file_digest(entree)
        extraction._extract_flat(entree, identity, "html", empreinte, extraction._noop)
    else:
        entree = source
        empreinte = extraction.file_digest(entree)
        extraction._extract_pdf(entree, identity, empreinte, extraction._noop)

    return Emission(
        partition_key=partition_key,
        cle=identity.key,
        lignes=releve_de_l_emission([e for lot in lots for e in lot], identity.key),
        cles_d_objet=sorted(temoin.cles),
        empreinte_de_l_entree=empreinte,
    )


# ─── Les cles d'objet ───────────────────────────────────────────────────────

# `images/<radical>/<element_id>_<label>.png` (`images.crop_and_upload`).
_ID_DANS_LA_CLE = re.compile(r"/([0-9a-f]{10})_[^/]+\.png$")


def raisons_des_cles_d_objet(
    partition_key: str,
    avant: Iterable[str],
    apres: Iterable[str],
    bilan: Bilan,
    declares: set[str],
) -> list[str]:
    """Les cles d'objet qui bougent sans que la declaration l'explique.

    Une cle porte l'`element_id` : elle disparait avec un identifiant deplace et
    reapparait sous son homologue. Toute autre difference est un rouge.
    """
    disparues = sorted(set(avant) - set(apres))
    apparues = sorted(set(apres) - set(avant))
    attendues = {
        str(d.apres["element_id"])
        for d in bilan.deplaces
        if d.apres is not None and d.avant["element_id"] in declares
    }

    def identifiant(cle: str) -> str:
        trouve = _ID_DANS_LA_CLE.search(cle)
        return trouve.group(1) if trouve else ""

    raisons: list[str] = []
    orphelines = [c for c in disparues if identifiant(c) not in declares]
    inattendues = [c for c in apparues if identifiant(c) not in attendues]
    if orphelines:
        raisons.append(
            f"{partition_key} : {len(orphelines)} cle(s) d'objet disparue(s) : {orphelines[:4]}"
        )
    if inattendues:
        raisons.append(
            f"{partition_key} : {len(inattendues)} cle(s) d'objet apparue(s) : {inattendues[:4]}"
        )
    return raisons


# ─── L'orchestration : ce que le script execute, et ce que la porte rejoue ──


@dataclass
class Monde:
    """Ce que le harnais lit du monde. Le script branche les vrais ; la porte, des faux.

    Attributes:
        documents_du_corpus: Les partitions que le capteur decouvrirait.
        documents_du_graphe: Les partitions dont le graphe porte un `Document`.
        ids_du_graphe: Par cle de document, les identifiants de ses elements.
        objets_listes: Par partition, les cles que MinIO LISTE sous le prefixe
            de ses crops, ou None si le document n'en produit pas (un HTML : ses
            images sont envoyees par le nettoyage, pas par l'extraction).
        reextraire: Reextrait une partition par le chemin de production.
    """

    documents_du_corpus: Callable[[], list[str]]
    documents_du_graphe: Callable[[], list[str]]
    ids_du_graphe: Callable[[str], set[str]]
    objets_listes: Callable[[str], set[str] | None]
    reextraire: Callable[[str], Emission]


def _ecarts_de_couverture(**ensembles: set[str]) -> list[str]:
    """Les documents qu'une source porte et qu'une autre n'a pas, DANS LES DEUX SENS.

    Aucune liste en dur : le corpus, le graphe et l'instantane se controlent
    l'un l'autre, et un document manquant d'un cote est un rouge.

    **LA VERSION PRECEDENTE NE COMPARAIT QUE `a - b` POUR `a < b`** en ordre
    alphabetique : `instantane - corpus`, `instantane - graphe` et
    `graphe - corpus` n'etaient jamais calcules. `mesure` du second audit du
    lot 11 : avec un `/corpus` VIDE, `comparer` rendait rc=0 et `OK` sans qu'un
    seul identifiant ait ete compare. Le corpus arrive par un MONTAGE, et ce
    depot a deja connu une purge qui emportait 24 fichiers sur 25.
    """
    raisons: list[str] = []
    noms = sorted(ensembles)
    for a in noms:
        for b in noms:
            if a == b:
                continue
            manquants = sorted(ensembles[a] - ensembles[b])
            if manquants:
                raisons.append(
                    f"{len(manquants)} document(s) dans {a} et pas dans {b} : {manquants[:4]}"
                )
    return raisons


def _verifier_l_emission(
    monde: Monde, emission: Emission, dire: Callable[[str], None]
) -> list[str]:
    """Les controles communs aux deux phases, sur l'emission d'UN document.

    L'emission doit etre CELLE du graphe ; les cles d'objet, celles que MinIO
    liste ; et chaque mutation doit etre vue et attribuee a son terme.
    """
    raisons: list[str] = []
    ids = monde.ids_du_graphe(emission.cle)
    confrontation = confronter_au_graphe(emission.lignes, ids)
    ecarts_de_formule = sum(
        ligne["element_id"] != identifiant_par_la_formule(ligne) for ligne in emission.lignes
    )
    dire(f"    elements emis            : {len(emission.lignes)}")
    dire(f"    sommets du graphe        : {len(ids)}")
    dire(f"    emis == graphe           : {confrontation.identiques}")
    dire(f"    emis seul / graphe seul  : {confrontation.emis_seul} / {confrontation.graphe_seul}")
    dire(f"    id emis != formule       : {ecarts_de_formule}")
    # Une emission VIDE contre un graphe vide passe cette confrontation : c'est
    # le controle negatif plus bas qui la rougit, toutes ses mutations y etant
    # nulles. Un second garde ici serait un garde qu'aucun test ne peut isoler.
    if confrontation.emis_seul or confrontation.graphe_seul:
        raisons.append(
            f"{emission.partition_key} : l'emission n'est pas le graphe "
            f"({confrontation.emis_seul} emis seul, {confrontation.graphe_seul} graphe seul)"
        )

    listes = monde.objets_listes(emission.partition_key)
    if listes is not None:
        dire(f"    cles d'objet emises/listees : {len(emission.cles_d_objet)} / {len(listes)}")
        if set(emission.cles_d_objet) != listes:
            raisons.append(
                f"{emission.partition_key} : cles d'objet emises != listing MinIO "
                f"({sorted(set(emission.cles_d_objet) ^ listes)[:4]})"
            )

    for controle in controle_negatif(emission.partition_key, emission.lignes):
        vu_du_graphe = (
            confronter_au_graphe(
                appliquer_la_mutation(MUTATIONS[controle.nom], emission.lignes), ids
            ).emis_seul
            if controle.mutes
            else 0
        )
        # La confrontation au graphe compte les identifiants MUTES absents du
        # graphe, pas les anciens disparus : les deux different par les collisions,
        # donc seul un zero y est un rouge.
        etat = "vu" if controle.ok and vu_du_graphe > 0 else "NON VU"
        dire(
            f"    controle negatif « {controle.nom} » : {controle.mutes} mutes "
            f"({controle.collisions} collisions), {controle.vus} vus, "
            f"{controle.mal_attribues} mal attribues, "
            f"{vu_du_graphe} hors graphe — {etat}"
        )
        if etat != "vu":
            raisons.append(f"{emission.partition_key} / mutation {controle.nom} : {etat}")
    return raisons


def figer(
    monde: Monde,
    dossier: Path,
    entete: Mapping[str, str],
    journal: list[str],
    dire: Callable[[str], None] = print,
) -> int:
    """AVANT la campagne : prouve que l'emission est le graphe, PUIS fige l'instantane.

    Rien n'est ecrit au moindre rouge : un instantane d'une emission qui n'est
    pas le graphe figerait autre chose que ce que l'agent lit.

    Returns:
        0 si l'instantane est ecrit, 1 sinon.
    """
    corpus = set(monde.documents_du_corpus())
    graphe = set(monde.documents_du_graphe())
    raisons = _ecarts_de_couverture(corpus=corpus, graphe=graphe)
    emissions: list[Emission] = []
    for partition_key in sorted(corpus | graphe):
        dire(f"=== {partition_key}")
        emission = monde.reextraire(partition_key)
        raisons += _verifier_l_emission(monde, emission, dire)
        emissions.append(emission)
    if journal:
        raisons.append(f"barrieres touchees : {journal}")

    dire(f"\nDOCUMENTS {len(emissions)}, ELEMENTS {sum(len(e.lignes) for e in emissions)}")
    if raisons:
        dire(f"ECHEC, aucun instantane ecrit — {len(raisons)} raison(s) :")
        for raison in raisons:
            dire(f"  - {raison}")
        return 1
    try:
        empreinte = ecrire_l_instantane(dossier, emissions, entete)
    except FileExistsError as exc:
        # Une trace d'appel n'est pas un verdict : le code de sortie EST le
        # comportement de ce script. `mesure` du second audit du lot 11.
        dire(f"ECHEC, aucun instantane ecrit — {exc}")
        return 1
    dire(f"OK : instantane ecrit dans {dossier}, empreinte {empreinte}")
    return 0


def comparer(
    monde: Monde,
    instantane: Instantane,
    attendue: str,
    declares: set[str],
    journal: list[str],
    dire: Callable[[str], None] = print,
) -> int:
    """APRES la campagne : confronte l'emission du jour a l'INSTANTANE.

    Args:
        attendue: L'empreinte que la table versionnee attend, rendue par
            :func:`empreinte_attendue`. **C'est une MESURE, plus une consigne :**
            un instantane refige apres la campagne porte une autre empreinte,
            donc rougit ici au lieu de re-tautologiser le harnais.

    Returns:
        0 si et seulement si l'ensemble deplace egale l'ensemble declare, que
        l'instantane est bien celui qu'on attend, et que tous les controles
        communs sont verts ; 1 sinon.
    """
    corpus = set(monde.documents_du_corpus())
    graphe = set(monde.documents_du_graphe())
    fige = set(instantane.documents)
    raisons = _ecarts_de_couverture(corpus=corpus, graphe=graphe, instantane=fige)
    bilans: list[Bilan] = []
    attribution: dict[str, int] = {}
    non_compares: list[str] = []
    for partition_key in sorted(corpus | graphe | fige):
        dire(f"=== {partition_key}")
        if partition_key not in fige or partition_key not in corpus:
            # JAMAIS UN `continue` NU : sauter en silence un document de
            # l'instantane etait le faux vert bloquant du second audit du lot 11
            # — un corpus vide rendait rc=0 et `OK`. Un document que l'instantane
            # porte et qu'on ne compare pas est une RAISON.
            manque = "de l'instantane" if partition_key not in fige else "du corpus"
            dire(f"    NON COMPARE : absent {manque}")
            if partition_key in fige:
                non_compares.append(partition_key)
            continue
        avant = instantane.documents[partition_key]
        emission = monde.reextraire(partition_key)
        raisons += _verifier_l_emission(monde, emission, dire)
        bilan = comparer_les_releves(partition_key, avant.lignes, emission.lignes)
        bilans.append(bilan)
        raisons += raisons_des_cles_d_objet(
            partition_key, avant.cles_d_objet, emission.cles_d_objet, bilan, declares
        )
        dire(f"    identiques a l'instantane: {bilan.identiques}")
        dire(f"    deplaces                 : {len(bilan.deplaces)}")
        dire(f"    apparus sans contrepartie: {len(bilan.apparus_sans_contrepartie)}")
        dire(f"    reassignes               : {len(bilan.reassignes)}")
        if avant.empreinte_de_l_entree != emission.empreinte_de_l_entree:
            dire("    L'ENTREE CONVERTIE A CHANGE depuis l'instantane (empreinte differente)")
        for d in bilan.deplaces:
            motif = "+".join(d.termes) or "disparu"
            attribution[motif] = attribution.get(motif, 0) + 1
        for d in bilan.deplaces[:6]:
            apres = d.apres or {}
            dire(
                f"      {d.avant['element_id']} -> {apres.get('element_id', '—')} "
                f"[{'+'.join(d.termes) or 'disparu'}] {d.avant['label']} "
                f"{d.avant['text50'][:30]!r} -> {str(apres.get('text50', ''))[:30]!r}"
            )
    if journal:
        raisons.append(f"barrieres touchees : {journal}")
    if non_compares:
        raisons.append(
            f"{len(non_compares)} document(s) de l'instantane n'ont PAS ete compares, "
            f"faute d'etre dans le corpus : {sorted(non_compares)[:4]}"
        )
    if instantane.empreinte != attendue:
        raisons.append(
            f"EMPREINTE INATTENDUE : l'instantane porte {instantane.empreinte}, la table "
            f"versionnee attend {attendue}. Un instantane refige apres la campagne "
            "comparerait le code du jour a lui-meme."
        )

    verdict = trancher(bilans, declares, raisons)
    dire(f"\nINSTANTANE {instantane.empreinte}")
    dire(f"DOCUMENTS COMPARES {len(bilans)} / {len(fige)} de l'instantane")
    dire(f"DEPLACES {len(verdict.deplaces)}, DECLARES {len(verdict.declares)}")
    dire(f"ATTRIBUTION (exacte, par appariement) : {dict(sorted(attribution.items()))}")
    if not verdict.ok:
        dire(f"ECHEC — {len(verdict.raisons)} raison(s) :")
        for raison in verdict.raisons:
            dire(f"  - {raison}")
        return 1
    dire("OK : l'ensemble deplace est exactement l'ensemble declare.")
    return 0
