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

from src.docling_service.embedding import CONTRACT_MODEL, HF_ORG_PREFIX, EmbeddingContractError
from src.docling_service.vectors import COLLECTION_NAME, _inscrire_le_modele

RACINE_DEPOT = Path(__file__).resolve().parents[2]


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
