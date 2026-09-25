"""Tests de `chunking` : texte encode, forme de l'id de chunk, filtre de contenu."""

from __future__ import annotations

import pytest

from src.docling_service.chunking import (
    chunk_id,
    contextualize,
    embedding_inputs,
    has_content,
)


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


class TestChunkId:
    """La forme de l'id ChromaDB, qui est une clause du contrat.

    Le test du site de production (`vectors.build_chunks`) est dans
    `test_vectors.py` (registre 5.1).
    """

    def test_un_chunk_seul_garde_l_id_nu(self):
        """La clause du contrat, que `verify_contract` compte (974 ids suffixes sur 4 365).

        `extract` purge le document par `source_path` avant de le reecrire : une
        autre forme d'id ne laisserait pas d'orphelin, mais romprait la clause
        (registre 4.31.B3).
        """
        assert chunk_id("abc1234567", 0, 1) == "abc1234567"

    def test_un_element_multi_chunks_est_suffixe(self):
        assert [chunk_id("abc1234567", i, 3) for i in range(3)] == [
            "abc1234567#0",
            "abc1234567#1",
            "abc1234567#2",
        ]

    def test_les_ids_d_un_meme_element_sont_uniques(self):
        ids = [chunk_id("abc1234567", i, 10) for i in range(10)]

        assert len(set(ids)) == 10

    def test_l_element_id_reste_lisible_dans_l_id_du_chunk(self):
        """Le suffixe s'ajoute a l'element_id, il ne le remplace pas.

        `rag-agent-chat` valide `/context/{element_id}` sur `^[a-f0-9]{10}$` et
        lit l'id du chunk dans un champ distinct : un id de chunk qui n'ouvrirait
        plus sur son element romprait la correspondance entre les deux champs.
        """
        assert chunk_id("abc1234567", 7, 9).startswith("abc1234567")


class TestEmbeddingInputs:
    """Ce que le modele recoit, construit a un seul endroit.

    `index_report` et `vectors` partagent cette fonction. Avec deux
    constructions, l'instrument comptait 65 chunks au-dela de la fenetre au
    lieu de 137 (registre 3.4, mesure sur 4 365 chunks).
    """

    TEXTES = ["la moyenne y est sensible", "Chunking\n\ndeja prefixe"]
    METAS = [{"section_title": "Outliers"}, {"section_title": "Chunking"}]

    def test_the_title_is_prefixed_when_the_setting_is_on(self):
        assert embedding_inputs(self.TEXTES, self.METAS, True)[0] == (
            "Outliers\n\nla moyenne y est sensible"
        )

    def test_nothing_is_prefixed_when_the_setting_is_off(self):
        """Le reglage a deux positions ; l'instrument doit etre exact dans les deux."""
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


class TestHasContent:
    """Le filtre qui decide si un texte merite un vecteur.

    La fonction vient de l'ancien `blocks.py` (registre 5.2).
    """

    def test_un_mot_porte_du_contenu(self):
        assert has_content("bonjour") is True

    def test_un_chiffre_porte_du_contenu(self):
        assert has_content("2") is True

    def test_la_ponctuation_seule_n_en_porte_pas(self):
        assert has_content("...") is False

    def test_un_filet_de_tableau_n_en_porte_pas(self):
        assert has_content("-" * 70) is False

    def test_le_texte_vide_n_en_porte_pas(self):
        assert has_content("") is False

    def test_les_puces_et_separateurs_n_en_portent_pas(self):
        assert has_content("  •  |  ") is False
