"""Tests des controles du contrat d'interface.

verify_contract rendait rc=0 et « Contrat respecte » sur un index ou 199 images
sur 209 n'avaient pas d'URL, ou rien ne verifiait l'ordre de `sequence`
(exigence 4), ni que `source_path` etait renseigne (exigence 3), ni quel modele
avait produit les vecteurs (exigence 1). Les controles etaient par ailleurs
intestables : le module faisait ses imports lourds au niveau du module.

Chaque test ci-dessous construit un index QUI PASSE l'ancien controle et que le
nouveau refuse. Un controle vert des deux cotes ne prouve rien.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.verify_contract import (
    chunks_incoherents,
    images_sans_url,
    inversions_de_page,
    sources_sans_chemin,
)


class TestInversionsDePage:
    """L'ordre de lecture, exigence 4 — et ce n'est PAS l'unicite sous un parent.

    « Aucun parent ne porte deux fois la meme valeur » est satisfait par une
    numerotation aleatoire distincte par parent. La propriete qui porte l'ordre
    est : trie par `sequence`, `page_no` ne decroit jamais (registre 6.16).
    """

    def test_a_document_in_reading_order_has_no_inversion(self):
        aretes = [("doc", 0, 1), ("doc", 1, 1), ("doc", 2, 3), ("doc", 3, 7)]
        assert inversions_de_page(aretes) == []

    def test_a_page_that_goes_backwards_is_reported(self):
        aretes = [("doc", 0, 5), ("doc", 1, 2)]
        assert inversions_de_page(aretes) == [("doc", 1, 5, 2)]

    def test_the_sequence_order_decides_and_not_the_input_order(self):
        """Les aretes arrivent du graphe dans un ordre quelconque."""
        aretes = [("doc", 3, 9), ("doc", 1, 2), ("doc", 2, 4), ("doc", 0, 1)]
        assert inversions_de_page(aretes) == []

    def test_sequence_restarts_at_zero_in_each_document(self):
        """Reserve 1 du registre 6.16 : elle n'est pas globalement monotone.

        Deux documents entrelaces ne doivent produire aucune inversion : sans le
        groupement par document, celui-ci en rendrait deux.
        """
        aretes = [("a", 0, 1), ("b", 0, 1), ("a", 1, 2), ("b", 1, 2)]
        assert inversions_de_page(aretes) == []

    def test_gaps_are_not_inversions(self):
        """Reserve 2 et 3 : `sequence` n'est pas contigue sous un parent.

        Le plus grand trou mesure entre deux enfants d'un meme parent vaut 993.
        Un controle qui exigerait la contiguite rougirait sur un graphe sain.
        """
        assert inversions_de_page([("doc", 0, 1), ("doc", 993, 4)]) == []

    def test_a_repeated_sequence_is_not_an_inversion_by_itself(self):
        assert inversions_de_page([("doc", 4, 2), ("doc", 4, 2)]) == []

    def test_no_edge_no_inversion(self):
        assert inversions_de_page([]) == []


class TestSourcesSansChemin:
    """Exigence 3 : `source_path` est l'identite d'un document, jamais filename."""

    def test_a_populated_source_path_passes(self):
        assert sources_sans_chemin([{"source_path": "htms/livre/1. Intro.html"}]) == 0

    def test_an_empty_source_path_is_counted(self):
        assert sources_sans_chemin([{"source_path": ""}, {"source_path": "a"}]) == 1

    def test_a_missing_key_counts_as_empty(self):
        assert sources_sans_chemin([{}]) == 1

    def test_whitespace_is_not_a_path(self):
        assert sources_sans_chemin([{"source_path": "   "}]) == 1


class TestChunksIncoherents:
    def test_a_single_chunk_element_is_coherent(self):
        assert chunks_incoherents([{"chunk_index": 0, "chunk_count": 1}]) == []

    def test_an_index_beyond_the_count_is_reported(self):
        assert chunks_incoherents([{"chunk_index": 3, "chunk_count": 3}]) == [(3, 3)]

    def test_a_negative_index_is_reported(self):
        assert chunks_incoherents([{"chunk_index": -1, "chunk_count": 2}]) == [(-1, 2)]

    def test_a_zero_count_is_reported(self):
        """Un element decoupe en zero chunk ne peut pas porter de chunk."""
        assert chunks_incoherents([{"chunk_index": 0, "chunk_count": 0}]) == [(0, 0)]

    def test_a_middle_chunk_of_a_long_element_is_coherent(self):
        assert chunks_incoherents([{"chunk_index": 4, "chunk_count": 9}]) == []


class TestImagesSansUrl:
    """Le controle qui rendait « Contrat respecte » sur 199 images muettes.

    L'agent ne sert que ce que le graphe reference : une image sans URL est
    payee en place et en temps, et reste inatteignable.
    """

    def test_every_image_carries_an_url(self):
        assert images_sans_url(["http://minio:9000/documents/a.png"]) == 0

    def test_an_empty_url_is_counted(self):
        assert images_sans_url(["", "http://minio:9000/documents/a.png", ""]) == 2

    def test_a_null_url_reads_as_absent(self):
        assert images_sans_url([None, ""]) == 2

    def test_no_image_no_anomaly(self):
        assert images_sans_url([]) == 0


class TestLeModuleResteImportableSansStore:
    """Les controles n'etaient testables par rien : le module importait
    `chromadb` et `nebula3` au niveau du module."""

    SONDE = (
        "import sys, src.verify_contract;"
        "sys.exit(1 if {'chromadb', 'nebula3'} & set(sys.modules) else 0)"
    )

    def test_importing_the_module_pulls_no_store_client(self):
        acheve = subprocess.run(
            [sys.executable, "-c", self.SONDE],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
