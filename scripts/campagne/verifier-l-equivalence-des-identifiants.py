"""Fige, puis compare, les `element_id` emis par la production. Sort en 1 au moindre ecart.

Deux gestes, tous deux necessaires a une campagne :

- ``figer <dossier>``, avant la purge : reextrait tout le corpus par le chemin
  de production, verifie que les identifiants emis sont ceux du graphe, puis
  ecrit l'instantane (un releve par element) et imprime son empreinte. Rien
  n'est ecrit au moindre rouge ;
- ``comparer <dossier> [--deplacements-annonces <fichier>]``, apres la
  reingestion : reextrait de nouveau et confronte le resultat a l'instantane,
  jamais a une reextraction du meme code (qui ne verrait aucun deplacement,
  registre 4.37). rc=0 si et seulement si l'ensemble deplace egale l'ensemble
  declare, vide par defaut.

Le script tourne dans l'image d'extraction : `docling` et `chromadb` ne font pas
partie des dependances du depot. Le `src` monte est celui de la branche mesuree
(registre 4.27) :

    docker compose run --rm --no-deps -T \\
      -v "$PWD/scripts":/app/scripts:ro \\
      -v "$PWD/documentation/campagnes":/app/documentation/campagnes:ro \\
      -v "$PWD/Datas":/corpus:ro \\
      -v "<un scratchpad>":/sp \\
      -v "$PWD/Datas/.cleaned":/sp/cleaned:ro \\
      -e COMMIT_MESURE="$(git rev-parse HEAD)" -e PYTHONPATH=/app -w /app \\
      docling-service \\
      python scripts/campagne/verifier-l-equivalence-des-identifiants.py \\
        comparer documentation/campagnes/<date>-instantane-des-identifiants

L'instantane est authentifie par le harnais lui-meme. `src.equivalence_des_identifiants`
fixe deux sites : `REPERTOIRE_DE_CAMPAGNE`, resolu depuis l'emplacement du
module (d'ou le montage de `documentation/campagnes` sous `/app`), et
`EMPREINTES_ATTENDUES`, la constante des empreintes. Les deux gestes refusent
tout dossier hors de ce repertoire, et `comparer` rougit si l'empreinte n'est
pas celle de la constante (registre 4.39.a). Sans cela, figer dans un dossier
quelconque puis comparer contre lui rendrait vert des deux cotes.

Les HTML sont compares sur leur copie nettoyee, attendue sous
`/sp/cleaned/<chemin de partition>`. Elle doit etre produite avec un
exportateur d'images, comme en production : `image_exporter=None` supprime
l'attribut `src` des images et fabrique une fausse derive. L'instantane porte le
SHA-256 de chaque entree convertie : devant un rouge, il dit si c'est l'entree
ou le code qui a change.

Le script n'ecrit dans aucun store, et c'est verifie a l'execution.
:func:`~src.equivalence_des_identifiants.armer_les_barrieres` barre d'abord les
constructeurs des trois SDK (`minio.Minio`, les pools `nebula3`, les fabriques
`chromadb`) : toute construction de client leve ensuite dans ce processus, par
quelque chemin que ce soit. Les portes d'ecriture de `src.docling_service` sont
barrees ensuite a tous leurs sites, comme seconde couche.

Les seuls clients du processus sont des clients de lecture, construits avant
l'armement et enveloppes : la session Nebula ne laisse passer que des verbes de
lecture, le client du stockage objet que `list_objects`.

Code de sortie : 0 si tout concorde, 1 sinon, 2 sur un usage faux.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import os
import sys
from pathlib import Path
from typing import Any

from src.equivalence_des_identifiants import (
    DossierHorsCampagneError,
    EmpreinteInattendueError,
    LectureSeule,
    Monde,
    SessionEnLecture,
    armer_les_barrieres,
    comparer,
    dossier_de_campagne,
    empreinte_attendue,
    figer,
    installer_la_capture,
    lire_l_instantane,
    lire_les_deplacements_annonces,
    reextraire,
)

CORPUS = Path("/corpus")
SCRATCHPAD = Path("/sp")


def documents_du_corpus() -> list[str]:
    """Les partitions que le capteur decouvrirait : meme glob, meme exclusion.

    C'est le geste de `factory._build_sensor` — `glob` sur `source.glob`, puis
    `is_ignored` — rejoue sur le corpus monte. Aucune liste en dur.
    """
    from src.pipeline.sources import load_sources

    trouves: list[str] = []
    for source in load_sources():
        for chemin in sorted(glob.glob(str(CORPUS / source.glob), recursive=True)):
            if not source.is_ignored(chemin):
                trouves.append(Path(chemin).relative_to(CORPUS).as_posix())
    return trouves


class Graphe:
    """Une session de lecture sur le graphe.

    Elle se construit avant l'armement, car apres, `ConnectionPool` leve. Elle
    porte donc sa propre protection : sa session est enveloppee dans
    :class:`SessionEnLecture`, qui refuse toute requete dont un fragment ne
    commence pas par un verbe de lecture. `release` et `close` sont les seuls
    autres gestes, et ils ne touchent pas au graphe.
    """

    def __init__(self, journal: list[str]) -> None:
        from nebula3.Config import Config
        from nebula3.gclient.net import ConnectionPool

        from src.docling_service.ngql import SPACE

        self._pool = ConnectionPool()
        self._pool.init(
            [(os.environ["NEBULA_HOST"], int(os.environ.get("NEBULA_PORT", "9669")))], Config()
        )
        self._session = SessionEnLecture(
            self._pool.get_session(os.environ["NEBULA_USER"], os.environ["NEBULA_PASSWORD"]),
            journal,
        )
        self._executer(f"USE {SPACE};")

    def _executer(self, requete: str) -> Any:
        resultat = self._session.execute(requete)
        if not resultat.is_succeeded():
            # Un graphe muet rendrait « aucun sommet », qui se lit « tout a bouge ».
            raise RuntimeError(f"nGQL echoue : {requete[:120]} -> {resultat.error_msg()}")
        return resultat

    def documents(self) -> list[str]:
        """Les partitions des sommets `Document`, `.cleaned/` retire."""
        from src.docling_service.elements import CLEANED_SUBDIR

        resultat = self._executer("MATCH (d:Document) RETURN d.Document.source_path AS s;")
        partitions: list[str] = []
        for rang in range(resultat.row_size()):
            segments = resultat.row_values(rang)[0].as_string().split("/")
            partitions.append("/".join(s for s in segments if s and s != CLEANED_SUBDIR))
        return partitions

    def ids(self, cle_du_document: str) -> set[str]:
        """Les descendants du `Document` par `PARENT_OF`, de proche en proche."""
        from src.docling_service.ngql import document_vid

        vus: set[str] = set()
        frontiere = [document_vid(cle_du_document)]
        while frontiere:
            lot, frontiere = frontiere[:200], frontiere[200:]
            liste = ", ".join('"' + vid.replace('"', '\\"') + '"' for vid in lot)
            resultat = self._executer(f"GO FROM {liste} OVER PARENT_OF YIELD dst(edge) AS d;")
            for rang in range(resultat.row_size()):
                enfant = resultat.row_values(rang)[0].as_string()
                if enfant not in vus:
                    vus.add(enfant)
                    frontiere.append(enfant)
        return vus

    def fermer(self) -> None:
        self._session.release()
        self._pool.close()


def client_du_stockage_en_lecture(journal: list[str]) -> LectureSeule:
    """Le client S3 du harnais, construit avant l'armement et enveloppe.

    Il passe par `images.build_client`, seul site de construction du depot :
    un appel direct au SDK ici serait un second endroit ou se decident `secure=`
    et l'ordre des arguments.

    Apres l'armement, le constructeur `minio.Minio` (bibliotheque cliente) leve.
    Ce client-ci, construit avant, survit a la barriere : il est donc enveloppe.
    Seule `list_objects` passe ; tout le reste leve et entre au journal partage,
    celui sur lequel `figer` et `comparer` rougissent (registre 4.40.e).
    """
    from src.docling_service.images import build_client

    return LectureSeule(
        build_client(
            os.environ["S3_ENDPOINT"],
            os.environ["S3_ACCESS_KEY"],
            os.environ["S3_SECRET_KEY"],
        ),
        {"list_objects"},
        "stockage objet du harnais",
        journal,
    )


def objets_listes(client: LectureSeule, partition_key: str) -> set[str] | None:
    """Les cles que le stockage objet liste sous le prefixe des crops d'un PDF.

    Lecture seule. None pour un HTML : ses images sont envoyees par le
    nettoyage, sous une cle qui ne porte pas d'`element_id`, et l'extraction
    n'en produit aucune.
    """
    if not partition_key.lower().endswith(".pdf"):
        return None
    from src.docling_service.elements import document_identity

    prefixe = f"images/{document_identity(partition_key).filename}/"
    return {
        str(objet.object_name)
        for objet in client.list_objects(os.environ["S3_BUCKET"], prefix=prefixe, recursive=True)
    }


def _commit() -> str:
    """Le commit du `src` monte, passe par `-e COMMIT_MESURE=$(git rev-parse HEAD)`.

    Le conteneur ne voit pas le `.git` du depot : le commit ne peut venir que de
    l'hote. S'il manque, le manifeste le dit plutot que de taire le champ.
    """
    return os.environ.get("COMMIT_MESURE") or "NON FOURNI (COMMIT_MESURE absent)"


def main() -> int:
    """Point d'entree : `figer` ou `comparer`."""
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    actions = analyseur.add_subparsers(dest="action", required=True)
    a_figer = actions.add_parser("figer")
    a_figer.add_argument("dossier", type=Path)
    a_comparer = actions.add_parser("comparer")
    a_comparer.add_argument("dossier", type=Path)
    a_comparer.add_argument("--deplacements-annonces", type=Path, default=None)
    arguments = analyseur.parse_args()

    # Deux refus en premier, avant tout armement et toute connexion : un dossier
    # hors du repertoire de campagne fixe n'est ni fige ni compare, et un
    # instantane absent de EMPREINTES_ATTENDUES n'est pas comparable. Refuser
    # apres s'etre connecte serait refuser trop tard, et en trace d'appel au lieu
    # d'un code de sortie (registre 4.40.g).
    try:
        dossier = dossier_de_campagne(arguments.dossier)
        # `figer` ecrit l'instantane : son empreinte ne peut pas etre attendue
        # avant qu'il existe. Le refus ne porte donc que sur `comparer`.
        attendue = empreinte_attendue(dossier) if arguments.action == "comparer" else ""
    except (DossierHorsCampagneError, EmpreinteInattendueError) as exc:
        print(f"ECHEC — {exc}")
        return 1

    # Les clients de lecture se construisent avant l'armement : apres,
    # `minio.Minio` et `ConnectionPool` levent. Ce sont les seuls clients de store
    # du processus, et chacun porte sa propre limite : verbes de lecture pour la
    # session Nebula, `list_objects` seule pour le stockage objet.
    journal: list[str] = []
    graphe = Graphe(journal)
    stockage_en_lecture = client_du_stockage_en_lecture(journal)

    armement = armer_les_barrieres(journal)
    sites = sum(len(s) for s in armement.sites.values())
    print(f"barrieres d'ecriture armees : {len(armement.sites)} portes, {sites} sites")
    for nom, liste in sorted(armement.sites.items()):
        print(f"    {nom:28s} {liste}")
    constructeurs = sum(len(noms) for noms in armement.sdk.barres.values())
    print(f"constructeurs de SDK barres : {constructeurs}")
    for module, noms in sorted(armement.sdk.barres.items()):
        print(f"    {module:32s} {noms}")
    for module, raison in sorted(armement.sdk.absents.items()):
        print(f"    {module:32s} ABSENT DU PROCESSUS — {raison}")
    lots = installer_la_capture()

    try:
        monde = Monde(
            documents_du_corpus=documents_du_corpus,
            documents_du_graphe=graphe.documents,
            ids_du_graphe=graphe.ids,
            objets_listes=lambda cle: objets_listes(stockage_en_lecture, cle),
            reextraire=lambda cle: reextraire(
                cle, CORPUS, SCRATCHPAD / "cleaned", lots, armement.temoin
            ),
        )
        if arguments.action == "figer":
            entete = {
                "date": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                "commit du code": _commit(),
                "source des identifiants": "emission de la production, prouvee egale au graphe",
            }
            return figer(monde, dossier, entete, armement.journal)
        instantane = lire_l_instantane(dossier)
        declares = lire_les_deplacements_annonces(arguments.deplacements_annonces)
        return comparer(monde, instantane, attendue, declares, armement.journal)
    finally:
        graphe.fermer()


if __name__ == "__main__":
    sys.exit(main())
