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

**L'echantillon de 400 etait justifie par une phrase d'exhaustivite** : « une
rupture de contrat est systematique ». C'est vrai d'un FORMAT — un ``element_id``
mal forme l'est pour tous — et faux d'un ORDRE, qui peut se casser sur un
document sur vingt. L'echantillonnage est donc borne au seul controle qu'il
justifie, la presence des ancres dans le graphe, dont le cout croit avec le
corpus. Les proprietes d'ordre sont verifiees sur la TOTALITE des aretes.

Sort en code d'erreur si une anomalie est detectee, pour un usage en
pre-deploiement.
"""

from __future__ import annotations

import random
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT_ELEMENT_ID = re.compile(r"^[a-f0-9]{10}$")

# Verifier chaque ancre dans le graphe demande une requete par paquet
# d'identifiants : sur un gros corpus, c'est le seul controle dont le cout
# croit vraiment. Un echantillon y suffit parce qu'une ancre absente traduit un
# desaccord de CALCUL d'identifiant, qui est systematique.
#
# Cette justification ne vaut PAS pour les proprietes d'ordre : elles sont
# verifiees sur la totalite des aretes, plus bas.
TAILLE_ECHANTILLON = 400


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

    L'agent reconstitue un element decoupe en concatenant ses chunks dans
    l'ordre de ``chunk_index``. Un index hors bornes lui fait sauter un morceau
    ou en attendre un qui n'existe pas, sans erreur.

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
        print("Index vide : rien a verifier.")
        return

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

    session = pool.get_session("root", "nebula")
    try:
        session.execute(f"USE {SPACE};")

        # L'ordre de lecture, sur la TOTALITE des aretes : une inversion peut
        # n'affecter qu'un document sur vingt, et un echantillon la manquerait.
        aretes = _lire_les_aretes(session)
        print(f"aretes PARENT_OF examinees     : {len(aretes)}")
        if not aretes:
            anomalies.append("aucune arete PARENT_OF : le graphe n'a pas de hierarchie")
        inversions = inversions_de_page(aretes)
        print(f"inversions de page dans l'ordre: {len(inversions)}")
        if inversions:
            anomalies.append(f"sequence non monotone (ex. {inversions[:3]})")

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


def _lire_les_aretes(session: Any) -> list[tuple[str, int, int]]:
    """Lit TOUTES les aretes PARENT_OF, avec leur sequence et leur page.

    Une seule requete pour tout le graphe, puis le rattachement au document se
    calcule en memoire par :func:`racine_de_chaque_element` : une requete de
    chemin variable par sommet couterait un aller-retour par element.
    """
    peres: dict[str, str] = {}
    brut: list[tuple[str, int, int]] = []
    for ligne in _lire(
        session,
        "MATCH (a)-[e:PARENT_OF]->(v) RETURN id(a) AS pere, id(v) AS fils, "
        "e.sequence AS seq, properties(v).page_no AS page;",
    ):
        pere, fils = ligne[0].as_string(), ligne[1].as_string()
        peres[fils] = pere
        page = 0 if ligne[3].is_null() else int(ligne[3].as_int())
        brut.append((fils, int(ligne[2].as_int()), page))

    racines = racine_de_chaque_element(peres)
    return [(racines.get(fils, fils), sequence, page) for fils, sequence, page in brut]


def _lire_les_urls_visuelles(session: Any) -> list[str | None]:
    """Lit ``minio_url`` sur tous les sommets Picture et Table."""
    urls: list[str | None] = []
    for tag in ("Picture", "Table"):
        for ligne in _lire(session, f"MATCH (v:{tag}) RETURN v.{tag}.minio_url AS url;"):
            valeur = ligne[0]
            urls.append(None if valeur.is_null() else valeur.as_string())
    return urls


def _verifier_les_ancres(session: Any, metadatas: Sequence[Mapping[str, Any]]) -> list[str]:
    """Verifie qu'un echantillon d'ancres existe comme noeuds du graphe."""
    random.seed(0)
    identifiants = sorted({str(m["element_id"]) for m in metadatas})
    echantillon = random.sample(identifiants, min(TAILLE_ECHANTILLON, len(identifiants)))
    liste = ", ".join(f'"{identifiant}"' for identifiant in echantillon)
    lignes = _lire(session, f"MATCH (v) WHERE id(v) IN [{liste}] RETURN count(DISTINCT id(v));")
    if not lignes:
        return ["comptage des ancres impossible"]
    trouves = int(lignes[0][0].as_int())
    print(f"ancres presentes dans le graphe : {trouves}/{len(echantillon)}")
    if trouves != len(echantillon):
        return [f"{len(echantillon) - trouves} ancres absentes du graphe"]
    return []


if __name__ == "__main__":
    main()
