"""Detection de la langue d'un document, sans dependance externe.

Pourquoi c'est utile ici : le modele d'embedding actuel n'est entraine que
sur de l'anglais. Une question francaise posee sur un ouvrage anglais fait
remonter en priorite les passages francais, meme hors sujet. Savoir dans
quelle langue est chaque document permet a l'agent de filtrer, de ponderer,
ou simplement de dire a l'utilisateur ce qu'il a sous la main.

**Methode.** Comptage de mots-outils (articles, prepositions, auxiliaires).
A l'echelle d'un document — des milliers de mots — c'est tres discriminant,
la ou ca serait fragile sur une phrase. Les mots presents dans plusieurs
langues sont **retires automatiquement** des listes au chargement du module :
``la`` sert le francais, l'espagnol et l'italien, il ne tranche rien et ne
doit peser dans aucun score.

**Limite assumee.** Sept langues reconnues, et ``UNKNOWN`` des que le doute
est permis — mieux vaut pas de reponse qu'une mauvaise. Passer a une vraie
bibliotheque de detection ne demanderait que de reecrire ``detect_language``,
le reste du pipeline ne connait que sa signature.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Valeur rendue quand aucune langue ne se degage.
UNKNOWN = ""

# Mots-outils par langue, sans accents (certains PDF les perdent a la
# conversion). Les collisions entre langues sont eliminees plus bas.
_RAW_STOPWORDS: dict[str, str] = {
    "en": (
        "the of and to in that is was he for it with as his on be at by this had not are but"
        " from or have an they which one you were her all she there would their we him been has"
        " when who will more if no out so said what up its about into than them can only other"
        " new some could time these two may then do first any my now such like our over man me"
        " even most made after also did many before must through back years where much your way"
        " well down should because each just those people how too little state good very make"
        " world still own see men work long get here between both life being under never same"
    ),
    "fr": (
        "les des une dans pour avec sur est sont qui que pas plus cette comme mais nous vous"
        " leur tout meme entre aussi ete etre avoir fait faire tres bien sans encore peut deux"
        " autre ainsi donc alors quand chaque toute leurs elles ceux celui cela dont ou sous"
        " apres avant depuis pendant toujours jamais souvent parfois beaucoup peu trop assez"
        " lorsque puisque afin selon vers chez malgre ils elle nos vos notre votre lui eux"
        " quelque plusieurs certains aucun tel telle premiere dernier meilleur nouveau"
    ),
    "es": (
        "los las una para con por sobre son que como pero mas este esta estos estas nos ustedes"
        " todo todos toda cuando cada sus ellos ellas aquel aquello donde despues antes desde"
        " durante siempre nunca mucho poco bastante aunque segun hacia entre sin ser estar tener"
        " hacer muy bien tambien ya solo otro otra primera ultimo mejor nuevo cual quien"
    ),
    "de": (
        "der die das und den dem des ein eine einer einem einen ist sind war waren nicht auch"
        " aber oder wenn dann noch nur schon sehr mehr wie durch bei nach vor uber unter zwischen"
        " gegen ohne seit wahrend werden wurde haben hatte kann konnen muss mussen soll sollen"
        " ihre ihrer seine seinen unser diese dieser dieses jeder alle beim zum zur vom"
    ),
    "it": (
        "gli una per con sono che come non piu questo questa questi queste anche molto bene"
        " quando ogni loro essi quello quella dove dopo prima durante sempre mai poco abbastanza"
        " anche secondo verso senza essere avere fare tra fra alcuni altro altra primo ultimo"
        " migliore nuovo quale chi perche quindi allora ancora gia soltanto nostro vostro"
    ),
    "pt": (
        "dos das uma para com por sobre sao que como mas mais este esta estes estas todo todos"
        " quando cada seus eles elas aquele aquilo onde depois antes desde durante sempre nunca"
        " muito pouco embora segundo entre sem ser estar ter fazer bem tambem apenas outro outra"
        " primeiro ultimo melhor novo qual quem porque entao ainda nosso vosso pelo pela"
    ),
    "nl": (
        "het een van en de dat is zijn was waren niet ook maar of als dan nog alleen zeer meer"
        " zoals door bij naar voor onder tussen tegen zonder sinds tijdens worden werd hebben"
        " had kan kunnen moet moeten zal zullen hun haar deze dit die elke alle om te met op aan"
    ),
}


def _unique_stopwords() -> dict[str, frozenset[str]]:
    """Retire des listes tout mot present dans plus d'une langue.

    ``la`` sert le francais, l'espagnol et l'italien : il ne tranche rien.
    Ne garder que les mots exclusifs evite qu'un vocabulaire partage ne fasse
    pencher un score au hasard.

    Returns:
        Les mots-outils exclusifs, par langue.
    """
    par_langue = {code: set(mots.split()) for code, mots in _RAW_STOPWORDS.items()}
    occurrences: Counter[str] = Counter()
    for mots in par_langue.values():
        occurrences.update(mots)
    partages = {mot for mot, nombre in occurrences.items() if nombre > 1}
    return {code: frozenset(mots - partages) for code, mots in par_langue.items()}


STOPWORDS: dict[str, frozenset[str]] = _unique_stopwords()

# Proportion minimale de mots-outils reconnus pour se prononcer. Un texte
# francais courant depasse largement ce seuil ; un listing de code, non.
MIN_HIT_RATIO = 0.02

# La langue gagnante doit devancer la suivante d'au moins ce facteur. Deux
# langues au coude a coude, c'est un document bilingue ou trop court.
MIN_MARGIN = 1.5

# En dessous, l'echantillon n'est pas representatif.
MIN_WORDS = 30

_WORD = re.compile(r"[a-z]+")


def _normalize(text: str) -> str:
    """Minuscules sans accents, pour coller aux listes de mots-outils."""
    decompose = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def detect_language(text: str) -> str:
    """Determine la langue d'un texte par ses mots-outils.

    Args:
        text: Texte du document, ou un echantillon representatif.

    Returns:
        Le code ISO 639-1 (``en``, ``fr``, ``es``, ``de``, ``it``, ``pt``,
        ``nl``), ou ``UNKNOWN`` si aucune langue ne se degage nettement.
    """
    mots = _WORD.findall(_normalize(text))
    if len(mots) < MIN_WORDS:
        return UNKNOWN

    frequences = Counter(mots)
    scores = {
        code: sum(frequences[mot] for mot in vocabulaire) for code, vocabulaire in STOPWORDS.items()
    }
    classement = sorted(scores.items(), key=lambda paire: -paire[1])
    gagnante, meilleur = classement[0]
    suivant = classement[1][1] if len(classement) > 1 else 0

    if meilleur / len(mots) < MIN_HIT_RATIO:
        return UNKNOWN
    if suivant and meilleur < suivant * MIN_MARGIN:
        return UNKNOWN
    return gagnante


def sample_text(texts: list[str], max_chars: int = 20_000) -> str:
    """Assemble un echantillon representatif a partir des textes d'un document.

    Prend les elements dans l'ordre jusqu'a la taille voulue : le debut d'un
    ouvrage suffit largement a en donner la langue, et cela evite de charger
    un livre entier en memoire pour une detection.

    Args:
        texts: Textes des elements, dans l'ordre du document.
        max_chars: Taille maximale de l'echantillon.

    Returns:
        L'echantillon concatene.
    """
    morceaux: list[str] = []
    taille = 0
    for texte in texts:
        nettoye = texte.strip()
        if not nettoye:
            continue
        morceaux.append(nettoye)
        taille += len(nettoye)
        if taille >= max_chars:
            break
    return " ".join(morceaux)
