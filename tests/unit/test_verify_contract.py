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

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.docling_service.elements import TAG_MAP
from src.docling_service.ngql import DOCUMENT_PROPERTIES, VERTEX_PROPERTIES
from src.verify_contract import (
    _lire_les_aretes,
    _lire_les_tags_sans_la_colonne,
    _verifier_le_graphe,
    _verifier_le_tag_document,
    _verifier_les_ancres,
    anomalie_de_colonne,
    chunks_incoherents,
    images_sans_url,
    inversions_de_page,
    jeux_de_chunks_incomplets,
    racine_de_chaque_element,
    rattacher_au_document,
    sommets_sans_profondeur,
    sources_sans_chemin,
)

RACINE_DEPOT = Path(__file__).resolve().parents[2]


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

        Le plus grand ecart mesure entre deux `sequence` consecutives d'un meme
        parent est une DIFFERENCE de 994 — 993 valeurs intercalaires — entre les
        rangs 203 et 1197. Les valeurs ci-dessous les reprennent telles quelles :
        un controle qui exigerait la contiguite rougirait sur un graphe sain. Le
        site canonique de ces chiffres est le docstring d'`inversions_de_page`.
        """
        assert inversions_de_page([("doc", 203, 1), ("doc", 1197, 4)]) == []

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


class TestJeuxDeChunksIncomplets:
    """LE CONTROLE DE BORNES NE VOIT PAS LA PANNE QUE LE MODULE ANNONCE.

    L'agent reconstitue un element decoupe en concatenant ses chunks dans l'ordre
    de `chunk_index`. Ce qui le casse est un morceau qui MANQUE — et chaque chunk
    PRESENT satisfait `0 <= index < count` meme quand un de ses freres a disparu.
    Le trou est invisible depuis un chunk isole.

    `mesure` le 31 aout 2026 sur l'index complet, 4 365 chunks et 3 750
    elements : `chunks_incoherents` rend 0 chunk fautif, et DEUX elements ont un
    jeu troue — `aa3de10738` (chunk_count 7, index 4 manquant) et `eb52c4ec8f`
    (chunk_count 4, index 3 manquant).
    """

    # Le cas reel, reduit : l'element annonce 7 chunks, six sont la.
    REEL = [
        {"element_id": "aa3de10738", "chunk_index": i, "chunk_count": 7} for i in (0, 1, 2, 3, 5, 6)
    ]

    def test_a_complete_set_is_not_reported(self):
        metas = [{"element_id": "e", "chunk_index": i, "chunk_count": 3} for i in range(3)]
        assert jeux_de_chunks_incomplets(metas) == []

    def test_a_single_chunk_element_is_complete(self):
        assert (
            jeux_de_chunks_incomplets([{"element_id": "e", "chunk_index": 0, "chunk_count": 1}])
            == []
        )

    def test_the_real_case_measured_on_the_live_index_is_reported(self):
        assert jeux_de_chunks_incomplets(self.REEL) == [("aa3de10738", 7, [4])]

    def test_the_bounds_check_stays_green_on_that_very_same_case(self):
        """LE TEMOIN, et c'est lui le resultat.

        Sans lui, ce fichier pourrait laisser croire que les deux controles se
        recouvrent. Ils ne se recouvrent pas : sur le cas reel, le controle de
        bornes rend zero, et c'est pourquoi il en fallait un second.
        """
        assert chunks_incoherents(self.REEL) == []

    def test_a_missing_last_chunk_is_reported(self):
        """Le cas le plus discret : la fin d'un element disparait."""
        metas = [{"element_id": "e", "chunk_index": i, "chunk_count": 4} for i in (0, 1, 2)]
        assert jeux_de_chunks_incomplets(metas) == [("e", 4, [3])]

    def test_several_missing_indexes_are_all_named(self):
        metas = [{"element_id": "e", "chunk_index": i, "chunk_count": 5} for i in (0, 4)]
        assert jeux_de_chunks_incomplets(metas) == [("e", 5, [1, 2, 3])]

    def test_two_holed_elements_are_both_reported_and_sorted(self):
        metas = self.REEL + [
            {"element_id": "eb52c4ec8f", "chunk_index": i, "chunk_count": 4} for i in (0, 1, 2)
        ]
        assert jeux_de_chunks_incomplets(metas) == [
            ("aa3de10738", 7, [4]),
            ("eb52c4ec8f", 4, [3]),
        ]

    def test_a_healthy_element_beside_a_holed_one_is_not_dragged_in(self):
        """Le garde ne rougit pas au hasard : il nomme l'element fautif."""
        metas = self.REEL + [
            {"element_id": "sain", "chunk_index": i, "chunk_count": 2} for i in (0, 1)
        ]
        assert [troue[0] for troue in jeux_de_chunks_incomplets(metas)] == ["aa3de10738"]

    def test_no_chunk_no_anomaly(self):
        assert jeux_de_chunks_incomplets([]) == []


class TestSommetsSansProfondeur:
    """La charge utile du §4.11 : le schema migre en place, les DONNEES non.

    Un `ALTER TAG ... ADD (depth int)` laisse a NULL tous les sommets deja
    ecrits, et seule une reingestion les renseigne. Un index a moitie migre est
    donc possible, et rien ne le signalait : l'agent lirait `depth` sur les
    sommets recents et NULL sur les anciens, sans qu'aucune erreur ne distingue
    « profondeur 0 » de « profondeur inconnue ».
    """

    def test_all_depths_written_is_no_anomaly(self):
        assert sommets_sans_profondeur([0, 1, 2, 3, 4, 5]) == 0

    def test_a_null_depth_is_counted(self):
        assert sommets_sans_profondeur([0, None, 2]) == 1

    def test_a_fully_unmigrated_index_is_fully_counted(self):
        """Le cas mesure au §4.11 : 188 sommets sur 188 a NULL apres l'ALTER."""
        assert sommets_sans_profondeur([None] * 188) == 188

    def test_zero_is_a_depth_and_not_an_absence(self):
        """LE TEMOIN. `depth = 0` est la racine d'un document, pas un trou.

        Un compteur ecrit `if not profondeur` — la faute naturelle — compterait
        les racines comme non migrees, et rougirait sur un graphe parfaitement
        sain. C'est exactement le piege que `depth` tend, puisque avec le plafond
        retire il vaut desormais 0 (registre §4.24).
        """
        assert sommets_sans_profondeur([0, 0, 0]) == 0

    def test_no_vertex_no_anomaly(self):
        assert sommets_sans_profondeur([]) == 0


# --- Session nGQL bouchonnee -------------------------------------------------
#
# Elle rend de VRAIES valeurs Nebula au sens du code : chaque cellule sait dire
# `is_null()` et `as_int()` / `as_string()`. C'est ce qui permet d'exercer le
# code de LECTURE lui-meme — la garde `is_null()` sur `sequence` — au lieu d'un
# substitut. Un montage qui bouchonnerait plus haut, au niveau de la liste
# d'aretes deja parsee, rendrait le defaut intestable : *mute le producteur, pas
# le consommateur.*


class _Cellule:
    """Une valeur nGQL : nulle, entiere ou chaine."""

    def __init__(self, valeur: int | str | None) -> None:
        self._valeur = valeur

    def is_null(self) -> bool:
        return self._valeur is None

    def as_int(self) -> int:
        if self._valeur is None:
            # C'est EXACTEMENT ce que fait le vrai client : appeler `as_int()`
            # sur une valeur nulle leve. Sans cette levee, le bouchon serait plus
            # indulgent que la production et le test vert des deux cotes.
            raise TypeError("InvalidValueTypeException: value is NULL")
        return int(self._valeur)

    def as_string(self) -> str:
        if self._valeur is None:
            raise TypeError("InvalidValueTypeException: value is NULL")
        return str(self._valeur)


class _Resultat:
    def __init__(self, lignes: list[list[int | str | None]], succes: bool = True) -> None:
        self._lignes = lignes
        self._succes = succes

    def is_succeeded(self) -> bool:
        return self._succes

    def error_msg(self) -> str:
        return "requete rejetee"

    def row_size(self) -> int:
        return len(self._lignes)

    def row_values(self, index: int) -> list[_Cellule]:
        return [_Cellule(valeur) for valeur in self._lignes[index]]


class _Session:
    """Rend une reponse par motif de requete, et journalise ce qu'on lui demande."""

    def __init__(self, reponses: dict[str, _Resultat]) -> None:
        self._reponses = reponses
        self.requetes: list[str] = []
        self.relachee = False

    def execute(self, requete: str) -> _Resultat:
        self.requetes.append(requete)
        for motif, reponse in self._reponses.items():
            if motif in requete:
                return reponse
        return _Resultat([])

    def release(self) -> None:
        """Le vrai client la porte, et `_verifier_le_graphe` l'appelle dans son
        `finally`. Sans elle, le bouchon serait plus etroit que la production."""
        self.relachee = True


class TestUneSequenceAbsenteSeRAPPORTE:
    """L'exigence 4 est « sequence ABSENTE ou non monotone ». La moitie « absente »
    faisait LEVER ce module.

    `verify_contract.py` appelait `int(ligne[2].as_int())` sans garde
    `is_null()`, alors que `page_no` en avait un a la ligne suivante. `mesure` de
    l'audit sur un space jetable : `InvalidValueTypeException`, et le rapport
    avortait sur une trace Python — au lieu de rapporter precisement le defaut
    qu'il existe pour trouver. Un outil de pre-deploiement qui plante ne dit pas
    « non conforme », il ne dit rien.
    """

    REQUETE = "PARENT_OF"

    def _session(self, lignes):
        return _Session({self.REQUETE: _Resultat(lignes)})

    def test_a_complete_graph_reports_no_missing_sequence(self):
        session = self._session([["docA", "t1", 0, 1], ["t1", "p1", 1, 2]])
        aretes, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == []
        assert len(aretes) == 2

    def test_a_null_sequence_is_reported_instead_of_raising(self):
        """LE GARDE. Sans lui, cet appel leve et le rapport n'existe pas."""
        session = self._session([["docA", "t1", 0, 1], ["t1", "p1", None, 2]])
        aretes, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == ["p1"]
        # L'arete sans ordre est ECARTEE du controle d'ordre, pas comptee a zero :
        # lui donner sequence 0 en ferait une premiere arete et pourrait
        # fabriquer une fausse inversion.
        assert [arete[0] for arete in aretes] == ["docA"]

    def test_every_missing_sequence_is_named(self):
        session = self._session([["docA", "a", None, 1], ["docA", "b", None, 2]])
        _, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == ["a", "b"]

    def test_a_null_page_is_still_tolerated_as_before(self):
        """Le temoin de la ligne d'a cote : `page_no` avait deja sa garde."""
        session = self._session([["docA", "t1", 0, None]])
        aretes, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == []
        assert aretes == [("docA", 0, 0)]

    def test_the_bouchon_would_raise_on_a_null_int(self):
        """LE TEMOIN DU HARNAIS. *Verifie ton harnais avant de croire ton rouge.*

        Si `_Cellule.as_int()` etait indulgente sur une valeur nulle, le test
        ci-dessus serait vert MEME SANS la garde `is_null()` dans le code. Ce
        test prouve que le bouchon reproduit bien la levee du vrai client.
        """
        with pytest.raises(TypeError):
            _Cellule(None).as_int()


class TestLeTagDocumentEstCOUVERT:
    """Le defaut que `_verifier_les_tags` a ferme restait ouvert D'UN TAG.

    `NebulaWriter._verifier_les_tags` recoit `sorted(set(TAG_MAP.values()))`,
    c'est-a-dire les 11 tags d'ELEMENT : le tag `Document` n'en fait pas partie,
    son schema lui etant propre. Or ses quatre `ALTER TAG Document ADD` sont
    `required=False` par construction — « la colonne existe deja » etant leur cas
    nominal — donc une migration REELLEMENT refusee ne disait rien.

    Et parmi ces colonnes, `source_path` EST l'exigence 3 du contrat : l'identite
    d'un document, celle qui distingue les deux `Preface.html` du corpus.
    """

    COMPLET = [[colonne] for colonne in DOCUMENT_PROPERTIES]

    def test_a_complete_tag_reports_nothing(self):
        session = _Session({"DESCRIBE TAG Document": _Resultat(self.COMPLET)})
        assert _verifier_le_tag_document(session) == []

    def test_a_missing_source_path_is_reported(self):
        """L'exigence 3, et c'est la colonne qui compte le plus des sept."""
        sans = [[c] for c in DOCUMENT_PROPERTIES if c != "source_path"]
        session = _Session({"DESCRIBE TAG Document": _Resultat(sans)})
        anomalies = _verifier_le_tag_document(session)
        assert len(anomalies) == 1
        assert "source_path" in anomalies[0]

    def test_the_state_of_a_pre_migration_space_is_reported(self):
        """Le tag tel que `services/nebulagraph.md:26` le decrit encore : 2 colonnes."""
        session = _Session({"DESCRIBE TAG Document": _Resultat([["filename"], ["type_file"]])})
        anomalies = _verifier_le_tag_document(session)
        assert len(anomalies) == 1
        for manquante in ("total_pages", "collection", "source_path", "language"):
            assert manquante in anomalies[0]

    def test_a_richer_tag_is_not_at_fault(self):
        """Le temoin : une colonne EN PLUS n'est pas une migration manquee."""
        session = _Session(
            {"DESCRIBE TAG Document": _Resultat([*self.COMPLET, ["colonne_future"]])}
        )
        assert _verifier_le_tag_document(session) == []

    def test_a_rejected_describe_is_reported_and_not_read_as_a_success(self):
        """Un DESCRIBE en echec rendait une liste vide, donc « aucune manquante ».

        C'est la meme famille que le reste : ne pas savoir n'est pas savoir que
        c'est bon.
        """
        session = _Session({"DESCRIBE TAG Document": _Resultat([], succes=False)})
        anomalies = _verifier_le_tag_document(session)
        assert len(anomalies) == 1
        assert "n'est pas verifiable" in anomalies[0]


# --- Un index VIDE, en sous-processus ---------------------------------------
#
# Le code de sortie EST le comportement : c'est ce qu'un `docker compose exec`
# remonte et ce qu'un `&&` lit dans une procedure de pre-deploiement. Un import
# laisserait attraper `SystemExit` — prouver qu'un objet a ete leve, pas que la
# commande echoue. Et bouchonner `chromadb` dans `sys.modules` laisserait le
# bouchon derriere soi, rendant l'ordre des tests significatif.

_CHROMA_VIDE = """
class _Collection:
    def get(self, include=None):
        return {"metadatas": [], "ids": []}

    metadata = {}


class HttpClient:
    def __init__(self, host=None, port=None):
        pass

    def get_or_create_collection(self, name=None):
        return _Collection()
"""


def _verifier_un_index_vide(tmp_path: Path):
    """Lance `python -m src.verify_contract` sur une collection vide."""
    bouchons = tmp_path / "bouchons"
    (bouchons / "chromadb").mkdir(parents=True, exist_ok=True)
    (bouchons / "chromadb" / "__init__.py").write_text(_CHROMA_VIDE, encoding="utf-8")
    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = os.pathsep.join([str(bouchons), str(RACINE_DEPOT)])
    return subprocess.run(
        [sys.executable, "-m", "src.verify_contract"],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestUnIndexVideNEstPasUnIndexConforme:
    """IL RENDAIT rc=0, ET TOUS LES CONTROLES VIVENT DERRIERE CE GARDE.

    `if not metadatas: print("Index vide..."); return` — donc une purge, une
    ingestion en echec ou un nom de collection errone passaient pour « Contrat
    respecte », dans un outil dont le docstring dit « pour un usage en
    pre-deploiement ». Le defaut preexistait sur `main:52-54`, mais sa portee
    s'est elargie a tout ce que le lot 3 a ajoute derriere lui.

    Ce test ne bouchonne PAS `nebula3` : le point est justement que le module
    sort AVANT de toucher au graphe. S'il allait plus loin, il echouerait sur
    l'import manquant et ce test serait vert pour la mauvaise raison — d'ou
    l'assertion sur le code de sortie ET sur le message.
    """

    def test_an_empty_index_exits_in_one(self, tmp_path):
        acheve = _verifier_un_index_vide(tmp_path)
        assert acheve.returncode == 1, acheve.stdout + acheve.stderr

    def test_it_says_the_index_is_empty_and_not_that_the_contract_holds(self, tmp_path):
        acheve = _verifier_un_index_vide(tmp_path)
        assert "ANOMALIE" in acheve.stdout
        assert "VIDE" in acheve.stdout
        assert "Contrat respecte" not in acheve.stdout

    def test_the_message_names_the_usual_causes(self, tmp_path):
        """Un rouge sans cause probable envoie le lecteur lire le code."""
        acheve = _verifier_un_index_vide(tmp_path)
        assert "purge" in acheve.stdout
        assert "collection" in acheve.stdout

    def test_it_did_not_reach_the_graph(self, tmp_path):
        """LE TEMOIN. Il sort sur l'index vide, pas sur un `nebula3` manquant.

        Sans lui, le rc=1 ci-dessus serait satisfait par un ModuleNotFoundError,
        et le test serait vert meme si le garde de l'index vide disparaissait.
        """
        acheve = _verifier_un_index_vide(tmp_path)
        assert "nebula3" not in acheve.stderr
        assert "Traceback" not in acheve.stderr


class TestToutesLesAncresSontVerifieesEtPasUnEchantillon:
    """LA DETTE TRANCHEE, ET GARDEE — sans quoi elle se reintroduit en silence.

    L'echantillon valait 400 sur 3 750 avec `random.seed(0)` : les MEMES 89 %
    n'etaient jamais verifies, execution apres execution. Une graine fixe ne fait
    pas d'un echantillon une couverture, elle fait d'un angle mort un angle mort
    STABLE.

    Sa justification — « le seul controle dont le cout croit vraiment avec le
    corpus » — est demolie par la mesure : le controle COMPLET des 3 750
    identifiants tient en une requete nGQL, `mesure` a **0,053 s** contre 0,008 s
    pour 400. Soit 6,6 fois le cout pour 9,4 fois la couverture.

    Et ce garde etait NECESSAIRE : `mesure`, remettre `identifiants[:400]`
    laissait la suite ENTIEREMENT VERTE. Retirer un echantillonnage sans garder
    son absence, c'est le laisser revenir au premier lot qui trouvera le controle
    lent — et il le trouvera lent, puisque personne ne remesure.
    """

    def test_every_identifier_reaches_the_query(self):
        metas = [{"element_id": f"{i:010x}"} for i in range(500)]
        session = _Session({"MATCH (v) WHERE id(v) IN": _Resultat([[500]])})
        assert _verifier_les_ancres(session, metas) == []
        requete = next(r for r in session.requetes if "MATCH (v) WHERE id(v) IN" in r)
        # LE POINT : les 500, et non les 400 premiers.
        for meta in metas:
            assert f'"{meta["element_id"]}"' in requete, (
                f"{meta['element_id']} absent de la requete : un echantillonnage a ete reintroduit"
            )

    def test_the_count_asserted_is_the_full_count(self):
        """Le temoin du precedent : le denominateur aussi doit etre complet.

        Un echantillonnage qui reduirait les deux cotes — la requete ET le
        compte attendu — resterait vert sur le test ci-dessus s'il ne verifiait
        que l'egalite. C'est pourquoi celui-la asserte les identifiants un par un
        et celui-ci le denominateur.
        """
        metas = [{"element_id": f"{i:010x}"} for i in range(500)]
        session = _Session({"MATCH (v) WHERE id(v) IN": _Resultat([[400]])})
        anomalies = _verifier_les_ancres(session, metas)
        assert anomalies == ["100 ancres absentes du graphe"]

    def test_a_missing_anchor_is_reported(self):
        metas = [{"element_id": "a" * 10}, {"element_id": "b" * 10}]
        session = _Session({"MATCH (v) WHERE id(v) IN": _Resultat([[1]])})
        assert _verifier_les_ancres(session, metas) == ["1 ancres absentes du graphe"]

    def test_an_empty_metadata_list_is_reported_and_not_read_as_a_success(self):
        assert _verifier_les_ancres(_Session({}), []) == ["aucun element_id a verifier"]

    def test_a_rejected_query_is_reported(self):
        session = _Session({"MATCH (v) WHERE id(v) IN": _Resultat([], succes=False)})
        assert _verifier_les_ancres(session, [{"element_id": "a" * 10}]) == [
            "comptage des ancres impossible"
        ]


# --- Le site d'appel de `_verifier_le_graphe` ---------------------------------
#
# Les contres de ce module sont gardes un par un, en fonctions pures. LEUR SITE
# D'APPEL ne l'etait pas, et c'est la que se decide s'ils rapportent ou non :
# `_verifier_le_graphe` lit, compte, et pose l'anomalie. `mesure` : remplacer
# `sans_fin = sommets_sans_profondeur(fins)` par `sans_fin = 0` laissait la suite
# entierement verte, alors que ce controle est celui que ce lot AJOUTE.
#
# `nebula3` n'est pas dans le venv du depot : il est bouchonne comme un vrai
# arbre de modules, par `monkeypatch.setitem`, donc REVOQUE a la fin du test —
# contrairement a un bouchon pose a la main dans `sys.modules`, il ne survit pas
# et l'ordre des tests ne devient pas significatif.


class _PoolBouchonne:
    """`ConnectionPool` bouchonne, rendant la session qu'on lui a donnee."""

    session: object

    def init(self, adresses: object, config: object) -> bool:
        return True

    def get_session(self, utilisateur: str, mot_de_passe: str) -> object:
        return type(self).session

    def close(self) -> None:
        return None


def _bouchonner_nebula3(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    """Pose un arbre `nebula3` minimal, portant ce que le module importe."""
    from types import SimpleNamespace

    _PoolBouchonne.session = session
    # `SimpleNamespace` et non `ModuleType` : l'import `from nebula3.Config import
    # Config` ne fait qu'une lecture d'attribut sur l'entree de `sys.modules`, et
    # un espace de noms porte ses attributs dans son type — donc ni `setattr`, que
    # `ruff` refuse, ni `type: ignore`, que la regle du depot interdit.
    for nom, module in [
        ("nebula3", SimpleNamespace()),
        ("nebula3.Config", SimpleNamespace(Config=type("Config", (), {}))),
        ("nebula3.gclient", SimpleNamespace()),
        ("nebula3.gclient.net", SimpleNamespace(ConnectionPool=_PoolBouchonne)),
    ]:
        monkeypatch.setitem(sys.modules, nom, module)


def _session_dun_graphe_sain(
    page_no_end: int | None,
    depth: int | None = 0,
    colonnes_des_tags: tuple[str, ...] = VERTEX_PROPERTIES,
) -> _Session:
    """Une session dont TOUS les controles passent, sauf ce qu'on lui fait porter.

    Le graphe rendu est sain de bout en bout — une arete `PARENT_OF` ordonnee, un
    tag `Document` complet, les onze tags d'element au schema complet, une image
    avec son URL, l'ancre presente. Seules les valeurs de `page_no_end`, de
    `depth` et le schema des tags sont a la main de l'appelant.

    Un montage qui rendrait un graphe vide produirait des anomalies pour d'autres
    raisons, et l'assertion « une seule anomalie, celle-ci » ne vaudrait plus
    rien.

    **`colonnes_des_tags` est arrive avec le registre 4.29.e.** Le controle lit
    desormais `DESCRIBE TAG` sur les tags d'ELEMENT avant de compter les NULL,
    pour distinguer « la colonne n'existe pas » de « les donnees sont a NULL ».
    Le bouchon devait donc pouvoir repondre a ce `DESCRIBE` — sans quoi il
    simulait un graphe dont AUCUN tag n'a la colonne, et les temoins d'un graphe
    sain rougissaient. *Verifie ton harnais avant de croire ton rouge.*

    L'ordre des cles compte : `DESCRIBE TAG Document` est teste AVANT le motif
    generique, `_Session` rendant la premiere correspondance.
    """
    return _Session(
        {
            "PARENT_OF": _Resultat([["docA", "e0000000ab", 0, 1]]),
            "page_no_end AS valeur": _Resultat([[page_no_end]]),
            "depth AS valeur": _Resultat([[depth]]),
            "DESCRIBE TAG Document": _Resultat([[c] for c in DOCUMENT_PROPERTIES]),
            "DESCRIBE TAG ": _Resultat([[c] for c in colonnes_des_tags]),
            "minio_url AS url": _Resultat([["http://minio:9000/documents/a.png"]]),
            "MATCH (v) WHERE id(v) IN": _Resultat([[1]]),
        }
    )


ANCRES = [{"element_id": "e0000000ab"}]


class TestLeControleDePageNoEndEstGardeASonSiteDAppel:
    """Registre 4.22 : la colonne migre en place, les DONNEES non.

    Un `ALTER TAG ... ADD (page_no_end int)` laisse a NULL tous les sommets deja
    ecrits, et seule une reingestion les renseigne. Sans ce compteur, l'agent
    lirait une page de fin sur les sommets recents et NULL sur les anciens, et
    rien ne distinguerait « cet element tient sur une page » de « on ne sait pas
    ou il finit ».

    Le controle etait livre. **Rien ne le gardait**, alors que `verify_contract`
    est importable cote hote depuis le lot 3 et que `test_verify_contract.py`
    existe : c'est le site d'appel qui manquait, pas la possibilite.
    """

    def test_un_sommet_sans_page_de_fin_est_rapporte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LE GARDE, sur un graphe sain PARTOUT AILLEURS.

        L'assertion porte sur UNE anomalie et une seule : c'est ce qui prouve que
        celle-ci vient bien du controle de `page_no_end` et non d'un montage
        approximatif.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=None))

        anomalies = _verifier_le_graphe(ANCRES)

        assert len(anomalies) == 1, anomalies
        assert "page_no_end" in anomalies[0], anomalies[0]
        assert "1 sommets sur 1" in anomalies[0], anomalies[0]

    def test_un_graphe_entierement_migre_ne_rapporte_rien(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN, et sans lui un `sans_fin = len(fins)` passerait le garde."""
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=7))

        assert _verifier_le_graphe(ANCRES) == []

    def test_une_page_de_fin_a_zero_est_une_valeur_et_non_une_absence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le second temoin, et c'est le piege que la colonne tend.

        Un compteur ecrit `if not fin` — la faute naturelle — rougirait sur un
        graphe sain. C'est exactement le temoin que `depth` porte deja depuis le
        lot 3, sur la colonne d'a cote.

        Il dit aussi ce que ce controle NE VOIT PAS, et qui est consigne au
        registre : un 0 y passe pour une valeur, alors que `ngql.py` ecrit qu'un 0
        dirait « page inconnue ». Ce test verrouille le comportement livre ; il ne
        pretend pas que ce comportement soit le dernier mot.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=0))

        assert _verifier_le_graphe(ANCRES) == []

    def test_une_colonne_absente_des_tags_prescrit_le_redemarrage_au_site_d_appel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE GARDE DU SITE D'APPEL pour le registre 4.29.e.

        La fonction pure est gardee ailleurs dans ce fichier. Ce test-ci prouve
        que `_verifier_le_graphe` LA TRAVERSE — c'est-a-dire qu'il lit
        `DESCRIBE TAG` sur les tags d'element avant de compter les NULL. Sans
        cela, la distinction existerait dans une fonction que la production
        n'appelle pas, ce qui est le defaut central de ce lot.

        Le montage reproduit le cas MESURE sur ce poste : la colonne n'est sur
        aucun tag, donc tous les sommets rendent NULL — les deux conditions sont
        vraies a la fois, et c'est la premiere qui doit parler.
        """
        sans_la_colonne = tuple(c for c in VERTEX_PROPERTIES if c != "page_no_end")
        _bouchonner_nebula3(
            monkeypatch,
            _session_dun_graphe_sain(page_no_end=None, colonnes_des_tags=sans_la_colonne),
        )

        anomalies = _verifier_le_graphe(ANCRES)

        assert len(anomalies) == 1, anomalies
        assert "N'EXISTE PAS" in anomalies[0], anomalies[0]
        assert "REDEMARRER" in anomalies[0], (
            f"le site d'appel prescrit encore la seule reingestion : {anomalies[0]}"
        )

    def test_le_controle_de_depth_est_garde_au_meme_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La colonne d'a cote, meme site d'appel, meme angle mort.

        `sans_depth = sommets_sans_profondeur(profondeurs)` -> `0` laissait aussi
        la suite verte. Le controle vient du lot 3, pas de celui-ci ; le garde est
        ecrit ici parce qu'il partage exactement ce site et que l'y laisser seul
        aurait ete refaire le defaut d'une colonne.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=7, depth=None))

        anomalies = _verifier_le_graphe(ANCRES)

        assert len(anomalies) == 1, anomalies
        assert "depth" in anomalies[0], anomalies[0]

    def test_le_bouchon_rend_bien_ce_que_le_code_de_lecture_attend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LE TEMOIN DU HARNAIS. *Verifie ton harnais avant de croire ton rouge.*

        Si le bouchon ne repondait a AUCUNE des requetes, `_lire` rendrait des
        listes vides partout : `fins` serait vide, `sans_fin` vaudrait 0, et le
        temoin « un graphe migre ne rapporte rien » serait vert pour la mauvaise
        raison — parce que rien n'a ete lu, non parce que tout est renseigne.
        """
        session = _session_dun_graphe_sain(page_no_end=7)
        _bouchonner_nebula3(monkeypatch, session)

        _verifier_le_graphe(ANCRES)

        lues = " ".join(session.requetes)
        assert "page_no_end AS valeur" in lues, session.requetes
        assert "depth AS valeur" in lues, session.requetes
        assert session.relachee, (
            "la session n'est pas relachee : le `finally` du module ne tourne pas, "
            "donc le montage ne reproduit pas la sortie reelle"
        )


class TestLesDeuxEtatsDUneColonneNeSeConfondentPlus:
    """Registre 4.29.e — LE MESSAGE PRESCRIVAIT LE GESTE QUI NE SUFFIT PAS.

    Quand des sommets n'avaient pas de `page_no_end`, le controle disait « le tag
    a migre, les donnees non — il faut une reingestion pour peupler la colonne ».
    Dans le cas mesure le 1er septembre 2026, **le tag n'avait PAS migre** :
    `DESCRIBE TAG Paragraph` rendait cinq colonnes, sans `page_no_end`. Le
    message decrivait donc un etat qui n'etait pas celui du poste.

    Et ce n'est pas une imprecision de vocabulaire : c'est `init_schema()` qui
    joue les `ALTER TAG`, et il n'est appele **qu'au demarrage du service**. Un
    operateur qui lit « il faut une reingestion » et s'execute ecrit contre un
    tag qui n'a pas la colonne, et **le graphd rejette chaque INSERT**. Le geste
    complet est « redemarrer `docling-service`, PUIS reingerer ».

    Le controle lit desormais `DESCRIBE TAG` **avant** de compter les NULL, et
    rend deux anomalies distinctes. Le temoin que le registre reclamait est le
    dernier test de cette classe : *les deux etats doivent rendre des messages
    DIFFERENTS, sans quoi la distinction serait faite dans le code et perdue dans
    la sortie.*
    """

    TAGS = ["Paragraph", "SectionHeader"]

    def test_la_colonne_absente_prescrit_le_redemarrage(self) -> None:
        message = anomalie_de_colonne("page_no_end", self.TAGS, 0, 0, "registre 4.22")

        assert message is not None
        assert "N'EXISTE PAS" in message, message
        assert "REDEMARRER" in message, (
            f"le message ne prescrit pas le redemarrage : {message}. Une "
            "reingestion seule ecrirait contre un tag sans la colonne"
        )
        assert "Paragraph" in message and "SectionHeader" in message, message

    def test_la_colonne_presente_mais_vide_prescrit_la_reingestion(self) -> None:
        message = anomalie_de_colonne("page_no_end", [], 15173, 15173, "registre 4.22")

        assert message is not None
        assert "EXISTE" in message, message
        assert "reingestion" in message, message
        assert "15173" in message, message

    def test_une_colonne_presente_et_renseignee_n_est_pas_une_anomalie(self) -> None:
        """LE TEMOIN : sans lui, un controle qui rougit toujours passerait."""
        assert anomalie_de_colonne("page_no_end", [], 0, 15173, "registre 4.22") is None

    def test_l_absence_de_colonne_prime_sur_le_comptage_des_null(self) -> None:
        """L'ordre des deux branches est le sujet du constat.

        Quand la colonne n'existe pas, TOUS les sommets rendent NULL : les deux
        conditions sont vraies en meme temps, et c'est exactement le cas mesure
        sur ce poste. Si le comptage passait d'abord, le message prescrirait la
        reingestion — le defaut d'origine, a l'identique.
        """
        message = anomalie_de_colonne("page_no_end", self.TAGS, 15173, 15173, "registre 4.22")

        assert message is not None
        assert "REDEMARRER" in message, (
            f"les deux etats sont vrais et c'est la REINGESTION qui a ete prescrite : {message}"
        )

    def test_les_deux_etats_rendent_des_messages_differents(self) -> None:
        """LE TEMOIN QUE LE REGISTRE RECLAMAIT, et il est le plus important.

        Sans lui, la distinction pourrait etre faite dans le code et perdue dans
        la sortie — deux branches qui rendent la meme phrase. C'est l'operateur
        qui lit la phrase, pas la branche.
        """
        absente = anomalie_de_colonne("page_no_end", self.TAGS, 15173, 15173, "registre 4.22")
        vide = anomalie_de_colonne("page_no_end", [], 15173, 15173, "registre 4.22")

        assert absente != vide, (
            "les deux etats rendent la meme phrase : la distinction est faite "
            "dans le code et perdue dans la sortie"
        )
        assert "REDEMARRER" in str(absente) and "REDEMARRER" not in str(vide), (
            f"le geste ne distingue pas les deux etats :\n  absente = {absente}\n  vide    = {vide}"
        )


class TestUnDescribeEnEchecCompteCommeUneColonneAbsente:
    """Le TREIZIEME garde creux du chantier, et il etait dans la fonction qui
    ENONCE l'invariant.

    Le docstring de `_lire_les_tags_sans_la_colonne` ecrit : « Un `DESCRIBE` en
    echec est compte comme "colonne absente" : ne pas pouvoir constater n'est pas
    constater que tout va bien. » C'est la lecon du cinquieme trou du lot 3
    (registre 4.4), et `_verifier_le_tag_document` la porte avec son propre test
    — `test_a_rejected_describe_is_reported_and_not_read_as_a_success`.

    **Cette fonction-ci ne l'avait pas.** `mesure` sur le code livre, AVANT ce
    garde :
    `if colonne not in colonnes` remplace par `if colonnes and colonne not in
    colonnes` laisse `make all` en `rc=0`, 857 tests, ZERO rouge. Le motif est
    celui des douze gardes creux precedents : *le test observe une absence.* Le
    compte de douze est derive au registre 4.31.B4 ; ne le recopie pas, renvoie-y.

    Ce que la mutation coute, et ce n'est pas une elegance : un graphd qui refuse
    le `DESCRIBE` rendrait `tags_sans_la_colonne == []`, donc
    `anomalie_de_colonne` prendrait sa SECONDE branche et prescrirait
    « reingerez » la ou il faut « redemarrez PUIS reingerez ». C'est le registre
    4.29.e rouvert par le commit qui le ferme.
    """

    TAGS = sorted(set(TAG_MAP.values()))
    MIGRE = [[colonne] for colonne in VERTEX_PROPERTIES]
    AVANT_MIGRATION = [[c] for c in VERTEX_PROPERTIES if c != "page_no_end"]

    def _describe(self, reponses: dict[str, _Resultat]) -> _Session:
        return _Session(reponses)

    def test_un_schema_entierement_migre_ne_rend_aucun_tag(self) -> None:
        """LE TEMOIN, et sans lui un garde qui rend toujours tout passerait."""
        session = self._describe({"DESCRIBE TAG": _Resultat(self.MIGRE)})

        assert _lire_les_tags_sans_la_colonne(session, "page_no_end") == []

    def test_un_tag_sans_la_colonne_est_nomme_et_lui_seul(self) -> None:
        session = self._describe(
            {
                "DESCRIBE TAG Table": _Resultat(self.AVANT_MIGRATION),
                "DESCRIBE TAG": _Resultat(self.MIGRE),
            }
        )

        assert _lire_les_tags_sans_la_colonne(session, "page_no_end") == ["Table"]

    def test_un_describe_rejete_est_compte_comme_colonne_absente(self) -> None:
        """LE GARDE. Un graphd qui refuse le `DESCRIBE` ne dit pas que tout va bien.

        `_lire` journalise l'echec et rend une liste vide : la fonction doit lire
        cette liste vide comme « je ne sais pas », donc comme une absence, et non
        comme « aucune colonne ne manque ».
        """
        session = self._describe({"DESCRIBE TAG": _Resultat([], succes=False)})

        manquants = _lire_les_tags_sans_la_colonne(session, "page_no_end")

        assert manquants == self.TAGS, (
            "un DESCRIBE en echec a ete lu comme « la colonne est la » : "
            f"{manquants}. Ne pas pouvoir constater n'est pas constater que "
            "tout va bien (registre 4.4, cinquieme trou)"
        )

    def test_un_seul_describe_rejete_suffit_a_nommer_son_tag(self) -> None:
        """La MOITIE que le temoin ne couvre pas : un echec partiel.

        Sans ce cas, un garde qui ne verrait que « tous les DESCRIBE echouent »
        resterait vert sur un graphd qui n'en refuse qu'un — l'etat le plus
        plausible, un tag verrouille par une migration en cours.
        """
        session = self._describe(
            {
                "DESCRIBE TAG SectionHeader": _Resultat([], succes=False),
                "DESCRIBE TAG": _Resultat(self.MIGRE),
            }
        )

        assert _lire_les_tags_sans_la_colonne(session, "page_no_end") == ["SectionHeader"]

    def test_l_anomalie_qui_en_decoule_prescrit_le_redemarrage(self) -> None:
        """LE TEMOIN DE BOUT EN BOUT, et c'est lui qui dit ce que le garde protege.

        Le garde ne vaut que par ce que son appelant en fait : un `DESCRIBE` en
        echec doit conduire au message « redemarrez PUIS reingerez », jamais a
        « reingerez ». Sans cette assertion, la fonction pourrait rendre la bonne
        liste et le rapport rester faux.
        """
        session = self._describe({"DESCRIBE TAG": _Resultat([], succes=False)})

        message = anomalie_de_colonne(
            "page_no_end",
            _lire_les_tags_sans_la_colonne(session, "page_no_end"),
            15173,
            15173,
            "registre 4.22",
        )

        assert message is not None
        assert "REDEMARRER" in message, (
            f"un DESCRIBE en echec prescrit la reingestion seule : {message}"
        )
