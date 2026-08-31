"""Tests unitaires pour le decoupage des textes longs."""

from __future__ import annotations

import pytest

from src.docling_service.chunking import (
    chunk_ids,
    chunk_text,
    contextualize,
    embedding_inputs,
)


class TestChunkText:
    def test_empty_returns_no_chunk(self):
        assert chunk_text("") == []

    def test_whitespace_only_returns_no_chunk(self):
        assert chunk_text("   \n\t ") == []

    def test_short_text_single_chunk(self):
        assert chunk_text("Un texte court.") == ["Un texte court."]

    def test_short_text_is_stripped(self):
        assert chunk_text("  bonjour  ") == ["bonjour"]

    def test_long_text_is_split(self):
        text = " ".join(f"mot{i}" for i in range(500))
        chunks = chunk_text(text, size=200, overlap=50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 200 for chunk in chunks)

    def test_nothing_is_lost(self):
        # Le point central : l'ancienne troncature a 1000 caracteres perdait
        # silencieusement la fin des paragraphes longs.
        text = " ".join(f"mot{i}" for i in range(400))
        chunks = chunk_text(text, size=200, overlap=50)
        assert "mot0" in chunks[0]
        assert "mot399" in chunks[-1]

    def test_chunks_overlap(self):
        text = " ".join(f"mot{i}" for i in range(200))
        chunks = chunk_text(text, size=200, overlap=60)
        # La fin d'un chunk se retrouve au debut du suivant.
        assert any(word in chunks[1] for word in chunks[0].split()[-3:])

    def test_no_overlap_still_covers_everything(self):
        text = " ".join(f"mot{i}" for i in range(200))
        chunks = chunk_text(text, size=100, overlap=0)
        joined = " ".join(chunks)
        assert "mot0" in joined
        assert "mot199" in joined

    def test_text_without_spaces_terminates(self):
        # Aucune frontiere de mot : la boucle doit progresser quand meme.
        chunks = chunk_text("x" * 1000, size=100, overlap=20)
        assert len(chunks) > 1
        assert all(chunk for chunk in chunks)

    def test_size_must_be_positive(self):
        with pytest.raises(ValueError):
            chunk_text("abc", size=0)

    def test_overlap_must_be_smaller_than_size(self):
        with pytest.raises(ValueError):
            chunk_text("abc", size=100, overlap=100)

    def test_negative_overlap_rejected(self):
        with pytest.raises(ValueError):
            chunk_text("abc", size=100, overlap=-1)


class TestContextualize:
    def test_titre_prepose(self):
        assert contextualize("Le texte.", "Mesures de dispersion") == (
            "Mesures de dispersion\n\nLe texte."
        )

    def test_titre_vide_laisse_le_texte_intact(self):
        assert contextualize("Le texte.", "") == "Le texte."

    def test_titre_en_espaces_laisse_le_texte_intact(self):
        assert contextualize("Le texte.", "   ") == "Le texte."

    def test_pas_de_repetition_si_le_texte_est_le_titre(self):
        # Le chunk qui *est* le titre ne doit pas se voir prefixer par lui-meme.
        assert contextualize("Mesures de dispersion", "Mesures de dispersion") == (
            "Mesures de dispersion"
        )

    def test_comparaison_insensible_a_la_casse(self):
        assert contextualize("MESURES de dispersion et suite", "Mesures de dispersion") == (
            "MESURES de dispersion et suite"
        )

    def test_texte_vide(self):
        assert contextualize("", "Titre") == "Titre\n\n"

    def test_titre_avec_espaces_est_nettoye(self):
        assert contextualize("Le texte.", "  Titre  ") == "Titre\n\nLe texte."


class TestChunkIds:
    def test_single_chunk_keeps_bare_id(self):
        # Les documents deja ingeres gardent leur identifiant : l'upsert les
        # met a jour au lieu de creer un doublon.
        assert chunk_ids("abc1234567", 1) == ["abc1234567"]

    def test_multiple_chunks_suffixed(self):
        assert chunk_ids("abc1234567", 3) == [
            "abc1234567#0",
            "abc1234567#1",
            "abc1234567#2",
        ]

    def test_ids_are_unique(self):
        ids = chunk_ids("abc1234567", 10)
        assert len(set(ids)) == 10


class TestEmbeddingInputs:
    """Ce que le MODELE recoit — et le seul site qui en decide.

    L'instrument de troncature tokenisait le texte STOCKE quand le modele
    encode le texte PREFIXE du titre : il annoncait 65 chunks au-dela de la
    fenetre la ou il y en avait 137 (registre 3.4, `mesure` sur 4 365 chunks).
    Ce n'etait pas une erreur de calcul mais une DIVERGENCE : deux endroits
    decidaient du meme texte. Il n'y en a plus qu'un, et ces tests le gardent.
    """

    TEXTES = ["la moyenne y est sensible", "Chunking\n\ndeja prefixe"]
    METAS = [{"section_title": "Outliers"}, {"section_title": "Chunking"}]

    def test_the_title_is_prefixed_when_the_setting_is_on(self):
        assert embedding_inputs(self.TEXTES, self.METAS, True)[0] == (
            "Outliers\n\nla moyenne y est sensible"
        )

    def test_nothing_is_prefixed_when_the_setting_is_off(self):
        """Le reglage a deux positions, et l'instrument doit dire vrai des deux."""
        assert embedding_inputs(self.TEXTES, self.METAS, False) == self.TEXTES

    def test_a_chunk_that_already_opens_on_its_title_is_left_alone(self):
        assert embedding_inputs(self.TEXTES, self.METAS, True)[1] == self.TEXTES[1]

    def test_a_missing_section_title_leaves_the_text_untouched(self):
        assert embedding_inputs(["texte"], [{}], True) == ["texte"]

    def test_texts_and_metadatas_must_align(self):
        """Un decalage d'un rang prefixerait chaque chunk du titre du suivant."""
        with pytest.raises(ValueError):
            embedding_inputs(["a", "b"], [{"section_title": "T"}], True)

    def test_the_result_stays_aligned_with_the_input(self):
        assert len(embedding_inputs(self.TEXTES, self.METAS, True)) == len(self.TEXTES)
