"""Le harnais qui prouve qu'une reingestion ne deplace aucun `element_id`.

**POURQUOI CE HARNAIS EST VERSIONNE, et non refait a chaque campagne.** Un lot
en lecture seule a mesure, sur 5 documents, que 3 840 elements reextraits par le
chemin de production redonnent 3 840 identifiants IDENTIQUES, 0 different. La
campagne de reingestion devra refaire EXACTEMENT cette mesure APRES coup, et
reconstruire la sonde inviterait une sonde DIFFERENTE — donc une comparaison
entre deux chiffres qui ne mesurent pas la meme chose.

**CE QUE CE MODULE GARDE, ET CE QUI LE GARDE LUI.** Il rend un zero, et une
campagne decidera sur ce zero. Un zero non double ne vaut rien : c'est
`tests/unit/test_equivalence_des_identifiants.py` qui double celui-ci, en
prouvant que les quatre mutations de :data:`MUTATIONS` — une par entree de
:func:`~src.docling_service.elements.compute_id` — font bien bouger ce que la
sonde compte. **Le controle negatif tourne dans la porte qualite**, parce qu'un
harnais dont la sonde n'est pas eprouvee est un faux temoin.

**LA MUTATION NULLE, ET C'EST LE DEFAUT QUI A DONNE CE MODULE SA FORME.** Le lot
qui a ecrit ce harnais dans son scratchpad a d'abord mute `page_no` sur la
condition `page_no >= 12` — or un chapitre HTML est TOUT ENTIER en page 1. La
mutation ne mutait rien, le controle negatif etait vert, et il ne controlait
rien. :func:`appliquer_la_mutation` REFUSE desormais ce cas : une mutation qui ne
deplace aucun identifiant leve :class:`MutationNulleError`. La garde vit dans le
producteur et non dans le test, parce qu'une mutation nulle doit etre impossible
a executer, pas seulement impossible a ecrire dans un test.

**CE MODULE N'ECRIT DANS AUCUN STORE, et ce n'est pas une intention.** Le
harnais reextrait par le chemin de PRODUCTION : `storage.persist`,
`vectors.write_elements`, `images.upload_file` sont a portee d'appel, et une
ligne oubliee ecrirait pour de bon. :func:`armer_les_barrieres` remplace ces
portes par des fonctions qui LEVENT. Ce qui ecrirait leve ; une relecture
n'aurait rien garanti.

**CE QUE CE MODULE NE GARDE PAS, et il faut le dire.** L'attribution de
:func:`comparer` — dire LEQUEL des quatre termes a bouge — se trompe quand
plusieurs elements d'une meme page partagent le meme `text[:50]`. Le cas existe
dans le corpus : chaines vides, `,`, fragments de code repetes. Le lot qui a
ecrit la premiere version l'a mesure a environ **2 % des cas**. Le COMPTE
d'identifiants deplaces, lui, est exact : c'est une comparaison d'ensembles. Ne
lis l'attribution que comme une piste.

Ce module s'importe cote hote : aucune dependance lourde au niveau du module,
les clients de stores sont importes dans la fonction qui en a besoin.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.docling_service.elements import compute_id

# Un releve d'element, tel que le harnais le manipule : les QUATRE entrees de
# `compute_id` et rien de plus. `text50` et non `text` : c'est deja la troncature
# qui entre dans la formule, et la porter entiere inviterait a comparer des
# textes la ou seule la formule compte.
Element = dict[str, Any]


class MutationNulleError(AssertionError):
    """Une mutation du controle negatif n'a deplace aucun identifiant.

    C'est un ECHEC DU CONTROLE, pas un resultat : une mutation qui ne mute rien
    rend le controle negatif vert sans rien prouver.
    """


class BarriereDEcritureError(AssertionError):
    """Le harnais a tente d'ecrire dans un store."""


@dataclass(frozen=True)
class Mutation:
    """Une perturbation d'UNE seule entree de :func:`compute_id`.

    Attributes:
        nom: Le nom sous lequel le controle negatif la designe.
        terme: L'entree de la formule qu'elle perturbe, parmi ``filename``,
            ``page_no``, ``position_in_page`` et ``text50``.
        cle: Transforme la cle du document. L'identite pour les trois mutations
            qui portent sur un element.
        element: Transforme un element EN PLACE. Ne rend rien : une mutation qui
            rendrait un nouvel element inviterait a oublier de le reprendre.
    """

    nom: str
    terme: str
    cle: Callable[[str], str]
    element: Callable[[Element], None]


@dataclass(frozen=True)
class Rapport:
    """Le bilan d'une comparaison entre un releve recalcule et le graphe.

    Attributes:
        identiques: Identifiants presents des deux cotes. **C'est le chiffre de
            la campagne.**
        recalcul_seul: Identifiants que la reextraction produit et que le graphe
            n'a pas.
        graphe_seul: Identifiants que le graphe porte et que la reextraction ne
            reproduit plus. Un identifiant deplace apparait des DEUX cotes de
            cette paire, et c'est voulu : il est perdu ici et invente la-bas.
        attribution: Par terme de la formule, le nombre de sommets du graphe
            qu'on lui impute. **Indicative** : voir la limite en tete de module.
        exemples: Quelques cas, pour lire un rouge sans relancer la sonde.
    """

    identiques: int
    recalcul_seul: int
    graphe_seul: int
    attribution: dict[str, int] = field(default_factory=dict)
    exemples: list[dict[str, Any]] = field(default_factory=list)


def recalculer_les_identifiants(elements: Sequence[Element], cle: str) -> list[Element]:
    """Rend les elements, chacun portant l'`id` que la formule lui donne.

    La formule appelee est celle de la PRODUCTION,
    :func:`~src.docling_service.elements.compute_id`, et non une copie. Une
    copie derive : c'est ce qui rendrait le harnais vert le jour ou la
    production changerait de formule.

    Args:
        elements: Releves d'elements, portant ``page_no``, ``page_position`` et
            ``text50``.
        cle: Cle du document — `identity.key`, et non le seul nom de fichier.

    Returns:
        Une NOUVELLE liste ; l'entree n'est pas modifiee. Les quatre passes du
        controle negatif partagent le meme releve source.
    """
    return [
        {
            **element,
            "id": compute_id(
                cle,
                int(element["page_no"]),
                int(element["page_position"]),
                str(element["text50"]),
            ),
        }
        for element in elements
    ]


# Les quatre entrees de `compute_id(filename, page_no, position_in_page, text)`,
# mutees une par une. Aucune condition ne porte sur `page_no` : c'est ce qui
# avait rendu la mutation `page_no` nulle sur un chapitre HTML, entierement en
# page 1. Elles portent toutes sur le RANG dans le releve, qui existe toujours.
MUTATIONS: dict[str, Mutation] = {
    "filename": Mutation(
        nom="filename",
        terme="filename",
        # Le cas d'un `CLEANED_SUBDIR` mal regle : la copie nettoyee entre dans
        # la formule a la place de la source.
        cle=lambda cle: f".cleaned/{cle}",
        element=lambda element: None,
    ),
    "page_no": Mutation(
        nom="page_no",
        terme="page_no",
        cle=lambda cle: cle,
        element=lambda element: (
            element.__setitem__("page_no", int(element["page_no"]) + 1)
            if int(element["page_position"]) >= 1
            else None
        ),
    ),
    "position_in_page": Mutation(
        nom="position_in_page",
        terme="position_in_page",
        cle=lambda cle: cle,
        # Le rang glisse d'un cran : le cas d'un element insere ou retire en
        # amont dans la page.
        element=lambda element: element.__setitem__(
            "page_position", int(element["page_position"]) + 1
        ),
    ),
    "text50": Mutation(
        nom="text50",
        terme="text50",
        cle=lambda cle: cle,
        # Un caractere change, sur un element sur deux : le cas d'une extraction
        # qui rend un texte legerement different — une espace, une puce, une
        # troncature.
        element=lambda element: (
            element.__setitem__("text50", "Z" + str(element["text50"])[1:])
            if int(element["page_position"]) % 2 == 0 and element["text50"]
            else None
        ),
    ),
}


def appliquer_la_mutation(
    mutation: Mutation, elements: Sequence[Element], cle: str
) -> tuple[str, list[Element]]:
    """Applique une mutation, et REFUSE de rendre une mutation sans effet.

    Args:
        mutation: La perturbation a appliquer.
        elements: Releve d'origine. **Il n'est pas modifie** : les quatre passes
            du controle negatif le partagent, et une mutation qui ecrirait
            dedans contaminerait les suivantes.
        cle: Cle du document.

    Returns:
        La cle mutee et le releve mute, prets pour
        :func:`recalculer_les_identifiants`.

    Raises:
        MutationNulleError: Si aucun identifiant ne bouge. C'est le defaut du lot
            precedent — `page_no >= 12` sur un document entierement en page 1 —
            et il ne doit plus pouvoir passer pour une preuve.
    """
    copies = [dict(element) for element in elements]
    for element in copies:
        mutation.element(element)
    cle_mutee = mutation.cle(cle)

    avant = {element["id"] for element in recalculer_les_identifiants(elements, cle)}
    apres = {element["id"] for element in recalculer_les_identifiants(copies, cle_mutee)}
    if avant == apres:
        raise MutationNulleError(
            f"la mutation « {mutation.nom} » n'a deplace aucun identifiant sur "
            f"{len(copies)} elements : elle ne prouve rien. Sa condition ne se "
            "declenche sur aucun element de ce document — resserre-la."
        )
    return cle_mutee, copies


def comparer(
    recalcules: Sequence[Element], sommets_du_graphe: Mapping[str, Mapping[str, Any]]
) -> Rapport:
    """Confronte les identifiants recalcules a ceux que le graphe porte.

    Args:
        recalcules: Elements issus de :func:`recalculer_les_identifiants`.
        sommets_du_graphe: Par identifiant de sommet, ses proprietes ``page_no``
            et ``text``. Le graphe ne stocke pas ``position_in_page`` ; les trois
            autres entrees de la formule y sont relisibles, ce qui suffit a
            l'attribution.

    Returns:
        Le :class:`Rapport`. ``identiques`` est le chiffre de la campagne.
    """
    ids_recalcules = {str(element["id"]) for element in recalcules}
    ids_graphe = set(sommets_du_graphe)

    graphe_seuls = sorted(ids_graphe - ids_recalcules)
    par_text50: dict[str, list[Element]] = {}
    for element in recalcules:
        par_text50.setdefault(str(element["text50"]), []).append(element)

    attribution: dict[str, int] = {}
    exemples: list[dict[str, Any]] = []
    for vid in graphe_seuls:
        proprietes = sommets_du_graphe[vid]
        text50 = str(proprietes.get("text") or "")[:50]
        page_no = int(proprietes.get("page_no") or 0)
        jumeaux = par_text50.get(text50, [])
        if jumeaux and any(int(e["page_no"]) == page_no for e in jumeaux):
            cause = "position_in_page"
        elif jumeaux:
            cause = "page_no"
        elif ids_recalcules and not ids_graphe & ids_recalcules:
            cause = "filename (cle du document)"
        else:
            cause = "text50 ou element disparu"
        attribution[cause] = attribution.get(cause, 0) + 1
        if len(exemples) < 8:
            exemples.append({"vid": vid, "cause": cause, "page_no": page_no, "text50": text50[:46]})

    return Rapport(
        identiques=len(ids_recalcules & ids_graphe),
        recalcul_seul=len(ids_recalcules - ids_graphe),
        graphe_seul=len(graphe_seuls),
        attribution=attribution,
        exemples=exemples,
    )


def armer_les_barrieres() -> dict[str, Callable[..., Any]]:
    """Remplace toute porte d'ecriture des trois stores par une levee.

    **« Ce qui ecrirait leve », et c'est la seule forme de preuve qui tienne
    ici.** Le harnais appelle les fonctions de production ; celles qui ecrivent
    sont a portee. Les remplacer par des fonctions qui levent transforme une
    ecriture oubliee en run casse, la ou une relecture l'aurait laissee passer.

    L'appel est irreversible dans le processus : le harnais n'a aucune raison de
    desarmer, et offrir un desarmement serait offrir le moyen d'ecrire.

    **CONSEQUENCE, ecrite ici pour que personne ne la decouvre :** les portes
    sont remplacees DANS les modules de production, donc l'effet survit dans
    `sys.modules`. Armer dans un processus partage contamine tout ce qui suit —
    `mesure` : arme dans le processus de pytest, cet appel fait tomber SIX tests
    de `tests/unit/test_storage.py`. Le harnais tourne dans un processus dedie,
    et le test de la porte qualite arme en SOUS-PROCESSUS.

    Returns:
        Par nom qualifie, la barriere posee. Le test de la porte qualite les
        appelle une par une et verifie que chacune leve.
    """
    from src.docling_service import images, nebula, storage, vectors

    def barriere(nom: str) -> Callable[..., Any]:
        def _leve(*_args: Any, **_kwargs: Any) -> Any:
            raise BarriereDEcritureError(
                f"{nom} appele : le harnais d'equivalence n'ecrit dans aucun store"
            )

        return _leve

    portes: dict[str, Any] = {
        "storage.persist": storage,
        "storage.forget_document": storage,
        "vectors.write_elements": vectors,
        "vectors.delete_document": vectors,
        "vectors.get_collection": vectors,
        "nebula.get_writer": nebula,
        "images.upload_file": images,
        "images.crop_and_upload": images,
        "images.get_client": images,
    }

    armees: dict[str, Callable[..., Any]] = {}
    for nom, module in portes.items():
        attribut = nom.split(".")[1]
        pose = barriere(nom)
        setattr(module, attribut, pose)
        armees[nom] = pose
    return armees
