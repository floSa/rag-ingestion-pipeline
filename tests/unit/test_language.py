"""Tests de la detection de langue."""

from __future__ import annotations

from src.docling_service.language import (
    MIN_WORDS,
    STOPWORDS,
    UNKNOWN,
    detect_language,
    sample_text,
)

ANGLAIS = """
The purpose of this chapter is to introduce the reader to the practice of
continuous delivery for machine learning models. We will see that the model
itself is only a small part of a much larger system, and that most of the
work lies in the pipeline that surrounds it. When a team deploys a model to
production for the first time, it usually discovers that the hard problems
are not the ones it expected. The data changes, the users behave in ways that
nobody predicted, and the metrics that looked good in the laboratory turn out
to be poor proxies for what the business actually cares about.
"""

FRANCAIS = """
L'objectif de ce chapitre est de presenter au lecteur la pratique de la
livraison continue des modeles d'apprentissage automatique. Nous verrons que
le modele lui-meme ne represente qu'une petite partie d'un systeme bien plus
vaste, et que l'essentiel du travail se trouve dans la chaine qui l'entoure.
Lorsqu'une equipe met un modele en production pour la premiere fois, elle
decouvre souvent que les problemes difficiles ne sont pas ceux qu'elle avait
prevus. Les donnees changent, les utilisateurs se comportent autrement, et
les mesures qui semblaient bonnes en laboratoire refletent mal la realite.
"""

ALLEMAND = """
Das Ziel dieses Kapitels ist es, den Leser in die Praxis der kontinuierlichen
Auslieferung von Modellen des maschinellen Lernens einzufuhren. Wir werden
sehen, dass das Modell selbst nur ein kleiner Teil eines viel groesseren
Systems ist und dass die meiste Arbeit in der Pipeline liegt, die es umgibt.
Wenn ein Team ein Modell zum ersten Mal in die Produktion bringt, stellt es
gewoehnlich fest, dass die schwierigen Probleme nicht die erwarteten sind.
"""


class TestStopwords:
    def test_ambiguous_words_are_removed(self):
        """« la » sert plusieurs langues : il ne doit peser dans aucun score."""
        for code, vocabulaire in STOPWORDS.items():
            assert "la" not in vocabulaire, code

    def test_each_language_keeps_discriminant_words(self):
        assert "the" in STOPWORDS["en"]
        assert "dans" in STOPWORDS["fr"]
        assert "und" in STOPWORDS["de"]

    def test_no_word_belongs_to_two_languages(self):
        vus: set[str] = set()
        for vocabulaire in STOPWORDS.values():
            assert not (vus & vocabulaire)
            vus |= vocabulaire


class TestDetectLanguage:
    def test_english(self):
        assert detect_language(ANGLAIS) == "en"

    def test_french(self):
        assert detect_language(FRANCAIS) == "fr"

    def test_german(self):
        assert detect_language(ALLEMAND) == "de"

    def test_french_with_accents(self):
        accentue = FRANCAIS.replace("modele", "modèle").replace("donnees", "données")
        assert detect_language(accentue) == "fr"

    def test_too_short_to_decide(self):
        assert detect_language("Bonjour tout le monde") == UNKNOWN

    def test_empty(self):
        assert detect_language("") == UNKNOWN

    def test_code_listing_is_not_a_language(self):
        """Un listing de code ne doit pas etre attribue a une langue au hasard."""
        code = " ".join(["def compute(x): return x * 2 + offset[i] / total"] * 20)
        assert detect_language(code) == UNKNOWN

    def test_mixed_document_returns_the_dominant_language(self):
        """Un ouvrage anglais citant du francais reste un ouvrage anglais."""
        assert detect_language(ANGLAIS * 4 + FRANCAIS) == "en"

    def test_near_tie_stays_undecided(self):
        """Deux langues au coude a coude : mieux vaut ne pas se prononcer."""
        anglais = " ".join(sorted(STOPWORDS["en"])[:40])
        francais = " ".join(sorted(STOPWORDS["fr"])[:40])
        assert detect_language(anglais + " " + francais) == UNKNOWN

    def test_sample_of_minimum_size_is_accepted(self):
        mots = " ".join(["the of and to in that is was for it with as on be at by this"] * 3)
        assert len(mots.split()) >= MIN_WORDS
        assert detect_language(mots) == "en"


class TestSampleText:
    def test_concatenates_in_order(self):
        assert sample_text(["premier", "second"], max_chars=100) == "premier second"

    def test_skips_empty_elements(self):
        assert sample_text(["a", "   ", "b"], max_chars=100) == "a b"

    def test_stops_once_long_enough(self):
        echantillon = sample_text(["x" * 30] * 10, max_chars=50)
        assert len(echantillon) < 200  # s'arrete des le seuil atteint

    def test_no_text_at_all(self):
        assert sample_text([]) == ""
