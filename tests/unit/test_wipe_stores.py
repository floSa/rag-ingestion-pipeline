"""Tests de la purge des stores.

Le test qui compte est celui du bucket MinIO : c'est le store que la purge
oubliait, et le mode d'echec est silencieux. Une purge qui laisse des objets
derriere elle ne leve rien, ne journalise rien, et rend un compte qui a l'air
juste — c'est en cela qu'elle ressemble aux autres pannes de cette chaine.
"""

from __future__ import annotations

import pytest

from src.wipe_stores import purge_bucket, purge_collection, purge_space


class ObjetMinio:
    """Objet MinIO minimal, tel que ``list_objects`` le rend."""

    def __init__(self, object_name: str) -> None:
        self.object_name = object_name


class FauxMinio:
    """Client MinIO qui reproduit la difference entre listage plat et recursif.

    C'est le point du test : ``list_objects(recursive=False)`` ne rend que les
    prefixes de premier niveau — ``images/`` — et jamais les objets qu'ils
    contiennent. Une purge batie dessus supprime zero objet en croyant avoir
    fini.
    """

    def __init__(self, objets: list[str], existe: bool = True) -> None:
        self.objets = list(objets)
        self.existe = existe
        self.supprimes: list[str] = []

    def bucket_exists(self, bucket: str) -> bool:
        return self.existe

    def list_objects(self, bucket: str, recursive: bool = False):
        if recursive:
            return [ObjetMinio(nom) for nom in self.objets]
        prefixes = sorted({nom.split("/", 1)[0] + "/" for nom in self.objets if "/" in nom})
        return [ObjetMinio(prefixe) for prefixe in prefixes]

    def remove_object(self, bucket: str, nom: str) -> None:
        if nom not in self.objets:
            raise KeyError(f"objet inexistant : {nom}")
        self.objets.remove(nom)
        self.supprimes.append(nom)


# Un bucket comme le pipeline le remplit : des crops sous images/{stem}/.
OBJETS = [
    "images/docling_paper/58363088aa_picture.png",
    "images/docling_paper/bcbe047fc2_table.png",
    "images/Practical MLOps/a56855cfa7_picture.png",
    "images/Practical MLOps/6b85633a11_picture.png",
    "images/Livre A/Preface/03795dc837_picture.png",
]


class TestPurgeBucket:
    def test_supprime_tous_les_objets(self):
        client = FauxMinio(OBJETS)
        assert purge_bucket(client, "documents") == len(OBJETS)
        assert client.objets == []

    def test_le_bucket_est_reellement_vide_apres(self):
        # Asserte l'etat du store, pas la valeur de retour : un compte juste
        # sur un bucket encore plein serait vert.
        client = FauxMinio(OBJETS)
        purge_bucket(client, "documents")
        assert list(client.list_objects("documents", recursive=True)) == []

    def test_descend_dans_les_prefixes(self):
        # Le defaut historique : un listage plat ne voit que « images/ » et la
        # purge laisse tout le contenu derriere elle.
        client = FauxMinio(OBJETS)
        purge_bucket(client, "documents")
        assert all(nom in client.supprimes for nom in OBJETS)

    def test_bucket_absent_ne_leve_pas(self):
        client = FauxMinio([], existe=False)
        assert purge_bucket(client, "documents") == 0

    def test_bucket_deja_vide(self):
        client = FauxMinio([])
        assert purge_bucket(client, "documents") == 0

    def test_un_echec_de_suppression_remonte(self):
        # Une purge partielle est pire qu'une purge absente : on croit repartir
        # propre et on re-ingere par-dessus des restes.
        client = FauxMinio(OBJETS)

        def refuse(bucket: str, nom: str) -> None:
            raise OSError("MinIO injoignable")

        client.remove_object = refuse
        with pytest.raises(OSError):
            purge_bucket(client, "documents")


class FausseReponse:
    def __init__(self, ok: bool, erreur: str = "") -> None:
        self.ok = ok
        self.erreur = erreur

    def is_succeeded(self) -> bool:
        return self.ok

    def error_msg(self) -> str:
        return self.erreur


class FausseSession:
    def __init__(self, reponse: FausseReponse) -> None:
        self.reponse = reponse
        self.requetes: list[str] = []

    def execute(self, requete: str) -> FausseReponse:
        self.requetes.append(requete)
        return self.reponse


class TestPurgeSpace:
    def test_drop_space_emis(self):
        session = FausseSession(FausseReponse(True))
        purge_space(session, "rag_space")
        assert session.requetes == ["DROP SPACE IF EXISTS rag_space;"]

    def test_succes_rapporte(self):
        session = FausseSession(FausseReponse(True))
        assert "supprime" in purge_space(session, "rag_space")

    def test_echec_rapporte_le_message(self):
        session = FausseSession(FausseReponse(False, "permission refusee"))
        assert "permission refusee" in purge_space(session, "rag_space")


class FauxChroma:
    def __init__(self) -> None:
        self.supprimees: list[str] = []

    def delete_collection(self, nom: str) -> None:
        self.supprimees.append(nom)


class TestPurgeCollection:
    def test_supprime_la_collection_nommee(self):
        client = FauxChroma()
        purge_collection(client, "rag_documents")
        assert client.supprimees == ["rag_documents"]
