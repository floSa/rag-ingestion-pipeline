"""Construction et echappement des requetes nGQL (NebulaGraph).

Module volontairement sans dependance externe : l'echappement est le point le
plus sensible de l'ecriture du graphe — un echappement rate produit un INSERT
rejete par le graphd, donc une perte de donnees invisible cote pipeline. Il
doit rester testable sans NebulaGraph ni Docling installes.

Les statements sont groupes : Nebula accepte plusieurs VALUES par INSERT, ce
qui evite un aller-retour reseau par element (des dizaines de milliers sur un
livre de 400 pages).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

# Bornes d'un INSERT groupe. On limite a la fois le nombre de valeurs et le
# poids en octets : un livre peut contenir des paragraphes tres longs, et un
# statement de plusieurs Mo fait grimper la latence et la memoire du graphd.
MAX_VALUES_PER_STATEMENT = 200
MAX_STATEMENT_BYTES = 262_144

PropertyValue = str | int | float | bool | None


def escape_ngql(value: str) -> str:
    """Echappe une chaine destinee a une litterale nGQL entre guillemets doubles.

    L'antislash est echappe EN PREMIER : sans cela les antislashs du LaTeX
    (``\\frac``, ``\\alpha``) forment des sequences d'echappement invalides et
    Nebula rejette l'INSERT. Les apostrophes ne sont PAS echappees — elles
    n'ont aucune signification a l'interieur d'une litterale entre guillemets
    doubles, et les echapper injectait des antislashs parasites dans le texte
    stocke.

    Args:
        value: Texte brut issu de l'extraction.

    Returns:
        Texte utilisable tel quel entre guillemets doubles.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def quote(value: str) -> str:
    """Retourne la litterale nGQL entre guillemets doubles pour ``value``."""
    return f'"{escape_ngql(value)}"'


def render(value: PropertyValue) -> str:
    """Serialise une valeur de propriete en litterale nGQL."""
    if value is None:
        return '""'
    # bool avant int : en Python, bool est une sous-classe de int.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return quote(value)


def vertex_value(vid: str, properties: Sequence[PropertyValue]) -> str:
    """Construit l'expression VALUES d'un vertex : ``"vid":(p1, p2, ...)``."""
    rendered = ", ".join(render(prop) for prop in properties)
    return f"{quote(vid)}:({rendered})"


def edge_value(src: str, dst: str, properties: Sequence[PropertyValue]) -> str:
    """Construit l'expression VALUES d'un edge : ``"src" -> "dst":(p1, ...)``."""
    rendered = ", ".join(render(prop) for prop in properties)
    return f"{quote(src)} -> {quote(dst)}:({rendered})"


def batch_values(values: Sequence[str]) -> Iterator[list[str]]:
    """Regroupe des expressions VALUES en paquets bornes en nombre et en octets.

    Args:
        values: Expressions produites par :func:`vertex_value` / :func:`edge_value`.

    Yields:
        Paquets non vides, dans l'ordre d'entree.
    """
    batch: list[str] = []
    weight = 0

    for value in values:
        value_weight = len(value.encode()) + 2  # separateur ", "
        too_many = len(batch) >= MAX_VALUES_PER_STATEMENT
        too_heavy = weight + value_weight > MAX_STATEMENT_BYTES
        if batch and (too_many or too_heavy):
            yield batch
            batch, weight = [], 0
        batch.append(value)
        weight += value_weight

    if batch:
        yield batch


def insert_vertex_statements(
    tag: str, properties: Sequence[str], values: Sequence[str]
) -> Iterator[str]:
    """Genere les INSERT VERTEX groupes pour un tag donne."""
    columns = ", ".join(properties)
    for batch in batch_values(values):
        yield f"INSERT VERTEX {tag}({columns}) VALUES {', '.join(batch)};"


def insert_edge_statements(
    edge: str, properties: Sequence[str], values: Sequence[str]
) -> Iterator[str]:
    """Genere les INSERT EDGE groupes pour un type d'arete donne."""
    columns = ", ".join(properties)
    for batch in batch_values(values):
        yield f"INSERT EDGE {edge}({columns}) VALUES {', '.join(batch)};"


def element_vertex_value(element: Mapping[str, Any], max_chars: int) -> str:
    """Construit l'expression VALUES d'un element, dans l'ordre de VERTEX_PROPERTIES.

    C'est ici, et non dans l'ecriture NebulaGraph, que se decide ce qu'un
    sommet porte : ce module n'a aucune dependance externe, donc la decision
    est verifiable sans graphd, et une colonne qui cesserait d'etre ecrite fait
    rougir un test.

    ``depth`` vaut 0 par defaut et non une chaine vide : c'est un entier, et 0
    est la profondeur d'un titre rattache au document — une valeur, pas une
    absence.

    Args:
        element: Element produit par ``DocumentAccumulator``.
        max_chars: Longueur au-dela de laquelle le texte est coupe dans le
            graphe. ChromaDB, lui, n'est pas coupe : les deux stores divergent
            sur ces elements-la, et ``nebula.py`` le compte.

    Returns:
        L'expression ``"vid":(p1, p2, ...)`` prete pour un INSERT groupe.
    """
    return vertex_value(
        str(element["id"]),
        (
            str(element["label"]),
            int(element["page_no"]),
            str(element.get("text") or "")[:max_chars],
            str(element.get("minio_url") or ""),
            int(element.get("depth") or 0),
        ),
    )


def tag_schema_statements(tags: Sequence[str]) -> list[str]:
    """Genere la creation ET la migration des tags d'element.

    Les deux sont necessaires et ne se remplacent pas :

    - ``CREATE TAG IF NOT EXISTS`` cree le tag sur un space neuf, et ne fait
      **rien** sur un space ou le tag existe deja, meme si son schema a change ;
    - ``ALTER TAG ... ADD`` ajoute la colonne manquante a un space deja peuple.
      Il echoue avec « Existed! » quand la colonne est la : cet echec est
      attendu et l'appelant le tolere.

    Ce que devient un space existant, `mesure` le 31 aout 2026 sur ``rag_space``
    peuple de 2 288 sommets : le tag gagne la colonne, et les sommets deja
    ecrits la portent a NULL. Le schema migre en place ; les **donnees**, non.
    Seule une reecriture du document — donc une reingestion — les renseigne.

    Args:
        tags: Tags d'element a creer, sans le tag ``Document``, dont le schema
            lui est propre.

    Returns:
        Les requetes, creations d'abord, migrations ensuite.
    """
    colonnes = ", ".join(
        f"{nom} {type_}" for nom, type_ in zip(VERTEX_PROPERTIES, VERTEX_TYPES, strict=True)
    )
    creations = [f"CREATE TAG IF NOT EXISTS {tag}({colonnes});" for tag in tags]
    # Un ALTER par colonne, et non pour la seule colonne du jour : c'est le
    # patron deja applique au tag Document, et il n'a aucune liste a tenir a
    # jour. Sur un space neuf les douze echouent en « Existed! », ce qui est
    # tolere ; sur un space ancien, seules les manquantes passent.
    migrations = [
        f"ALTER TAG {tag} ADD ({nom} {type_});"
        for tag in tags
        for nom, type_ in zip(VERTEX_PROPERTIES, VERTEX_TYPES, strict=True)
    ]
    return creations + migrations


def missing_vertex_columns(colonnes_lues: Iterable[str]) -> tuple[str, ...]:
    """Retourne les colonnes de VERTEX_PROPERTIES absentes d'un tag reel.

    Sert a constater qu'une migration a REELLEMENT eu lieu, et pas seulement
    qu'elle a ete demandee. Une migration echoue silencieusement — l'appelant
    tolere l'echec d'un ALTER, puisque « la colonne existe deja » en est le cas
    nominal — et son echec ne se verrait autrement qu'a la premiere ecriture,
    sur un rejet du graphd pour colonne inconnue.

    Ce n'est pas une precaution theorique. `mesure` le 31 aout 2026 sur
    ``rag_space`` peuple de 15 196 sommets : onze tags sur douze ont migre, le
    douzieme a ete refuse avec « Schema exisited before! », et ``init_schema()``
    a rendu **True**. Nebula conserve l'historique de schema d'un tag et
    n'autorise jamais une colonne supprimee a revenir sous le meme nom : une
    migration n'est donc PAS reversible, et un ``ALTER ... DROP`` condamne le
    tag jusqu'a la recreation du space.

    Args:
        colonnes_lues: Noms de colonnes rendus par ``DESCRIBE TAG``.

    Returns:
        Les colonnes manquantes, dans l'ordre du schema. Vide si le tag est
        complet ; un tag plus riche que le schema n'est pas en faute.
    """
    presentes = set(colonnes_lues)
    return tuple(colonne for colonne in VERTEX_PROPERTIES if colonne not in presentes)


# Longueur des identifiants de noeud declaree a la creation du space, en
# OCTETS et non en caracteres. Un titre francais un peu long depassait les 64
# octets d'origine : « Kimi K3 — l'architecture d'un modele pense pour
# l'efficacite » en fait 70, les accents comptant double et le tiret cadratin
# triple. Le graphd rejetait alors l'insertion du document entier.
#
# Nebula ne sait pas modifier ce type apres coup : passer a 256 suppose de
# recreer le space (purge des stores).
VID_MAX_BYTES = 256

# Le schema d'un sommet d'element, et son SEUL site. Il en existait deux —
# celui-ci, mort et faux, et celui de nebula.py, vivant — et la duplication a
# survecu a trois campagnes de mesure (registre 5.3). Une constante morte qui
# decrit faussement le schema qu'on vient de changer est un piege : elle se
# relit comme une definition.
#
# `depth` est la derniere colonne, et elle est arrivee la parce que l'agent ne
# pouvait lire AUCUN niveau declare sur un titre (registre 4.11). Il pouvait
# remonter les aretes PARENT_OF ; il ne pouvait pas savoir a quelle profondeur
# il etait arrive sans les compter lui-meme. Et le substitut suppose — la
# metadonnee `depth` de ChromaDB — ne substitue rien : aucun `section_header`
# n'est jamais un chunk (registre 4.24, mesure).
VERTEX_PROPERTIES = ("label", "page_no", "text", "minio_url", "depth")

# Le type nGQL de chaque colonne de VERTEX_PROPERTIES, dans le meme ordre. Les
# deux tuples sont lus ensemble par :func:`tag_schema_statements` : les
# desaligner produit un CREATE TAG qui n'a pas les colonnes que les INSERT
# ecrivent, donc un rejet du graphd sur chaque element.
VERTEX_TYPES = ("string", "int", "string", "string", "int")

DOCUMENT_PROPERTIES = (
    "filename",
    "type_file",
    "total_pages",
    "collection",
    "source_path",
    "language",
    "content_hash",
)


def document_vid(filename: str) -> str:
    """Construit l'identifiant du noeud Document, borne a la longueur admise.

    L'identifiant reste lisible — ``doc_mon_livre`` — ce qui rend les requetes
    manuelles dans Nebula Studio praticables. Au-dela de la limite, il est
    tronque sur une frontiere de caractere et suffixe d'une empreinte, pour que
    deux titres partageant leur debut ne se confondent pas.

    Args:
        filename: Nom du document, sans extension.

    Returns:
        Un identifiant tenant dans ``VID_MAX_BYTES`` octets.
    """
    lisible = f"doc_{filename}"
    encode = lisible.encode()
    if len(encode) <= VID_MAX_BYTES:
        return lisible

    empreinte = hashlib.sha256(filename.encode()).hexdigest()[:10]
    garde = VID_MAX_BYTES - len(empreinte) - 1
    return f"{encode[:garde].decode(errors='ignore')}_{empreinte}"
