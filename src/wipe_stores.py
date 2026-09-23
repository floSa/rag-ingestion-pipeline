"""Purge des stores avant une re-ingestion propre.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python -m src.wipe_stores
    docker compose restart docling-service   # recree le schema NebulaGraph

Utile notamment quand la chaine d'extraction change : les identifiants
d'elements derivent de leur texte, si bien qu'une extraction modifiee produit
de nouveaux identifiants et laisse les anciens en orphelins.

**Et le HTML nettoye, pas seulement les stores.** `Datas/.cleaned/` n'etait pas
purge. **LE MOTIF ECRIT ICI ETAIT FAUX**, et il a survecu a quatre lots : il
disait que « l'asset `cleaned_html` ne se rematerialise pas si son fichier
existe deja ». `mesure` le 22 septembre 2026, en appelant le corps livre de
l'asset DEUX fois sur une copie temporaire, la seconde sur une destination
remplie d'un contenu perime : la destination est reecrite, le contenu perime
disparait, et le resultat est l'octet du premier nettoyage. `clean_html_file`
ecrit sa destination sans la regarder ; il n'existe aucun court-circuit
« le fichier nettoye existe, on ne refait pas » (registre 4.33.a).

Ce que la purge retire reellement, et qui n'est pas ce que cette phrase
promettait, est a :func:`purge_cleaned`.

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


class CiblePurgeRefuseeError(RuntimeError):
    """Base commune des deux refus de :func:`purge_cleaned`.

    Elle existe pour qu'un appelant puisse les attraper ENSEMBLE sans enumerer
    une liste qui perimerait au prochain garde ajoute. Les deux sont levees AVANT
    tout `rmtree` : ce module ne supprime rien dont il n'ait etabli la cible.
    """


class CibleHorsRacineError(CiblePurgeRefuseeError):
    """La cible de la purge n'est pas strictement contenue dans la racine.

    Le cas d'un `SOURCE_DIR` mal regle, et celui d'un `.cleaned` qui serait un
    LIEN vers l'exterieur. Voir :func:`purge_cleaned`.
    """


class CibleHorsDuNettoyeError(CiblePurgeRefuseeError):
    """La cible est bien dans la racine, mais ce n'est pas le repertoire nettoye.

    C'EST LA FAMILLE DU 4.29.a, et le containment seul ne la voyait pas :
    `Datas/htms` est strictement contenu dans `Datas` — il portait 24 des 25
    fichiers du corpus versionne. Voir :func:`purge_cleaned`.
    """


def purge_cleaned(repertoire: Path, racine: Path) -> int:
    """Supprime le HTML nettoye, et REFUSE toute cible qui n'est pas lui.

    **LA RAISON ECRITE ICI PENDANT QUATRE LOTS ETAIT FAUSSE.** Elle disait que
    « l'asset `cleaned_html` ne se rematerialise pas si son fichier de sortie
    existe deja », et en tirait qu'une reingestion sans purge repartirait du HTML
    PERIME. `mesure` le 22 septembre 2026, corps livre de l'asset appele DEUX
    fois sur une copie temporaire, la seconde sur une destination remplie d'un
    contenu perime : la destination est reecrite, le contenu perime disparait, et
    l'octet rendu est celui du premier nettoyage. Rien de ce qu'une reingestion
    retouche n'avait besoin de cette purge (registre 4.33.a).

    **CE QUE LA PURGE RETIRE, ET ELLE SEULE : LES ORPHELINS.** `mesure`, meme
    jour, meme harnais : deux documents nettoyes, la source de l'un retiree du
    corpus, l'autre rematerialise — la copie nettoyee du document DISPARU est
    toujours la. Aucun chemin ne la reecrit, aucun ne l'efface :

    - le capteur ne la voit pas. Le glob d'une source est ancre sous son propre
      sous-repertoire — `htms/**/*.html` — et `.cleaned` porte de surcroit un
      point de tete, que `glob` n'ouvre jamais, meme derriere `**` ;
    - `cleaned_html` ne peut pas s'executer pour elle : son controle d'existence
      porte sur la SOURCE, et la source n'existe plus.

    **Et un orphelin est perime SANS RECOURS.** Il porte les URL MinIO de ses
    images — `cleaning.py` reecrit les `img src` — et la purge du bucket, trois
    blocs plus haut dans le meme `main()`, vient de supprimer les objets qu'elles
    designent. Or `cleaned_html` est le SEUL chemin qui re-televerse ces images
    (`mesure` de la campagne du 2 septembre 2026 : 0 objet dans le bucket avant
    le geste 3, 199 apres), et il ne s'executera jamais pour un document absent.
    Sans cette purge, `wipe_stores` laisserait donc derriere lui un artefact
    DERIVE d'un document que le corpus n'a plus, pointant des objets qui
    n'existent plus. C'est tout ce que cette purge fait, et c'est ce que
    « repartir propre » veut dire ici.

    **CETTE PHRASE A DIT « LE SEUL ENDROIT DU SYSTEME », ET C'ETAIT FAUX.** La
    passe de relecture du lot 9 l'a mise en defaut sur son propre depot : la
    PARTITION DYNAMIQUE Dagster d'un document retire du corpus n'est jamais
    supprimee non plus, ni son historique de materialisations, et `wipe_stores`
    n'y touche pas davantage. Le depot ne porte AUCUN appel de suppression de
    partition dynamique — mesure et commande au registre 4.34.g, site canonique
    de ce chiffre.

    Ce que cette purge retire est donc le seul artefact derive qui porte des URL
    MinIO mortes, ce qui est plus etroit que la phrase precedente, et ce qui se
    garde.

    Le garde de cette propriete est `TestCeQueLaPurgeDuNettoyeRetireVRAIMENT`,
    dans `tests/unit/test_factory.py` : il tient les DEUX natures, celle qui est
    reecrite et celle qui survit. Une seule des deux serait creuse.

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

    **LA SECONDE BORNE, POSEE PAR LE LOT 9, ET C'EST ELLE QUI FERME LA FAMILLE
    DU 4.29.a.** Le containment seul acceptait `Datas/htms` et `Datas/database`,
    qui sont strictement contenus dans la racine — c'est exactement ce qui a
    emporte 24 des 25 fichiers du corpus versionne quand `CLEANED_SUBDIR` etait
    encore un reglage. Le reglage a disparu, mais cette fonction est PUBLIQUE et
    son garde ne tenait plus que par la constante de son appelant. La cible doit
    desormais etre `racine/CLEANED_SUBDIR` elle-meme, ou vivre dessous : une
    valeur mal posee ne peut plus sortir du repertoire nettoye, quel que soit
    l'appelant. Le refus est distinct du precedent — `CibleHorsDuNettoyeError`
    contre `CibleHorsRacineError` — parce qu'il envoie l'operateur regarder
    autre chose : la premiere accuse `SOURCE_DIR`, la seconde accuse l'argument.

    **`cleaned_root(base)` N'EST PAS RESOLU, ET C'EST DELIBERE.** Le resoudre
    ferait suivre au garde le meme lien que la cible : un `.cleaned` qui serait
    un lien vers `Datas/htms` resoudrait des DEUX cotes vers `Datas/htms`, la
    comparaison serait vraie, et `rmtree` emporterait le corpus avec la
    benediction du garde. Compare a sa forme nominale — `racine` resolue plus la
    constante — le lien est refuse. Garde par
    `test_un_lien_du_nettoye_vers_le_corpus_est_refuse`.

    L'ORDRE DES DEUX CONTROLES decide de la CAUSE NOMMEE, pas du verdict : un
    lien qui sort de la racine est refuse dans les deux ordres. Le containment
    passe en premier parce qu'il nomme la bonne cause pour ce cas-la — la cible
    resolue est hors de la racine, et c'est ce que l'operateur doit voir en
    premier.

    Args:
        repertoire: Repertoire du HTML nettoye. La seule valeur nominale est
            ``elements.cleaned_root(racine)``.
        racine: Racine des donnees (``source_dir``). La cible doit y etre
            strictement contenue, ET etre le repertoire nettoye ou un de ses
            descendants.

    Returns:
        Le nombre de fichiers retires. Une purge muette ne dit pas si elle a
        retire un fichier ou vingt-deux.

    Raises:
        CibleHorsRacineError: Si la cible n'est pas strictement contenue dans
            ``racine``.
        CibleHorsDuNettoyeError: Si la cible est dans ``racine`` sans etre le
            repertoire nettoye ni l'un de ses descendants.
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
