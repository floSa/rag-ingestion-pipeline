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
    racine_de_chaque_element,
    rattacher_au_document,
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


class TestRacineDeChaqueElement:
    """Le rattachement au document, et RIEN NE LE GARDAIT.

    `mesure` : neutraliser la remontee de `racine_de_chaque_element` laissait 639
    tests verts, et `test_verify_contract.py` n'IMPORTAIT MEME PAS cette
    fonction. *Ce qu'un test n'importe pas, il ne teste pas.*

    Ce qu'elle garde n'est pas visible sur elle seule : c'est la classe suivante,
    la COMPOSITION, qui montre que sa neutralisation rend le seul controle
    d'ordre du contrat inerte — en rendant zero anomalie, ce qui se lit comme un
    succes.
    """

    def test_a_direct_child_is_attached_to_its_document(self):
        assert racine_de_chaque_element({"a": "doc"}) == {"a": "doc"}

    def test_a_deep_chain_walks_all_the_way_up(self):
        """Le graphe reel va jusqu'a 5 sauts (registre §3.2)."""
        peres = {"e": "d", "d": "c", "c": "b", "b": "a", "a": "doc"}
        racines = racine_de_chaque_element(peres)
        assert racines == dict.fromkeys("abcde", "doc")

    def test_two_documents_do_not_bleed_into_each_other(self):
        """La raison d'etre de la fonction : `sequence` repart a 0 par document."""
        peres = {"a1": "doc1", "b1": "a1", "a2": "doc2", "b2": "a2"}
        racines = racine_de_chaque_element(peres)
        assert racines == {"a1": "doc1", "b1": "doc1", "a2": "doc2", "b2": "doc2"}

    def test_a_cycle_is_returned_to_itself_instead_of_looping_forever(self):
        """Le graphe est acyclique aujourd'hui ; un controle ne doit pas en dependre."""
        racines = racine_de_chaque_element({"a": "b", "b": "a"})
        assert set(racines) == {"a", "b"}

    def test_an_element_with_no_parent_is_absent_from_the_result(self):
        assert racine_de_chaque_element({}) == {}


class TestLaCompositionQuiPorteLOrdre:
    """LE GARDE DE M12. Le controle d'ordre passe par le rattachement.

    `inversions_de_page` groupe par document. Si le rattachement rend chaque
    element a lui-meme, chaque groupe ne porte plus qu'UNE arete, et une seule
    arete ne peut pas etre en desordre : le controle rend zero anomalie sur un
    graphe reellement casse.

    C'est la leçon « asserte depuis le cote qui PRODUIT le comportement » : un
    test de `racine_de_chaque_element` seule aurait pu passer sans que le
    controle d'ordre soit garde.
    """

    # Deux documents, chacun a deux niveaux, et une VRAIE inversion de page dans
    # le premier : l'element vu en sequence 2 est en page 2 alors que la
    # sequence 1 etait en page 9.
    PERES = {"t1": "docA", "p1": "t1", "p2": "t1", "t2": "docB", "p3": "t2"}
    ARETES = [
        ("t1", 0, 1),
        ("p1", 1, 9),
        ("p2", 2, 2),  # <- l'inversion
        ("t2", 0, 1),
        ("p3", 1, 3),
    ]

    def test_the_inversion_is_reported_once_attached_to_its_document(self):
        rattachees = rattacher_au_document(self.PERES, self.ARETES)
        assert inversions_de_page(rattachees) == [("docA", 2, 9, 2)]

    def test_without_the_attachment_the_very_same_graph_looks_clean(self):
        """LE TEMOIN, et c'est lui le resultat.

        Sans rattachement, chaque element est son propre groupe : la meme
        inversion devient invisible. C'est ce que la mutation produit, et c'est
        pourquoi ce garde asserte la composition et non la fonction seule.
        """
        non_rattachees = [(element, sequence, page) for element, sequence, page in self.ARETES]
        assert inversions_de_page(non_rattachees) == []

    def test_the_second_document_is_not_polluted_by_the_first(self):
        """Un document sain reste sain : le garde ne rougit pas au hasard."""
        rattachees = rattacher_au_document(self.PERES, self.ARETES)
        assert [
            anomalie for anomalie in inversions_de_page(rattachees) if anomalie[0] == "docB"
        ] == []

    def test_a_clean_two_document_graph_reports_nothing(self):
        peres = {"t1": "docA", "p1": "t1", "t2": "docB", "p2": "t2"}
        aretes = [("t1", 0, 1), ("p1", 1, 2), ("t2", 0, 1), ("p2", 1, 2)]
        assert inversions_de_page(rattacher_au_document(peres, aretes)) == []


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
