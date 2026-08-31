"""Le graphe est-il plat ? La reponse depend du CHAPITRE, et ce test le prouve.

C'est le test que l'audit du lot 1 reclame, et le seul qui distingue « Docling
imbrique » de « CE chapitre-la imbrique ». Le chantier a failli supprimer un lot
entier sur un antecedent jamais mesure : le constat 3.2 raisonnait juste sur
« si Docling n'imbrique pas », et personne n'avait mesure le SI. Un raisonnement
juste sur un antecedent faux se relit comme une preuve.

Il couvre les DEUX cas, et c'est le point :

- un chapitre imbrique rend une distribution de rangs NON degeneree ;
- le chapitre `Practical MLflow .../10. Unifying GenAI Systems with MLflow.html`
  rend 8 titres TOUS de rang 0 — c'est le seul chapitre retenu du corpus sans
  aucune balise <h2> (`mesure` par l'audit du lot 1 sur les 22), et son graphe
  est REELLEMENT plat. Un test qui ne couvrirait que le premier lirait cette
  platitude-la comme un defaut.

CE QU'IL PROUVE ET CE QU'IL NE PROUVE PAS. Il rejoue le code de rang sur des
arbres Docling captures depuis les captures reelles et versionnees, et non sur
un arbre fabrique a la main — le reproche exact fait a
``test_hierarchie_bout_en_bout.py``, qui pose lui-meme les parents qu'il
verifie. Ce qu'il ne voit pas est un changement de comportement de DOCLING :
c'est ``scripts/capturer-larbre-docling.py --verifier`` qui le voit, et docling
est epingle a 2.117.0.

La conversion ne peut pas entrer dans ``make test`` : `mesure` le 31 aout 2026,
``uv pip install docling==2.117.0`` ajoute 85 paquets — torch et quinze paquets
NVIDIA CUDA — et retrograde ``websockets``, sur une chaine qui tourne sur
processeur et dont le pyproject dit que les deps lourdes vivent dans l'image.

Le NETTOYAGE, lui, tourne pour de vrai : il ne demande que trafilatura et
readability, qui sont la. C'est ce qui relie la capture au corpus versionne — si
le HTML change, ou si le nettoyage change, les empreintes divergent et le test
rougit en demandant une nouvelle capture.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml
from bs4 import BeautifulSoup

from src.docling_service import ranking
from src.pipeline.cleaning import clean_html
from src.pipeline.sources import CleaningOptions

RACINE = Path(__file__).resolve().parents[2]
FIXTURE = RACINE / "tests" / "fixtures" / "arbres_docling.yaml"

# Les deux cas attendus. Le test ECHOUE si la capture n'en porte pas exactement
# ces deux-la : une fixture amputee rendrait une boucle vide, donc un vert qui
# ne prouve rien. Deux developpeurs de ce chantier se sont fabrique un faux vert
# en bouclant sur une liste non protegee — les noms de fichiers du corpus
# portent des espaces et de la ponctuation.
CAS_ATTENDUS = frozenset({"imbrique", "plat"})


class _Ref:
    """Reference Docling : porte un ``cref`` et sait le resoudre."""

    def __init__(self, cref: str, items: dict[str, Any]) -> None:
        self.cref = cref
        self._items = items

    def resolve(self, _document: Any) -> Any:
        return self._items.get(self.cref)


class _Item:
    """Item Docling reduit a ce que le code de rang lit : label et parent."""

    def __init__(self, label: str, parent_cref: str, items: dict[str, Any]) -> None:
        self.label = label
        self.parent = _Ref(parent_cref, items) if parent_cref else None


@pytest.fixture(scope="module")
def capture() -> dict[str, Any]:
    """L'arbre capture par ``scripts/capturer-larbre-docling.py``."""
    assert FIXTURE.is_file(), f"{FIXTURE} absent : rejouer le script de capture."
    contenu: dict[str, Any] = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert set(contenu) == CAS_ATTENDUS, (
        f"la capture porte {sorted(contenu)} au lieu de {sorted(CAS_ATTENDUS)} : "
        "un cas manquant rendrait ce fichier vert sans rien verifier"
    )
    return contenu


def _rangs(cas: dict[str, Any]) -> list[int]:
    """Rejoue le code de rang de production sur l'arbre capture."""
    items: dict[str, Any] = {}
    for reference, info in cas["items"].items():
        items[reference] = _Item(info["label"], info["parent"], items)
    rangs = (ranking.flat_rank(items[reference], None) for reference in cas["ordre"])
    return [rang for rang in rangs if rang is not None]


def _fichier(cas: dict[str, Any]) -> Path:
    return RACINE / cas["source"]


class TestLaCaptureDecritBienCeChapitreLa:
    """Un test qui choisit son cas doit prouver qu'il l'a atteint."""

    def test_the_captured_file_exists_under_its_exact_name(self, capture):
        for nom in CAS_ATTENDUS:
            chemin = _fichier(capture[nom])
            assert chemin.is_file(), f"{nom} : {chemin} introuvable"

    def test_the_raw_html_still_hashes_to_what_was_captured(self, capture):
        """Le lien avec le corpus VERSIONNE. Si le HTML bouge, la capture ment."""
        for nom in CAS_ATTENDUS:
            cas = capture[nom]
            brut = _fichier(cas).read_bytes()
            assert hashlib.sha256(brut).hexdigest() == cas["sha256_brut"], (
                f"{nom} : le HTML source a change depuis la capture"
            )

    def test_the_real_cleaning_still_produces_what_was_captured(self, capture):
        """Le nettoyage tourne pour de vrai, et son resultat est scelle.

        C'est ce maillon qui interdit a la capture de decrire autre chose que ce
        que le pipeline convertit reellement.
        """
        for nom in CAS_ATTENDUS:
            cas = capture[nom]
            brut = _fichier(cas).read_text(encoding="utf-8", errors="ignore")
            nettoye, _ = clean_html(brut, CleaningOptions())
            assert hashlib.sha256(nettoye.encode()).hexdigest() == cas["sha256_nettoye"], (
                f"{nom} : le nettoyage ne rend plus ce qui a ete converti ; "
                "rejouer scripts/capturer-larbre-docling.py"
            )

    def test_the_two_cases_are_different_chapters(self, capture):
        sources = {capture[nom]["source"] for nom in CAS_ATTENDUS}
        assert len(sources) == 2


class TestUnChapitreImbriqueNEstPasPlat:
    def test_the_rank_distribution_is_not_degenerate(self, capture):
        """Un graphe plat rendrait un seul rang. Celui-ci en rend quatre."""
        distribution = Counter(_rangs(capture["imbrique"]))
        assert len(distribution) > 1, f"distribution degeneree : {dict(distribution)}"
        assert dict(sorted(distribution.items())) == {0: 5, 1: 10, 2: 21, 3: 3}

    def test_most_headings_are_nested_under_another_heading(self, capture):
        rangs = _rangs(capture["imbrique"])
        imbriques = sum(1 for rang in rangs if rang > 0)
        assert imbriques == 34
        assert imbriques > len(rangs) - imbriques

    def test_the_root_headings_match_the_h1_tags_of_the_source(self, capture):
        """L'invariant mesure par le pilote sur 22 chapitres sur 22.

        Il relie la sortie du code de rang a une propriete du HTML d'entree que
        personne ne calcule : le nombre de titres de rang 0 egale le nombre de
        balises <h1>. Aucun des deux cotes ne peut deriver sans l'autre.
        """
        cas = capture["imbrique"]
        brut = _fichier(cas).read_text(encoding="utf-8", errors="ignore")
        h1 = len(BeautifulSoup(brut, "lxml").find_all("h1"))
        assert Counter(_rangs(cas))[0] == h1 == 5


class TestLeChapitrePlatEstReellementPlat:
    """Et ce n'est pas un defaut : c'est ce que la capture contient."""

    def test_every_heading_sits_at_rank_zero(self, capture):
        rangs = _rangs(capture["plat"])
        assert len(rangs) == 8
        assert set(rangs) == {0}

    def test_the_flatness_comes_from_the_source_which_has_no_h2(self, capture):
        """La cause est dans la capture HTML, pas dans le code de rang.

        Sans cette assertion, le test ci-dessus se lirait comme la preuve que le
        code echoue a imbriquer — c'est l'inverse : il n'y a rien a imbriquer.
        """
        brut = _fichier(capture["plat"]).read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(brut, "lxml")
        assert len(soup.find_all("h2")) == 0
        assert len(soup.find_all("h1")) == 8
        assert len(_rangs(capture["plat"])) == len(soup.find_all("h1"))

    def test_the_two_chapters_do_not_behave_the_same(self, capture):
        """La comparaison EST le resultat : « Docling imbrique » serait faux ici,
        « Docling n'imbrique pas » serait faux la-bas."""
        assert len(set(_rangs(capture["imbrique"]))) == 4
        assert len(set(_rangs(capture["plat"]))) == 1
