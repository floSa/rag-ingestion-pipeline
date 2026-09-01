"""Verification mecanique du contrat d'interface avec rag-agent-chat.

A lancer depuis le reseau Docker, apres une ingestion :

    docker compose exec docling-service python -m src.verify_contract

Le contrat vit dans ``src/pipeline/schemas.py`` et dans
``documentation/axes_amelioration.md`` §0. Ce script verifie qu'il est tenu dans
les faits — c'est le genre de derive qui ne se voit autrement qu'a l'usage, dans
les reponses de l'agent.

**Ce qu'il verifiait, et ce qu'il laissait passer.** Il ne regardait que la
FORME des identifiants et la presence des ancres. Il rendait donc rc=0 et
« Contrat respecte » sur un index ou **199 images sur 209 n'avaient pas d'URL**
(`mesure`, 31 aout 2026), ou rien ne verifiait l'ordre de ``sequence``
(exigence 4), ni que ``source_path`` etait renseigne (exigence 3), ni quel
modele avait produit les vecteurs (exigence 1) — la panne la plus couteuse du
systeme, et la seule parfaitement silencieuse.

**L'echantillon est SUPPRIME.** Il etait justifie par une phrase
d'exhaustivite — « une rupture de contrat est systematique », vraie d'un FORMAT
et fausse d'un ORDRE — puis borne au seul controle « dont le cout croit vraiment
avec le corpus », la presence des ancres. Cette derniere justification est
demolie par la mesure : le controle COMPLET des 3 750 identifiants tient en une
requete nGQL, **0,053 s** — contre 0,008 s pour 400, soit 6,6 fois le cout pour
9,4 fois la couverture. Un echantillon de 400 sur 3 750 avec `random.seed(0)`
laissait les MEMES 89 % jamais verifies, execution apres execution. **Tout est
desormais verifie sur la totalite.**

**Cinq trous fermes par la reparation du lot 3**, et le premier est le pire :

1. **il rendait rc=0 SUR UN INDEX VIDE**, et tous les controles vivent derriere
   ce garde. Une purge, une ingestion echouee ou un nom de collection errone
   passaient pour « Contrat respecte » ;
2. **il LEVAIT au lieu de rapporter quand ``sequence`` est absente.** L'exigence
   4 est « absente OU non monotone » : la moitie « absente » avortait le rapport
   sur une `InvalidValueTypeException` ;
3. **``chunks_incoherents`` ne voyait pas la panne que ce docstring nomme.** Un
   morceau qui MANQUE est invisible depuis un chunk isole, chaque chunk present
   satisfaisant ses bornes. Voir :func:`jeux_de_chunks_incomplets` ;
4. **``depth`` n'etait pas verifie non nul sur les sommets**, alors que le schema
   migre en place et les donnees non (registre §4.11) ;
5. **le tag ``Document`` n'etait pas couvert.** ``NebulaWriter._verifier_les_tags``
   ne recoit que les 11 tags d'element ; les quatre `ALTER TAG Document ADD`
   restaient `required=False` sans constatation — dont ``source_path``, exigence
   3 du contrat.

Sort en code d'erreur si une anomalie est detectee, pour un usage en
pre-deploiement. **Un index vide en est une.**
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT_ELEMENT_ID = re.compile(r"^[a-f0-9]{10}$")

# L'ECHANTILLON D'ANCRES EST SUPPRIME, et c'est une dette tranchee sur une
# mesure.
#
# Il valait 400 sur 3 750 avec `random.seed(0)`, donc LES MEMES 89 % N'ETAIENT
# JAMAIS VERIFIES, execution apres execution — une graine fixe ne fait pas d'un
# echantillon une couverture, elle fait d'un angle mort un angle mort STABLE.
#
# Sa justification etait « le seul controle dont le cout croit vraiment avec le
# corpus ». Elle est demolie par la mesure : le controle COMPLET sur les 3 750
# identifiants tient en UNE requete nGQL, en **0,053 s** — contre 0,008 s pour
# 400 (`mesure` le 31 aout 2026 sur l'index complet, chronometre autour du seul
# `session.execute`). Il n'y avait donc rien a echantillonner.
#
# Ce qui reste vrai de l'ancienne justification, et qui est ecrit ici pour que
# personne ne la reintroduise : une ancre absente traduit un desaccord de CALCUL
# d'identifiant, qui est systematique. C'est exactement pourquoi un echantillon
# semblait suffire — et c'est une phrase d'exhaustivite : elle est vraie d'un
# desaccord de formule, fausse d'une perte qui ne touche qu'un document.


def inversions_de_page(aretes: Sequence[tuple[str, int, int]]) -> list[tuple[str, int, int, int]]:
    """Verifie l'ordre de lecture porte par ``sequence`` (exigence 4).

    La propriete exigee est : **trie par ``sequence``, ``page_no`` ne decroit
    jamais**. Ce n'est PAS « aucun parent ne porte deux fois la meme valeur »,
    qui est l'unicite sous un parent : une numerotation aleatoire distincte par
    parent la satisferait sans porter aucun ordre (registre 6.16).

    Trois reserves, toutes mesurees, et qui dictent la forme de ce controle :

    1. **``sequence`` repart a 0 dans chaque document.** Elle n'est donc pas
       globalement monotone, et la verification est bornee au document. Sans ce
       groupement, deux documents entrelaces rendraient des inversions fausses ;
    2. **elle n'est pas contigue sous un parent, par construction** — c'est un
       ordre de lecture global, pas un rang sous le parent ;
    3. **le plus grand trou entre deux enfants consecutifs d'un meme parent vaut
       993.** Un controle qui exigerait la contiguite rougirait sur un graphe
       sain.

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
    l'ordre ne se verifie donc qu'a l'INTERIEUR d'un document, et il faut savoir
    lequel. Le graphe est un arbre par document — 0 sommet a deux parents,
    acyclique, une racine ``Document`` par document (`mesure` par l'audit du
    lot 1) — donc remonter les parents suffit.

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

    C'EST LA COMPOSITION, ET C'EST ELLE QUI PORTE LE DEFAUT. Prise seule,
    :func:`racine_de_chaque_element` a l'air d'une commodite ; c'est en la
    composant avec :func:`inversions_de_page` qu'on voit ce qu'elle garde.
    Neutraliser sa remontee rend chaque element a lui-meme, donc chaque
    « document » du groupement ne porte plus qu'UNE arete, donc plus aucune
    inversion n'est possible : **le seul controle d'ordre du contrat (exigence 4)
    devient inerte, en rendant zero anomalie.** `mesure` : sur un graphe portant
    une vraie inversion, le code livre rapporte ``[('doc', 2, 9, 2)]`` et la
    mutation rapporte ``[]``.

    Cette fonction existe donc pour que la composition soit testable sans graphd,
    et non pour factoriser une ligne.

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

    Controle de FORME, borne a ce qu'un couple dit de lui-meme. La panne que le
    docstring de ce module nomme — un morceau qui manque — ne se voit pas ici :
    voir :func:`jeux_de_chunks_incomplets`, qui regarde l'element entier.

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

    LE CONTROLE DE BORNES NE VOIT PAS LA PANNE QUE CE MODULE ANNONCE, et c'est
    mesure. L'agent reconstitue un element decoupe en concatenant ses chunks dans
    l'ordre de ``chunk_index`` ; ce qui le casse est un morceau qui MANQUE, pas
    un index hors bornes. Or chaque chunk PRESENT satisfait ``0 <= index <
    count`` meme quand un de ses freres a disparu : le trou est invisible depuis
    un chunk isole, il ne se voit qu'en regardant l'element entier.

    `mesure` le 31 aout 2026 sur l'index complet — 4 365 chunks, 3 750 elements,
    produit par le code du lot 3 : ``chunks_incoherents`` rend **0 chunk
    fautif**, et **2 elements** ont un jeu troue —

        element_id=aa3de10738  chunk_count=7  presents=[0,1,2,3,5,6]  manque 4
        element_id=eb52c4ec8f  chunk_count=4  presents=[0,1,2]        manque 3

    La CAUSE n'est pas ici : ``anchoring.resolve_anchors`` fixe ``chunk_count``
    AVANT que ``vectors.build_chunks`` ne jette les chunks echouant ``has_content``
    ou plus courts que ``min_chunk_chars``. Le compte annonce est donc celui
    d'avant le filtrage. C'est une perte de texte silencieuse, elle est consignee
    au registre pour le lot 4, et ce controle ne fait que la rendre bruyante.

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

    C'est la charge utile du §4.11 : ``depth`` a ete ajoute au schema pour que
    l'agent puisse lire le niveau d'un titre, aucun ``section_header`` n'etant
    jamais un chunk (§4.24). Or **le schema migre en place et les donnees non** :
    un `ALTER TAG ... ADD` laisse a NULL tous les sommets deja ecrits, et seule
    une reingestion les renseigne.

    Un index a moitie migre est donc parfaitement possible, et rien ne le
    signalait : l'agent lirait `depth` sur les sommets recents et `NULL` sur les
    anciens, sans qu'aucune erreur ne distingue « profondeur 0 » de « profondeur
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

    ``RESTRICT_MEDIA_TO_GRAPH`` etant actif cote agent, il ne sert que ce que le
    graphe reference : une image sans URL est televersee, payee en place et en
    temps, et reste **inatteignable**. C'est ce que l'ancien controle laissait
    passer au vert.

    La REPARATION de la chaine d'images n'est pas ici — c'est une perte de
    donnees, registre 3.5. Ce compteur la rend seulement bruyante.

    Args:
        urls: Valeurs de ``minio_url`` lues sur les sommets visuels. ``None``
            est la forme que rend le graphe pour une propriete jamais ecrite.

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
        # UN INDEX VIDE N'EST PAS UN INDEX CONFORME, et ce garde rendait rc=0.
        # Tous les controles de ce module vivent derriere lui : une purge, une
        # ingestion echouee ou un nom de collection errone passaient donc pour
        # « Contrat respecte » dans un outil dont le docstring dit « pour un
        # usage en pre-deploiement ». Le defaut preexistait sur `main:52-54`,
        # mais sa portee s'est elargie a tout ce que le lot 3 a ajoute.
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

    # Le controle de bornes ci-dessus ne voit PAS un morceau qui manque : chaque
    # chunk present satisfait ses bornes meme quand un de ses freres a disparu.
    troues = jeux_de_chunks_incomplets(metadatas)
    print(f"elements au jeu de chunks troue: {len(troues)}")
    if troues:
        anomalies.append(
            f"{len(troues)} elements dont des chunks MANQUENT (ex. {troues[:3]}, en "
            "(element_id, chunk_count, index manquants)) : l'agent reconstitue un "
            "texte troue sans erreur. Cause au registre, lot 4"
        )

    # Exigence 1 : le modele qui a REELLEMENT produit les vecteurs.
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
        metadatas: Metadonnees des chunks, pour l'echantillon d'ancres.

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

        # L'ordre de lecture, sur la TOTALITE des aretes : une inversion peut
        # n'affecter qu'un document sur vingt, et un echantillon la manquerait.
        aretes, sans_sequence = _lire_les_aretes(session)
        print(f"aretes PARENT_OF examinees     : {len(aretes) + len(sans_sequence)}")
        if not aretes and not sans_sequence:
            anomalies.append("aucune arete PARENT_OF : le graphe n'a pas de hierarchie")
        # L'exigence 4 est « sequence ABSENTE ou non monotone ». Les deux moities
        # se rapportent ; ce module levait sur la premiere.
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

        # La charge utile du §4.11 : le schema migre en place, les DONNEES non.
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

        # LE MEME CONTROLE POUR `page_no_end`, ET POUR LA MEME RAISON. La colonne
        # est ajoutee par le lot 4 (registre 4.22) : le schema migre en place, les
        # DONNEES non, donc un index ecrit avant ce lot porte NULL partout. Sans
        # ce compteur, l'agent lirait une page de fin sur les sommets recents et
        # NULL sur les anciens, et rien ne distinguerait « cet element tient sur
        # une page » de « on ne sait pas ou il finit ». C'est mot pour mot le
        # quatrieme des cinq trous que l'audit du lot 3 a trouves, sur la colonne
        # que ce lot-ci ajoute : ne pas l'ecrire aurait ete refaire le defaut
        # dans le geste qui le connait.
        fins = _lire_un_entier_sur_les_sommets(session, "page_no_end")
        sans_fin = sommets_sans_profondeur(fins)
        print(f"sommets sans page_no_end       : {sans_fin}/{len(fins)}")
        if sans_fin:
            anomalies.append(
                f"{sans_fin} sommets sur {len(fins)} sans page_no_end : le tag a "
                "migre, les donnees non — il faut une reingestion pour peupler la "
                "colonne (registre 4.22). L'agent ne peut pas distinguer « cet "
                "element tient sur une page » de « on ne sait pas ou il finit »"
            )

        anomalies.extend(_verifier_le_tag_document(session))

        urls = _lire_les_urls_visuelles(session)
        sans_url = images_sans_url(urls)
        print(f"sommets visuels sans minio_url : {sans_url}/{len(urls)}")
        if sans_url:
            anomalies.append(
                f"{sans_url} sommets visuels sur {len(urls)} sans minio_url : "
                "l'agent ne peut pas les servir (registre 3.5)"
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
    """Lit TOUTES les aretes PARENT_OF, avec leur sequence et leur page.

    Une seule requete pour tout le graphe, puis le rattachement au document se
    calcule en memoire par :func:`racine_de_chaque_element` : une requete de
    chemin variable par sommet couterait un aller-retour par element.

    Returns:
        Les aretes rattachees a leur document, et la liste des extremites dont
        ``sequence`` est ABSENTE — la moitie de l'exigence 4 sur laquelle ce
        module levait au lieu de rapporter.
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
        # `seq` N'AVAIT PAS DE GARDE `is_null()`, alors que `page` en avait un a
        # la ligne suivante. L'exigence 4 est « sequence ABSENTE ou non
        # monotone » : sur la moitie « absente », `as_int()` levait
        # `InvalidValueTypeException` (`mesure` sur un space jetable) et le
        # rapport AVORTAIT sur une trace Python — au lieu de rapporter
        # precisement le defaut qu'il existe pour trouver.
        if ligne[2].is_null():
            sans_sequence.append(fils)
            continue
        page = 0 if ligne[3].is_null() else int(ligne[3].as_int())
        brut.append((fils, int(ligne[2].as_int()), page))

    return rattacher_au_document(peres, brut), sans_sequence


def _lire_les_profondeurs(session: Any) -> list[int | None]:
    """Lit ``depth`` sur tous les sommets d'element.

    Le filtrage se fait EN PYTHON et non par un `WHERE` nGQL, et c'est un piege
    de mesure a connaitre : sur `rag_space`, un
    ``MATCH (v:Tag) WHERE v.Tag.<prop> == ...`` rend **`IndexNotFound`** sur un
    tag qui ne porte AUCUN index de tag, alors qu'un simple
    ``RETURN v.Tag.<prop>`` passe. `mesure` le 31 aout 2026 : le filtre passe sur
    `Document`, qui porte `doc_index`, et echoue sur `SectionHeader`, qui n'en a
    pas. Registre §4.27.
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


def _verifier_le_tag_document(session: Any) -> list[str]:
    """Constate que le tag ``Document`` porte toutes ses colonnes.

    LE DEFAUT QUE `_verifier_les_tags` A FERME RESTAIT OUVERT D'UN TAG.
    `NebulaWriter._verifier_les_tags` recoit ``sorted(set(TAG_MAP.values()))``,
    c'est-a-dire les **11 tags d'element** — le tag ``Document`` n'en fait pas
    partie, son schema lui etant propre. Or ses quatre `ALTER TAG Document ADD`
    (`nebula.py:333-340`) sont `required=False` par construction, « la colonne
    existe deja » etant leur cas nominal : une migration REELLEMENT refusee ne
    disait donc rien. Parmi ces colonnes, `source_path` **est l'exigence 3 du
    contrat** — l'identite d'un document.

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


def _lire_les_urls_visuelles(session: Any) -> list[str | None]:
    """Lit ``minio_url`` sur tous les sommets Picture et Table."""
    urls: list[str | None] = []
    for tag in ("Picture", "Table"):
        for ligne in _lire(session, f"MATCH (v:{tag}) RETURN v.{tag}.minio_url AS url;"):
            valeur = ligne[0]
            urls.append(None if valeur.is_null() else valeur.as_string())
    return urls


def _verifier_les_ancres(session: Any, metadatas: Sequence[Mapping[str, Any]]) -> list[str]:
    """Verifie que TOUTES les ancres existent comme noeuds du graphe.

    Sur la TOTALITE des identifiants, et non sur un echantillon : voir le
    commentaire en tete de module. Une seule requete, `mesure` a 0,053 s sur les
    3 750 identifiants de l'index complet.
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
