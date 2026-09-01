"""Le graphe est-il plat ? La reponse depend du CHAPITRE, et ce test le prouve.

C'est le test que l'audit du lot 1 reclame, et le seul qui distingue « Docling
imbrique » de « CE chapitre-la imbrique ». Le chantier a failli supprimer un lot
entier sur un antecedent jamais mesure : le constat 3.2 raisonnait juste sur
« si Docling n'imbrique pas », et personne n'avait mesure le SI. Un raisonnement
juste sur un antecedent faux se relit comme une preuve.

Il couvre les DEUX cas, et c'est le point :

- un chapitre imbrique rend une distribution de rangs NON degeneree ;
- le chapitre `Practical MLflow .../10. Unifying GenAI Systems with MLflow.html`
  rend 8 titres TOUS de rang 0, et son graphe est REELLEMENT plat. Un test qui ne
  couvrirait que le premier lirait cette platitude-la comme un defaut.

ET LA CAUSE DE CETTE PLATITUDE N'EST PAS CELLE QUI AVAIT ETE ECRITE. Ce fichier
portait un test nomme `test_the_flatness_comes_from_the_source_which_has_no_h2`,
recopie du registre §3.2, qui affirmait que ce chapitre etait « le seul chapitre
retenu sans aucune balise <h2> ». **C'est faux : ils sont trois** (`mesure` sur
les 22 chapitres retenus). Les deux `Preface.html` n'ont aucun <h2> non plus et
S'IMBRIQUENT quand meme — `{0: 9, 1: 4}` et `{0: 8, 1: 4}` sur le graphe vivant.
« Sans aucun <h2> » n'est donc PAS la propriete discriminante, et ce test
assertait une causalite que la mesure dement.

Ce qui discrimine, et ce que ce fichier asserte desormais : AUCUN TITRE N'EST
RENDU SOUS LE NIVEAU DE TETE — titres rendus = <h1>. La cause mesuree, sur ce
chapitre-ci, est que sa seule balise de titre sous <h1> est la legende de sa
figure, que Docling classe `caption` et non titre.

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
from src.docling_service.matter import is_front_back_matter
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


def _titres(cas: dict[str, Any]) -> list[str]:
    """References des items que Docling a etiquetes comme des titres."""
    return [r for r in cas["ordre"] if cas["items"][r]["label"] in ranking.HEADING_LABELS]


def _rangs(cas: dict[str, Any]) -> list[int]:
    """Rejoue le code de rang de production sur l'arbre capture.

    LE FILTRE DES ``None`` EST UN SILENCE, ET IL A CACHE UN DEFAUT REEL. Cette
    fonction jetait les ``None`` sans jamais dire combien elle en jetait. Or
    ``flat_rank`` rend ``None`` pour deux raisons qui n'ont rien a voir :

    - l'item n'est pas un titre — c'est le cas nominal, et il y en a des
      milliers ;
    - **l'item EST un titre et aucun signal n'a repondu** — c'est un defaut, et
      il tombait dans le meme filtre.

    C'est exactement ce qui est arrive. La capture omettait les noeuds de groupe,
    deux titres du chapitre imbrique avaient un groupe pour parent direct, leur
    chaine de parents ne se remontait plus, et ils disparaissaient ici. Le test
    assertait **39** titres la ou le graphe reel en porte **41**, et le chiffre
    faux avait ete recopie au registre sous l'etiquette `mesure`.

    D'ou l'assertion ci-dessous : **autant de rangs que de titres**. Elle vaut
    pour tous les appelants a la fois, parce que c'est le silence qui etait
    structurel, pas l'oubli d'un test.

    Args:
        cas: Un des deux cas de la capture.

    Returns:
        Le rang de chaque titre, dans l'ordre de la capture.
    """
    items: dict[str, Any] = {}
    for reference, info in cas["items"].items():
        items[reference] = _Item(info["label"], info["parent"], items)
    rangs = [ranking.flat_rank(items[reference], None) for reference in cas["ordre"]]
    classes = [rang for rang in rangs if rang is not None]
    titres = _titres(cas)
    assert len(classes) == len(titres), (
        f"{len(titres) - len(classes)} titre(s) sur {len(titres)} n'ont recu AUCUN "
        f"rang et seraient jetes en silence. Les references concernees : "
        f"{[r for r in titres if ranking.flat_rank(items[r], None) is None]}. "
        "Cause la plus probable : la capture ne porte pas les noeuds de groupe "
        "qui sont sur leur chaine de parents — rejouer "
        "scripts/capturer-larbre-docling.py, qui passe with_groups=True."
    )
    return classes


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

    def test_no_parent_reference_points_outside_the_capture(self, capture):
        """LA CAPTURE EST UN ARBRE COMPLET, ET C'EST CE QUI LUI MANQUAIT.

        ``ranking.docling_parent_rank`` REMONTE la chaine des parents. Une
        reference qui pointe un noeud absent de la capture casse la remontee : la
        resolution rend ``None``, la boucle sort, et le titre perd son rang.

        C'est ce qui s'etait produit. ``document.iterate_items()`` ne rend jamais
        les noeuds de groupe, et la capture en omettait **262** — 257 sur le
        chapitre imbrique, 5 sur le plat. Elle portait alors **1 175**
        references de parent pointant dans le vide, dont celles de deux titres.

        Cette assertion est structurelle : elle rougit pour toute famille de
        noeud oubliee, pas seulement pour les groupes. C'est ce qui la rend utile
        au prochain changement de version de Docling.
        """
        for nom in sorted(CAS_ATTENDUS):
            items = capture[nom]["items"]
            perdues = {
                reference: info["parent"]
                for reference, info in items.items()
                if info["parent"] and info["parent"] != "#/body" and info["parent"] not in items
            }
            assert not perdues, (
                f"{nom} : {len(perdues)} reference(s) de parent pointent un noeud "
                f"absent de la capture (ex. {sorted(perdues.items())[:3]}). "
                "Rejouer scripts/capturer-larbre-docling.py."
            )

    def test_the_capture_carries_the_anonymous_containers(self, capture):
        """Les groupes sont dans la capture, et ils portent bien un label non-titre.

        Deux proprietes distinctes, et il faut les deux :

        - les noeuds de groupe SONT la — sans eux, la mutation « compter les
          conteneurs anonymes comme des titres » n'a rien a mordre, et le test
          bati sur du reel devient aveugle au mecanisme qu'il existe pour
          eprouver. C'etait le defaut central de cette fixture ;
        - **aucun** d'eux ne porte un label de titre. Si Docling se mettait a
          etiqueter un groupe ``section_header``, la remontee le compterait, et
          tous les rangs sous ce groupe augmenteraient d'un.

        `mesure` : 257 groupes sur le chapitre imbrique, 5 sur le plat, et leurs
        labels sont ``inline``, ``list``, ``section`` et ``unspecified``.
        """
        for nom, attendus in (("imbrique", 257), ("plat", 5)):
            items = capture[nom]["items"]
            groupes = {r: i["label"] for r, i in items.items() if r.startswith("#/groups/")}
            assert len(groupes) == attendus, (
                f"{nom} : {len(groupes)} noeuds de groupe au lieu de {attendus}. "
                "La capture doit passer with_groups=True."
            )
            titres_deguises = {r: l for r, l in groupes.items() if l in ranking.HEADING_LABELS}
            assert not titres_deguises, (
                f"{nom} : des conteneurs anonymes portent un label de titre "
                f"({titres_deguises}) : la remontee les compterait comme des niveaux"
            )


class TestUnChapitreImbriqueNEstPasPlat:
    def test_the_rank_distribution_is_not_degenerate(self, capture):
        """Un graphe plat rendrait un seul rang. Celui-ci en rend quatre."""
        distribution = Counter(_rangs(capture["imbrique"]))
        assert len(distribution) > 1, f"distribution degeneree : {dict(distribution)}"
        assert dict(sorted(distribution.items())) == {0: 5, 1: 10, 2: 21, 3: 5}

    def test_most_headings_are_nested_under_another_heading(self, capture):
        rangs = _rangs(capture["imbrique"])
        imbriques = sum(1 for rang in rangs if rang > 0)
        assert imbriques == 36
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

    def test_nothing_survives_below_the_top_level(self, capture):
        """LA PROPRIETE QUI DISCRIMINE, et ce n'est pas « aucun <h2> ».

        Sans cette assertion, `test_every_heading_sits_at_rank_zero` se lirait
        comme la preuve que le code echoue a imbriquer — c'est l'inverse : il n'y
        a rien a imbriquer.

        Ce test s'appelait `test_the_flatness_comes_from_the_source_which_has_no_h2`
        et assertait `len(find_all("h2")) == 0` comme la CAUSE. C'etait une
        causalite fausse : trois des 22 chapitres retenus n'ont aucun <h2>, et
        deux s'imbriquent (voir le test suivant). La propriete partagee par les
        trois ne peut pas expliquer ce qui n'arrive qu'a un seul.

        La propriete qui discrimine se lit des deux cotes a la fois : le nombre de
        titres RENDUS egale le nombre de <h1>, donc rien ne survit sous le niveau
        de tete. Le chapitre imbrique, lui, rend 41 titres pour 5 <h1>.
        """
        brut = _fichier(capture["plat"]).read_text(encoding="utf-8", errors="ignore")
        h1 = len(BeautifulSoup(brut, "lxml").find_all("h1"))
        assert len(_rangs(capture["plat"])) == h1 == 8
        # Et le contraste, dans la meme assertion : c'est lui qui empeche de lire
        # « titres == h1 » comme une propriete de Docling plutot que du chapitre.
        assert len(_rangs(capture["imbrique"])) == 41
        assert Counter(_rangs(capture["imbrique"]))[0] == 5

    def test_the_absence_of_h2_is_shared_by_three_chapters_so_it_explains_nothing(self, capture):
        """LE CONTRE-EXEMPLE QUI TUE LA CAUSALITE FAUSSE, a pleine portee du corpus.

        Le chantier a recopie « le SEUL chapitre retenu sans aucun <h2> » trois
        fois — registre §3.2, mandat §5.1 ter, et le test de ce fichier — sans
        jamais le remesurer. Ce test le mesure, sur les 22 chapitres que le
        capteur retient reellement, et non sur une liste ecrite a la main.

        Il n'asserte PAS que les deux Prefaces s'imbriquent : cela demanderait la
        conversion Docling, qui ne peut pas entrer dans `make test` (voir le
        docstring du module). Cette mesure-la vit au registre §3.2, avec sa
        commande et sa provenance. Ce que ce test etablit suffit a l'argument :
        la propriete « aucun <h2> » est PARTAGEE, donc elle ne discrimine rien.

        Cout `mesure` : +0,86 s, la lecture des balises des 22 chapitres. C'est
        paye volontairement — il convertit une mesure ecrite dans un document,
        recopiee trois fois sans verification, en un garde qui rougit.
        """
        racine = RACINE / "Datas" / "htms"
        retenus = [f for f in sorted(racine.rglob("*.html")) if not is_front_back_matter(f.stem)]
        assert len(retenus) == 22, (
            f"{len(retenus)} chapitres retenus au lieu de 22 : le corpus ou le "
            "capteur a change, et la mesure ci-dessous ne porte plus"
        )
        sans_h2 = [
            f
            for f in retenus
            if not BeautifulSoup(f.read_text(encoding="utf-8", errors="ignore"), "lxml").find_all(
                "h2"
            )
        ]
        assert len(sans_h2) == 3, (
            f"{len(sans_h2)} chapitres retenus sans <h2> au lieu de 3 : {[f.name for f in sans_h2]}"
        )
        plat = _fichier(capture["plat"])
        assert plat in sans_h2, (
            "le chapitre plat doit faire partie des trois : c'est ce qui rend le "
            "contre-exemple pertinent"
        )
        # Les deux autres NE SONT PAS le chapitre plat, et c'est tout l'argument.
        assert len([f for f in sans_h2 if f != plat]) == 2

    def test_the_only_heading_tag_below_h1_is_a_figure_caption(self, capture):
        """LA CAUSE MESUREE, sur ce chapitre-ci et sans generaliser.

        Le chapitre porte une seule balise de titre sous <h1>, un <h6>, et c'est
        la legende de sa figure. Docling la classe `caption` et la rattache a
        l'image : elle ne devient donc jamais un titre. Les deux Prefaces, elles,
        portent quatre <h6> qui sont des libelles d'admonition — Tip, Note,
        Warning, Note — que Docling rend comme des titres.

        Ce test asserte ce qui est MESURE dans la capture : 1 picture, 1 caption,
        8 items a label de titre pour 8 <h1>. Que toute legende de figure en <h6>
        devienne un `caption` serait une generalisation a partir d'un cas, et
        elle n'est pas assertee ici.
        """
        items = capture["plat"]["items"]
        labels = Counter(info["label"] for info in items.values())
        assert labels["caption"] == 1
        assert labels["picture"] == 1
        assert len(_titres(capture["plat"])) == 8

        brut = _fichier(capture["plat"]).read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(brut, "lxml")
        # Une seule balise de titre sous <h1>, et c'est la legende de la figure.
        sous_h1 = [t for n in range(2, 7) for t in soup.find_all(f"h{n}")]
        assert len(sous_h1) == 1
        assert sous_h1[0].get_text(strip=True).startswith("Figure 10-1.")

    def test_the_two_chapters_do_not_behave_the_same(self, capture):
        """La comparaison EST le resultat : « Docling imbrique » serait faux ici,
        « Docling n'imbrique pas » serait faux la-bas."""
        assert len(set(_rangs(capture["imbrique"]))) == 4
        assert len(set(_rangs(capture["plat"]))) == 1
