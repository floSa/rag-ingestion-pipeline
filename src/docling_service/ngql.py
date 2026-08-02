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
from collections.abc import Iterator, Sequence

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


# Longueur des identifiants de noeud declaree a la creation du space, en
# OCTETS et non en caracteres. Un titre francais un peu long depassait les 64
# octets d'origine : « Kimi K3 — l'architecture d'un modele pense pour
# l'efficacite » en fait 70, les accents comptant double et le tiret cadratin
# triple. Le graphd rejetait alors l'insertion du document entier.
#
# Nebula ne sait pas modifier ce type apres coup : passer a 256 suppose de
# recreer le space (purge des stores).
VID_MAX_BYTES = 256

VERTEX_PROPERTIES = ("label", "page_no", "text", "minio_url")
DOCUMENT_PROPERTIES = ("filename", "type_file", "total_pages")


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
