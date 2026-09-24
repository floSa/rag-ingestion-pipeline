"""Compte, DEPUIS L'INSTANTANE SEUL, les elements que Docling rend emiettes.

**CE QU'ON COMPTE ET POURQUOI.** Docling decoupe un `<li>` ou un paragraphe mis
en forme (gras, lien, code en ligne) en PLUSIEURS elements, parfois d'un seul
caractere — l'exemple du registre est « ) ». Chacun coute une place de fenetre
et un marqueur `[src:...]` au prompt de l'agent. On le CONSIGNE et on le COMPTE ;
on ne le corrige pas, parce que le corriger deplacerait des milliers
d'identifiants (registre 4.37.g, decision du proprietaire du 24 septembre 2026).

**LECTURE SEULE, ET RIEN QUE L'INSTANTANE.** Aucune conversion, aucun store,
aucun client. L'instantane porte `text50`, TRONQUE A 50 CARACTERES : toute
longueur STRICTEMENT INFERIEURE a 50 y est donc exacte, et une longueur de 50
signifie « 50 ou plus », qu'on ne sait pas departager. Tous les comptes de ce
script portent sur des longueurs exactes ; la borne « moins de 50 » est
exclusive pour cette raison, et elle est dite a cote de chaque tableau.

Il ne touche a rien : il tourne sur l'HOTE, sans docling ni chromadb, parce
qu'il ne lit que des TSV. `PYTHONPATH=.` parce que le depot n'est pas installe.

    PYTHONPATH=. uv run python scripts/campagne/compter-l-emiettement.py \\
        documentation/campagnes/2026-09-24-instantane-des-identifiants
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from src.equivalence_des_identifiants import lire_l_instantane

# Les tranches de longueur, bornes INCLUSIVES aux deux bouts. « vide » est a
# part : un texte vide n'est pas un texte court, c'est un element sans texte.
TRANCHES: tuple[tuple[str, int, int], ...] = (
    ("1 car.", 1, 1),
    ("2-5", 2, 5),
    ("6-20", 6, 20),
    ("21-49", 21, 49),
)


def _format_du_document(partition_key: str) -> str:
    """HTML ou PDF, lu de l'extension de la partition. Aucune liste en dur."""
    return partition_key.rsplit(".", 1)[-1].upper()


def _tableau(titre: str, entetes: list[str], rangs: list[list[str]]) -> str:
    largeurs = [
        max(len(str(entete)), *(len(str(rang[i])) for rang in rangs)) if rangs else len(entete)
        for i, entete in enumerate(entetes)
    ]
    lignes = [f"\n{titre}"]
    lignes.append("  ".join(str(e).ljust(largeurs[i]) for i, e in enumerate(entetes)))
    lignes.append("  ".join("-" * largeur for largeur in largeurs))
    for rang in rangs:
        lignes.append("  ".join(str(v).ljust(largeurs[i]) for i, v in enumerate(rang)))
    return "\n".join(lignes)


def main() -> int:
    """Point d'entree : imprime les tableaux, ne rend jamais autre chose que 0."""
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("dossier", type=Path)
    arguments = analyseur.parse_args()

    instantane = lire_l_instantane(arguments.dossier)
    print(f"INSTANTANE {instantane.empreinte}")
    print(f"DOCUMENTS {len(instantane.documents)}")

    # Un enregistrement par element : (format, label, longueur du text50, texte).
    elements: list[tuple[str, str, int, str]] = []
    for partition_key, emission in sorted(instantane.documents.items()):
        forme = _format_du_document(partition_key)
        for ligne in emission.lignes:
            texte = str(ligne["text50"])
            elements.append((forme, str(ligne["label"]), len(texte), texte))
    print(f"ELEMENTS {len(elements)}")
    tronques = sum(1 for _, _, longueur, _ in elements if longueur == 50)
    print(
        f"dont text50 de longueur 50, donc TRONQUES et de longueur reelle inconnue : {tronques}\n"
        "Tous les tableaux ci-dessous portent sur des longueurs EXACTES : une\n"
        "longueur strictement inferieure a 50 n'est jamais tronquee."
    )

    formes = sorted({forme for forme, _, _, _ in elements})

    print(
        _tableau(
            "=== TRANCHES DE LONGUEUR, PAR FORMAT "
            '(bornes inclusives ; « vide » = text50 == "") ===',
            ["tranche", *formes, "total"],
            [
                [
                    "vide",
                    *[
                        str(sum(1 for f, _, lo, _ in elements if f == forme and lo == 0))
                        for forme in formes
                    ],
                    str(sum(1 for _, _, lo, _ in elements if lo == 0)),
                ]
            ]
            + [
                [
                    nom,
                    *[
                        str(sum(1 for f, _, lo, _ in elements if f == forme and bas <= lo <= haut))
                        for forme in formes
                    ],
                    str(sum(1 for _, _, lo, _ in elements if bas <= lo <= haut)),
                ]
                for nom, bas, haut in TRANCHES
            ]
            + [
                [
                    "< 50 (1-49)",
                    *[
                        str(sum(1 for f, _, lo, _ in elements if f == forme and 1 <= lo <= 49))
                        for forme in formes
                    ],
                    str(sum(1 for _, _, lo, _ in elements if 1 <= lo <= 49)),
                ],
                [
                    "TOUS",
                    *[str(sum(1 for f, _, _, _ in elements if f == forme)) for forme in formes],
                    str(len(elements)),
                ],
            ],
        )
    )

    labels = sorted({label for _, label, _, _ in elements})
    print(
        _tableau(
            "=== TRANCHES DE LONGUEUR, PAR LABEL (tous formats confondus) ===",
            ["label", "vide", *[nom for nom, _, _ in TRANCHES], "< 50 (1-49)", "tous"],
            [
                [
                    label,
                    str(sum(1 for _, la, lo, _ in elements if la == label and lo == 0)),
                    *[
                        str(
                            sum(1 for _, la, lo, _ in elements if la == label and bas <= lo <= haut)
                        )
                        for _, bas, haut in TRANCHES
                    ],
                    str(sum(1 for _, la, lo, _ in elements if la == label and 1 <= lo <= 49)),
                    str(sum(1 for _, la, _, _ in elements if la == label)),
                ]
                for label in labels
            ],
        )
    )

    for forme in formes:
        print(
            _tableau(
                f"=== TRANCHES DE LONGUEUR, PAR LABEL, FORMAT {forme} ===",
                ["label", "vide", *[nom for nom, _, _ in TRANCHES], "< 50 (1-49)", "tous"],
                [
                    [
                        label,
                        str(
                            sum(
                                1
                                for f, la, lo, _ in elements
                                if f == forme and la == label and lo == 0
                            )
                        ),
                        *[
                            str(
                                sum(
                                    1
                                    for f, la, lo, _ in elements
                                    if f == forme and la == label and bas <= lo <= haut
                                )
                            )
                            for _, bas, haut in TRANCHES
                        ],
                        str(
                            sum(
                                1
                                for f, la, lo, _ in elements
                                if f == forme and la == label and 1 <= lo <= 49
                            )
                        ),
                        str(sum(1 for f, la, _, _ in elements if f == forme and la == label)),
                    ]
                    for label in labels
                    if any(f == forme and la == label for f, la, _, _ in elements)
                ],
            )
        )

    frequents = Counter(
        texte for _, _, longueur, texte in elements if 1 <= longueur <= 20
    ).most_common(20)
    print(
        _tableau(
            "=== LES 20 TEXTES COURTS LES PLUS FREQUENTS (1 a 20 caracteres, texte exact) ===",
            ["rang", "occurrences", "long.", "texte"],
            [
                [str(rang), str(nombre), str(len(texte)), repr(texte)]
                for rang, (texte, nombre) in enumerate(frequents, start=1)
            ],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
