"""Le garde contre le melange de DEUX modeles d'embedding dans une collection.

C'est l'exigence 1 du contrat, et la panne la plus couteuse du systeme : les deux
modeles candidats rendent 384 dimensions, donc ChromaDB accepte sans broncher,
aucune sonde ne voit rien, et la recherche rend des passages plausibles et faux.
Un `.env` change entre deux ingestions suffit.

`vectors._inscrire_le_modele` est le seul endroit qui refuse cela — il LEVE quand
la collection porte deja un autre modele — et **rien ne le gardait**. `mesure` :
remplacer sa levee par un `logger.warning` laissait 639 tests verts. Le registre
§4.4 ecrivait « (`mesure` : la levee se produit) », c'est-a-dire une observation
faite a la main une fois, pas un garde.

POURQUOI CE FICHIER N'EXISTAIT PAS, et c'est mecanique : `vectors.py` importait
`chromadb` au niveau du module, et `chromadb` n'est pas dans le venv du depot —
les deps lourdes vivent dans `Dockerfile.docling`. Aucun test ne pouvait donc
importer le module, et *ce qu'un test n'importe pas, il ne teste pas*. L'import
est desormais differe dans `get_collection`, exactement comme le lot 3 l'avait
fait pour `index_report`, `verify_contract` et `verify_data` (registre §3.4, §4.4,
§4.5). C'est le meme defaut, sur le quatrieme module, et personne ne l'avait vu
parce que c'est le seul des quatre dont le contrat est un `raise`.
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
    """Collection ChromaDB reduite a ce que le garde lit et ecrit.

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
        """Sans quoi deux ecritures du MEME modele se liraient comme deux modeles."""
        collection = _Collection()
        _inscrire_le_modele(collection, HF_ORG_PREFIX + CONTRACT_MODEL)
        assert collection.modifications == [{"embedding_model": CONTRACT_MODEL}]


class TestUneCollectionDejaTracee:
    def test_the_same_model_writes_nothing(self):
        collection = _Collection({"embedding_model": CONTRACT_MODEL})
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == []

    def test_the_same_model_under_its_prefixed_name_is_the_same_model(self):
        """Le temoin du test suivant.

        Sans lui, un garde qui leverait sur TOUT — y compris sur le bon modele
        ecrit sous son nom prefixe — passerait le test de levee et rendrait
        l'ingestion impossible.
        """
        collection = _Collection({"embedding_model": HF_ORG_PREFIX + CONTRACT_MODEL})
        _inscrire_le_modele(collection, CONTRACT_MODEL)
        assert collection.modifications == []


class TestUnAutreModeleFaitLEVER:
    """LE GARDE LUI-MEME. Il asserte la LEVEE, pas un journal.

    Un `logger.warning` a la place du `raise` laisse le job vert et la collection
    melangee : l'ingestion ecrit par-dessus, deux espaces vectoriels cohabitent, et
    plus rien ne peut les separer apres coup. C'est pourquoi ce test asserte
    l'exception ET l'absence d'ecriture.
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

        Sans ce cas, un garde qui ne comparerait qu'au modele du contrat serait
        vert : la collection porterait le bon modele et l'ingestion tournerait
        avec le mauvais, ce qui est exactement la meme panne dans l'autre sens.
        """
        collection = _Collection({"embedding_model": CONTRACT_MODEL})
        with pytest.raises(EmbeddingContractError):
            _inscrire_le_modele(collection, "all-MiniLM-L6-v2")
        assert collection.modifications == []


class TestLeModuleResteImportableSansChromadb:
    """La raison mecanique pour laquelle ce fichier n'existait pas.

    `chromadb` n'est pas dans le venv du depot. Si `vectors.py` le reimportait au
    niveau du module, tout ce fichier deviendrait une erreur de collecte — et un
    fichier qui ne se collecte pas ne garde rien. Le sous-processus est
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
    """Registre 4.2 : les identifiants derivent du texte, donc un texte modifie
    laisse les ANCIENS chunks derriere lui.

    `NebulaWriter.delete_document` existait pour le graphe et n'avait aucun
    appelant ; cote ChromaDB, il n'existait meme pas. Or le capteur Dagster
    declenche sur `mtime` : mettre a jour un document est le chemin NOMINAL.
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
        """LE TEMOIN du precedent."""
        collection = CollectionEspionne()
        delete_document(IDENTITE, collection=collection)

        for clause in collection.suppressions:
            assert "filename" not in clause, clause

    def test_une_purge_sur_un_document_absent_ne_leve_pas(self):
        """Le cas nominal d'une PREMIERE ingestion : il n'y a rien a purger."""
        collection = CollectionEspionne(chunks=[])

        assert delete_document(IDENTITE, collection=collection) == 0

    def test_la_purge_compte_ce_qu_elle_a_reellement_retire(self):
        """Le compteur la ou il y a perte : une purge muette ne dit pas si elle
        a retire 3 chunks ou 3 000."""
        collection = CollectionEspionne(chunks=["a", "b", "c", "d", "e"])

        assert delete_document(IDENTITE, collection=collection) == 5


class TestChunkCountNeMentPlus:
    """Registre 4.28.a : `chunk_count` etait fixe AVANT le filtrage des chunks.

    `anchoring.resolve_anchors` compte les chunks qui partagent une ancre ;
    `build_chunks` en jette ensuite ceux qui echouent `has_content` ou sont plus
    courts que `min_chunk_chars`. Le compte annonce est celui d'AVANT.

    `mesure` le 1er septembre 2026 sur l'index vivant, 4 365 chunks et 3 750
    elements — chiffres reproduits a l'unite pres :

        element_id=aa3de10738  chunk_count=7  presents=[0,1,2,3,5,6]  MANQUE 4
        element_id=eb52c4ec8f  chunk_count=4  presents=[0,1,2]        MANQUE 3

    **LA MESURE QUI A DECIDE.** Les deux elements sont des blocs de CODE decoupes
    en fenetres successives, et les chunks conserves se raccordent bord a bord :
    `#3` finit sur `self.model_info = mlflow .` et `#5` reprend sur
    `log_model ( python_model = self ,`. Le morceau manquant est donc une fenetre
    du MILIEU d'un texte continu, entre deux fenetres gardees.

    **CE QUI EST TRANCHE, ET POURQUOI.** Les deux issues que le mandat pose ne
    sont pas equivalentes :

    - *recalculer `chunk_count` apres filtrage* rendrait le compte exact et
      **rendrait la perte silencieuse a nouveau** : l'agent concatenerait 6
      chunks annonces 6 et obtiendrait un texte troue qu'il ne peut plus
      detecter. Le controle `jeux_de_chunks_incomplets` (registre 4.4)
      redeviendrait vert sur un index toujours casse. C'est ajuster le compteur
      a la perte au lieu de la fermer — exactement le defaut que ce lot traque ;
    - *cesser de filtrer* ferme la perte. Le compte devient exact **parce que
      rien ne manque**, et non parce qu'on a corrige le compte.

    La seconde est retenue, et **bornee** : le filtre garde son motif pour un
    chunk qui est le SEUL de son element — un fragment isole n'apporte rien a une
    recherche, et l'element reste dans le graphe. Il cesse de s'appliquer a un
    chunk qui a des FRERES : la, ce n'est pas un fragment isole, c'est la
    continuation d'un texte dont les voisins sont conserves. Le motif ecrit du
    filtre — « trop court pour porter du sens » — suppose un chunk autonome, et
    cette supposition est fausse pour une fenetre du milieu.

    Prix assume : quelques vecteurs de faible valeur pour une recherche, en
    echange d'un texte entier. Sur l'index mesure, cela vaut **2 chunks sur
    4 365**.
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
            "minio_url": "",
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

    # Le morceau du MILIEU est court : c'est le cas mesure sur `aa3de10738`.
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
        """LE TEMOIN, et c'est lui qui borne la decision.

        Sans lui, « cesser de filtrer » aurait emporte le motif entier du filtre :
        un fragment de mise en page isole — un filet de tableau, une puce —
        entrerait dans l'index vectoriel. Le filtre garde son sens pour un chunk
        autonome ; il le perd pour une fenetre du milieu.
        """
        ids, textes, metas = self._construire(monkeypatch, ["cd"])

        assert ids == [], f"un fragment isole de 2 caracteres ne doit pas etre indexe : {textes}"

    def test_un_chunk_seul_sans_caractere_alphanumerique_reste_ecarte(self, monkeypatch):
        """Le second temoin : `has_content` garde son sens sur un chunk autonome."""
        ids, textes, metas = self._construire(monkeypatch, ["|---|---|" * 10])

        assert ids == [], f"un artefact de mise en page ne doit pas etre indexe : {textes}"

    def test_un_chunk_sans_ancre_reste_ecarte_et_ne_troue_aucun_compte(self, monkeypatch):
        """Un chunk qu'on ne sait pas rattacher est ecarte — inchange — et il ne
        peut pas trouer un compte : `resolve_anchors` ne le compte jamais."""
        from src.docling_service import vectors as module

        morceaux = self._chunks(["a" * 200, "b" * 200], meme_ancre=False)
        monkeypatch.setattr(
            module, "get_chunker", lambda: type("C", (), {"chunk": lambda s, d: morceaux})()
        )
        ids, textes, metas = module.build_chunks(self.ELEMENTS, IDENTITE, None, document=object())

        assert len(ids) == 1, f"seul le chunk dont l'ancre est connue est ecrit : {ids}"
        assert metas[0]["chunk_count"] == 1
