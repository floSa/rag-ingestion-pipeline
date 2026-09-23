"""Reextrait des documents et prouve qu'aucun `element_id` n'a bouge. Sort en 1 sinon.

**LE GESTE QUE LA CAMPAGNE DE REINGESTION DEVRA REFAIRE.** Avant la campagne,
ce script atteste que le pipeline redonne les memes identifiants que ceux du
graphe ; apres, il atteste que la reingestion ne les a pas deplaces. Les deux
mesures doivent etre LA MEME, et c'est pour cela que ce script est versionne :
reconstruire la sonde inviterait une sonde differente, donc deux chiffres qui
ne se comparent pas.

Il tourne DANS l'image d'extraction, comme `verifier-le-jeu-de-questions.py`,
et pour la meme raison — `docling` et `chromadb` n'appartiennent pas aux
dependances du depot. Le `src` monte est celui de la branche MESUREE, et non
celui du clone principal (registre 4.27) :

    docker run --rm --network rag_network \\
      -v "$PWD/src":/app/src:ro -v "$PWD/scripts":/app/scripts:ro \\
      -v "<clone principal>/Datas":/corpus:ro \\
      -v "<un scratchpad>":/sp \\
      -v /var/lib/docker/volumes/rag-ingestion-pipeline_docling_models/_data:/tmp/.cache \\
      --env-file <clone principal>/.env \\
      -e HOME=/tmp -e PYTHONPATH=/app -w /app \\
      rag-ingestion-pipeline-docling-service \\
      python scripts/campagne/verifier-l-equivalence-des-identifiants.py /sp/cibles.json

**LES HTML VEULENT LEUR COPIE NETTOYEE**, produite par l'etape 1 du pipeline et
attendue sous `<scratchpad>/cleaned/<chemin de partition>`. C'est ELLE que
l'asset Dagster envoie a l'extraction, et non la source. Et elle doit etre
produite avec un exportateur d'images TEMOIN — qui calcule la meme cle et la
meme URL sans rien envoyer — et jamais avec `image_exporter=None` :
`cleaning.py` SUPPRIME alors l'attribut `src` au lieu de le reecrire, le HTML
nettoye ne fait plus les memes octets, Docling voit une autre entree, et la
mesure rend une fausse derive.

**CE SCRIPT N'ECRIT DANS AUCUN STORE**, et ce n'est pas une intention :
:func:`~src.equivalence_des_identifiants.armer_les_barrieres` remplace les
portes d'ecriture des trois stores par des fonctions qui LEVENT, avant toute
conversion. `storage.persist` est ensuite remplace par une CAPTURE — elle
collecte les elements et ne rappelle jamais la production, donc les barrieres
posees dessous restent le filet : une capture qui appellerait a travers
leverait.

**LE CONTROLE NEGATIF TOURNE ICI AUSSI**, en plus de la porte qualite. Les
quatre mutations de :data:`~src.equivalence_des_identifiants.MUTATIONS` sont
appliquees au releve reel du jour, et chacune DOIT faire bouger des
identifiants. Le vert de la porte qualite prouve la sonde sur un jeu d'essai ;
celui-ci la prouve sur le document reellement mesure, qui est le seul dont la
forme puisse rendre une mutation nulle — c'est exactement ce qui est arrive au
lot qui a ecrit la premiere version de ce harnais.

Le code de sortie EST le comportement : 0 si tout identifiant se retrouve, 1 au
premier desaccord ou si une mutation ne prouve rien.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.equivalence_des_identifiants import (
    MUTATIONS,
    MutationNulleError,
    appliquer_la_mutation,
    armer_les_barrieres,
    comparer,
    recalculer_les_identifiants,
)

CORPUS = Path("/corpus")
SCRATCHPAD = Path("/sp")

# Les tags d'element du graphe. `Document` en est exclu : ce n'est pas un
# element, il ne porte pas d'`element_id`, et le compter fausserait le total.
TAGS = (
    "Paragraph",
    "SectionHeader",
    "ListItem",
    "Table",
    "Picture",
    "Formula",
    "Code",
    "Caption",
    "Footnote",
    "PageHeader",
    "PageFooter",
)


def _capturer_les_elements() -> list[dict[str, Any]]:
    """Remplace `storage.persist` par une capture et rend la liste qu'elle remplit.

    La capture ne rappelle jamais la production : les barrieres armees dessous
    restent actives, donc une capture qui appellerait a travers leverait.

    Returns:
        La liste, remplie au fil des appels de `persist`.
    """
    from src.docling_service import extraction, storage

    vus: list[dict[str, Any]] = []

    def capture(
        elements: Any, identity: Any = None, facts: Any = None, document: Any = None
    ) -> int:
        vus.extend(dict(element) for element in elements)
        return 0

    storage.persist = capture  # type: ignore[assignment]
    extraction.storage.persist = capture  # type: ignore[assignment]
    return vus


def _temoin_des_images() -> list[str]:
    """Remplace `images.crop_and_upload` par un TEMOIN INERTE et rend ses cles.

    **POURQUOI UN TEMOIN ET NON LA BARRIERE.** `crop_and_upload` est sur le
    chemin NOMINAL d'un PDF : `_convert_batch` l'appelle pour renseigner
    `minio_url` de chaque element visuel. La barriere nue y leve, les lots
    tombent, `_extract_pdf` tente la purge du document partiel — qui leve a son
    tour — et le harnais ne mesure plus rien. `mesure` : barriere nue sur le PDF
    du corpus, 6 lots sur 15 en echec, `BatchExtractionError`.

    Le temoin calcule LA MEME CLE et LA MEME URL que la production — meme
    f-string, meme garde sur la bbox — et n'ouvre aucun client MinIO. Les
    barrieres posees dessous (`images.get_client`, `images.upload_file`) restent
    le filet : un temoin qui appellerait a travers leverait.

    La cle porte l'`element_id` (`images/<radical>/<element_id>_<label>.png`),
    donc la collecter permet de dire, apres coup, si une reingestion a deplace
    des cles d'objet — ce qu'un simple compte de sommets ne dirait pas.

    Returns:
        La liste des cles, remplie au fil des appels.
    """
    from src.docling_service import extraction, images

    cles: list[str] = []

    def temoin(
        doc: Any,
        pdf_stem: str,
        page_no: int,
        bbox: dict[str, float],
        image_id: str,
        element_type: str,
    ) -> str | None:
        # Meme garde que la production : sans bbox complete, elle rend None et
        # n'ecrit aucune cle. Un temoin plus permissif inventerait des objets.
        if not bbox or not all(key in bbox for key in ("l", "t", "r", "b")):
            return None
        objet = f"images/{pdf_stem}/{image_id}_{element_type}.png"
        cles.append(objet)
        return images.object_url(objet)

    images.crop_and_upload = temoin  # type: ignore[assignment]
    extraction.images.crop_and_upload = temoin  # type: ignore[assignment]
    return cles


def reextraire(partition_key: str, vus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reextrait un document par le chemin de PRODUCTION et rend son releve.

    Les fonctions appelees sont `_extract_flat` et `_extract_pdf`, celles de la
    production : seule la frontiere des stores est remplacee.

    Args:
        partition_key: Chemin du document relatif a `Datas/`.
        vus: La liste que la capture remplit, videe a l'entree.

    Returns:
        Un releve par element : `page_no`, `page_position`, `text50`.

    Raises:
        FileNotFoundError: Si la source, ou la copie nettoyee d'un HTML, manque.
    """
    from src.docling_service import extraction
    from src.docling_service.elements import document_identity

    vus.clear()
    identity = document_identity(partition_key)
    source = CORPUS / partition_key
    if not source.exists():
        raise FileNotFoundError(f"introuvable dans le corpus : {source}")

    if partition_key.lower().endswith(".html"):
        nettoye = SCRATCHPAD / "cleaned" / partition_key
        if not nettoye.exists():
            raise FileNotFoundError(
                f"copie nettoyee manquante : {nettoye}. C'est elle que le "
                "pipeline convertit, et non la source."
            )
        extraction._extract_flat(
            nettoye, identity, "html", extraction.file_digest(nettoye), extraction._noop
        )
    else:
        extraction._extract_pdf(source, identity, extraction.file_digest(source), extraction._noop)

    return [
        {
            "label": str(element.get("label") or ""),
            "page_no": int(element.get("page_no") or 0),
            "page_position": int(element.get("page_position") or 0),
            "text50": str(element.get("text") or "")[:50],
        }
        for element in vus
    ]


def lire_le_graphe(cle_du_document: str) -> dict[str, dict[str, Any]]:
    """Rend les sommets d'element d'un document, par identifiant. LECTURE SEULE.

    Args:
        cle_du_document: `identity.key` du document.

    Returns:
        Par identifiant de sommet, ses `page_no` et `text`.

    Raises:
        RuntimeError: Si une requete echoue — un graphe muet rendrait un
            « aucun sommet », qui se lit comme « tout a bouge ».
    """
    import os

    from nebula3.Config import Config
    from nebula3.gclient.net import ConnectionPool

    from src.docling_service.ngql import SPACE, document_vid

    pool = ConnectionPool()
    pool.init([(os.environ["NEBULA_HOST"], int(os.environ.get("NEBULA_PORT", "9669")))], Config())
    session = pool.get_session(os.environ["NEBULA_USER"], os.environ["NEBULA_PASSWORD"])

    def executer(requete: str) -> Any:
        resultat = session.execute(requete)
        if not resultat.is_succeeded():
            raise RuntimeError(f"nGQL echoue : {requete[:120]} -> {resultat.error_msg()}")
        return resultat

    try:
        executer(f"USE {SPACE};")
        racine = document_vid(cle_du_document)
        # Les descendants du Document par PARENT_OF, de proche en proche.
        vus: set[str] = set()
        frontiere = [racine]
        while frontiere:
            lot, frontiere = frontiere[:200], frontiere[200:]
            liste = ", ".join('"' + vid.replace('"', '\\"') + '"' for vid in lot)
            resultat = executer(f"GO FROM {liste} OVER PARENT_OF YIELD dst(edge) AS d;")
            for rang in range(resultat.row_size()):
                enfant = resultat.row_values(rang)[0].as_string()
                if enfant not in vus:
                    vus.add(enfant)
                    frontiere.append(enfant)

        sommets: dict[str, dict[str, Any]] = {}
        ordonnes = sorted(vus)
        for debut in range(0, len(ordonnes), 300):
            lot = ordonnes[debut : debut + 300]
            liste = ", ".join('"' + vid.replace('"', '\\"') + '"' for vid in lot)
            for tag in TAGS:
                resultat = executer(
                    f"MATCH (v:{tag}) WHERE id(v) IN [{liste}] RETURN id(v) AS vid, "
                    f"v.{tag}.page_no AS p, v.{tag}.text AS t;"
                )
                for rang in range(resultat.row_size()):
                    ligne = resultat.row_values(rang)
                    sommets[ligne[0].as_string()] = {
                        "tag": tag,
                        "page_no": ligne[1].as_int() if ligne[1].is_int() else -1,
                        "text": ligne[2].as_string() if ligne[2].is_string() else "",
                    }
        return sommets
    finally:
        session.release()
        pool.close()


def main() -> int:
    """Mesure l'equivalence sur les documents cibles, controle negatif compris."""
    if len(sys.argv) < 2:
        print("usage : verifier-l-equivalence-des-identifiants.py <cibles.json>")
        return 2

    armees = armer_les_barrieres()
    print(f"barrieres d'ecriture armees : {len(armees)} portes\n")
    vus = _capturer_les_elements()
    cles_d_objet = _temoin_des_images()

    from src.docling_service.elements import document_identity

    cibles = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    echecs: list[str] = []
    total_identiques = total_graphe_seul = 0

    for partition_key in cibles:
        print(f"=== {partition_key}", flush=True)
        cle = document_identity(partition_key).key
        cles_d_objet.clear()
        releve = reextraire(partition_key, vus)
        sommets = lire_le_graphe(cle)
        rapport = comparer(recalculer_les_identifiants(releve, cle), sommets)

        print(f"    elements reextraits      : {len(releve)}")
        print(f"    sommets du graphe        : {len(sommets)}")
        print(f"    identifiants IDENTIQUES  : {rapport.identiques}")
        print(f"    presents au recalcul seul: {rapport.recalcul_seul}")
        print(f"    presents au graphe seul  : {rapport.graphe_seul}")
        if cles_d_objet:
            print(f"    cles d'objet calculees   : {len(cles_d_objet)}")
        total_identiques += rapport.identiques
        total_graphe_seul += rapport.graphe_seul
        if rapport.graphe_seul or rapport.recalcul_seul:
            print(f"    attribution (INDICATIVE) : {rapport.attribution}")
            for exemple in rapport.exemples[:4]:
                print(f"      {exemple}")
            echecs.append(partition_key)

        # ─── LE CONTROLE NEGATIF, SUR LE RELEVE REEL DU JOUR ────────────────
        for nom, mutation in sorted(MUTATIONS.items()):
            try:
                cle_mutee, mutes = appliquer_la_mutation(mutation, releve, cle)
            except MutationNulleError as exc:
                print(f"    MUTATION NULLE « {nom} » : {exc}")
                echecs.append(f"{partition_key} / mutation {nom}")
                continue
            vu = comparer(recalculer_les_identifiants(mutes, cle_mutee), sommets)
            if vu.graphe_seul == 0:
                print(f"    LA SONDE NE VOIT PAS « {nom} » : le zero ci-dessus ne vaut rien")
                echecs.append(f"{partition_key} / mutation {nom}")
            else:
                print(f"    controle negatif « {nom} » : {vu.graphe_seul} deplaces, vu")

    print(f"\nTOTAL identiques {total_identiques}, graphe seul {total_graphe_seul}")
    if echecs:
        print(f"ECHEC sur {len(echecs)} : {echecs}")
        return 1
    print("OK : aucun element_id n'a bouge, et la sonde sait dire le contraire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
