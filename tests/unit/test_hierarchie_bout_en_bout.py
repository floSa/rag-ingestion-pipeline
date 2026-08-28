"""La hierarchie des titres, depuis les items Docling jusqu'au graphe.

Pourquoi ce fichier existe. ``test_elements.py`` verifie deja que
``DocumentAccumulator`` imbrique correctement les titres — mais il lui INJECTE
``heading_rank`` a la main. Il reste donc vert si ``flat_rank`` et
``pdf_heading_rank`` rendent toujours ``None`` : dans ce cas tous les titres
recoivent le rang 0, deviennent freres sous le document, et l'on retombe
exactement sur le graphe plat mesure en production — 901 ``SectionHeader``
enfants du ``Document``, 0 enfant d'un autre ``SectionHeader``.

Autrement dit, le test existant est vert des deux cotes du defaut.

Ceux-ci partent donc d'items tels que Docling les rend, traversent le calcul du
rang, et n'assertent qu'a l'arrivee : la forme de l'arbre. Ils rougissent des
que le rang cesse de remonter, quelle qu'en soit la cause.
"""

from __future__ import annotations

from typing import Any

from src.docling_service.elements import ROOT_REFERENCE, DocumentAccumulator, document_identity
from src.docling_service.ranking import flat_rank, pdf_heading_rank

# ─── Doublures fideles a ce que Docling expose ───────────────────────────────


class Ref:
    """Reference Docling vers un item parent (``#/texts/3``)."""

    def __init__(self, cref: str, cible: Any = None) -> None:
        self.cref = cref
        self._cible = cible

    def resolve(self, _document: Any) -> Any:
        return self._cible


class ItemHtml:
    """Item Docling issu d'une capture HTML : le parent est declare."""

    def __init__(self, label: str, text: str, parent: Any = None) -> None:
        self.label = label
        self.text = text
        # Docling remonte jusqu'a #/body, qui clot la chaine.
        self.parent = parent if parent is not None else Ref("#/body")


class ItemMarkdown:
    """Item Docling issu d'un Markdown : ``level`` est renseigne."""

    def __init__(self, label: str, text: str, level: int | None = None) -> None:
        self.label = label
        self.text = text
        if level is not None:
            self.level = level


def enchaine(item: ItemHtml, parent: ItemHtml) -> ItemHtml:
    """Rattache un item a son parent, comme le fait Docling sur du HTML."""
    item.parent = Ref("#/texts/0", parent)
    return item


def profondeurs(elements: list[dict[str, Any]]) -> list[int]:
    return [int(e["depth"]) for e in elements if e["label"] in ("title", "section_header")]


def ingere(items: list[Any], rangs: list[int | None]) -> list[dict[str, Any]]:
    """Passe des items dans l'accumulateur avec les rangs calcules."""
    acc = DocumentAccumulator(document_identity("htms/Livre/Chapitre.html"))
    paires = zip(items, rangs, strict=True)
    return [acc.add_item(item, None, heading_rank=rang) for item, rang in paires]


# ─── HTML : le parent declare par Docling ────────────────────────────────────


class TestHtmlLeRangRemonteDuParentDocling:
    def test_un_titre_racine_a_le_rang_zero(self):
        assert flat_rank(ItemHtml("title", "Chapitre 3"), None) == 0

    def test_un_sous_titre_a_le_rang_un(self):
        chapitre = ItemHtml("title", "Chapitre 3")
        section = enchaine(ItemHtml("section_header", "3.2"), chapitre)
        assert flat_rank(section, None) == 1

    def test_un_sous_sous_titre_a_le_rang_deux(self):
        chapitre = ItemHtml("title", "Chapitre 3")
        section = enchaine(ItemHtml("section_header", "3.2"), chapitre)
        sous = enchaine(ItemHtml("section_header", "3.2.1"), section)
        assert flat_rank(sous, None) == 2

    def test_un_paragraphe_n_a_pas_de_rang(self):
        assert flat_rank(ItemHtml("text", "Du texte."), None) is None

    def test_l_arbre_produit_a_trois_niveaux(self):
        # LE test. Sur le graphe de production : 0 chemin de longueur 3.
        chapitre = ItemHtml("title", "Chapitre 3")
        section = enchaine(ItemHtml("section_header", "3.2"), chapitre)
        sous = enchaine(ItemHtml("section_header", "3.2.1"), section)
        corps = ItemHtml("text", "Le contenu.")
        items = [chapitre, section, sous, corps]

        elements = ingere(items, [flat_rank(i, None) for i in items])
        assert profondeurs(elements) == [0, 1, 2]

    def test_chaque_titre_a_pour_parent_le_titre_qui_le_domine(self):
        chapitre = ItemHtml("title", "Chapitre 3")
        section = enchaine(ItemHtml("section_header", "3.2"), chapitre)
        sous = enchaine(ItemHtml("section_header", "3.2.1"), section)
        items = [chapitre, section, sous]

        e = ingere(items, [flat_rank(i, None) for i in items])
        assert e[0]["reference_id"] == ROOT_REFERENCE
        assert e[1]["reference_id"] == e[0]["id"]
        assert e[2]["reference_id"] == e[1]["id"]

    def test_un_frere_referme_le_niveau(self):
        chap1 = ItemHtml("title", "Chapitre 1")
        sec = enchaine(ItemHtml("section_header", "1.1"), chap1)
        chap2 = ItemHtml("title", "Chapitre 2")
        items = [chap1, sec, chap2]

        e = ingere(items, [flat_rank(i, None) for i in items])
        assert e[2]["reference_id"] == ROOT_REFERENCE
        assert profondeurs(e) == [0, 1, 0]


# ─── Markdown : l'attribut level ─────────────────────────────────────────────


class TestMarkdownLeRangVientDuNiveau:
    def test_le_niveau_devient_le_rang(self):
        assert flat_rank(ItemMarkdown("section_header", "##", level=1), None) == 1
        assert flat_rank(ItemMarkdown("section_header", "###", level=2), None) == 2

    def test_l_arbre_markdown_s_imbrique(self):
        items = [
            ItemMarkdown("title", "Titre", level=0),
            ItemMarkdown("section_header", "Section", level=1),
            ItemMarkdown("section_header", "Sous-section", level=2),
        ]
        e = ingere(items, [flat_rank(i, None) for i in items])
        assert profondeurs(e) == [0, 1, 2]

    def test_sans_aucun_signal_les_titres_restent_freres(self):
        # Comportement documente et volontaire : jamais de hierarchie inventee.
        items = [
            ItemMarkdown("title", "A"),
            ItemMarkdown("title", "B"),
        ]
        rangs = [flat_rank(i, None) for i in items]
        assert rangs == [None, None]
        e = ingere(items, rangs)
        assert profondeurs(e) == [0, 0]


# ─── PDF : la taille de police ───────────────────────────────────────────────

# Un ouvrage compose en 20/16/13 points, corps a 10.
RANGS_TAILLES = {20.0: 0, 16.0: 1, 13.0: 2}
CORPS = 10.0
BOITE = {"l": 50.0, "t": 700.0, "r": 500.0, "b": 680.0}


class TestPdfLeRangVientDeLaTaille:
    def test_la_plus_grande_taille_est_le_rang_zero(self):
        assert pdf_heading_rank("title", BOITE, 20.0, CORPS, RANGS_TAILLES, []) == 0

    def test_les_tailles_suivantes_descendent_les_niveaux(self):
        assert pdf_heading_rank("section_header", BOITE, 16.0, CORPS, RANGS_TAILLES, []) == 1
        assert pdf_heading_rank("section_header", BOITE, 13.0, CORPS, RANGS_TAILLES, []) == 2

    def test_l_arbre_pdf_a_trois_niveaux(self):
        tailles = [20.0, 16.0, 13.0]
        items = [ItemHtml("section_header", f"T{i}") for i in range(3)]
        rangs = [
            pdf_heading_rank("section_header", BOITE, t, CORPS, RANGS_TAILLES, []) for t in tailles
        ]
        assert profondeurs(ingere(items, rangs)) == [0, 1, 2]

    def test_un_titre_pas_plus_grand_que_le_corps_ne_remet_pas_l_arbre_a_zero(self):
        # « Then: » — faux titre detecte en pleine page. Lui donner le rang 0
        # en ferait un chapitre et refermerait tous les niveaux ouverts.
        rang = pdf_heading_rank("section_header", BOITE, CORPS, CORPS, RANGS_TAILLES, [])
        assert rang == max(RANGS_TAILLES.values()) + 1

    def test_un_titre_dans_une_figure_ne_remet_pas_l_arbre_a_zero(self):
        figure = [(0.0, 600.0, 600.0, 750.0)]
        rang = pdf_heading_rank("section_header", BOITE, 20.0, CORPS, RANGS_TAILLES, figure)
        assert rang == max(RANGS_TAILLES.values()) + 1

    def test_un_faux_titre_ne_referme_pas_les_niveaux_ouverts(self):
        # L'arbre attendu : Chapitre > Section > (faux titre). Le faux titre
        # descend, il ne remonte pas.
        items = [ItemHtml("section_header", t) for t in ("Chapitre", "Section", "Then:")]
        rangs = [
            pdf_heading_rank("section_header", BOITE, 20.0, CORPS, RANGS_TAILLES, []),
            pdf_heading_rank("section_header", BOITE, 16.0, CORPS, RANGS_TAILLES, []),
            pdf_heading_rank("section_header", BOITE, CORPS, CORPS, RANGS_TAILLES, []),
        ]
        e = ingere(items, rangs)
        assert e[2]["reference_id"] == e[1]["id"]
        assert profondeurs(e) == [0, 1, 2]

    def test_un_document_d_une_seule_taille_reste_plat(self):
        # Aucune hierarchie inventee : sans classement, pas de niveaux.
        assert pdf_heading_rank("section_header", BOITE, 12.0, CORPS, {}, []) is None

    def test_un_paragraphe_n_a_pas_de_rang(self):
        assert pdf_heading_rank("text", BOITE, 20.0, CORPS, RANGS_TAILLES, []) is None

    def test_sans_boite_le_titre_est_inclassable(self):
        rang = pdf_heading_rank("section_header", None, 20.0, CORPS, RANGS_TAILLES, [])
        assert rang == max(RANGS_TAILLES.values()) + 1


# ─── La regression qu'on pretend garder ──────────────────────────────────────


class TestLeGrapheNeDoitPlusEtrePlat:
    """Ce que ces tests protegent, dit en une assertion.

    Sur le graphe de production : 901 ``SectionHeader`` enfants du
    ``Document``, 0 enfant d'un autre ``SectionHeader``, 0 chemin de longueur
    3. Si le calcul du rang cesse de remonter — pour n'importe quelle raison —
    on y revient, et c'est ici que ca se voit.
    """

    def test_un_document_a_trois_niveaux_ne_produit_pas_que_des_freres(self):
        chapitre = ItemHtml("title", "Chapitre 3")
        section = enchaine(ItemHtml("section_header", "3.2"), chapitre)
        sous = enchaine(ItemHtml("section_header", "3.2.1"), section)
        items = [chapitre, section, sous]

        elements = ingere(items, [flat_rank(i, None) for i in items])
        titres = [e for e in elements if e["label"] in ("title", "section_header")]
        sous_le_document = [e for e in titres if e["reference_id"] == ROOT_REFERENCE]
        sous_un_titre = [e for e in titres if e["reference_id"] != ROOT_REFERENCE]

        assert len(sous_le_document) == 1, "tous les titres sont redevenus freres du document"
        assert len(sous_un_titre) == 2
        assert max(profondeurs(elements)) >= 2, "l'arbre est retombe a deux niveaux"
