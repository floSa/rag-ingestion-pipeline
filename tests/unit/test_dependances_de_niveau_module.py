"""Quels modules du service d'extraction portent une dependance de niveau module.

`documentation/services/docling.md` enumere ces modules ; ce fichier verifie
que la phrase suit le code. Mesure : quatorze modules sur dix-huit n'ont pas de
dependance de niveau module, quatre en portent une. `embedding.py`,
`nebula.py` et `vectors.py` placent leurs imports lourds dans la fonction qui
en a besoin, ce qui permet a `test_vectors.py` et `test_nebula.py` d'exister
(registre §3.4, §4.4, §4.28.d, §6.8).

Ce fichier ne verifie pas l'importabilite cote hote, qui est une autre
propriete : `bs4`, `minio` et `pydantic_settings` sont dans le venv du depot,
donc trois des quatre modules a dependance s'importent quand meme. C'est
`test_importabilite_cote_hote.py` qui la verifie.

Le balayage est statique, a l'AST : il n'importe rien, donc il ne depend ni du
venv, ni de l'ordre des tests, ni de la presence d'un bouchon.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parents[2]
PAQUET = RACINE_DEPOT / "src" / "docling_service"

# Les noms qui ne sont pas des dependances externes : le paquet lui-meme et la
# racine du depot. Un import relatif est ecarte a la lecture.
NOMS_INTERNES = {"src", "docling_service"}

# Ce que la mesure rend, et ce que `services/docling.md` annonce. L'egalite est
# voulue : un module de plus ou de moins ici oblige a relire la phrase du
# document.
DEPENDANCES_ATTENDUES = {
    "extraction.py": {"bs4"},
    "images.py": {"minio"},
    "main.py": {"fastapi"},
    "settings.py": {"pydantic_settings"},
}

# Modules dont les imports lourds sont differes dans une fonction.
DEVERROUILLES = ("embedding.py", "nebula.py", "vectors.py")


def _dependances_de_niveau_module(chemin: Path) -> set[str]:
    """Rend les paquets externes importes par le corps du module.

    Args:
        chemin: Fichier Python a lire.

    Returns:
        Les paquets racines, hors bibliotheque standard, hors depot, hors
        imports relatifs. Un import ecrit dans une fonction n'y est pas.
    """
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    racines: set[str] = set()
    for noeud in arbre.body:  # le corps du module, et rien d'imbrique
        if isinstance(noeud, ast.Import):
            racines.update(alias.name.split(".")[0] for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and not noeud.level:
            racines.add((noeud.module or "").split(".")[0])
    return {
        racine
        for racine in racines
        if racine and racine not in sys.stdlib_module_names and racine not in NOMS_INTERNES
    }


def _balayer() -> dict[str, set[str]]:
    """Rend, par module du paquet, ses dependances de niveau module."""
    return {
        chemin.name: _dependances_de_niveau_module(chemin)
        for chemin in sorted(PAQUET.glob("*.py"))
        if chemin.name != "__init__.py"  # marqueur de paquet, vide
    }


class TestLeCompteDesDependancesDeNiveauModule:
    """Le dénombrement de `services/docling.md`, converti en garde."""

    def test_le_balayage_a_bien_lu_le_paquet(self) -> None:
        """Contre-epreuve, placee en premier.

        Un balayage qui ne trouverait aucun fichier rendrait « 0 dependance », et
        les tests suivants passeraient sans rien verifier. La borne du nombre de
        modules est inferieure (ajouter un module ne fait pas echouer ce test),
        mais la detection est verifiee par une egalite sur un cas connu : sans
        elle, un `_dependances_de_niveau_module` qui rendrait toujours
        l'ensemble vide passerait.
        """
        balayage = _balayer()

        assert len(balayage) >= 18, f"le balayage n'a lu que {len(balayage)} modules"
        assert balayage["extraction.py"] == {"bs4"}, (
            "le balayage ne detecte plus les dependances : `extraction.py` en "
            f"porte une, il rend {balayage['extraction.py']}"
        )
        assert balayage["ngql.py"] == set(), (
            "le balayage rend une dependance sur un module qui n'en a pas : il "
            f"compte autre chose que ce qu'il annonce ({balayage['ngql.py']})"
        )

    def test_seuls_quatre_modules_portent_une_dependance(self) -> None:
        """Une divergence avec la phrase de `services/docling.md` fait echouer ce test."""
        porteurs = {nom: deps for nom, deps in _balayer().items() if deps}

        assert porteurs == DEPENDANCES_ATTENDUES, (
            "les modules a dependance de niveau module ne sont plus ceux "
            f"annonces : {porteurs}. Relis la phrase de "
            "`documentation/services/docling.md` — elle donne un COMPTE et une "
            "LISTE, et le chantier a deja perdu ce compte une fois"
        )

    def test_les_trois_modules_deverrouilles_le_sont_encore(self) -> None:
        """`embedding.py`, `nebula.py` et `vectors.py` importent localement leurs deps lourdes.

        Remettre l'un d'eux au niveau du module rendrait son fichier de tests
        impossible a collecter, et la phrase de `docling.md` fausse.
        """
        balayage = _balayer()

        reverrouilles = {nom: balayage[nom] for nom in DEVERROUILLES if balayage[nom]}
        assert reverrouilles == {}, (
            f"un module deverrouille a repris un import de niveau module : "
            f"{reverrouilles}. C'etait la cause mecanique de six angles morts "
            "du chantier — ce qu'un test n'importe pas, il ne teste pas"
        )
