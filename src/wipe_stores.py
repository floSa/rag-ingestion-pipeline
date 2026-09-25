"""Purge des stores avant une re-ingestion propre.

Vide les trois stores (collection ChromaDB, bucket du stockage objet, space
NebulaGraph) et le HTML nettoye. A lancer dans un conteneur jetable du service
d'extraction, puis redemarrer le service pour recreer le schema NebulaGraph :

    docker compose run --rm --no-deps -T -e PYTHONPATH=/app -w /app \\
      docling-service python -m src.wipe_stores
    docker compose restart docling-service

Utile quand la chaine d'extraction change : les identifiants d'elements
derivent de leur texte, donc une extraction modifiee produit de nouveaux
identifiants et laisse les anciens en orphelins.

Le bucket est vide lui aussi. L'agent ne sert que les objets references par le
graphe (``RESTRICT_MEDIA_TO_GRAPH=true``), donc un objet orphelin n'est pas une
fuite ; mais il occupe de la place a chaque reingestion.

Le HTML nettoye (`Datas/.cleaned/`) est purge pour retirer les copies de
documents sortis du corpus : voir :func:`purge_cleaned`. Son sous-repertoire est
la constante `src.docling_service.elements.CLEANED_SUBDIR`, et non un reglage
(registre 4.29.a) : un reglage mal pose pouvait faire viser le corpus ou les
stores.
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
    """Vide un bucket du stockage objet de tous ses objets.

    Sans ``recursive=True``, ``list_objects`` ne rend que les prefixes de
    premier niveau, et le contenu de ``images/{stem}/`` resterait en place sans
    erreur.

    Les objets sont supprimes un a un plutot que par ``remove_objects`` : ce
    dernier rend un iterateur d'erreurs qu'il faut consommer, faute de quoi
    une suppression echouee passe inapercue.

    Args:
        client: Client S3.
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

    Une propriete de tag s'ajoute en place : ``ALTER TAG ... ADD`` reussit sur
    un space peuple (`mesure` le 31 aout 2026, 15 196 sommets), et le service
    joue cette migration a chaque demarrage. Le ``vid_type`` du space, lui, ne
    change pas en place (voir ``VID_MAX_BYTES`` dans ``ngql.py``).

    Purger sert donc a repeupler une colonne ajoutee (les sommets deja ecrits
    gardent NULL jusqu'a leur reecriture), et c'est le seul recours apres un
    ``ALTER ... DROP``, que Nebula ne permet pas de defaire.

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


class CiblePurgeRefuseeError(RuntimeError):
    """Base commune des deux refus de :func:`purge_cleaned`.

    Elle permet d'attraper les deux ensemble sans les enumerer. Les deux sont
    levees avant tout `rmtree`.
    """


class CibleHorsRacineError(CiblePurgeRefuseeError):
    """La cible de la purge n'est pas strictement contenue dans la racine.

    Le cas d'un `SOURCE_DIR` mal regle, et celui d'un `.cleaned` qui serait un
    lien vers l'exterieur. Voir :func:`purge_cleaned`.
    """


class CibleHorsDuNettoyeError(CiblePurgeRefuseeError):
    """La cible est bien dans la racine, mais ce n'est pas le repertoire nettoye.

    Le containment seul ne suffit pas (registre 4.29.a) : `Datas/htms` est
    strictement contenu dans `Datas`, et porte l'essentiel du corpus versionne.
    Voir :func:`purge_cleaned`.
    """


def purge_cleaned(repertoire: Path, racine: Path) -> int:
    """Supprime le HTML nettoye, et refuse toute autre cible.

    Ce que la purge retire : les copies nettoyees des documents sortis du
    corpus. Une reingestion n'en a pas besoin pour les autres : l'asset
    `cleaned_html` reecrit sa destination a chaque materialisation (`mesure`
    le 22 septembre 2026, registre 4.33.a). Mais la copie d'un document retire
    n'est ni reecrite ni effacee par aucun autre chemin :

    - le capteur ne la voit pas : le glob d'une source est ancre sous son
      sous-repertoire (`htms/**/*.html`), et `glob` n'ouvre pas `.cleaned`,
      qui commence par un point ;
    - `cleaned_html` ne s'execute pas pour elle, car son controle d'existence
      porte sur la source, qui n'existe plus.

    Cette copie porte les adresses d'objets de ses images (`cleaning.py`
    reecrit les `img src`), et la purge du bucket, plus haut dans `main()`,
    vient de supprimer ces objets. Sans cette purge, il resterait un fichier
    derive d'un document absent, pointant des objets supprimes.

    La partition dynamique Dagster d'un document retire n'est pas supprimee
    non plus, ni par ce module ni ailleurs (registre 4.34.g).

    Ce comportement est teste par `TestCeQueLaPurgeDuNettoyeRetireVRAIMENT`,
    dans `tests/unit/test_factory.py`.

    Deux refus protegent `rmtree`. Sous `Datas/` vivent le corpus versionne
    (dont le contenu entre dans le calcul d'`element_id`) et `Datas/database/`,
    les bind mounts des stores ; `rmtree` ne lit pas `.gitignore`. Le refus
    leve, et `main()` le compte parmi ses `echecs` (code de sortie 1). Aucun
    repli silencieux sur une cible par defaut.

    1. Containment strict dans ``racine``, sur les chemins resolus des deux
       cotes : un lien symbolique vers l'exterieur passerait une comparaison
       textuelle, et `rmtree` le suivrait. Leve :class:`CibleHorsRacineError`,
       qui designe `SOURCE_DIR`.
    2. La cible est `racine/CLEANED_SUBDIR` ou un descendant. Leve
       :class:`CibleHorsDuNettoyeError`, qui designe l'argument. Sans cette
       borne, `Datas/htms` ou `Datas/database`, strictement contenus dans la
       racine, seraient acceptes (registre 4.29.a).

    ``cleaned_root(base)`` n'est volontairement pas resolu. Le resoudre ferait
    suivre au controle le meme lien que la cible : un `.cleaned` lie vers
    `Datas/htms` resoudrait des deux cotes vers `Datas/htms`, et serait accepte.
    Teste par `test_un_lien_du_nettoye_vers_le_corpus_est_refuse`.

    L'ordre des deux controles decide de la cause nommee, pas du verdict : un
    lien qui sort de la racine est refuse dans les deux ordres, et le
    containment nomme la bonne cause.

    Args:
        repertoire: Repertoire du HTML nettoye. La seule valeur nominale est
            ``elements.cleaned_root(racine)``.
        racine: Racine des donnees (``source_dir``). La cible doit y etre
            strictement contenue, ET etre le repertoire nettoye ou un de ses
            descendants.

    Returns:
        Le nombre de fichiers retires.

    Raises:
        CibleHorsRacineError: Si la cible n'est pas strictement contenue dans
            ``racine``.
        CibleHorsDuNettoyeError: Si la cible est dans ``racine`` sans etre le
            repertoire nettoye ni l'un de ses descendants.
    """
    cible = repertoire.resolve()
    base = racine.resolve()
    # `parents` exclut le chemin lui-meme : le containment est donc strict, et
    # une cible egale a la racine est refusee.
    if base not in cible.parents:
        raise CibleHorsRacineError(
            f"cible {cible} hors de {base} : refus de purger. La cible est "
            f"SOURCE_DIR/{CLEANED_SUBDIR}, et c'est donc SOURCE_DIR qui est mal "
            f"regle — une racine vide, relative ou pointant ailleurs fait viser "
            f"un repertoire qui n'est pas le sien, et sous Datas/ vivent le "
            f"corpus versionne et les stores de Datas/database/"
        )
    nettoye = cleaned_root(base)
    if cible != nettoye and nettoye not in cible.parents:
        raise CibleHorsDuNettoyeError(
            f"cible {cible} hors de {nettoye} : refus de purger. Cette fonction ne "
            f"supprime que le repertoire du HTML nettoye et ce qu'il contient. Une "
            f"cible bien contenue dans {base} mais autre est precisement ce qui "
            f"emportait le corpus versionne quand le sous-repertoire etait un "
            f"reglage (registre 4.29.a)"
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
        # `except` large voulu : les trois stores doivent etre tentes meme si
        # le premier est a terre. `chromadb` leve des types differents selon
        # la couche (HTTP, protocole, collection absente), sans base commune.
        # L'echec n'est pas avale : le store est nomme, et `echecs` fait sortir
        # en 1.
        print(f"ChromaDB : {exc}")
        echecs.append("ChromaDB")

    # L'adresse du stockage vide est affichee avant la purge, pour que
    # l'operateur voie quel serveur est vise. `S3_ENDPOINT` n'a pas de valeur
    # par defaut (`src/reglages_s3.py`) : sans elle, les reglages echouent
    # avant ce point.
    print(f"\n--- Stockage objet ({settings.s3_endpoint}) ---")
    try:
        supprimes = purge_bucket(images.get_client(), settings.s3_bucket)
        print(f"{supprimes} objets supprimes du bucket {settings.s3_bucket}")
    except Exception as exc:
        # `except` large, meme motif : le graphe doit encore etre tente. Le
        # client leve `S3Error`, mais aussi des erreurs reseau `urllib3` qui
        # n'en descendent pas. L'echec est nomme et compte.
        print(f"Stockage objet : {exc}")
        echecs.append("Stockage objet")

    print("\n--- NebulaGraph ---")
    writer = get_writer()
    try:
        # use_space=False : on ne se place pas dans le space qu'on s'apprete a
        # supprimer, et le DROP doit rester possible meme s'il n'existe plus.
        with writer.session(use_space=False) as session:
            print(purge_space(session, SPACE))
    except Exception as exc:
        # `except` large : le HTML nettoye doit encore etre tente et le bilan
        # affiche. `NebulaWriter.session` leve `NebulaError`, et le pool
        # sous-jacent ses propres erreurs de transport. L'echec est nomme et
        # compte.
        print(f"NebulaGraph : {exc}")
        echecs.append("NebulaGraph")
    finally:
        writer.close()

    print("\n--- HTML nettoye ---")
    try:
        # `source_dir` appartient a `PipelineSettings` : le redefinir ici
        # creerait une seconde source pour le chemin a supprimer. L'import est
        # local pour que ce module reste importable sans les dependances de
        # l'orchestrateur.
        from src.pipeline.settings import get_settings as get_pipeline_settings

        reglages = get_pipeline_settings()
        # `cleaned_root` est la seule derivation du chemin, partagee avec
        # l'asset qui ecrit ce repertoire. La racine est passee pour que
        # `purge_cleaned` fasse lui-meme ses controles, quel que soit
        # l'appelant.
        nettoye = cleaned_root(reglages.source_dir)
        retires = purge_cleaned(nettoye, Path(reglages.source_dir))
        print(f"{retires} fichiers retires de {nettoye}")
    except Exception as exc:
        # `except` large, meme motif : le bilan doit s'afficher. `rmtree` leve
        # `OSError`, dont les erreurs de permission d'un repertoire ecrit par
        # Docker en `root`. Les refus de `purge_cleaned` passent aussi ici :
        # un refus est une purge incomplete, qui sort en 1 avec sa cause.
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
