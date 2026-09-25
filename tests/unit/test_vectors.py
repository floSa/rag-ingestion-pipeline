"""Le refus de melanger deux modeles d'embedding dans une collection, et `build_chunks`.

Exigence 1 du contrat : les deux modeles candidats rendent 384 dimensions,
donc ChromaDB accepte le melange, aucune sonde ne le voit, et la recherche
rend des passages plausibles et faux. Un `.env` change entre deux ingestions
suffit.

`vectors._inscrire_le_modele` est le seul endroit qui refuse ce melange : il
leve quand la collection porte deja un autre modele (registre §4.4).

`vectors.py` importe `chromadb` localement (`get_collection`) : `chromadb`
n'est pas dans le venv du depot, et un import de module rendrait ce fichier
de tests impossible a collecter (meme cas que `index_report`,
`verify_contract` et `verify_data`, registre §3.4, §4.4, §4.5).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src.docling_service.elements import DocumentIdentity
from src.docling_service.embedding import CONTRACT_MODEL, HF_ORG_PREFIX, EmbeddingContractError
from src.docling_service.vectors import (
    COLLECTION_NAME,
    _inscrire_le_modele,
    delete_document,
)

RACINE_DEPOT = Path(__file__).resolve().parents[2]

# Le chemin d'un des deux Preface.html du corpus : le cas d'ecole de l'exigence 3.
IDENTITE = DocumentIdentity(
    source_path="htms/MLOps with Databricks/Preface.html",
    key="htms/MLOps with Databricks/Preface",
    filename="Preface",
    collection="MLOps with Databricks",
)


class CollectionEspionne:
    """Collection ChromaDB bouchonnee qui retient les clauses de suppression."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self._chunks = ["0123456789#0", "0123456789#1", "abcdef0123"] if chunks is None else chunks
        self.suppressions: list[dict[str, Any]] = []

    def get(self, where: dict[str, Any], include: list[str] | None = None) -> dict[str, Any]:
        return {"ids": list(self._chunks)}

    def delete(self, where: dict[str, Any]) -> None:
        self.suppressions.append(dict(where))
        self._chunks = []


class _Collection:
    """Collection ChromaDB reduite a ce que `_inscrire_le_modele` lit et ecrit.

    Elle enregistre les appels a ``modify`` : « la collection a ete tracee » et
    « le code est alle jusqu'au bout » ne sont pas la meme chose.
    """

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata
        self.modifications: list[dict[str, Any]] = []

    def modify(self, metadata: dict[str, Any]) -> None:
        self.modifications.append(metadata)
        self.metadata = {**(self.metadata or {}), **metadata}


class TestUneCollectionVierge:
    """Le cas de tout index ecrit avant que la tracabilite n'existe."""

    def test_a_collection_with_no_metadata_gets_the_model_written_on_it(self):
        collection = _Collection()
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == [{"embedding_model": CONTRACT_MODEL}]

    def test_an_empty_metadata_dict_is_the_same_case(self):
        collection = _Collection({})
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == [{"embedding_model": CONTRACT_MODEL}]

    def test_the_org_prefix_is_stripped_before_being_written(self):
        """Sans quoi deux ecritures du meme modele se liraient comme deux modeles."""
        collection = _Collection()
        _inscrire_le_modele(collection, HF_ORG_PREFIX + CONTRACT_MODEL)
        assert collection.modifications == [{"embedding_model": CONTRACT_MODEL}]


class TestUneCollectionDejaTracee:
    def test_the_same_model_writes_nothing(self):
        collection = _Collection({"embedding_model": CONTRACT_MODEL})
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == []

    def test_the_same_model_under_its_prefixed_name_is_the_same_model(self):
        """Contre-epreuve du test suivant.

        Sans ce test, un controle qui leverait sur tout, y compris sur le bon
        modele ecrit sous son nom prefixe, passerait le test de levee et
        rendrait l'ingestion impossible.
        """
        collection = _Collection({"embedding_model": HF_ORG_PREFIX + CONTRACT_MODEL})
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == []


class TestUnAutreModeleFaitLEVER:
    """Un autre modele fait lever, et rien n'est ecrit.

    Un simple journal a la place du `raise` laisserait le job reussir et la
    collection melangee : deux espaces vectoriels cohabiteraient, sans moyen de
    les separer apres coup. Ces tests verifient donc l'exception et l'absence
    d'ecriture.
    """

    def test_another_model_raises(self):
        collection = _Collection({"embedding_model": "all-MiniLM-L6-v2"})
        with pytest.raises(EmbeddingContractError):
            _inscrire_le_modele(collection, CONTRACT_MODEL)

    def test_the_collection_is_left_untouched_when_it_raises(self):
        """La levee ne suffit pas : il ne faut pas non plus avoir ecrit avant."""
        collection = _Collection({"embedding_model": "all-MiniLM-L6-v2"})
        with pytest.raises(EmbeddingContractError):
            _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == []
        assert collection.metadata == {"embedding_model": "all-MiniLM-L6-v2"}

    def test_the_message_names_both_models_and_the_way_out(self):
        """Le message est la seule chose qu'un exploitant verra du job echoue."""
        collection = _Collection({"embedding_model": "all-MiniLM-L6-v2"})
        with pytest.raises(EmbeddingContractError) as leve:
            _inscrire_le_modele(collection, CONTRACT_MODEL)
        message = str(leve.value)
        assert "all-MiniLM-L6-v2" in message
        assert CONTRACT_MODEL in message
        assert COLLECTION_NAME in message
        assert "EMBEDDING_MODEL_NAME" in message

    def test_the_direction_does_not_matter(self):
        """Le contrat est « deux modeles differents », pas « le mauvais des deux ».

        Sans ce cas, un controle qui ne comparerait qu'au modele du contrat
        passerait : la collection porterait le bon modele et l'ingestion
        tournerait avec le mauvais, la meme panne dans l'autre sens.
        """
        collection = _Collection({"embedding_model": CONTRACT_MODEL})
        with pytest.raises(EmbeddingContractError):
            _inscrire_le_modele(collection, "all-MiniLM-L6-v2")
        assert collection.modifications == []


class TestLeModuleResteImportableSansChromadb:
    """`vectors.py` s'importe sans `chromadb`.

    `chromadb` n'est pas dans le venv du depot. Si `vectors.py` l'importait au
    niveau du module, tout ce fichier deviendrait une erreur de collecte. Le sous-processus est
    volontaire : dans l'interpreteur courant, `chromadb` figure deja dans
    `sys.modules` si un autre test l'a bouchonne, et l'ordre des tests
    deviendrait significatif.
    """

    def test_importing_the_module_pulls_no_store_client(self):
        processus = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import src.docling_service.vectors as v; "
                "assert 'chromadb' not in sys.modules, 'chromadb importe a l import'; "
                "print(v.COLLECTION_NAME)",
            ],
            cwd=RACINE_DEPOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert processus.returncode == 0, processus.stderr
        assert COLLECTION_NAME in processus.stdout


class TestLaPurgeDUnDocumentDansLIndexVectoriel:
    """Registre 4.2 : sans purge, un texte modifie laisserait ses anciens chunks.

    Les identifiants derivent du texte. `vectors.delete_document` est le
    pendant ChromaDB de `NebulaWriter.delete_document`. Le capteur Dagster
    declenche sur `mtime` : mettre a jour un document est le cas nominal.
    """

    def test_la_purge_supprime_par_source_path_et_non_par_nom(self):
        collection = CollectionEspionne()
        supprimes = delete_document(IDENTITE, collection=collection)

        assert collection.suppressions == [{"source_path": IDENTITE.source_path}], (
            "la purge doit viser `source_path`, l'identite d'un document "
            "(contrat, exigence 3) : le corpus porte deux Preface.html, et une "
            "purge par `filename` emporterait les deux"
        )
        assert supprimes == 3

    def test_la_purge_ne_vise_jamais_le_filename(self):
        """Contre-epreuve du precedent."""
        collection = CollectionEspionne()
        delete_document(IDENTITE, collection=collection)

        for clause in collection.suppressions:
            assert "filename" not in clause, clause

    def test_une_purge_sur_un_document_absent_ne_leve_pas(self):
        """Le cas nominal d'une PREMIERE ingestion : il n'y a rien a purger."""
        collection = CollectionEspionne(chunks=[])

        assert delete_document(IDENTITE, collection=collection) == 0

    def test_la_purge_compte_ce_qu_elle_a_reellement_retire(self):
        """Une purge muette ne dirait pas si elle a retire 3 chunks ou 3 000."""
        collection = CollectionEspionne(chunks=["a", "b", "c", "d", "e"])

        assert delete_document(IDENTITE, collection=collection) == 5


class TestChunkCountNeMentPlus:
    """Registre 4.28.a : `chunk_count` annonce exactement les chunks presents.

    `anchoring.resolve_anchors` compte les chunks qui partagent une ancre
    avant que `build_chunks` ne filtre ceux qui echouent `has_content` ou sont
    plus courts que `min_chunk_chars`. Jeter un chunk qui a des freres ferait
    donc mentir le compte. Mesure le 1er septembre 2026 sur l'index vivant
    (4 365 chunks, 3 750 elements), avant que le filtre ne soit borne :

        element_id=aa3de10738  chunk_count=7  presents=[0,1,2,3,5,6]  MANQUE 4
        element_id=eb52c4ec8f  chunk_count=4  presents=[0,1,2]        MANQUE 3

    Les deux elements sont des blocs de code decoupes en fenetres successives
    qui se raccordent bord a bord : `#3` finit sur `self.model_info = mlflow .`
    et `#5` reprend sur `log_model ( python_model = self ,`. Le morceau
    manquant est une fenetre du milieu d'un texte continu.

    Deux options :

    - recalculer `chunk_count` apres filtrage rendrait le compte exact mais la
      perte indetectable : l'agent concatenerait 6 chunks annonces 6 et
      obtiendrait un texte troue, et le controle `jeux_de_chunks_incomplets`
      (registre 4.4) ne verrait plus rien ;
    - ne plus filtrer les chunks qui ont des freres supprime la perte : le
      compte devient exact parce que rien ne manque.

    La seconde est retenue. Le filtre reste applique a un chunk qui est le
    seul de son element : un fragment isole n'apporte rien a une recherche, et
    l'element reste dans le graphe. Il ne s'applique plus a une fenetre du
    milieu, dont les voisins sont conserves. Cout : 2 chunks sur 4 365 sur
    l'index mesure.
    """

    @staticmethod
    def _chunks(textes: list[str], meme_ancre: bool = True) -> Any:
        """Un document Docling bouchonne dont les chunks partagent une ancre."""

        class Morceau:
            def __init__(self, texte: str, ref: str) -> None:
                self.text = texte
                self.meta = type("M", (), {"doc_items": [type("I", (), {"self_ref": ref})()]})()

        return [
            Morceau(texte, "#/texts/0" if meme_ancre else f"#/texts/{i}")
            for i, texte in enumerate(textes)
        ]

    ELEMENTS = [
        {
            "id": "aa3de10738",
            "self_ref": "#/texts/0",
            "label": "code",
            "page_no": 1,
            "text": "un bloc de code",
            "media_url": "",
            "object_key": "",
            "depth": 1,
            "order": 0,
            "reference_id": "DOC",
        }
    ]

    def _construire(self, monkeypatch: Any, textes: list[str]) -> Any:
        from src.docling_service import vectors as module

        monkeypatch.setattr(
            module,
            "get_chunker",
            lambda: type("C", (), {"chunk": lambda s, d: self._chunks(textes)})(),
        )
        return module.build_chunks(self.ELEMENTS, IDENTITE, None, document=object())

    # Le morceau du milieu est court : c'est le cas mesure sur `aa3de10738`.
    TEXTES = ["a" * 200, "b" * 200, "cd", "d" * 200]

    def test_le_jeu_de_chunks_d_un_element_est_complet(self, monkeypatch):
        ids, textes, metas = self._construire(monkeypatch, self.TEXTES)

        indices = sorted(m["chunk_index"] for m in metas)
        assert indices == [0, 1, 2, 3], (
            f"le jeu est troue : {indices}. L'agent concatene ce qu'il trouve et "
            "rend un texte troue, sans aucune erreur"
        )

    def test_chunk_count_egale_le_nombre_de_chunks_reellement_ecrits(self, monkeypatch):
        ids, textes, metas = self._construire(monkeypatch, self.TEXTES)

        assert {m["chunk_count"] for m in metas} == {len(ids)}

    def test_le_morceau_court_du_milieu_est_conserve(self, monkeypatch):
        """Le fait mesure : la fenetre du milieu porte du texte, et il revient."""
        ids, textes, metas = self._construire(monkeypatch, self.TEXTES)

        assert "cd" in textes, textes

    def test_un_chunk_seul_et_trop_court_reste_ecarte(self, monkeypatch):
        """Le filtre reste applique a un chunk autonome.

        Sans ce test, ne plus filtrer du tout passerait : un fragment de mise
        en page isole (filet de tableau, puce) entrerait dans l'index
        vectoriel.
        """
        ids, textes, metas = self._construire(monkeypatch, ["cd"])

        assert ids == [], f"un fragment isole de 2 caracteres ne doit pas etre indexe : {textes}"

    def test_un_chunk_seul_sans_caractere_alphanumerique_reste_ecarte(self, monkeypatch):
        """Le second temoin : `has_content` garde son sens sur un chunk autonome."""
        ids, textes, metas = self._construire(monkeypatch, ["|---|---|" * 10])

        assert ids == [], f"un artefact de mise en page ne doit pas etre indexe : {textes}"

    def test_un_chunk_sans_ancre_reste_ecarte_et_ne_troue_aucun_compte(self, monkeypatch):
        """Un chunk sans ancre est ecarte, et il ne peut pas trouer un compte :
        `resolve_anchors` ne le compte jamais."""
        from src.docling_service import vectors as module

        morceaux = self._chunks(["a" * 200, "b" * 200], meme_ancre=False)
        monkeypatch.setattr(
            module, "get_chunker", lambda: type("C", (), {"chunk": lambda s, d: morceaux})()
        )
        ids, textes, metas = module.build_chunks(self.ELEMENTS, IDENTITE, None, document=object())

        assert len(ids) == 1, f"seul le chunk dont l'ancre est connue est ecrit : {ids}"
        assert metas[0]["chunk_count"] == 1


class TestLaFormeDeLIdDeChunkEstGardeeLaOuElleEstEcrite:
    """La forme de l'id de chunk, verifiee sur les ids que `build_chunks` ecrit.

    Id nu pour un element d'un seul chunk, suffixe `#n` au-dela : c'est une
    clause du contrat, fixee par `chunking.chunk_id` seul (registre 5.1), que
    `build_chunks` appelle. `verify_contract` compte les ids suffixes : 974 sur
    4 365, mesure le 2 septembre 2026. Un suffixe inconditionnel porterait ce
    compte a 4 365 sur 4 365.

    Une forme erronee ne duplique pas les chunks a la reingestion :
    `extraction.extract` appelle `storage.forget_document` avant la
    conversion, et `vectors.delete_document` supprime par
    `where={"source_path": ...}`, jamais par id. Elle rompt en revanche la
    clause du contrat.
    """

    @staticmethod
    def _chunks(textes: list[str], meme_ancre: bool) -> Any:
        class Morceau:
            def __init__(self, texte: str, ref: str) -> None:
                self.text = texte
                self.meta = type("M", (), {"doc_items": [type("I", (), {"self_ref": ref})()]})()

        return [
            Morceau(texte, "#/texts/0" if meme_ancre else f"#/texts/{i}")
            for i, texte in enumerate(textes)
        ]

    ELEMENTS = [
        {
            "id": "aa3de10738",
            "self_ref": "#/texts/0",
            "label": "text",
            "page_no": 1,
            "text": "un paragraphe",
            "media_url": "",
            "object_key": "",
            "depth": 1,
            "order": 0,
            "reference_id": "DOC",
        },
        {
            "id": "bb3de10739",
            "self_ref": "#/texts/1",
            "label": "text",
            "page_no": 1,
            "text": "un autre paragraphe",
            "media_url": "",
            "object_key": "",
            "depth": 1,
            "order": 1,
            "reference_id": "DOC",
        },
    ]

    def _ids(self, monkeypatch: Any, textes: list[str], meme_ancre: bool) -> list[str]:
        from src.docling_service import vectors as module

        monkeypatch.setattr(
            module,
            "get_chunker",
            lambda: type("C", (), {"chunk": lambda s, d: self._chunks(textes, meme_ancre)})(),
        )
        ids, _, _ = module.build_chunks(self.ELEMENTS, IDENTITE, None, document=object())
        return ids

    def test_un_element_d_un_seul_chunk_est_ecrit_sous_son_id_nu(self, monkeypatch):
        """Un suffixe inconditionnel fait echouer ce test.

        Deux elements, un chunk chacun : les deux ids doivent etre nus. Un `#0`
        ici signifie que la clause du contrat est rompue au seul site qui ecrit.
        """
        ids = self._ids(monkeypatch, ["a" * 200, "b" * 200], meme_ancre=False)

        assert ids == ["aa3de10738", "bb3de10739"], (
            f"les ids portent un suffixe alors que chaque element tient en un "
            f"chunk : {ids}. C'est la clause du contrat sur la forme de l'id, "
            "celle que `verify_contract` compte sous « ids de chunk suffixes »"
        )

    def test_un_element_multi_chunks_est_ecrit_suffixe(self, monkeypatch):
        """Contre-epreuve du precedent.

        Sans ce test, un `chunk_id` qui rendrait toujours l'id nu passerait le
        test ci-dessus, et deux chunks du meme element s'ecraseraient l'un
        l'autre a l'upsert.
        """
        ids = self._ids(monkeypatch, ["a" * 200, "b" * 200, "c" * 200], meme_ancre=True)

        assert ids == ["aa3de10738#0", "aa3de10738#1", "aa3de10738#2"], (
            f"trois chunks du meme element partagent un id : {ids}. Ils "
            "s'ecraseraient l'un l'autre a l'upsert"
        )

    def test_les_ids_ecrits_sont_tous_distincts(self, monkeypatch):
        """Les ids ecrits sont distincts, ce qui rend l'upsert correct.

        La propriete est verifiee sur ce que la production ecrit : un `upsert`
        avec un id repete n'ecrit qu'un vecteur pour deux chunks.
        """
        ids = self._ids(monkeypatch, ["a" * 200, "b" * 200, "c" * 200], meme_ancre=True)

        assert len(set(ids)) == len(ids), f"ids repetes : {ids}"


class TestPageNoEndAtteintLaMetadonneeDeChunk:
    """Registre 4.22, cote vectoriel : `page_no_end` atteint la metadonnee de chunk.

    `page_no_end` est ecrit dans les deux stores. Le cote graphe est verifie
    par `test_ngql.py::test_page_no_end_falls_back_on_page_no_and_not_on_zero`,
    le cote vectoriel ici.

    `verify_contract` ne verrait pas une erreur : il controle la presence de la
    cle, et ne compte que les `None`. Un `0` partout passerait pour une valeur,
    et un agent lirait « cet element finit page 0 ».

    Le repli est `page_no` et jamais 0, comme au site du graphe : un element
    qui tient sur une page finit sur sa page d'entree, et 0 n'est pas un
    numero de page.
    """

    ELEMENT_QUI_ENJAMBE = {
        "id": "aa3de10738",
        "self_ref": "#/texts/0",
        "label": "text",
        "page_no": 7,
        "page_no_end": 8,
        "text": "un paragraphe qui enjambe",
        "media_url": "",
        "object_key": "",
        "depth": 1,
        "order": 0,
        "reference_id": "DOC",
    }

    @staticmethod
    def _metadonnees(monkeypatch: Any, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Les metadonnees que `build_chunks` ecrirait dans ChromaDB."""
        from src.docling_service import vectors as module

        class Morceau:
            def __init__(self, texte: str) -> None:
                self.text = texte
                self.meta = type(
                    "M", (), {"doc_items": [type("I", (), {"self_ref": "#/texts/0"})()]}
                )()

        monkeypatch.setattr(
            module,
            "get_chunker",
            lambda: type("C", (), {"chunk": lambda s, d: [Morceau("a" * 200)]})(),
        )
        _, _, metas = module.build_chunks(elements, IDENTITE, None, document=object())
        return metas

    def test_la_page_de_fin_d_un_element_qui_enjambe_atteint_chromadb(self, monkeypatch):
        """C'est la valeur que l'agent lit pour cadrer une citation.

        Une citation « page 7 » sur cet element couvre en realite 7 et 8 : sans
        `page_no_end`, l'agent ne peut pas le dire (registre 4.22).
        """
        metas = self._metadonnees(monkeypatch, [self.ELEMENT_QUI_ENJAMBE])

        assert metas, "aucun chunk produit : le cas voulu n'est pas atteint"
        assert metas[0]["page_no_end"] == 8, (
            f"page_no_end vaut {metas[0]['page_no_end']} au lieu de 8 : la page "
            "de fin n'atteint pas l'index vectoriel, et rien ne le dirait — "
            "`verify_contract` ne compte que les None, donc un 0 passe pour une "
            "valeur"
        )
        assert metas[0]["page_no"] == 7, metas[0]

    def test_un_element_sans_page_de_fin_retombe_sur_sa_page_d_entree(self, monkeypatch):
        """Sans page de fin, le repli est `page_no`, jamais 0.

        Un element qui tient sur une page finit sur sa page d'entree. Un 0
        dirait « page inconnue » (voir `ngql.py`). Sans ce test, un repli
        inconditionnel a 0 passerait des que l'element ne porte pas la cle.
        """
        sans_fin = {k: v for k, v in self.ELEMENT_QUI_ENJAMBE.items() if k != "page_no_end"}
        metas = self._metadonnees(monkeypatch, [sans_fin])

        assert metas[0]["page_no_end"] == 7, (
            f"le repli vaut {metas[0]['page_no_end']} au lieu de la page d'entree : "
            "0 dirait « page inconnue » et non « tient sur une page »"
        )

    def test_une_page_de_fin_a_zero_retombe_aussi_sur_la_page_d_entree(self, monkeypatch):
        """`0` est traite comme une absence, pas comme une page.

        C'est la forme que porte un element ecrit avant que la colonne n'existe.
        Le laisser passer a 0 ferait ecrire dans ChromaDB la valeur que le graphe
        refuse d'y ecrire, et les deux stores divergeraient en silence.
        """
        metas = self._metadonnees(monkeypatch, [{**self.ELEMENT_QUI_ENJAMBE, "page_no_end": 0}])

        assert metas[0]["page_no_end"] == 7, metas[0]

    def test_la_page_de_fin_ne_recopie_pas_la_page_d_entree_sur_un_element_qui_enjambe(
        self, monkeypatch
    ):
        """La page de fin n'est pas une simple copie de la page d'entree.

        Un `page_no_end=int(element.get("page_no") or 0)` passerait les deux
        tests ci-dessus, le repli etant precisement `page_no`. Seul un element
        qui enjambe les distingue, d'ou son usage dans le premier test de cette
        classe.
        """
        metas = self._metadonnees(monkeypatch, [self.ELEMENT_QUI_ENJAMBE])

        assert metas[0]["page_no_end"] != metas[0]["page_no"], (
            "la page de fin recopie la page d'entree : l'enjambement est perdu"
        )
