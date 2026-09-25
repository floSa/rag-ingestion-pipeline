"""Verification mecanique du contrat d'interface avec rag-agent-chat.

A lancer apres une ingestion, dans un conteneur jetable du service
d'extraction :

    docker compose run --rm --no-deps -T -e PYTHONPATH=/app -w /app \\
      docling-service python -m src.verify_contract

Le contrat vit dans ``src/pipeline/schemas.py`` et dans
``documentation/axes_amelioration.md`` §0. Ce script verifie qu'il est tenu dans
les donnees. Sans lui, une derive ne se verrait qu'a l'usage, dans les reponses
de l'agent.

Controles, tous sur la totalite de l'index (aucun echantillon) :

- un index vide est une anomalie ;
- forme de ``element_id``, egalite ``element_id == graph_node_id``, cles de
  metadonnees attendues ;
- ``source_path`` renseigne (exigence 3) ;
- bornes de ``chunk_index`` / ``chunk_count``, et jeux de chunks complets par
  element (:func:`jeux_de_chunks_incomplets`) ;
- modele qui a produit les vecteurs (exigence 1) ;
- dans le graphe : ``sequence`` presente et ordre de lecture tenu (exigence 4),
  ``depth`` et ``page_no_end`` renseignes, colonnes du tag ``Document``,
  ``media_url`` et ``object_key`` des sommets visuels, presence de toutes les
  ancres.

Controler toutes les ancres coute une requete nGQL : 0,053 s pour 3 750
identifiants, contre 0,008 s pour un echantillon de 400 (`mesure` le 31 aout
2026). Un echantillon a graine fixe laissait les memes 89 % jamais verifies.

Sort en code 1 si une anomalie est detectee, pour un usage en
pre-deploiement.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT_ELEMENT_ID = re.compile(r"^[a-f0-9]{10}$")

# Le geste qui declenche une reingestion (registre 4.32.a). Le capteur de
# source ne relance pas seul un fichier deja vu : son `run_key` depend du
# `mtime`, donc une purge suivie d'une attente laisserait les stores vides.
#
# Le prefixe « reingerer: » est celui que lit `src/pipeline/factory.py`. Il est
# recopie ici parce que ce module tourne dans le conteneur d'extraction, ou
# Dagster n'est pas installe. `test_verify_contract.py` compare les deux
# constantes et exerce le capteur sur ce prefixe.
COMMENT_REINGERER = (
    "La reingestion ne part pas toute seule, elle SE DEMANDE : posez le curseur du "
    "capteur de la source (pdfs_sensor, livres_html_sensor, ...) sur "
    "« reingerer:<etiquette> » — interface Dagster, Overview > Sensors > le capteur > "
    "Cursor. L'etiquette est libre et obligatoire, et elle doit etre NEUVE a chaque "
    "geste : deux gestes portant la meme etiquette construisent les memes cles de run, "
    "et Dagster refuse la seconde. Le capteur journalise alors le nombre de demandes "
    "perdues."
)

# Les ancres sont toutes verifiees, sans echantillon. Un desaccord de formule
# d'identifiant serait systematique et se verrait sur un echantillon ; une perte
# qui ne touche qu'un document, non. Le controle complet coute une requete
# (0,053 s pour 3 750 identifiants, `mesure` le 31 aout 2026).


def inversions_de_page(aretes: Sequence[tuple[str, int, int]]) -> list[tuple[str, int, int, int]]:
    """Verifie l'ordre de lecture porte par ``sequence`` (exigence 4).

    La propriete exigee est : trie par ``sequence``, ``page_no`` ne decroit
    jamais. L'unicite de ``sequence`` sous un parent ne suffirait pas : une
    numerotation aleatoire distincte la satisferait sans porter aucun ordre
    (registre 6.16).

    Site de reference des trois reserves de lecture de ``sequence``
    (registre 6.16). `mesure` le 2 septembre 2026 sur le graphe complet :
    15 173 aretes, 763 parents, 23 documents, 0 arete sans ``sequence``,
    valeurs de 0 a 1 269.

    1. ``sequence`` repart a 0 dans chaque document. Tout « avant / apres » se
       lit donc a l'interieur d'un document, d'ou le groupement par document.
       `mesure` : 23 aretes portent ``sequence == 0``, pour 23 documents.
    2. ``sequence`` n'est pas contigue sous un parent. C'est un ordre de lecture
       global, pas un rang sous le parent : l'ecart entre deux freres vaut la
       taille du sous-arbre du frere precedent. `mesure` : 167 parents sur 763
       (21,9 %) portent des valeurs non contigues.
    3. Le plus grand ecart entre deux ``sequence`` consecutives sous un meme
       parent vaut 994 (difference `1197 - 203`, soit 993 valeurs
       intercalaires). Il se trouve sous la racine d'un chapitre HTML,
       ``doc_htms/MLOps with Databricks/7. Foundation Models and Context
       Engineering``. Un controle qui exigerait la contiguite echouerait sur un
       graphe sain.

    Consequences pour un agent : une « fenetre d'elements » calculee comme « les
    enfants de P dont ``sequence`` est dans [s-k, s+k] » rend moins d'elements
    que demande ; une ``sequence`` non contigue n'indique pas une perte de
    donnees.

    Le registre 6.16 reste ouvert : ces reserves doivent aussi figurer dans la
    documentation de ``rag-agent-chat``, dans l'autre depot. Ici, l'enonce
    destine a l'agent est dans ``documentation/llm_integration_plan.md``.

    Args:
        aretes: Triplets ``(document, sequence, page_no)``, dans n'importe quel
            ordre.

    Returns:
        Les inversions, en ``(document, sequence, page_precedente, page_vue)``.
        Vide si l'ordre est tenu.
    """
    par_document: dict[str, list[tuple[int, int]]] = {}
    for document, sequence, page_no in aretes:
        par_document.setdefault(document, []).append((sequence, page_no))

    anomalies: list[tuple[str, int, int, int]] = []
    for document, couples in par_document.items():
        precedente: int | None = None
        for sequence, page_no in sorted(couples):
            if precedente is not None and page_no < precedente:
                anomalies.append((document, sequence, precedente, page_no))
            precedente = page_no
    return anomalies


def racine_de_chaque_element(peres: Mapping[str, str]) -> dict[str, str]:
    """Rattache chaque element au document d'ou part sa chaine de parents.

    ``sequence`` repart a 0 dans chaque document (registre 6.16, reserve 1) :
    l'ordre ne se verifie qu'a l'interieur d'un document, et il faut savoir
    lequel. Le graphe est un arbre par document (aucun sommet a deux parents,
    acyclique, une racine ``Document`` par document) : remonter les parents
    suffit.

    Args:
        peres: Le parent de chaque element, tel que le rendent les aretes.

    Returns:
        La racine de chaque element. Un element dont la chaine boucle est rendu
        a lui-meme plutot que de faire tourner la remontee sans fin : le graphe
        est acyclique aujourd'hui, et un controle ne doit pas dependre de ca.
    """
    racines: dict[str, str] = {}
    for element in peres:
        chemin: list[str] = []
        courant = element
        vus: set[str] = set()
        while courant in peres and courant not in vus and courant not in racines:
            vus.add(courant)
            chemin.append(courant)
            courant = peres[courant]
        racine = racines.get(courant, courant)
        for traverse in chemin:
            racines[traverse] = racine
    return racines


def rattacher_au_document(
    peres: Mapping[str, str], aretes: Sequence[tuple[str, int, int]]
) -> list[tuple[str, int, int]]:
    """Remplace l'extremite fille de chaque arete par le document qui la porte.

    Cette fonction existe pour tester sans graphd la composition de
    :func:`racine_de_chaque_element` et :func:`inversions_de_page`. Sans la
    remontee, chaque element serait son propre « document », chaque groupe ne
    porterait qu'une arete, et :func:`inversions_de_page` ne pourrait plus
    trouver aucune inversion : le controle d'ordre (exigence 4) rendrait zero
    anomalie sur un graphe fautif.

    Args:
        peres: Le parent de chaque element, tel que le rendent les aretes.
        aretes: Triplets ``(element, sequence, page_no)``.

    Returns:
        Les memes triplets, l'element remplace par la racine de sa chaine.
    """
    racines = racine_de_chaque_element(peres)
    return [(racines.get(element, element), sequence, page) for element, sequence, page in aretes]


def sources_sans_chemin(metadatas: Sequence[Mapping[str, Any]]) -> int:
    """Compte les chunks dont ``source_path`` est vide (exigence 3).

    ``source_path`` est l'identite d'un document, jamais ``filename`` seul : le
    corpus porte deux ``Preface.html``, un par ouvrage. Un chunk sans chemin est
    un chunk que l'agent ne peut rattacher a aucun livre.

    Args:
        metadatas: Metadonnees des chunks.

    Returns:
        Le nombre de chunks concernes.
    """
    return sum(1 for meta in metadatas if not str(meta.get("source_path") or "").strip())


def chunks_incoherents(metadatas: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Releve les couples ``chunk_index`` / ``chunk_count`` impossibles.

    Controle de forme, limite a ce qu'un couple dit de lui-meme. Un morceau
    manquant ne se voit pas ici : voir :func:`jeux_de_chunks_incomplets`, qui
    regarde l'element entier.

    Args:
        metadatas: Metadonnees des chunks.

    Returns:
        Les couples fautifs, en ``(chunk_index, chunk_count)``.
    """
    fautifs: list[tuple[int, int]] = []
    for meta in metadatas:
        index = int(meta.get("chunk_index") or 0)
        count = int(meta.get("chunk_count") or 0)
        if count < 1 or not 0 <= index < count:
            fautifs.append((index, count))
    return fautifs


def jeux_de_chunks_incomplets(
    metadatas: Sequence[Mapping[str, Any]],
) -> list[tuple[str, int, list[int]]]:
    """Releve les elements dont le jeu ``{chunk_index}`` n'est pas complet.

    L'agent reconstitue un element decoupe en concatenant ses chunks dans
    l'ordre de ``chunk_index``. Un morceau manquant casse cette reconstitution,
    mais chaque chunk present satisfait encore ``0 <= index < count`` : le trou
    ne se voit qu'en regardant l'element entier.

    `mesure` le 31 aout 2026 sur l'index complet (4 365 chunks, 3 750
    elements) : ``chunks_incoherents`` rendait 0 chunk fautif, et 2 elements
    avaient un jeu troue.

        element_id=aa3de10738  chunk_count=7  presents=[0,1,2,3,5,6]  manque 4
        element_id=eb52c4ec8f  chunk_count=4  presents=[0,1,2]        manque 3

    Cause constatee alors : ``anchoring.resolve_anchors`` fixait
    ``chunk_count`` avant que ``vectors.build_chunks`` ne retire les chunks sans
    contenu ou plus courts que ``min_chunk_chars``. Ce controle signale la
    perte ; il ne la repare pas.

    Args:
        metadatas: Metadonnees des chunks, tout l'index.

    Returns:
        Un triplet ``(element_id, chunk_count, index manquants)`` par element
        troue, tries. Vide si tous les jeux sont complets.
    """
    presents: dict[str, set[int]] = {}
    annonces: dict[str, int] = {}
    for meta in metadatas:
        element = str(meta.get("element_id") or "")
        presents.setdefault(element, set()).add(int(meta.get("chunk_index") or 0))
        annonces[element] = int(meta.get("chunk_count") or 0)

    troues: list[tuple[str, int, list[int]]] = []
    for element, vus in presents.items():
        manquants = sorted(set(range(annonces[element])) - vus)
        if manquants:
            troues.append((element, annonces[element], manquants))
    return sorted(troues)


def sommets_sans_profondeur(profondeurs: Sequence[int | None]) -> int:
    """Compte les sommets dont ``depth`` n'est pas renseigne.

    ``depth`` permet a l'agent de lire le niveau d'un titre, aucun
    ``section_header`` n'etant un chunk (registre 4.11, 4.24). Le schema migre
    en place, les donnees non : un `ALTER TAG ... ADD` laisse a NULL les
    sommets deja ecrits, et seule une reingestion les renseigne. Sans ce
    compte, un index a moitie migre ne se distinguerait pas d'un index complet,
    et l'agent ne pourrait pas separer « profondeur 0 » de « profondeur
    inconnue ».

    Args:
        profondeurs: Valeurs de ``depth`` lues sur les sommets, ``None`` etant la
            forme que rend le graphe pour une propriete jamais ecrite.

    Returns:
        Le nombre de sommets sans profondeur.
    """
    return sum(1 for profondeur in profondeurs if profondeur is None)


def images_sans_url(urls: Sequence[str | None]) -> int:
    """Compte les sommets visuels qui ne portent aucune URL d'objet.

    ``RESTRICT_MEDIA_TO_GRAPH`` etant actif cote agent, l'agent ne sert que ce
    que le graphe reference : une image televersee sans URL dans le graphe
    reste inatteignable. Ce compteur signale la perte (registre 3.5) ; il ne
    la repare pas.

    Args:
        urls: Valeurs d'une colonne de media — ``media_url`` ou ``object_key``
            — lues sur les sommets visuels. ``None`` est la forme que rend le
            graphe pour une propriete jamais ecrite.

    Returns:
        Le nombre de sommets sans URL.
    """
    return sum(1 for url in urls if not str(url or "").strip())


def main() -> None:
    """Execute les verifications et sort en erreur si l'une echoue."""
    import chromadb

    from src.docling_service.embedding import index_model_gap
    from src.docling_service.settings import get_settings
    from src.docling_service.vectors import COLLECTION_NAME
    from src.pipeline.schemas import ChunkMetadata

    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    result = collection.get(include=["metadatas"])
    metadatas = result["metadatas"]
    chunk_ids = result["ids"]

    if not metadatas:
        # Un index vide n'est pas conforme : une purge, une ingestion en echec
        # ou un nom de collection errone ne doivent pas passer pour « Contrat
        # respecte ».
        print(f"chunks examines                : 0 dans la collection {COLLECTION_NAME}")
        print()
        print(
            "ANOMALIE : l'index est VIDE. Aucune propriete du contrat n'est "
            "verifiable, et un index vide n'est pas un index conforme. Causes "
            "usuelles : purge sans reingestion, ingestion en echec, ou nom de "
            "collection errone."
        )
        sys.exit(1)

    anomalies: list[str] = []
    print(f"chunks examines                : {len(metadatas)}")

    mauvais = [
        str(m.get("element_id", ""))
        for m in metadatas
        if not FORMAT_ELEMENT_ID.match(str(m.get("element_id", "")))
    ]
    print(f"element_id au mauvais format   : {len(mauvais)}")
    if mauvais:
        anomalies.append(f"element_id invalides (ex. {mauvais[:3]})")

    divergents = sum(1 for m in metadatas if m.get("element_id") != m.get("graph_node_id"))
    print(f"element_id != graph_node_id    : {divergents}")
    if divergents:
        anomalies.append("element_id et graph_node_id divergent")

    attendues = set(ChunkMetadata.model_fields)
    manquantes = sorted({cle for m in metadatas for cle in attendues - set(m)})
    print(f"cles de metadonnees manquantes : {manquantes or 'aucune'}")
    if manquantes:
        anomalies.append(f"metadonnees manquantes : {manquantes}")

    print(f"ids de chunk suffixes en #n    : {sum(1 for i in chunk_ids if '#' in i)}")

    # Exigence 3 : source_path est l'identite d'un document.
    sans_chemin = sources_sans_chemin(metadatas)
    print(f"chunks sans source_path        : {sans_chemin}")
    if sans_chemin:
        anomalies.append(f"{sans_chemin} chunks sans source_path")

    incoherents = chunks_incoherents(metadatas)
    print(f"chunk_index hors de chunk_count: {len(incoherents)}")
    if incoherents:
        anomalies.append(f"chunk_index incoherents (ex. {incoherents[:3]})")

    # Le controle de bornes ci-dessus ne voit pas un morceau manquant : chaque
    # chunk present satisfait ses bornes meme quand un frere a disparu.
    troues = jeux_de_chunks_incomplets(metadatas)
    print(f"elements au jeu de chunks troue: {len(troues)}")
    if troues:
        anomalies.append(
            f"{len(troues)} elements dont des chunks MANQUENT (ex. {troues[:3]}, en "
            "(element_id, chunk_count, index manquants)) : l'agent reconstitue un "
            "texte troue sans erreur. Cause au registre, lot 4"
        )

    # Exigence 1 : le modele qui a produit les vecteurs.
    ecart = index_model_gap(settings.embedding_model_name, _modele_enregistre(collection))
    print(f"modele des vecteurs            : {_modele_enregistre(collection) or 'NON TRACE'}")
    if ecart:
        anomalies.append(ecart)

    anomalies.extend(_verifier_le_graphe(metadatas))

    print()
    if anomalies:
        for anomalie in anomalies:
            print(f"ANOMALIE : {anomalie}")
        sys.exit(1)
    print("Contrat respecte.")


def _modele_enregistre(collection: Any) -> str | None:
    """Nom du modele inscrit sur la collection, ou None s'il ne l'est pas."""
    metadata = getattr(collection, "metadata", None) or {}
    valeur = metadata.get("embedding_model")
    return str(valeur) if valeur else None


def _verifier_le_graphe(metadatas: Sequence[Mapping[str, Any]]) -> list[str]:
    """Controle les proprietes que seul le graphe porte.

    Args:
        metadatas: Metadonnees des chunks, pour le controle des ancres.

    Returns:
        Les anomalies constatees.
    """
    from nebula3.Config import Config
    from nebula3.gclient.net import ConnectionPool

    from src.docling_service.nebula import SPACE
    from src.docling_service.settings import get_settings

    settings = get_settings()
    anomalies: list[str] = []
    pool = ConnectionPool()
    if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
        print("NebulaGraph injoignable.")
        return ["NebulaGraph injoignable : aucune propriete de graphe verifiee"]

    session = pool.get_session(settings.nebula_user, settings.nebula_password)
    try:
        session.execute(f"USE {SPACE};")

        # L'ordre de lecture, sur toutes les aretes : une inversion peut ne
        # toucher qu'un document sur vingt, et un echantillon la manquerait.
        aretes, sans_sequence = _lire_les_aretes(session)
        print(f"aretes PARENT_OF examinees     : {len(aretes) + len(sans_sequence)}")
        if not aretes and not sans_sequence:
            anomalies.append("aucune arete PARENT_OF : le graphe n'a pas de hierarchie")
        # L'exigence 4 est « sequence absente ou non monotone » : les deux cas
        # sont rapportes.
        print(f"aretes sans sequence           : {len(sans_sequence)}")
        if sans_sequence:
            anomalies.append(
                f"{len(sans_sequence)} aretes PARENT_OF sans sequence (ex. "
                f"{sans_sequence[:3]}) : l'agent ne peut pas ordonner ces enfants"
            )
        inversions = inversions_de_page(aretes)
        print(f"inversions de page dans l'ordre: {len(inversions)}")
        if inversions:
            anomalies.append(f"sequence non monotone (ex. {inversions[:3]})")

        # Registre 4.11 : le schema migre en place, les donnees non.
        profondeurs = _lire_les_profondeurs(session)
        sans_depth = sommets_sans_profondeur(profondeurs)
        print(f"sommets sans depth             : {sans_depth}/{len(profondeurs)}")
        if sans_depth:
            anomalies.append(
                f"{sans_depth} sommets sur {len(profondeurs)} sans depth : le tag a "
                "migre mais les donnees non — seule une reingestion les renseigne "
                "(registre 4.11). L'agent ne peut pas distinguer « profondeur 0 » "
                "de « profondeur inconnue »"
            )

        # Meme controle pour `page_no_end` (registre 4.22), et pour la meme
        # raison : un index ecrit avant l'ajout de la colonne porte NULL
        # partout. Le DESCRIBE precede le comptage : des NULL ne disent pas si
        # la colonne existe, et les deux etats demandent des gestes differents
        # (registre 4.29.e).
        tags_sans_fin = _lire_les_tags_sans_la_colonne(session, "page_no_end")
        fins = _lire_un_entier_sur_les_sommets(session, "page_no_end")
        sans_fin = sommets_sans_profondeur(fins)
        print(
            f"sommets sans page_no_end       : {sans_fin}/{len(fins)}"
            + (f", colonne absente de {len(tags_sans_fin)} tags" if tags_sans_fin else "")
        )
        anomalie = anomalie_de_colonne(
            "page_no_end", tags_sans_fin, sans_fin, len(fins), "registre 4.22"
        )
        if anomalie:
            anomalies.append(
                anomalie + ". L'agent ne peut pas distinguer « cet element tient "
                "sur une page » de « on ne sait pas ou il finit »"
            )

        anomalies.extend(_verifier_le_tag_document(session))

        # Le contrat publie `media_url` et `object_key`. Les deux comptes sont
        # separes parce qu'ils ont des causes differentes : une adresse
        # manquante vient de la chaine d'images (registre 3.5) ; une cle
        # manquante vient d'un sommet ecrit avant l'ajout de la colonne, donc
        # d'une reingestion qui n'a pas eu lieu.
        urls, cles = _lire_les_medias_visuels(session)
        sans_url = images_sans_url(urls)
        print(f"sommets visuels sans media_url : {sans_url}/{len(urls)}")
        if sans_url:
            anomalies.append(
                f"{sans_url} sommets visuels sur {len(urls)} sans media_url : "
                "l'agent ne peut pas les servir (registre 3.5)"
            )
        sans_cle = images_sans_url(cles)
        print(f"sommets visuels sans object_key : {sans_cle}/{len(cles)}")
        if sans_cle:
            anomalies.append(
                f"{sans_cle} sommets visuels sur {len(cles)} sans object_key : "
                "la cle de l'objet n'a pas ete ecrite. L'adresse porte l'hote et "
                "perime avec lui ; sans la cle, un consommateur doit defaire "
                "l'adresse a sa facon pour retrouver l'objet"
            )

        anomalies.extend(_verifier_les_ancres(session, metadatas))
    finally:
        session.release()
        pool.close()
    return anomalies


def _lire(session: Any, requete: str) -> list[Any]:
    """Execute une requete et rend ses lignes, vide en cas d'echec."""
    resultat = session.execute(requete)
    if not resultat.is_succeeded():
        print(f"Requete nGQL en echec : {resultat.error_msg()}")
        return []
    return [resultat.row_values(ligne) for ligne in range(resultat.row_size())]


def _lire_les_aretes(session: Any) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Lit toutes les aretes PARENT_OF, avec leur sequence et leur page.

    Une seule requete pour tout le graphe, puis le rattachement au document se
    calcule en memoire par :func:`racine_de_chaque_element` : une requete de
    chemin variable par sommet couterait un aller-retour par element.

    Returns:
        Les aretes rattachees a leur document, et la liste des extremites dont
        ``sequence`` est absente (premier cas de l'exigence 4).
    """
    peres: dict[str, str] = {}
    brut: list[tuple[str, int, int]] = []
    sans_sequence: list[str] = []
    for ligne in _lire(
        session,
        "MATCH (a)-[e:PARENT_OF]->(v) RETURN id(a) AS pere, id(v) AS fils, "
        "e.sequence AS seq, properties(v).page_no AS page;",
    ):
        pere, fils = ligne[0].as_string(), ligne[1].as_string()
        peres[fils] = pere
        # Sur une valeur NULL, `as_int()` leve `InvalidValueTypeException` :
        # une `sequence` absente est donc testee avant, et rapportee.
        if ligne[2].is_null():
            sans_sequence.append(fils)
            continue
        page = 0 if ligne[3].is_null() else int(ligne[3].as_int())
        brut.append((fils, int(ligne[2].as_int()), page))

    return rattacher_au_document(peres, brut), sans_sequence


def _lire_les_profondeurs(session: Any) -> list[int | None]:
    """Lit ``depth`` sur tous les sommets d'element.

    Le filtrage se fait en Python et non par un `WHERE` nGQL : sur
    `rag_space`, ``MATCH (v:Tag) WHERE v.Tag.<prop> == ...`` rend
    `IndexNotFound` sur un tag sans index de tag, alors qu'un simple
    ``RETURN v.Tag.<prop>`` passe. `mesure` le 31 aout 2026 : le filtre passe
    sur `Document`, qui porte `doc_index`, et echoue sur `SectionHeader`
    (registre 4.27).
    """
    return _lire_un_entier_sur_les_sommets(session, "depth")


def _lire_un_entier_sur_les_sommets(session: Any, propriete: str) -> list[int | None]:
    """Lit une propriete entiere sur tous les sommets d'element.

    Args:
        session: Session NebulaGraph.
        propriete: Nom de la colonne a lire.

    Returns:
        Une valeur par sommet, ``None`` pour une propriete jamais ecrite.
    """
    from src.docling_service.elements import TAG_MAP

    valeurs: list[int | None] = []
    for tag in sorted(set(TAG_MAP.values())):
        requete = f"MATCH (v:{tag}) RETURN v.{tag}.{propriete} AS valeur;"
        for ligne in _lire(session, requete):
            valeur = ligne[0]
            valeurs.append(None if valeur.is_null() else int(valeur.as_int()))
    return valeurs


def anomalie_de_colonne(
    colonne: str,
    tags_sans_la_colonne: Sequence[str],
    sommets_sans_valeur: int,
    sommets_lus: int,
    registre: str,
) -> str | None:
    """Distingue « le tag n'a pas la colonne » de « les donnees sont a NULL ».

    Les deux etats demandent des gestes differents (registre 4.29.e) :

    - la colonne n'existe pas : `init_schema()` joue les `ALTER TAG ... ADD`,
      et il n'est appele qu'au demarrage du service (`main.py`, dans le
      `lifespan`). Il faut redemarrer `docling-service`, puis reingerer. Une
      reingestion seule ecrirait contre un tag sans la colonne, et le graphd
      rejetterait chaque `INSERT` ;
    - la colonne existe, les valeurs sont a NULL : le schema a migre, les
      donnees non. Une reingestion suffit.

    Cas constate le 1er septembre 2026 : `DESCRIBE TAG Paragraph` ne portait
    pas `page_no_end`, alors que le message d'alors prescrivait une
    reingestion seule.

    Args:
        colonne: Nom de la colonne controlee.
        tags_sans_la_colonne: Tags dont ``DESCRIBE TAG`` ne porte pas la colonne.
        sommets_sans_valeur: Nombre de sommets dont la valeur est ``NULL``.
        sommets_lus: Nombre de sommets examines.
        registre: Renvoi au constat, pour que le message soit actionnable.

    Returns:
        L'anomalie, ou ``None`` si la colonne existe partout et est renseignee.
    """
    if tags_sans_la_colonne:
        return (
            f"la colonne {colonne} N'EXISTE PAS sur {len(tags_sans_la_colonne)} "
            f"tags ({', '.join(tags_sans_la_colonne)}) : le schema n'a PAS migre. "
            f"C'est init_schema() qui joue les ALTER TAG, et il n'est appele qu'au "
            f"DEMARRAGE du service. Le geste est REDEMARRER docling-service PUIS "
            f"reingerer — une reingestion seule ecrirait contre un tag sans la "
            f"colonne, et le graphd rejetterait chaque INSERT ({registre}). "
            f"{COMMENT_REINGERER}"
        )
    if sommets_sans_valeur:
        return (
            f"{sommets_sans_valeur} sommets sur {sommets_lus} sans {colonne} : la "
            f"colonne EXISTE, le schema a donc migre, mais les donnees non — seule "
            f"une reingestion les renseigne ({registre}). {COMMENT_REINGERER}"
        )
    return None


def _lire_les_tags_sans_la_colonne(session: Any, colonne: str) -> list[str]:
    """Rend les tags d'element dont ``DESCRIBE TAG`` ne porte pas la colonne.

    Meme mecanisme que :func:`_verifier_le_tag_document` et
    ``ngql.missing_vertex_columns``, applique aux tags d'element.

    Un ``DESCRIBE`` en echec compte comme une colonne absente. Sinon,
    :func:`anomalie_de_colonne` prendrait sa seconde branche et prescrirait une
    reingestion seule, la ou il faut redemarrer puis reingerer (registre
    4.29.e, 4.31.B4). Ce cas est teste par
    `TestUnDescribeEnEchecCompteCommeUneColonneAbsente`.

    Args:
        session: Session NebulaGraph.
        colonne: Nom de la colonne cherchee.

    Returns:
        Les tags qui ne la portent pas, dans l'ordre. Un ``DESCRIBE`` en echec
        est compte comme « colonne absente » : ne pas pouvoir constater n'est
        pas constater que tout va bien.
    """
    from src.docling_service.elements import TAG_MAP

    manquants: list[str] = []
    for tag in sorted(set(TAG_MAP.values())):
        lignes = _lire(session, f"DESCRIBE TAG {tag};")
        colonnes = {ligne[0].as_string() for ligne in lignes}
        if colonne not in colonnes:
            manquants.append(tag)
    return manquants


def _verifier_le_tag_document(session: Any) -> list[str]:
    """Constate que le tag ``Document`` porte toutes ses colonnes.

    `NebulaWriter._verifier_les_tags` ne controle que les 11 tags d'element
    (``sorted(set(TAG_MAP.values()))``) ; le tag ``Document`` a son propre
    schema. Ses `ALTER TAG Document ADD` sont `required=False`, « la colonne
    existe deja » etant le cas nominal : une migration refusee n'y leve rien.
    Ce controle constate donc les colonnes apres coup. Parmi elles,
    `source_path` est l'exigence 3 du contrat.

    Returns:
        Les anomalies constatees.
    """
    from src.docling_service.ngql import DOCUMENT_PROPERTIES

    lignes = _lire(session, "DESCRIBE TAG Document;")
    if not lignes:
        return ["DESCRIBE TAG Document impossible : le schema n'est pas verifiable"]
    lues = {ligne[0].as_string() for ligne in lignes}
    manquantes = [colonne for colonne in DOCUMENT_PROPERTIES if colonne not in lues]
    print(f"colonnes du tag Document       : {len(lues)}, manquantes {manquantes or 'aucune'}")
    if manquantes:
        return [
            f"le tag Document ne porte pas {manquantes} (colonnes lues : "
            f"{sorted(lues)}). Nebula n'autorise pas une colonne supprimee a "
            "revenir sous le meme nom : ce space doit etre recree"
        ]
    return []


def _lire_les_medias_visuels(session: Any) -> tuple[list[str | None], list[str | None]]:
    """Lit ``media_url`` ET ``object_key`` sur tous les sommets Picture et Table.

    Les deux colonnes sont lues par la meme requete, pour que les deux comptes
    portent sur le meme etat du graphe.

    Returns:
        Les adresses et les cles, dans le meme ordre et de meme longueur.
        ``None`` est la forme que rend le graphe pour une propriete jamais
        ecrite.
    """
    urls: list[str | None] = []
    cles: list[str | None] = []
    for tag in ("Picture", "Table"):
        requete = f"MATCH (v:{tag}) RETURN v.{tag}.media_url AS url, v.{tag}.object_key AS cle;"
        for ligne in _lire(session, requete):
            url, cle = ligne[0], ligne[1]
            urls.append(None if url.is_null() else url.as_string())
            cles.append(None if cle.is_null() else cle.as_string())
    return urls, cles


def _verifier_les_ancres(session: Any, metadatas: Sequence[Mapping[str, Any]]) -> list[str]:
    """Verifie que toutes les ancres existent comme noeuds du graphe.

    Sur tous les identifiants, sans echantillon : voir le commentaire en tete
    de module. Une seule requete.
    """
    identifiants = sorted({str(m["element_id"]) for m in metadatas})
    if not identifiants:
        return ["aucun element_id a verifier"]
    liste = ", ".join(f'"{identifiant}"' for identifiant in identifiants)
    lignes = _lire(session, f"MATCH (v) WHERE id(v) IN [{liste}] RETURN count(DISTINCT id(v));")
    if not lignes:
        return ["comptage des ancres impossible"]
    trouves = int(lignes[0][0].as_int())
    print(f"ancres presentes dans le graphe : {trouves}/{len(identifiants)}")
    if trouves != len(identifiants):
        return [f"{len(identifiants) - trouves} ancres absentes du graphe"]
    return []


if __name__ == "__main__":
    main()
