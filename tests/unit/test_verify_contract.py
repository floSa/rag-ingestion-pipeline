"""Tests des controles du contrat d'interface.

Chaque test construit un index qui viole une exigence du contrat (ordre de
`sequence`, `source_path`, modele des vecteurs, URL des images, jeux de
chunks...) et verifie que `verify_contract` le signale. Chaque controle a aussi
un cas sain qui ne doit rien signaler.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from dagster import DagsterInstance, build_sensor_context

from src.docling_service.elements import TAG_MAP
from src.docling_service.ngql import DOCUMENT_PROPERTIES, VERTEX_PROPERTIES
from src.pipeline.factory import PREFIXE_REINGESTION, build_source
from src.pipeline.sources import SourceConfig
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
    """L'ordre de lecture (exigence 4), distinct de l'unicite sous un parent.

    Une numerotation aleatoire distincte par parent satisferait l'unicite. La
    propriete qui porte l'ordre est : trie par `sequence`, `page_no` ne decroit
    jamais (registre 6.16).
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
        """Reserve 1 du registre 6.16 : `sequence` n'est pas globalement monotone.

        Deux documents entrelaces ne doivent produire aucune inversion ; sans le
        groupement par document, ce cas en rendrait deux.
        """
        aretes = [("a", 0, 1), ("b", 0, 1), ("a", 1, 2), ("b", 1, 2)]
        assert inversions_de_page(aretes) == []

    def test_gaps_are_not_inversions(self):
        """Reserves 2 et 3 : `sequence` n'est pas contigue sous un parent.

        Les valeurs reprennent le plus grand ecart mesure sous un meme parent,
        entre les rangs 203 et 1197. Un controle qui exigerait la contiguite
        echouerait sur ce graphe sain. Chiffres de reference : docstring
        d'`inversions_de_page`.
        """
        assert inversions_de_page([("doc", 203, 1), ("doc", 1197, 4)]) == []

    def test_a_repeated_sequence_is_not_an_inversion_by_itself(self):
        assert inversions_de_page([("doc", 4, 2), ("doc", 4, 2)]) == []

    def test_no_edge_no_inversion(self):
        assert inversions_de_page([]) == []


class TestRacineDeChaqueElement:
    """Le rattachement de chaque element a son document.

    La classe suivante teste la composition avec le controle d'ordre : sans
    remontee, ce controle rendrait zero anomalie sur un graphe fautif.
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
    """Le controle d'ordre passe par le rattachement au document (mutation M12).

    `inversions_de_page` groupe par document. Si le rattachement rendait chaque
    element a lui-meme, chaque groupe ne porterait qu'une arete, et le controle
    rendrait zero anomalie sur un graphe fautif. Tester
    `racine_de_chaque_element` seule ne couvrirait pas ce cas : le test porte
    donc sur la composition.
    """

    # Deux documents, chacun a deux niveaux, et une inversion de page dans
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
        """Cas de controle : sans rattachement, la meme inversion est invisible.

        Chaque element forme alors son propre groupe. C'est ce que produirait la
        mutation, d'ou le test sur la composition.
        """
        non_rattachees = [(element, sequence, page) for element, sequence, page in self.ARETES]
        assert inversions_de_page(non_rattachees) == []

    def test_the_second_document_is_not_polluted_by_the_first(self):
        """Un document sain ne produit aucune inversion."""
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
    """Le compte des sommets visuels sans URL.

    L'agent ne sert que ce que le graphe reference : une image sans URL reste
    inatteignable.
    """

    def test_every_image_carries_an_url(self):
        assert images_sans_url(["http://stockage-de-test:8333/documents/a.png"]) == 0

    def test_an_empty_url_is_counted(self):
        assert images_sans_url(["", "http://stockage-de-test:8333/documents/a.png", ""]) == 2

    def test_a_null_url_reads_as_absent(self):
        assert images_sans_url([None, ""]) == 2

    def test_no_image_no_anomaly(self):
        assert images_sans_url([]) == 0


class TestLeModuleResteImportableSansStore:
    """Le module s'importe sans `chromadb` ni `nebula3`, ce qui le rend testable."""

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
    """Un chunk manquant se voit sur l'element entier, pas sur un chunk isole.

    L'agent reconstitue un element decoupe en concatenant ses chunks dans l'ordre
    de `chunk_index`. Chaque chunk present satisfait `0 <= index < count` meme
    quand un frere a disparu : le controle de bornes ne voit pas le trou.

    `mesure` le 31 aout 2026 sur l'index complet (4 365 chunks, 3 750
    elements) : `chunks_incoherents` rendait 0 chunk fautif, et deux elements
    avaient un jeu troue, `aa3de10738` (chunk_count 7, index 4 manquant) et
    `eb52c4ec8f` (chunk_count 4, index 3 manquant).
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
        """Sur le cas reel, le controle de bornes ne signale rien.

        Les deux controles ne se recouvrent pas : c'est pourquoi le second
        existe.
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
        """Seul l'element fautif est nomme."""
        metas = self.REEL + [
            {"element_id": "sain", "chunk_index": i, "chunk_count": 2} for i in (0, 1)
        ]
        assert [troue[0] for troue in jeux_de_chunks_incomplets(metas)] == ["aa3de10738"]

    def test_no_chunk_no_anomaly(self):
        assert jeux_de_chunks_incomplets([]) == []


class TestSommetsSansProfondeur:
    """Registre 4.11 : le schema migre en place, les donnees non.

    Un `ALTER TAG ... ADD (depth int)` laisse a NULL tous les sommets deja
    ecrits, et seule une reingestion les renseigne. Le compte distingue un index
    a moitie migre, ou l'agent ne pourrait pas separer « profondeur 0 » de
    « profondeur inconnue ».
    """

    def test_all_depths_written_is_no_anomaly(self):
        assert sommets_sans_profondeur([0, 1, 2, 3, 4, 5]) == 0

    def test_a_null_depth_is_counted(self):
        assert sommets_sans_profondeur([0, None, 2]) == 1

    def test_a_fully_unmigrated_index_is_fully_counted(self):
        """Le cas mesure au §4.11 : 188 sommets sur 188 a NULL apres l'ALTER."""
        assert sommets_sans_profondeur([None] * 188) == 188

    def test_zero_is_a_depth_and_not_an_absence(self):
        """`depth = 0` est une valeur (racine d'un document), pas un trou.

        Un compteur ecrit `if not profondeur` compterait les racines comme non
        migrees, et echouerait sur un graphe sain (registre 4.24).
        """
        assert sommets_sans_profondeur([0, 0, 0]) == 0

    def test_no_vertex_no_anomaly(self):
        assert sommets_sans_profondeur([]) == 0


# --- Session nGQL bouchonnee -------------------------------------------------
#
# Elle rend des cellules qui se comportent comme les valeurs Nebula : chacune
# repond a `is_null()` et `as_int()` / `as_string()`. Le code de lecture reel
# (dont le test `is_null()` sur `sequence`) est ainsi exerce. Un bouchon pose
# plus haut, sur la liste d'aretes deja lue, ne l'exercerait pas.


class _Cellule:
    """Une valeur nGQL : nulle, entiere ou chaine."""

    def __init__(self, valeur: int | str | None) -> None:
        self._valeur = valeur

    def is_null(self) -> bool:
        return self._valeur is None

    def as_int(self) -> int:
        if self._valeur is None:
            # Comme le vrai client : `as_int()` sur une valeur nulle leve. Sans
            # cette levee, le bouchon serait plus indulgent que la production.
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
        `finally`."""
        self.relachee = True


class TestUneSequenceAbsenteSeRAPPORTE:
    """L'exigence 4 est « sequence absente ou non monotone » : une `sequence`
    absente est rapportee, sans lever.

    Sur une valeur nulle, `as_int()` leve `InvalidValueTypeException`
    (`mesure` sur un space jetable). Sans le test `is_null()`, le rapport
    s'arreterait sur une trace Python au lieu de signaler l'anomalie.
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
        """Une arete sans `sequence` est rapportee, et l'appel ne leve pas."""
        session = self._session([["docA", "t1", 0, 1], ["t1", "p1", None, 2]])
        aretes, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == ["p1"]
        # L'arete sans ordre est ecartee du controle d'ordre, pas comptee a zero :
        # lui donner sequence 0 en ferait une premiere arete et pourrait
        # fabriquer une fausse inversion.
        assert [arete[0] for arete in aretes] == ["docA"]

    def test_every_missing_sequence_is_named(self):
        session = self._session([["docA", "a", None, 1], ["docA", "b", None, 2]])
        _, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == ["a", "b"]

    def test_a_null_page_is_still_tolerated_as_before(self):
        """Un `page_no` nul est lu comme 0, sans lever."""
        session = self._session([["docA", "t1", 0, None]])
        aretes, sans_sequence = _lire_les_aretes(session)
        assert sans_sequence == []
        assert aretes == [("docA", 0, 0)]

    def test_the_bouchon_would_raise_on_a_null_int(self):
        """Verifie le bouchon : `_Cellule.as_int()` leve sur une valeur nulle.

        Si le bouchon etait indulgent, le test sur `sequence` absente passerait
        meme sans le test `is_null()` dans le code.
        """
        with pytest.raises(TypeError):
            _Cellule(None).as_int()


class TestLeTagDocumentEstCOUVERT:
    """Les colonnes du tag `Document`, que `_verifier_les_tags` ne couvre pas.

    `NebulaWriter._verifier_les_tags` ne controle que les 11 tags d'element. Les
    `ALTER TAG Document ADD` sont `required=False` (« la colonne existe deja »
    est le cas nominal), donc une migration refusee n'y leve rien.

    Parmi ces colonnes, `source_path` est l'exigence 3 du contrat : l'identite
    d'un document, qui distingue les deux `Preface.html` du corpus.
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
        """Une colonne en plus n'est pas une migration manquee."""
        session = _Session(
            {"DESCRIBE TAG Document": _Resultat([*self.COMPLET, ["colonne_future"]])}
        )
        assert _verifier_le_tag_document(session) == []

    def test_a_rejected_describe_is_reported_and_not_read_as_a_success(self):
        """Un DESCRIBE en echec est rapporte, et non lu comme « aucune manquante »."""
        session = _Session({"DESCRIBE TAG Document": _Resultat([], succes=False)})
        anomalies = _verifier_le_tag_document(session)
        assert len(anomalies) == 1
        assert "n'est pas verifiable" in anomalies[0]


# --- Un index VIDE, en sous-processus ---------------------------------------
#
# Le code de sortie est le comportement teste : c'est ce que lit un `&&` dans
# une procedure de pre-deploiement. Un import direct ne verrait qu'un
# `SystemExit` leve. Bouchonner `chromadb` dans `sys.modules` laisserait le
# bouchon en place pour les tests suivants.

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
    """Un index vide sort en code 1.

    Une purge, une ingestion en echec ou un nom de collection errone ne doivent
    pas passer pour « Contrat respecte ».

    `nebula3` n'est pas bouchonne : le module doit sortir avant de toucher au
    graphe. S'il allait plus loin, il echouerait sur l'import manquant, lui
    aussi en code 1 ; d'ou l'assertion sur le code de sortie et sur le message.
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
        """Le message nomme les causes probables."""
        acheve = _verifier_un_index_vide(tmp_path)
        assert "purge" in acheve.stdout
        assert "collection" in acheve.stdout

    def test_it_did_not_reach_the_graph(self, tmp_path):
        """La sortie vient de l'index vide, pas d'un `nebula3` manquant.

        Sans ce test, un `ModuleNotFoundError` suffirait a produire le code 1
        attendu ci-dessus.
        """
        acheve = _verifier_un_index_vide(tmp_path)
        assert "nebula3" not in acheve.stderr
        assert "Traceback" not in acheve.stderr


class TestToutesLesAncresSontVerifieesEtPasUnEchantillon:
    """Toutes les ancres sont verifiees, sans echantillon.

    Un echantillon de 400 sur 3 750 a graine fixe laissait les memes 89 %
    jamais verifies. Le controle complet coute une requete nGQL : 0,053 s
    contre 0,008 s pour 400 (`mesure` le 31 aout 2026). Ce test echoue si un
    echantillonnage (par exemple `identifiants[:400]`) est reintroduit.
    """

    def test_every_identifier_reaches_the_query(self):
        metas = [{"element_id": f"{i:010x}"} for i in range(500)]
        session = _Session({"MATCH (v) WHERE id(v) IN": _Resultat([[500]])})
        assert _verifier_les_ancres(session, metas) == []
        requete = next(r for r in session.requetes if "MATCH (v) WHERE id(v) IN" in r)
        # Les 500, et non les 400 premiers.
        for meta in metas:
            assert f'"{meta["element_id"]}"' in requete, (
                f"{meta['element_id']} absent de la requete : un echantillonnage a ete reintroduit"
            )

    def test_the_count_asserted_is_the_full_count(self):
        """Le denominateur aussi porte sur tous les identifiants.

        Un echantillonnage qui reduirait a la fois la requete et le compte
        attendu garderait l'egalite : le test precedent verifie donc les
        identifiants un par un, et celui-ci le denominateur.
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
# Les controles sont testes un par un en fonctions pures ci-dessus. Ces tests-ci
# portent sur leur site d'appel, `_verifier_le_graphe`, qui lit, compte et pose
# l'anomalie : sans eux, remplacer `sans_fin = sommets_sans_profondeur(fins)`
# par `sans_fin = 0` passerait inapercu.
#
# `nebula3` n'est pas dans le venv du depot : il est bouchonne par
# `monkeypatch.setitem`, donc retire a la fin de chaque test.


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
    """Une session dont tous les controles passent, sauf ce que l'appelant fixe.

    Le graphe rendu est sain : une arete `PARENT_OF` ordonnee, un tag `Document`
    complet, les onze tags d'element au schema complet, une image avec son URL,
    l'ancre presente. L'appelant fixe les valeurs de `page_no_end` et de
    `depth`, et le schema des tags. Ainsi l'assertion « une seule anomalie,
    celle-ci » a un sens.

    `colonnes_des_tags` repond au `DESCRIBE TAG` des tags d'element, lu avant le
    comptage des NULL (registre 4.29.e).

    L'ordre des cles compte : `DESCRIBE TAG Document` precede le motif
    generique, `_Session` rendant la premiere correspondance.
    """
    return _Session(
        {
            "PARENT_OF": _Resultat([["docA", "e0000000ab", 0, 1]]),
            "page_no_end AS valeur": _Resultat([[page_no_end]]),
            "depth AS valeur": _Resultat([[depth]]),
            "DESCRIBE TAG Document": _Resultat([[c] for c in DOCUMENT_PROPERTIES]),
            "DESCRIBE TAG ": _Resultat([[c] for c in colonnes_des_tags]),
            "media_url AS url": _Resultat(
                [["http://stockage-de-test:8333/documents/a.png", "a.png"]]
            ),
            "MATCH (v) WHERE id(v) IN": _Resultat([[1]]),
        }
    )


ANCRES = [{"element_id": "e0000000ab"}]


class TestLeControleDePageNoEndEstGardeASonSiteDAppel:
    """Registre 4.22 : la colonne migre en place, les donnees non.

    Un `ALTER TAG ... ADD (page_no_end int)` laisse a NULL tous les sommets deja
    ecrits, et seule une reingestion les renseigne. Sans ce compte, rien ne
    distinguerait « cet element tient sur une page » de « on ne sait pas ou il
    finit ».
    """

    def test_un_sommet_sans_page_de_fin_est_rapporte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Des `page_no_end` NULL sur un graphe sain par ailleurs : une anomalie.

        L'assertion porte sur une anomalie et une seule, ce qui prouve qu'elle
        vient du controle de `page_no_end` et non du montage.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=None))

        anomalies = _verifier_le_graphe(ANCRES)

        assert len(anomalies) == 1, anomalies
        assert "page_no_end" in anomalies[0], anomalies[0]
        assert "1 sommets sur 1" in anomalies[0], anomalies[0]

    def test_un_graphe_entierement_migre_ne_rapporte_rien(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un graphe migre ne rapporte rien (exclut `sans_fin = len(fins)`)."""
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=7))

        assert _verifier_le_graphe(ANCRES) == []

    def test_une_page_de_fin_a_zero_est_une_valeur_et_non_une_absence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un `page_no_end` a 0 est compte comme une valeur.

        Un compteur ecrit `if not fin` echouerait sur un graphe sain (meme cas
        que `depth`). Limite connue, consignee au registre : `ngql.py` dit
        qu'un 0 signifie « page inconnue », et ce controle ne le voit pas. Ce
        test fixe le comportement actuel.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=0))

        assert _verifier_le_graphe(ANCRES) == []

    def test_une_colonne_absente_des_tags_prescrit_le_redemarrage_au_site_d_appel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_verifier_le_graphe` lit `DESCRIBE TAG` avant de compter les NULL.

        Registre 4.29.e, au site d'appel : la fonction pure est testee plus bas.
        Le montage reproduit le cas mesure : la colonne n'est sur aucun tag, donc
        tous les sommets rendent NULL. Les deux conditions sont vraies, et c'est
        la colonne absente qui doit etre rapportee.
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
        """Meme test pour `depth`, au meme site d'appel.

        Sans lui, remplacer `sans_depth = sommets_sans_profondeur(profondeurs)`
        par `0` passerait inapercu.
        """
        _bouchonner_nebula3(monkeypatch, _session_dun_graphe_sain(page_no_end=7, depth=None))

        anomalies = _verifier_le_graphe(ANCRES)

        assert len(anomalies) == 1, anomalies
        assert "depth" in anomalies[0], anomalies[0]

    def test_le_bouchon_rend_bien_ce_que_le_code_de_lecture_attend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifie le bouchon : les requetes attendues recoivent une reponse.

        Si le bouchon ne repondait a aucune requete, `_lire` rendrait des listes
        vides, `sans_fin` vaudrait 0, et « un graphe migre ne rapporte rien »
        passerait parce que rien n'a ete lu.
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
    """Registre 4.29.e : colonne absente et donnees a NULL rendent deux messages.

    `init_schema()` joue les `ALTER TAG`, et il n'est appele qu'au demarrage du
    service. Si la colonne n'existe pas, une reingestion seule ecrit contre un
    tag sans la colonne, et le graphd rejette chaque INSERT : il faut
    redemarrer `docling-service`, puis reingerer. Si la colonne existe et que
    les valeurs sont NULL, une reingestion suffit.

    Cas constate le 1er septembre 2026 : `DESCRIBE TAG Paragraph` rendait cinq
    colonnes, sans `page_no_end`. Le dernier test de cette classe verifie que
    les deux etats produisent des messages differents.
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
        """Une colonne presente et renseignee ne produit aucune anomalie."""
        assert anomalie_de_colonne("page_no_end", [], 0, 15173, "registre 4.22") is None

    def test_l_absence_de_colonne_prime_sur_le_comptage_des_null(self) -> None:
        """La colonne absente est testee avant le comptage des NULL.

        Quand la colonne n'existe pas, tous les sommets rendent NULL : les deux
        conditions sont vraies. Si le comptage passait d'abord, le message
        prescrirait une reingestion seule.
        """
        message = anomalie_de_colonne("page_no_end", self.TAGS, 15173, 15173, "registre 4.22")

        assert message is not None
        assert "REDEMARRER" in message, (
            f"les deux etats sont vrais et c'est la REINGESTION qui a ete prescrite : {message}"
        )

    def test_les_deux_branches_nomment_le_geste_qui_declenche_la_reingestion(self) -> None:
        """Registre 4.32.a : les deux branches nomment le geste de reingestion.

        Le capteur de source ne relance pas seul un fichier deja vu (`run_key`
        fonde sur le `mtime`) : le message doit dire comment demander la
        reingestion. Le prefixe prescrit est compare a la constante de
        `factory.py`, celle que lit le capteur.
        """
        for message in (
            anomalie_de_colonne("page_no_end", self.TAGS, 0, 0, "registre 4.22"),
            anomalie_de_colonne("page_no_end", [], 15173, 15173, "registre 4.22"),
        ):
            assert message is not None
            assert PREFIXE_REINGESTION in message, (
                f"la branche ne dit pas COMMENT reingerer : {message}"
            )

    def test_le_geste_nomme_est_celui_que_le_capteur_lit_vraiment(self) -> None:
        """Le capteur honore le prefixe que le message prescrit.

        Le test precedent compare deux constantes, qui pourraient changer
        ensemble. Celui-ci interroge le capteur lui-meme, sur un curseur portant
        ce prefixe.
        """
        source = SourceConfig(name="temoin", glob="captures/**/*.html", type="html")
        built = build_source(source)
        with DagsterInstance.ephemeral() as instance:
            contexte = build_sensor_context(
                instance=instance, cursor=f"{PREFIXE_REINGESTION}etiquette-du-temoin"
            )
            built.sensor(contexte)

        assert contexte.cursor == "{}", (
            "le capteur n'a pas reconnu le marqueur que le message d'anomalie "
            f"prescrit : curseur inchange ({contexte.cursor!r})"
        )

    def test_les_deux_etats_rendent_des_messages_differents(self) -> None:
        """Les deux etats produisent des messages differents.

        Sans ce test, deux branches pourraient rendre la meme phrase, et la
        distinction serait perdue pour l'operateur qui lit la sortie.
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
    """Un `DESCRIBE` en echec compte comme une colonne absente (registre 4.31.B4).

    `_verifier_le_tag_document` applique deja cette regle, avec son test
    `test_a_rejected_describe_is_reported_and_not_read_as_a_success`. Ici, elle
    porte sur `_lire_les_tags_sans_la_colonne`.

    Si un `DESCRIBE` refuse rendait `tags_sans_la_colonne == []`,
    `anomalie_de_colonne` prendrait sa seconde branche et prescrirait une
    reingestion seule, la ou il faut redemarrer puis reingerer (registre
    4.29.e). Ces tests echouent si la condition devient
    `if colonnes and colonne not in colonnes`.
    """

    TAGS = sorted(set(TAG_MAP.values()))
    MIGRE = [[colonne] for colonne in VERTEX_PROPERTIES]
    AVANT_MIGRATION = [[c] for c in VERTEX_PROPERTIES if c != "page_no_end"]

    def _describe(self, reponses: dict[str, _Resultat]) -> _Session:
        return _Session(reponses)

    def test_un_schema_entierement_migre_ne_rend_aucun_tag(self) -> None:
        """Des tags qui portent la colonne ne sont pas rendus."""
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
        """Un `DESCRIBE` refuse partout : tous les tags sont rendus.

        `_lire` journalise l'echec et rend une liste vide. La fonction la lit
        comme une absence de colonne, et non comme « aucune colonne ne manque ».
        """
        session = self._describe({"DESCRIBE TAG": _Resultat([], succes=False)})

        manquants = _lire_les_tags_sans_la_colonne(session, "page_no_end")

        assert manquants == self.TAGS, (
            "un DESCRIBE en echec a ete lu comme « la colonne est la » : "
            f"{manquants}. Ne pas pouvoir constater n'est pas constater que "
            "tout va bien (registre 4.4, cinquieme trou)"
        )

    def test_un_seul_describe_rejete_suffit_a_nommer_son_tag(self) -> None:
        """Un echec partiel : seul le tag dont le `DESCRIBE` echoue est rendu.

        C'est le cas le plus plausible, par exemple un tag verrouille par une
        migration en cours.
        """
        session = self._describe(
            {
                "DESCRIBE TAG SectionHeader": _Resultat([], succes=False),
                "DESCRIBE TAG": _Resultat(self.MIGRE),
            }
        )

        assert _lire_les_tags_sans_la_colonne(session, "page_no_end") == ["SectionHeader"]

    def test_l_anomalie_qui_en_decoule_prescrit_le_redemarrage(self) -> None:
        """De bout en bout : un `DESCRIBE` en echec mene a « redemarrer puis
        reingerer ».

        La fonction pourrait rendre la bonne liste et le rapport rester faux :
        ce test verifie le message final.
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
