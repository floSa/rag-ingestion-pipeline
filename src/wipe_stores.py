"""Purge des stores avant une re-ingestion propre.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python -m src.wipe_stores
    docker compose restart docling-service   # recree le schema NebulaGraph

Utile notamment quand la chaine d'extraction change : les identifiants
d'elements derivent de leur texte, si bien qu'une extraction modifiee produit
de nouveaux identifiants et laisse les anciens en orphelins.

**Et le HTML nettoye, pas seulement les stores.** `Datas/.cleaned/` n'etait pas
purge, et c'est le piege le plus discret de cette purge : le HTML nettoye porte
les URL MinIO des images, et l'asset Dagster `cleaned_html` ne se rematerialise
pas si son fichier existe deja. Une purge suivie d'une reingestion repartait donc
du HTML PERIME, pointant des objets que la purge venait de supprimer. `mesure` :
13 objets dans le bucket, 199 URL referencees dans `Datas/.cleaned/`. Voir
:func:`purge_cleaned`.

**Ce module SUPPRIME des repertoires, et sa cible ne vient plus d'un reglage.**
Elle venait de `CLEANED_SUBDIR`, annonce dans `.env.example` : quatre de ses
valeurs faisaient viser `Datas/` ou son parent, et une cinquieme famille — toute
valeur bien contenue mais fausse, `htms`, `database` — passait le containment et
detruisait le corpus ou les stores. Le sous-repertoire est desormais la constante
`src.docling_service.elements.CLEANED_SUBDIR` (registre 4.29.a).

`purge_cleaned` GARDE son containment, et il n'est pas devenu inutile : il
protege contre un `source_dir` mal regle, qui reste un reglage legitime. Le
detail et le motif du refus dur sont a son docstring.

**Les trois stores, pas deux.** Le bucket MinIO etait laisse intact : les crops
d'images des ingestions precedentes y survivaient a toute purge. Ce n'est pas
une fuite — l'agent ne sert que les objets references par le graphe
(``RESTRICT_MEDIA_TO_GRAPH=true``), donc un objet dont le noeud a disparu est
deja inaccessible — mais c'est de la place perdue qui grossit a chaque
re-ingestion, et un bucket qu'on ne peut plus lire pour se rassurer.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from src.docling_service.elements import CLEANED_SUBDIR, cleaned_root
from src.docling_service.settings import get_settings

# Les noms de collection et de space vivent dans nebula.py et vectors.py, qui
# tirent nebula3 et chromadb. Ils sont importes dans main() : ce module doit
# rester importable sans les clients, pour que la purge soit testable hors de
# l'image d'extraction.


def purge_collection(client: Any, nom: str) -> None:
    """Supprime la collection ChromaDB.

    Args:
        client: Client ChromaDB.
        nom: Nom de la collection.
    """
    client.delete_collection(nom)


def purge_bucket(client: Any, bucket: str) -> int:
    """Vide un bucket MinIO de tous ses objets.

    ``recursive=True`` n'est pas un detail : sans lui, ``list_objects`` ne rend
    que les prefixes de premier niveau et la purge laisse derriere elle tout le
    contenu de ``images/{stem}/``, sans rien signaler.

    Les objets sont supprimes un a un plutot que par ``remove_objects`` : ce
    dernier rend un iterateur d'erreurs qu'il faut penser a consommer, et une
    erreur non consommee est une suppression qu'on croit faite.

    Args:
        client: Client MinIO.
        bucket: Nom du bucket a vider.

    Returns:
        Le nombre d'objets supprimes.
    """
    if not client.bucket_exists(bucket):
        return 0

    supprimes = 0
    for objet in client.list_objects(bucket, recursive=True):
        client.remove_object(bucket, objet.object_name)
        supprimes += 1
    return supprimes


def purge_space(session: Any, space: str) -> str:
    """Supprime le space NebulaGraph.

    Ce docstring affirmait « le schema n'evolue pas en place : c'est un DROP
    puis une recreation au redemarrage du service, jamais une migration ».
    C'est faux d'une PROPRIETE de tag : ``ALTER TAG ... ADD`` reussit sur un
    space peuple (`mesure`, 31 aout 2026, 15 196 sommets), et le service joue
    cette migration a chaque demarrage. Ce qui n'evolue effectivement pas en
    place est le ``vid_type`` du space — voir ``VID_MAX_BYTES`` dans ``ngql.py``.

    Purger reste donc le geste qu'il faut quand on veut REPEUPLER une colonne
    ajoutee : le schema migre, les donnees non, et les sommets deja ecrits
    gardent NULL jusqu'a leur reecriture. Et c'est le seul recours apres un
    ``ALTER ... DROP``, que Nebula n'autorise jamais a defaire.

    Args:
        session: Session NebulaGraph.
        space: Nom du space.

    Returns:
        Un message decrivant le resultat.
    """
    result = session.execute(f"DROP SPACE IF EXISTS {space};")
    if result.is_succeeded():
        return f"space {space} supprime"
    return f"DROP SPACE : {result.error_msg()}"


class CibleHorsRacineError(RuntimeError):
    """La cible de la purge n'est pas strictement contenue dans la racine.

    Levee AVANT tout `rmtree`. Voir :func:`purge_cleaned` pour ce que ce refus
    protege et pourquoi il est dur.
    """


def purge_cleaned(repertoire: Path, racine: Path) -> int:
    """Supprime le HTML nettoye, et REFUSE toute cible hors de la racine.

    `Datas/.cleaned/` n'etait PAS purge, et cela ne se voit pas. Le HTML nettoye
    porte les URL MinIO des images — `cleaning.py` reecrit les `img src` — et
    l'asset Dagster `cleaned_html` ne se rematerialise pas si son fichier de
    sortie existe deja.

    `mesure` le 1er septembre 2026 : le bucket porte **13** objets, tous des
    crops du PDF, et `Datas/.cleaned/` reference **199** URL
    `http://minio:9000/...` d'objets qui n'existent PAS. Une purge suivie d'une
    reingestion repart donc du HTML nettoye PERIME et pointe 199 objets absents.
    **Reextraire ne suffit pas** : seule une execution de `cleaned_html` les
    restaure, en re-televersant les images depuis les captures (registre 4.28.b).

    **CE DOCSTRING AFFIRMAIT « La cible est le SOUS-REPERTOIRE nettoye, JAMAIS
    `Datas/` ». C'ETAIT UNE PHRASE D'EXHAUSTIVITE, ET ELLE ETAIT FAUSSE SOUS
    CONFIGURATION.** `main()` calculait
    `Path(reglages.source_dir) / reglages.cleaned_subdir`, et `CLEANED_SUBDIR`
    etait un reglage annonce a l'operateur. Quatre de ses valeurs faisaient viser
    la racine ou au-dessus, et deux autres, bien contenues, detruisaient le
    corpus ou les stores (`mesure`) :

    ==================== =========================================
    ``CLEANED_SUBDIR``   Ce que `main()` passait a ce `rmtree`
    ==================== =========================================
    ``""``               ``/x/Datas`` — ``Path(base) / ""`` vaut ``base``
    ``"."``              ``/x/Datas``, apres resolution
    ``".."``             ``/x`` — le PARENT de la racine
    ``"/quelque/part"``  ``/quelque/part`` — un absolu REMPLACE la base
    ``"htms"``           ``/x/Datas/htms`` — CONTENU, donc accepte : 24 des 25
                         fichiers du corpus versionne
    ``"database"``       ``/x/Datas/database`` — CONTENU : les cinq stores
    ==================== =========================================

    **Le reglage n'existe plus** (registre 4.29.a) : la cible est
    `elements.cleaned_root(source_dir)`, et les six valeurs ci-dessus sont
    inertes. Ce qui suit decrit donc ce que ce garde protege encore, et il
    protege encore quelque chose : `source_dir` reste un reglage.

    Sur ce poste, `Datas/` porte le corpus VERSIONNE — 25 fichiers,
    57 381 999 octets, dont le contenu entre dans le calcul d'`element_id`
    (contrat, exigences 2 et 3) — **et** `Datas/database/`, les bind mounts de
    ChromaDB, Nebula, MinIO et Postgres, c'est-a-dire l'antecedent mesure du
    chantier. `rmtree` ne lit pas `.gitignore` : aucun garde-fou git ne s'y
    opposerait.

    **LE REFUS EST DUR, ET LA PORTEE DE CETTE FONCTION EST DESORMAIS BORNEE AU
    LIEU D'ETRE PROMISE.** Ni avertissement, ni repli sur le defaut : une cible
    qui n'est pas STRICTEMENT contenue dans `racine` apres resolution leve, et
    `main()` la verse a ses `echecs` — code de sortie 1. *Une purge qui ne sait
    pas ce qu'elle vise ne purge pas.* Un repli silencieux sur `.cleaned` serait
    pire : l'operateur croirait avoir configure une cible que le code aurait
    remplacee sans le dire, ce qui est la famille de defaut que ce lot ferme.

    La comparaison porte sur le chemin RESOLU des deux cotes : un `.cleaned` qui
    serait un lien symbolique vers l'exterieur passerait toute comparaison
    textuelle, et `rmtree` suivrait le lien.

    Args:
        repertoire: Repertoire du HTML nettoye, tel que le reglage le designe.
        racine: Racine des donnees (``source_dir``). La cible doit y etre
            strictement contenue.

    Returns:
        Le nombre de fichiers retires. Une purge muette ne dit pas si elle a
        retire un fichier ou vingt-deux.

    Raises:
        CibleHorsRacineError: Si la cible n'est pas strictement contenue dans
            ``racine``.
    """
    cible = repertoire.resolve()
    base = racine.resolve()
    # `parents` EXCLUT le chemin lui-meme : c'est ce qui rend le containment
    # STRICT, donc ce qui refuse une cible egale a la racine — le cas d'un
    # `source_dir` qui pointe la ou la constante devrait mener.
    if base not in cible.parents:
        raise CibleHorsRacineError(
            f"cible {cible} hors de {base} : refus de purger. La cible est "
            f"SOURCE_DIR/{CLEANED_SUBDIR}, et c'est donc SOURCE_DIR qui est mal "
            f"regle — une racine vide, relative ou pointant ailleurs fait viser "
            f"un repertoire qui n'est pas le sien, et sous Datas/ vivent le "
            f"corpus versionne et les stores de Datas/database/"
        )
    if not cible.exists():
        return 0
    fichiers = sum(1 for chemin in cible.rglob("*") if chemin.is_file())
    shutil.rmtree(cible)
    return fichiers


def main() -> None:
    """Purge les trois stores et le HTML nettoye, et rend compte de chacun."""
    import chromadb

    from src.docling_service import images
    from src.docling_service.nebula import SPACE, get_writer
    from src.docling_service.vectors import COLLECTION_NAME

    settings = get_settings()
    echecs: list[str] = []

    print("--- ChromaDB ---")
    try:
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        purge_collection(client, COLLECTION_NAME)
        print(f"collection {COLLECTION_NAME} supprimee")
    except Exception as exc:
        # LARGEUR VOULUE : les trois stores doivent etre TENTES, meme si le
        # premier est a terre. Une purge qui s'arreterait a la premiere panne
        # laisserait les deux autres peuples, et c'est l'etat exact que ce
        # script existe pour eviter. `chromadb` leve par ailleurs ses propres
        # types selon la couche qui echoue — HTTP, protocole, collection
        # absente — sans base commune sur laquelle se raccrocher.
        #
        # La consequence n'est PAS avalee : le store est nomme, et `echecs`
        # fait sortir en 1 plus bas. Ce qui manquait etait cette phrase.
        print(f"ChromaDB : {exc}")
        echecs.append("ChromaDB")

    print("\n--- MinIO ---")
    try:
        supprimes = purge_bucket(images.get_client(), settings.minio_bucket)
        print(f"{supprimes} objets supprimes du bucket {settings.minio_bucket}")
    except Exception as exc:
        # LARGEUR VOULUE, meme motif que ci-dessus : le graphe doit encore etre
        # tente. `minio` leve `S3Error`, mais aussi les erreurs reseau de
        # `urllib3` qui n'en descendent pas. L'echec est nomme et compte.
        print(f"MinIO : {exc}")
        echecs.append("MinIO")

    print("\n--- NebulaGraph ---")
    writer = get_writer()
    try:
        # use_space=False : on ne se place pas dans le space qu'on s'apprete a
        # supprimer, et le DROP doit rester possible meme s'il n'existe plus.
        with writer.session(use_space=False) as session:
            print(purge_space(session, SPACE))
    except Exception as exc:
        # LARGEUR VOULUE : c'est le dernier des trois, mais l'`except` doit
        # rester large pour que le `finally` ferme le pool et que le bilan
        # s'affiche. `NebulaWriter.session` leve `NebulaError`, et le pool
        # sous-jacent leve ses propres types de transport avant meme d'y
        # arriver. L'echec est nomme et compte.
        print(f"NebulaGraph : {exc}")
        echecs.append("NebulaGraph")
    finally:
        writer.close()

    print("\n--- HTML nettoye ---")
    try:
        # `source_dir` appartient a `PipelineSettings` et non aux reglages du
        # service : le recopier ici en creerait un second site, donc une
        # divergence possible sur le chemin qu'on s'apprete a SUPPRIMER. L'import
        # est local pour que ce module reste importable sans les dependances de
        # l'orchestrateur.
        from src.pipeline.settings import get_settings as get_pipeline_settings

        reglages = get_pipeline_settings()
        # LA RACINE EST PASSEE, ET C'EST CE QUI REND LE REFUS POSSIBLE. La cible
        # ne se compose plus que d'UN reglage — `source_dir` — et d'une constante.
        # `cleaned_root` est le seul site de la derivation, partage avec l'asset
        # qui ECRIT ce repertoire : deux calculs du meme chemin peuvent diverger,
        # et ici la divergence supprimerait le mauvais. `purge_cleaned` decide du
        # containment, pas cet appelant — un controle pose ici laisserait la
        # fonction publique sans garde pour tout autre appelant.
        nettoye = cleaned_root(reglages.source_dir)
        retires = purge_cleaned(nettoye, Path(reglages.source_dir))
        print(f"{retires} fichiers retires de {nettoye}")
    except Exception as exc:
        # LARGEUR VOULUE, meme motif que les trois stores : le bilan doit se
        # former. `rmtree` leve `OSError` mais aussi les erreurs de permission
        # d'un repertoire ecrit par Docker en `root`, cas connu de ce depot.
        # `CibleHorsRacineError` passe volontairement par ici : un refus de
        # containment EST une purge incomplete, et il doit sortir en 1 comme les
        # trois autres. Il est nomme dans la sortie avec sa cause.
        print(f"HTML nettoye : {exc}")
        echecs.append("HTML nettoye")

    print("\nRedemarrer docling-service pour recreer le schema.")
    if echecs:
        # Une purge partielle est pire qu'une purge absente : on croit repartir
        # propre et on re-ingere par-dessus des restes.
        print(f"PURGE INCOMPLETE : {', '.join(echecs)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
