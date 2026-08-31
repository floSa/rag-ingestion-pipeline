"""Reperage des parties d'un ouvrage qui ne sont pas du contenu.

Un livre ne contient pas que du livre. Couverture, page de copyright, table
des matieres et surtout **index** n'ont aucune valeur pour un RAG : l'index
est une liste de mots suivis de numeros de page, sans une seule phrase. Il
n'apporte rien, mais comme il contient tout le vocabulaire de l'ouvrage, il
ressort sur presque toutes les questions. C'est le pire profil possible :
tres attirant, totalement creux.

Deux reperages, selon ce que le document fournit :

- **Par le nom** (``is_front_back_matter``) : pour un livre decoupe en
  fichiers, le nom du fichier dit la partie (``Index.html``). Le pipeline
  s'en sert pour ne meme pas creer de partition.
- **Par les signets du PDF** (``pages_to_skip``) : un PDF porte sa propre
  table des matieres, avec les pages de debut. Point capital, ces pages sont
  les pages **physiques** du fichier, resolues par le lecteur PDF : elles ne
  souffrent pas du decalage classique entre la numerotation imprimee dans le
  livre et le rang reel de la page. Lire les numeros imprimes dans la table
  des matieres exposerait a un decalage d'une a deux pages ; lire les signets
  non.

Filet de sortie quand un PDF n'a pas de signets, ou que son index n'y figure
pas : ``detect_index_pages`` reconnait un index a sa **forme**, sans jamais
lire son sens — des lignes courtes qui se terminent par des numeros de page.

Module volontairement sans dependance : il est importe des deux cotes, par le
service d'extraction comme par l'orchestrateur.
"""

from __future__ import annotations

import re
import unicodedata

# Parties ecartees par defaut. La regle : on ne garde pas ce qui n'a pas de
# phrases. Preface, glossaire et annexes n'y figurent pas volontairement — ce
# sont de la prose, et un glossaire repond meme tres bien aux questions
# « c'est quoi X ? ».
FRONT_BACK_MATTER_TITLES: frozenset[str] = frozenset(
    {
        "index",
        "indexes",
        "table of contents",
        "contents",
        "toc",
        "table des matieres",
        "sommaire",
        "cover",
        "front cover",
        "back cover",
        "couverture",
        "title page",
        "half title",
        "page de titre",
        "copyright",
        "copyright page",
        "credits",
        "colophon",
        "dedication",
        "dedicace",
        "about the author",
        "about the authors",
        "about the reviewer",
        "about the reviewers",
        "about the technical reviewer",
        "about the technical reviewers",
        "customer feedback",
        "why subscribe",
    }
)

# Numerotation de tete frequente dans les noms de chapitres decoupes en
# fichiers : « 0. Preface », « 13 Appendix », « A. Key Terms », « iv) Notes ».
# Applique sur le titre deja passe en minuscules. La ponctuation est exigee
# pour les lettres, sans quoi « A Practical Guide » perdrait son premier mot.
_LEADING_NUMBER = re.compile(r"^(?:\d+[.)\-–—]?|[ivxlcdm]+[.)\-–—]|[a-z][.)\-–—])\s+")

# Ponctuation et decorations a neutraliser avant comparaison.
_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

# Ligne d'index : un terme puis un ou plusieurs numeros de page, eventuellement
# separes par des virgules ou des tirets (« Analysis of Variance  40, 204 »).
_INDEX_LINE = re.compile(r"\d+\s*(?:[,\-–]\s*\d+\s*)*$")

# Au-dela, une section « a ecarter » est forcement une erreur de lecture des
# signets (un signet parent qui couvre tout l'ouvrage) : on l'ignore.
MAX_SKIP_RATIO = 0.35

# Un index vit dans la derniere partie du livre. Restreindre la detection par
# la forme a cette zone evite de confondre un index avec un tableau de
# resultats numeriques au milieu d'un chapitre.
INDEX_SEARCH_TAIL_RATIO = 0.25

# Seuils de la detection par la forme.
INDEX_MIN_LINES = 15
INDEX_MIN_NUMBERED_RATIO = 0.55
INDEX_MAX_LINE_LENGTH = 60


def normalize_title(title: str) -> str:
    """Ramene un titre a une forme comparable : sans accents, ni numero, ni ponctuation.

    ``« 13. Appendix »``, ``« Appendix »`` et ``« APPENDIX! »`` donnent tous
    ``appendix``.

    Args:
        title: Titre brut, tel qu'il vient d'un signet ou d'un nom de fichier.

    Returns:
        Le titre normalise, chaine vide si rien n'en reste.
    """
    sans_accent = unicodedata.normalize("NFKD", title)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    minuscule = sans_accent.lower().strip()
    sans_numero = _LEADING_NUMBER.sub("", minuscule)
    # Si le titre n'etait qu'un numero, la suppression laisse le champ vide :
    # on repart du titre complet plutot que de renvoyer une chaine vide.
    candidat = sans_numero or minuscule
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", candidat)).strip()


def is_front_back_matter(title: str, titles: frozenset[str] | None = None) -> bool:
    """Indique si un titre designe une partie sans contenu d'ouvrage.

    Args:
        title: Nom de fichier sans extension, ou libelle de signet.
        titles: Liste de titres a ecarter. Par defaut ``FRONT_BACK_MATTER_TITLES``.

    Returns:
        ``True`` si la partie est a ecarter.
    """
    return normalize_title(title) in (titles or FRONT_BACK_MATTER_TITLES)


def _section_spans(toc: list[tuple[int, str, int]], total_pages: int) -> list[tuple[str, int, int]]:
    """Convertit les signets en sections (titre, premiere page, derniere page).

    Une section court jusqu'au signet suivant de niveau equivalent ou superieur.

    Args:
        toc: Signets ``(niveau, titre, page)``, pages physiques 1-indexees.
        total_pages: Nombre de pages du document.

    Returns:
        Une entree par signet, bornes incluses.
    """
    spans: list[tuple[str, int, int]] = []

    for position, (level, title, page) in enumerate(toc):
        if not 1 <= page <= total_pages:
            continue
        fin = total_pages
        for suivant_level, _, suivant_page in toc[position + 1 :]:
            if suivant_level <= level and suivant_page > page:
                fin = suivant_page - 1
                break
        spans.append((title, page, max(page, fin)))

    return spans


def pages_to_skip(
    toc: list[tuple[int, str, int]],
    total_pages: int,
    titles: frozenset[str] | None = None,
) -> set[int]:
    """Pages a ne pas convertir, deduites des signets du PDF.

    Les pages des signets reconnus comme hors contenu sont ecartees. Une
    section qui depasserait ``MAX_SKIP_RATIO`` du document est ignoree : c'est
    le signe d'un signet parent qui englobe tout l'ouvrage, pas d'un index.

    Args:
        toc: Signets ``(niveau, titre, page)`` tels que ``fitz.Document.get_toc``
            les fournit — pages physiques, sans decalage.
        total_pages: Nombre de pages du document.
        titles: Liste de titres a ecarter. Par defaut ``FRONT_BACK_MATTER_TITLES``.

    Returns:
        Les numeros de page (1-indexes) a sauter.
    """
    if total_pages <= 0:
        return set()

    plafond = max(1, int(total_pages * MAX_SKIP_RATIO))
    ignorees: set[int] = set()

    for title, debut, fin in _section_spans(toc, total_pages):
        if not is_front_back_matter(title, titles):
            continue
        if fin - debut + 1 > plafond:
            continue
        ignorees.update(range(debut, fin + 1))

    return ignorees


def looks_like_index_page(text: str) -> bool:
    """Reconnait une page d'index a sa forme, sans en lire le sens.

    Une page d'index n'a pas de phrases : des lignes courtes, terminees par un
    ou plusieurs numeros de page.

    Args:
        text: Texte brut de la page.

    Returns:
        ``True`` si la page presente le profil d'un index.
    """
    lignes = [ligne.strip() for ligne in text.splitlines() if ligne.strip()]
    if len(lignes) < INDEX_MIN_LINES:
        return False

    numerotees = sum(1 for ligne in lignes if _INDEX_LINE.search(ligne))
    if numerotees / len(lignes) < INDEX_MIN_NUMBERED_RATIO:
        return False

    longueur_moyenne = sum(len(ligne) for ligne in lignes) / len(lignes)
    return longueur_moyenne <= INDEX_MAX_LINE_LENGTH


def detect_index_pages(page_texts: dict[int, str], total_pages: int) -> set[int]:
    """Repere l'index d'un PDF sans signets, par la forme de ses dernieres pages.

    On remonte depuis la fin tant que les pages ont le profil d'un index, et on
    s'arrete au premier texte normal. Seule la queue du document est examinee :
    un tableau de resultats en plein chapitre ne doit pas etre pris pour un index.

    Args:
        page_texts: Texte des pages de la zone examinee, indexe par numero de page.
        total_pages: Nombre de pages du document.

    Returns:
        Les numeros de page (1-indexes) du bloc d'index terminal.
    """
    if total_pages <= 0:
        return set()

    limite = total_pages - max(1, int(total_pages * INDEX_SEARCH_TAIL_RATIO))
    trouvees: set[int] = set()

    for page in range(total_pages, limite, -1):
        texte = page_texts.get(page)
        if texte is None or not looks_like_index_page(texte):
            break
        trouvees.add(page)

    return trouvees


# En dessous, un PDF n'a pas de couche texte exploitable : c'est un scan, et
# le convertir produirait un document vide sans que rien ne le signale. Une
# page de livre normale porte 1 500 a 3 000 caracteres, une page scannee sans
# OCR en rend zero. Le seuil est volontairement tres bas : mieux vaut ingerer
# un atlas presque muet que rejeter un ouvrage legitime.
MIN_CHARS_PER_PAGE = 50

# Nombre de pages sondees pour juger de la couche texte.
TEXT_LAYER_SAMPLE = 20


def sample_pages(ranges: list[tuple[int, int]], sample_size: int = TEXT_LAYER_SAMPLE) -> list[int]:
    """Choisit des pages reparties sur tout le document.

    Repartir plutot que prendre les premieres : un livre commence souvent par
    des pages de garde presque vides, qui feraient conclure a tort au scan.

    Args:
        ranges: Plages de pages conservees.
        sample_size: Nombre de pages souhaitees.

    Returns:
        Les numeros de page a sonder, dans l'ordre.
    """
    pages = [page for debut, fin in ranges for page in range(debut, fin + 1)]
    if len(pages) <= sample_size:
        return pages
    pas = len(pages) / sample_size
    return [pages[int(rang * pas)] for rang in range(sample_size)]


def has_text_layer(texts: list[str], min_chars_per_page: int = MIN_CHARS_PER_PAGE) -> bool:
    """Indique si les pages sondees portent du texte selectionnable.

    Args:
        texts: Textes bruts des pages sondees.
        min_chars_per_page: Moyenne minimale de caracteres par page.

    Returns:
        ``False`` si le document est vraisemblablement un scan sans OCR.
    """
    if not texts:
        return True
    total = sum(len(texte.strip()) for texte in texts)
    return total / len(texts) >= min_chars_per_page


def kept_ranges(total_pages: int, skipped: set[int]) -> list[tuple[int, int]]:
    """Regroupe les pages conservees en plages contigues.

    Args:
        total_pages: Nombre de pages du document.
        skipped: Pages a sauter.

    Returns:
        Les plages ``(premiere, derniere)`` a convertir, dans l'ordre.
    """
    plages: list[tuple[int, int]] = []
    debut: int | None = None

    for page in range(1, total_pages + 1):
        if page in skipped:
            if debut is not None:
                plages.append((debut, page - 1))
                debut = None
        elif debut is None:
            debut = page

    if debut is not None:
        plages.append((debut, total_pages))

    return plages
