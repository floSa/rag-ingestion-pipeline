#!/usr/bin/env python3
"""Les huit criteres d'essai d'une passerelle S3, par appel direct.

Ce script evalue un candidat au stockage objet, quel qu'il soit. Pour chaque
critere et chaque jeu d'identifiants, il fait l'appel que le pipeline ou
l'agent ferait, et lit le refus dans l'exception ; il n'interroge aucune
console ni page d'etat. Il a servi le 25 septembre 2026 pour valider SeaweedFS,
et reste utilisable tel quel.

Il ne construit pas ses clients par `images.build_client`, qui lit les
reglages de la pile en service. Un essai doit viser une passerelle candidate,
avec deux jeux d'identifiants distincts et sans toucher a la configuration du
depot : il a donc ses propres variables `ESSAI_S3_*`, ce qui permet aussi le
controle negatif.

Pourquoi par appel direct : un refus S3 (403 `AccessDenied`) remonte chez
`rag-agent-chat` en 404 sans message, et le corpus parait simplement
incomplet. Ici, le code HTTP et le code S3 sont imprimes tels quels.

Pourquoi la bibliotheque `minio` (minio-py) : c'est un client S3 generique, et
celui qu'utilisent `src/docling_service/images.py` et l'agent. `boto3` ou
`aws s3` valideraient un autre dialecte. minio-py envoie notamment un
`GetBucketLocation` avant tout echange sur un bucket qu'il ne connait pas
encore (`Minio._get_region`) : c'est le critere 1, eliminatoire, car une
passerelle qui ne le sert pas ne sert rien.

Les identifiants viennent de l'environnement, jamais de la ligne de commande :
un secret passe en argument se lit dans la table des processus.

    ESSAI_S3_ENDPOINT=seaweedfs:8333 \\
    ESSAI_S3_BUCKET=documents \\
    ESSAI_S3_RW_ACCESS_KEY=... ESSAI_S3_RW_SECRET_KEY=... \\
    ESSAI_S3_RO_ACCESS_KEY=... ESSAI_S3_RO_SECRET_KEY=... \\
    python scripts/campagne/essayer-la-passerelle-s3.py

Controle negatif : rejouer la meme commande avec un jeu d'identifiants faux ;
le script doit alors sortir en 1.

Sortie : 0 si les sept criteres passent, 1 des qu'un seul echoue. Le critere 8
(l'empreinte des cles apres reingestion) ne vit pas ici : il se mesure sur le
store reel, pas sur un bucket d'essai.
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error

# Les cles d'epreuve de l'alphabet `[\w\-./]`, plus ce que le corpus reel
# contient deja et qui n'en fait pas partie : l'espace, et le deux-points
# pleine chasse `：` (U+FF1A), que les titres de chapitres trainent depuis la
# source. Une passerelle qui normalise l'un des deux rend une cle differente de
# celle qu'elle a recue, et le graphe pointe alors dans le vide sans qu'aucune
# erreur ne le dise.
CLES_D_EPREUVE: tuple[str, ...] = (
    "simple.png",
    "un/chemin/imbrique/page-12.png",
    "MAJUSCULES_et_minuscules-42.PNG",
    "point.dans.le.nom.v2.png",
    "tiret-bas_et-tiret-42.png",
    "avec un espace.png",
    "deux points ： pleine chasse.png",
    "accents-éèêàçüñ.png",
    "chiffres/0123456789.png",
    "a" * 120 + ".png",
)


class EchecDeCritereError(Exception):
    """Un critere n'est pas satisfait. Le message est ce qui sera imprime."""


@dataclass
class Verdict:
    """Le resultat d'un critere : son numero, son intitule, son sort."""

    numero: int
    intitule: str
    passe: bool
    detail: str
    eliminatoire: bool = False
    observations: list[str] = field(default_factory=list)


@dataclass
class Reglages:
    """Ce dont l'essai a besoin, lu dans l'environnement."""

    endpoint: str
    bucket: str
    rw_access_key: str
    rw_secret_key: str
    ro_access_key: str
    ro_secret_key: str
    cles: tuple[str, ...]


def _exiger(nom: str) -> str:
    """Lit une variable d'environnement, ou echoue en le disant."""
    valeur = os.environ.get(nom, "")
    if not valeur:
        raise SystemExit(f"Variable d'environnement manquante : {nom}")
    return valeur


def charger_les_reglages(chemin_des_cles: str | None) -> Reglages:
    """Assemble les reglages de l'essai.

    Args:
        chemin_des_cles: Fichier de cles reelles, une par ligne. Absent, les
            cles d'epreuve suffisent -- mais elles ne prouvent que l'alphabet,
            pas le corpus.

    Returns:
        Les reglages complets.
    """
    cles = CLES_D_EPREUVE
    if chemin_des_cles:
        with open(chemin_des_cles, encoding="utf-8") as fichier:
            reelles = tuple(ligne.rstrip("\n") for ligne in fichier if ligne.rstrip("\n"))
        cles = cles + reelles
    return Reglages(
        endpoint=_exiger("ESSAI_S3_ENDPOINT"),
        bucket=_exiger("ESSAI_S3_BUCKET"),
        rw_access_key=_exiger("ESSAI_S3_RW_ACCESS_KEY"),
        rw_secret_key=_exiger("ESSAI_S3_RW_SECRET_KEY"),
        ro_access_key=_exiger("ESSAI_S3_RO_ACCESS_KEY"),
        ro_secret_key=_exiger("ESSAI_S3_RO_SECRET_KEY"),
        cles=cles,
    )


def construire(reglages: Reglages, *, ecriture: bool) -> Any:
    """Construit un client `minio-py`, comme le pipeline et l'agent le font.

    Aucune region n'est fixee au constructeur, volontairement : la fixer
    supprimerait le `GetBucketLocation` du critere 1, et l'essai passerait sur
    une passerelle qui ne le sert pas.
    """
    return Minio(
        reglages.endpoint,
        access_key=reglages.rw_access_key if ecriture else reglages.ro_access_key,
        secret_key=reglages.rw_secret_key if ecriture else reglages.ro_secret_key,
        secure=False,
    )


def demander_la_region(client: Any, bucket: str) -> str:
    """Emet un vrai `GetBucketLocation`, cache de regions vide.

    `minio-py` retient la region du bucket au premier appel : sans ce vidage,
    le deuxieme critere qui la demande ne quitterait plus le processus.
    """
    client._region_map.pop(bucket, None)
    region: str = client._get_region(bucket)
    return region


def _refus(appel: Callable[[], Any]) -> tuple[bool, str]:
    """Execute un appel qui doit etre refuse, et rend le refus lisible.

    Returns:
        (refuse, description). `description` porte le code HTTP et le code S3
        tels que la passerelle les a rendus -- c'est le seul endroit ou un 403
        se distingue d'un 404.
    """
    try:
        resultat = appel()
        if isinstance(resultat, Iterator) or hasattr(resultat, "__next__"):
            list(resultat)
    except S3Error as exc:
        return True, f"refuse (HTTP {exc.response.status} / {exc.code})"
    except Exception as exc:  # noqa: BLE001 - tout refus non-S3 compte aussi
        return True, f"refuse ({type(exc).__name__}: {exc})"
    return False, "ACCEPTE -- le jeu lecture seule a pu faire l'operation"


def critere_1(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """`GetBucketLocation` servi, des deux cotes. ELIMINATOIRE."""
    observations = []
    for nom, client in (("ecriture", rw), ("lecture seule", ro)):
        region = demander_la_region(client, reglages.bucket)
        observations.append(f"{nom} : region « {region} »")
    return Verdict(
        1,
        "GetBucketLocation servi (les deux jeux)",
        True,
        "les deux clients obtiennent une region",
        eliminatoire=True,
        observations=observations,
    )


def critere_2(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """Deux jeux d'identifiants distincts, tous deux acceptes."""
    if reglages.rw_access_key == reglages.ro_access_key:
        raise EchecDeCritereError("les deux jeux portent la meme cle d'acces")
    observations = []
    for nom, client in (("ecriture", rw), ("lecture seule", ro)):
        seaux = [seau.name for seau in client.list_buckets()]
        observations.append(f"{nom} : authentifie, {len(seaux)} bucket(s) visible(s)")
    return Verdict(
        2,
        "Deux jeux aux droits distincts, sans console proprietaire",
        True,
        "les deux jeux sont declares par fichier et acceptes par la passerelle",
        observations=observations,
    )


def critere_3(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """Le jeu lecture seule lit : region, listage, objet, metadonnees."""
    temoin = "essai/critere-3-temoin.bin"
    rw.put_object(reglages.bucket, temoin, BytesIO(b"temoin"), 6)
    observations = [f"region : « {demander_la_region(ro, reglages.bucket)} »"]
    listage = list(ro.list_objects(reglages.bucket, prefix="essai/", recursive=True))
    observations.append(f"ListBucket : {len(listage)} objet(s) sous « essai/ »")
    reponse = ro.get_object(reglages.bucket, temoin)
    try:
        corps = reponse.read()
    finally:
        reponse.close()
        reponse.release_conn()
    if corps != b"temoin":
        raise EchecDeCritereError(f"GetObject rend {corps!r} au lieu de b'temoin'")
    observations.append("GetObject : 6 octets, identiques")
    stat = ro.stat_object(reglages.bucket, temoin)
    observations.append(f"StatObject : taille {stat.size}")
    return Verdict(
        3,
        "Le jeu lecture seule : GetBucketLocation, ListBucket, GetObject, StatObject",
        True,
        "les quatre lectures passent",
        observations=observations,
    )


def critere_4(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """Adresses path-style : `scheme://host[:port]/<bucket>/<objet>`."""
    del ro
    vues: list[str] = []
    origine = rw._http.urlopen

    def espion(methode: str, url: str, **kwargs: Any) -> Any:
        vues.append(url)
        return origine(methode, url, **kwargs)

    rw._http.urlopen = espion
    try:
        rw.stat_object(reglages.bucket, "essai/critere-3-temoin.bin")
    finally:
        rw._http.urlopen = origine
    if not vues:
        raise EchecDeCritereError("aucune URL observee : l'espion n'a rien capte")
    url = vues[-1]
    decoupe = urlsplit(url)
    attendu = f"/{reglages.bucket}/essai/critere-3-temoin.bin"
    if decoupe.netloc != reglages.endpoint:
        raise EchecDeCritereError(
            f"l'hote emis est « {decoupe.netloc} », pas « {reglages.endpoint} » : "
            "le bucket est passe dans le nom de domaine (virtual-host style)"
        )
    if decoupe.path != attendu:
        raise EchecDeCritereError(f"chemin emis « {decoupe.path} », attendu « {attendu} »")
    return Verdict(
        4,
        "Adresses path-style",
        True,
        "le bucket est dans le CHEMIN, pas dans l'hote",
        observations=[f"URL emise : {url}"],
    )


def critere_5(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """Les cinq ecritures avec le jeu ecriture, chacune refusee en lecture seule."""
    observations: list[str] = []
    seau_neuf = f"{reglages.bucket}-essai-make-bucket"
    objet = "essai/critere-5.bin"

    if not rw.bucket_exists(reglages.bucket):
        raise EchecDeCritereError(f"bucket_exists dit faux sur « {reglages.bucket} »")
    observations.append("ecriture / bucket_exists : vrai")

    if rw.bucket_exists(seau_neuf):
        rw.remove_bucket(seau_neuf)
    rw.make_bucket(seau_neuf)
    observations.append(f"ecriture / make_bucket : « {seau_neuf} » cree")
    rw.remove_bucket(seau_neuf)

    rw.put_object(reglages.bucket, objet, BytesIO(b"critere 5"), 9)
    observations.append("ecriture / put_object : 9 octets")
    trouves = [
        objet.object_name
        for objet in rw.list_objects(reglages.bucket, prefix="essai/", recursive=True)
    ]
    if objet not in trouves:
        raise EchecDeCritereError(f"list_objects ne rend pas « {objet} »")
    observations.append(f"ecriture / list_objects : {len(trouves)} objet(s)")
    rw.remove_object(reglages.bucket, objet)
    observations.append("ecriture / remove_object : fait")

    refus = (
        ("make_bucket", lambda: ro.make_bucket(f"{reglages.bucket}-interdit")),
        (
            "put_object",
            lambda: ro.put_object(reglages.bucket, "essai/interdit.bin", BytesIO(b"x"), 1),
        ),
        (
            "remove_object",
            lambda: ro.remove_object(reglages.bucket, "essai/critere-3-temoin.bin"),
        ),
    )
    echecs: list[str] = []
    for nom, appel in refus:
        refuse, detail = _refus(appel)
        observations.append(f"lecture seule / {nom} : {detail}")
        if not refuse:
            echecs.append(nom)
    if echecs:
        raise EchecDeCritereError(f"le jeu lecture seule a pu : {', '.join(echecs)}")

    # `bucket_exists` et `list_objects` restent permis en lecture seule : ils
    # relevent du critere 3, et les refuser casserait l'agent.
    observations.append(
        "lecture seule / bucket_exists et list_objects : permis, c'est le critere 3"
    )
    return Verdict(
        5,
        "Les cinq ecritures avec le jeu ecriture, "
        "les trois destructrices refusees en lecture seule",
        True,
        "droits separes, verifies par appel",
        observations=observations,
    )


def critere_6(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """L'alphabet des cles preserve a l'aller-retour, octet pour octet."""
    del ro
    prefixe = "essai/alphabet/"
    ecrites: list[str] = []
    for index, cle in enumerate(reglages.cles):
        complete = prefixe + cle
        rw.put_object(reglages.bucket, complete, BytesIO(f"{index}".encode()), len(str(index)))
        ecrites.append(complete)
    relues = {
        objet.object_name
        for objet in rw.list_objects(reglages.bucket, prefix=prefixe, recursive=True)
    }
    perdues = [cle for cle in ecrites if cle not in relues]
    if perdues:
        exemple = perdues[0]
        attendu_nfc = unicodedata.normalize("NFC", exemple)
        voisines = [c for c in relues if unicodedata.normalize("NFC", c) == attendu_nfc]
        indice = f" (une forme normalisee existe : {voisines[0]!r})" if voisines else ""
        raise EchecDeCritereError(
            f"{len(perdues)} cle(s) ne reviennent pas a l'identique, p.ex. {exemple!r}{indice}"
        )
    for cle in ecrites:
        rw.stat_object(reglages.bucket, cle)
    for cle in ecrites:
        rw.remove_object(reglages.bucket, cle)
    return Verdict(
        6,
        "Alphabet des cles preserve a l'aller-retour",
        True,
        f"{len(ecrites)} cles ecrites, listees et statees a l'identique",
        observations=[
            f"dont {len(CLES_D_EPREUVE)} cles d'epreuve "
            f"et {len(reglages.cles) - len(CLES_D_EPREUVE)} cles reelles du corpus"
        ],
    )


def critere_7(reglages: Reglages, rw: Any, ro: Any) -> Verdict:
    """Les droits mesures par APPEL DIRECT : le code HTTP, pas l'ecran."""
    del rw
    refuse, detail = _refus(
        lambda: ro.put_object(reglages.bucket, "essai/critere-7.bin", BytesIO(b"x"), 1)
    )
    if not refuse:
        raise EchecDeCritereError("le jeu lecture seule a pu ecrire")
    if "HTTP 403" not in detail:
        raise EchecDeCritereError(
            f"le refus n'est pas un 403 mais « {detail} » : un 404 remonterait chez "
            "l'agent comme une image simplement absente, et le droit mal pose "
            "resterait invisible"
        )
    return Verdict(
        7,
        "Droits mesures par appel direct : un refus est un 403, pas un 404",
        True,
        detail,
        observations=["l'ecart 403/404 est justement ce qu'aucune console ne montre"],
    )


CRITERES: tuple[Callable[[Reglages, Any, Any], Verdict], ...] = (
    critere_1,
    critere_2,
    critere_3,
    critere_4,
    critere_5,
    critere_6,
    critere_7,
)


def preparer_le_bucket(reglages: Reglages, rw: Any) -> None:
    """Cree le bucket d'essai s'il manque.

    `GetBucketLocation` sur un bucket inexistant echoue pour une raison qui
    n'est pas celle du critere 1 : l'essai serait rouge sans rien dire de la
    passerelle.
    """
    if not rw.bucket_exists(reglages.bucket):
        rw.make_bucket(reglages.bucket)


def nettoyer(reglages: Reglages, rw: Any) -> None:
    """Retire ce que l'essai a depose. Il ne laisse rien derriere lui."""
    for objet in rw.list_objects(reglages.bucket, prefix="essai/", recursive=True):
        rw.remove_object(reglages.bucket, objet.object_name)


def main() -> int:
    """Joue les sept criteres et rend le verdict."""
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--cles",
        default=None,
        help="fichier de cles reelles (une par ligne) a ajouter au critere 6",
    )
    arguments = analyseur.parse_args()
    reglages = charger_les_reglages(arguments.cles)

    print(f"Passerelle S3 : {reglages.endpoint}, bucket « {reglages.bucket} »")
    print(
        f"Jeux : ecriture « {reglages.rw_access_key[:4]}… », "
        f"lecture seule « {reglages.ro_access_key[:4]}… »\n"
    )

    rw = construire(reglages, ecriture=True)
    ro = construire(reglages, ecriture=False)

    try:
        preparer_le_bucket(reglages, rw)
    except Exception as exc:  # noqa: BLE001 - la preparation n'est pas un critere
        print(f"PREPARATION IMPOSSIBLE : {type(exc).__name__}: {exc}")
        print("\nVERDICT : ROUGE (le bucket d'essai n'a pas pu etre cree)")
        return 1

    verdicts: list[Verdict] = []
    for fonction in CRITERES:
        numero = int(fonction.__name__.split("_")[1])
        try:
            verdict = fonction(reglages, rw, ro)
        except EchecDeCritereError as exc:
            verdict = Verdict(numero, fonction.__doc__ or "", False, str(exc))
        except Exception as exc:  # noqa: BLE001 - un critere qui leve est un critere rouge
            verdict = Verdict(numero, fonction.__doc__ or "", False, f"{type(exc).__name__}: {exc}")
        if numero == 1:
            verdict.eliminatoire = True
        verdicts.append(verdict)
        marque = "OK  " if verdict.passe else "ECHEC"
        print(f"[{marque}] critere {verdict.numero} — {verdict.intitule.strip()}")
        for observation in verdict.observations:
            print(f"         {observation}")
        print(f"         → {verdict.detail}")
        if not verdict.passe and verdict.eliminatoire:
            print("\nCritere 1 ECHOUE et il est ELIMINATOIRE : l'essai s'arrete ici.")
            return 1

    try:
        nettoyer(reglages, rw)
    except Exception as exc:  # noqa: BLE001 - le menage rate ne juge pas la passerelle
        print(f"\n(menage incomplet : {exc})")

    rouges = [verdict for verdict in verdicts if not verdict.passe]
    print(f"\n{len(verdicts) - len(rouges)}/{len(verdicts)} criteres passes.")
    if rouges:
        print("VERDICT : ROUGE — " + ", ".join(f"critere {v.numero}" for v in rouges))
        return 1
    print("VERDICT : VERT — les sept criteres passent sur cette passerelle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
