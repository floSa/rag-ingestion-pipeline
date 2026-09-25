"""Factory Dagster : genere partitions, assets, job et sensor pour chaque source.

Toutes les sources (PDF comme HTML) suivent le meme mecanisme :

- une partition dynamique par fichier (cle = chemin relatif a ``Datas/``) ;
- un sensor qui detecte les nouveaux fichiers / modifications via mtime ;
- un job qui materialise les assets de la source pour la partition.

Les sources HTML ont un asset de nettoyage supplementaire en amont de
l'extraction Docling.

NB : pas de ``from __future__ import annotations`` ici — Dagster valide le type
reel de l'argument ``context`` des assets, pas sa forme differee en chaine.
"""

import glob as globlib
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    AssetsDefinition,
    AssetSelection,
    Backoff,
    DagsterInstance,
    DefaultSensorStatus,
    DynamicPartitionsDefinition,
    Failure,
    RetryPolicy,
    RunRequest,
    RunsFilter,
    SensorDefinition,
    SensorEvaluationContext,
    SensorResult,
    SkipReason,
    asset,
    define_asset_job,
    sensor,
)

# Tags que le daemon pose sur un run cree depuis une ``RunRequest`` ; il les
# interroge pour savoir si une cle de run est deja consommee. Ils sont importes
# et non recopies : une chaine recopiee divergerait en silence a une montee de
# version de Dagster, alors qu'un import casse leve des le chargement du module.
from dagster._core.storage.tags import RUN_KEY_TAG, SENSOR_NAME_TAG

if TYPE_CHECKING:
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

from src.docling_service.elements import cleaned_path
from src.pipeline.cleaning import clean_html_file
from src.pipeline.media import ExportateurDImages

# Importes et non redefinis, pour qu'une seule definition existe.
# `STATUTS_EN_COURS` se definit par soustraction des etats terminaux (voir
# `reindex_job`). `_decrire_le_run` ecrit la phrase « le run X est en Y depuis
# N s » des raisons de saut (registre 4.15). Pas d'import circulaire :
# `reindex_job` n'importe pas ce module ; il ne recoit que les noms des jobs
# d'ingestion, passes par `definitions.py`.
from src.pipeline.reindex_job import STATUTS_EN_COURS, _decrire_le_run
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig

# Reprise automatique des echecs transitoires : service d'extraction redemarre
# (sa file vit en memoire), coupure reseau, store momentanement indisponible.
# Sur une ingestion de plusieurs heures sans surveillance, c'est ce qui evite
# de retrouver des partitions rouges pour une raison sans rapport avec les
# documents. Les echecs propres au document, eux, ne sont pas retentes.
EXTRACTION_RETRY_POLICY = RetryPolicy(max_retries=2, delay=120, backoff=Backoff.EXPONENTIAL)

# GESTE DE REINGESTION (registre 4.32.a).
#
# Dagster refuse toute ``RunRequest`` dont le ``run_key`` figure deja dans
# l'historique des runs, sans limite de temps. Avec une cle deterministe sur
# ``(source, partition, mtime)``, un fichier dont le ``mtime`` n'a pas bouge ne
# peut donc pas etre reingere. Mesure du 2 septembre 2026, curseur vide :
# 23 cles demandees, aucun run cree, ``skip_reason=None`` (detail dans
# ``documentation/campagnes/2026-09-02-premiere-campagne-de-reference.md``).
#
# 1. Declenchement. Poser a la main le curseur du capteur sur
#    ``reingerer:<etiquette>`` ; c'est le seul declencheur. Le marqueur remplace
#    le curseur JSON, donc le tick qui le lit ne connait plus aucun ``mtime`` :
#    il redemande une partition par fichier. La cle de run porte l'etiquette au
#    lieu du ``mtime``, ce qui la rend neuve pour Dagster.
#
# 2. Ce qui empeche une reingestion non demandee. Deux protections, pour deux
#    scenarios differents :
#
#    - Le curseur. Il vit dans le stockage de l'instance, survit au
#      rechargement du code, et ce module ne l'efface jamais. Tant qu'il
#      existe, un corpus inchange ne produit aucune demande.
#    - La forme de la cle. Sans marqueur, elle reste
#      ``{source}_{partition}_{mtime}``. Cela compte quand le curseur seul est
#      perdu et que l'historique des runs reste intact : remise a zero du
#      curseur depuis l'interface, capteur renomme, code location renommee. Le
#      capteur redemande alors tout le corpus ; Dagster ne cree aucun run, car
#      les cles sont deja consommees, et le capteur le signale avec un compte
#      (:func:`_cles_deja_consommees`). ``TestLaCleNominaleEstInchangee`` fige
#      cette propriete.
#
#    Limite : si le curseur et l'historique disparaissent ensemble, tout est
#    reingere sans avertissement, quelle que soit la forme de la cle. Les deux
#    vivent dans le meme Postgres (``dagster.yaml`` : ``run_storage`` et
#    ``schedule_storage``), ce qui s'est produit au registre 4.26.
#    ``test_des_cles_neuves_ne_declenchent_aucun_avertissement`` constate ce
#    comportement sur une instance vierge.
#
# 3. Geste repete. Avec la meme etiquette, le second tick reconstruit les memes
#    cles, et Dagster ne creerait aucun run. Le capteur le verifie avant
#    d'emettre et le signale avec un compte (:func:`_cles_deja_consommees`).
#    Une etiquette neuve relance la reingestion.
#
# 4. Ingestion deja en cours sur la source. Le controle porte sur le job, donc
#    sur tout run de la source (reingestion ou ingestion nominale). Le marqueur
#    n'est alors ni honore ni consomme : le tick rend un ``SkipReason``, le
#    curseur reste en place, et le tick suivant relit le marqueur. Sans ce
#    controle, un second marqueur d'etiquette neuve creerait un second jeu de
#    runs en parallele du premier. Deux runs sur la meme partition
#    reecriraient alors ``Datas/.cleaned/<fichier>`` en meme temps. C'est
#    possible : ``dagster.yaml`` fixe ``max_concurrent_runs: 2``, sans cle de
#    concurrence par partition (registre 4.33.c).
#
#    Ce controle ne s'applique qu'au marqueur. Un fichier modifie pendant une
#    reingestion produit une cle neuve sur une partition en cours. Le bloquer
#    empecherait de detecter un nouveau depot pendant une reingestion de
#    plusieurs heures ; ce cas reste ouvert au registre.
#
# L'etiquette est libre et obligatoire (une date, un motif). Un marqueur sans
# etiquette est refuse : sans elle, le geste ne serait pas repetable.
PREFIXE_REINGESTION = "reingerer:"


@dataclass
class SourceDefinitions:
    """Objets Dagster generes pour une source."""

    partitions: DynamicPartitionsDefinition
    assets: list[AssetsDefinition]
    job: "UnresolvedAssetJobDefinition"
    sensor: SensorDefinition


def _request_extraction(
    context: AssetExecutionContext, file_path: str, source_path: str
) -> dict[str, Any]:
    """Soumet un fichier au service Docling et suit le job jusqu'a son terme.

    L'extraction ne tient pas dans une requete HTTP : un livre de plusieurs
    centaines de pages depasse tout timeout raisonnable, et la requete bloquee
    faisait echouer le run pendant que le service continuait d'ecrire. On
    soumet, puis on interroge.

    Args:
        context: Contexte d'execution de l'asset (journalisation).
        file_path: Chemin du document, vu par le service.
        source_path: Chemin relatif a ``Datas/`` — la cle de partition. Il
            porte l'identite du document : c'est lui qui distingue deux
            chapitres homonymes appartenant a deux ouvrages differents.

    Returns:
        Bilan du job : elements, chunks, pages.

    Raises:
        RuntimeError: Si le job echoue, ou si le service l'a oublie.
        TimeoutError: Si le plafond par document est atteint.
    """
    settings = get_settings()
    base_url = settings.docling_service_url.rstrip("/")

    _wait_until_ready(context, base_url)
    context.log.info(f"Soumission a l'extraction : {file_path}")
    response = requests.post(
        f"{base_url}/extract",
        json={"filepath": file_path, "source_path": source_path},
        timeout=settings.extraction_submit_timeout,
    )
    response.raise_for_status()
    job_id = str(response.json()["job_id"])
    context.log.info(f"Job {job_id} en file pour {file_path}")

    return _await_job(context, base_url, job_id)


def _wait_until_ready(context: AssetExecutionContext, base_url: str) -> None:
    """Attend que le service d'extraction soit pret avant de lui soumettre un job.

    Au demarrage de la stack, le service charge ses modeles et initialise le
    schema du graphe : soumettre avant condamnerait le premier run pour une
    raison qui n'a rien a voir avec le document.

    Raises:
        RuntimeError: Si le service n'est toujours pas pret au bout du delai.
    """
    settings = get_settings()
    deadline = time.monotonic() + settings.extraction_readiness_timeout
    announced = False

    while True:
        try:
            response = requests.get(f"{base_url}/health", timeout=15)
            if response.status_code == 200:
                return
            detail = response.json()
        except requests.RequestException as exc:
            detail = str(exc)

        if time.monotonic() > deadline:
            raise RuntimeError(f"Service Docling toujours pas pret : {detail}")
        if not announced:
            context.log.info(f"Service Docling en cours de demarrage : {detail}")
            announced = True
        time.sleep(settings.extraction_poll_seconds)


def _await_job(context: AssetExecutionContext, base_url: str, job_id: str) -> dict[str, Any]:
    """Interroge un job jusqu'a son etat terminal, en journalisant l'avancement.

    Le premier sondage est immediat, puis l'intervalle croit jusqu'a
    ``extraction_poll_seconds``. Un chapitre HTML s'extrait en une seconde :
    attendre l'intervalle plein avant de regarder ajouterait, sur un corpus de
    plusieurs dizaines de fichiers, plus d'attente que de travail.
    """
    settings = get_settings()
    deadline = time.monotonic() + settings.extraction_timeout_seconds
    consecutive_failures = 0
    last_progress: str = ""
    interval = 0.0

    while True:
        if interval:
            time.sleep(interval)
        interval = min(max(interval * 2, 1.0), settings.extraction_poll_seconds)

        try:
            response = requests.get(f"{base_url}/jobs/{job_id}", timeout=30)
        except requests.RequestException as exc:
            consecutive_failures += 1
            if consecutive_failures > settings.extraction_max_poll_failures:
                raise RuntimeError(
                    f"Service Docling injoignable apres {consecutive_failures} sondages : {exc}"
                ) from exc
            context.log.warning(f"Sondage {job_id} en echec ({exc}), nouvelle tentative.")
            continue

        if response.status_code == 404:
            # Le service a redemarre : la file est en memoire, le job est perdu.
            # Erreur transitoire : la politique de reprise de l'asset la
            # rattrape sans intervention.
            raise RuntimeError(
                f"Job {job_id} inconnu du service Docling (redemarrage ?). Nouvelle tentative."
            )
        response.raise_for_status()
        consecutive_failures = 0

        snapshot: dict[str, Any] = response.json()
        progress = str(snapshot.get("progress") or {})
        if progress != last_progress:
            context.log.info(f"Job {job_id} : {snapshot['status']} — {progress}")
            last_progress = progress

        status = snapshot.get("status")
        if status == "success":
            return snapshot
        if status == "failed":
            # Echec propre au document (page illisible, format inattendu) :
            # inutile de reconvertir plusieurs centaines de pages pour obtenir
            # la meme erreur. On coupe court aux reprises.
            raise Failure(
                description=f"Extraction en echec : {snapshot.get('error')}",
                allow_retries=False,
            )

        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Job {job_id} toujours en cours apres "
                f"{settings.extraction_timeout_seconds}s : abandon."
            )


def _build_html_assets(
    source: SourceConfig,
    partitions: DynamicPartitionsDefinition,
) -> list[AssetsDefinition]:
    """Assets d'une source HTML : nettoyage puis extraction."""

    @asset(
        name="cleaned_html",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
    )
    def cleaned_html(context: AssetExecutionContext) -> str:
        """Nettoie le HTML source (boilerplate, nav, bruit SingleFile)."""
        settings = get_settings()
        source_path = Path(settings.source_dir) / context.partition_key
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        dest_path = cleaned_path(settings.source_dir, context.partition_key)

        exporter: ExportateurDImages | None = None
        if source.cleaning.export_images:
            doc_key = Path(context.partition_key).with_suffix("").as_posix()
            exporter = ExportateurDImages(doc_key=doc_key)

        report = clean_html_file(source_path, dest_path, source.cleaning, image_exporter=exporter)

        if report.strategy == "precleaned":
            context.log.warning(
                f"Content extraction below thresholds for {context.partition_key}; "
                "keeping pre-cleaned HTML."
            )
        context.add_output_metadata(
            {
                "strategy": report.strategy,
                "raw_bytes": report.raw_bytes,
                "cleaned_bytes": report.cleaned_bytes,
                "text_chars": report.text_chars,
                # Denominateur de la perte (registre 4.6). Seul, `text_chars` ne
                # dit pas si le nettoyage a retire du boilerplate ou ampute un
                # chapitre : `min_text_ratio` accepte un candidat qui garde 5 %
                # du texte. Le ratio est lu sur le bilan et non recalcule ici,
                # pour qu'un seul calcul existe.
                "precleaned_text_chars": report.precleaned_text_chars,
                "text_ratio": report.text_ratio,
                "images_exported": exporter.exported if exporter else 0,
            }
        )
        return str(dest_path)

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
        retry_policy=EXTRACTION_RETRY_POLICY,
        ins={"cleaned_html": AssetIn(key=AssetKey([source.name, "cleaned_html"]))},
    )
    def extracted_document(context: AssetExecutionContext, cleaned_html: str) -> dict[str, Any]:
        """Envoie le HTML nettoye au service Docling."""
        result = _request_extraction(context, cleaned_html, context.partition_key)
        _record_metadata(context, result)
        return result

    return [cleaned_html, extracted_document]


def _build_direct_assets(
    source: SourceConfig,
    partitions: DynamicPartitionsDefinition,
) -> list[AssetsDefinition]:
    """Asset d'une source sans pre-traitement (PDF, Markdown) : extraction directe.

    Le Markdown rejoint le PDF plutot que le HTML : il est deja propre, il n'y
    a ni boilerplate a retirer ni image inline a exporter.
    """

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
        retry_policy=EXTRACTION_RETRY_POLICY,
    )
    def extracted_document(context: AssetExecutionContext) -> dict[str, Any]:
        """Envoie le document source au service Docling."""
        settings = get_settings()
        file_path = Path(settings.source_dir) / context.partition_key
        if not file_path.exists():
            # Le fichier a disparu entre la detection du sensor et le run :
            # echouer plutot que de marquer la partition comme materialisee.
            raise FileNotFoundError(f"Source file not found: {file_path}")
        result = _request_extraction(context, str(file_path), context.partition_key)
        _record_metadata(context, result)
        return result

    return [extracted_document]


def _record_metadata(context: AssetExecutionContext, result: dict[str, Any]) -> None:
    """Publie le bilan d'extraction dans les metadonnees de l'asset.

    Aucun appel reseau ici : la reindexation de l'agent est declenchee en fin
    d'ingestion par ``reindex_job.py``, hors du chemin du document.

    Les cles publiees distinguent un document ecarte comme doublon d'un document
    ingere vide (registre 4.10). ``extract`` rend ``elements: 0`` dans les deux
    cas, mais ``duplicate_of`` seulement dans le premier. Le premier cas est
    voulu, le second est une panne.

    ``duplicate_of`` n'est publie que sur un document reellement ecarte : une cle
    presente et vide sur tous les documents finirait par ne plus etre lue.
    ``failed_batches`` est publie comme un compte, avec son detail dans
    ``failed_batches_detail`` : un run rouge ne dit pas combien de pages
    manquent (registre 4.1).
    """
    # Le bilan d'`extract` est dans `progress`, pas au premier niveau du
    # `snapshot()` : `main._run_extraction` appelle `job.report(**result)`, qui
    # range tout dans `progress`. Lire le premier niveau publierait des zeros
    # sans aucune erreur.
    progress = result.get("progress") or {}
    lots_en_echec = list(progress.get("failed_batches") or [])
    metadonnees: dict[str, Any] = {
        "elements": progress.get("elements", 0),
        "chunks": progress.get("chunks", 0),
        "pages": progress.get("pages", progress.get("pages_total", 0)),
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "pages_skipped": progress.get("pages_skipped", 0),
        "ocr": bool(progress.get("ocr", False)),
        "language": str(progress.get("language") or ""),
        "failed_batches": len(lots_en_echec),
        "failed_batches_detail": "; ".join(str(lot) for lot in lots_en_echec),
    }
    doublon = progress.get("duplicate_of")
    if doublon:
        metadonnees["duplicate_of"] = str(doublon)
    context.add_output_metadata(metadonnees)


class CurseurIllisibleError(RuntimeError):
    """Le curseur est du JSON bien forme, mais ce n'est pas un curseur de capteur.

    Cas typique : le marqueur de reingestion pose dans le JSON au lieu de
    remplacer le curseur entier, par exemple
    ``{"captures/p.html": "reingerer:2026-09-22"}``. La valeur n'est alors pas
    un mtime.

    Le capteur ne rattrape pas cette erreur, volontairement. Le tick echoue, puis
    echoue de nouveau au tick suivant, car ``update_cursor`` n'est pas atteint et
    le curseur fautif reste en place. L'erreur reste donc visible jusqu'a ce que
    le curseur soit corrige.

    La rattraper pour reinitialiser le curseur serait pire : un curseur vide fait
    redemander tout le corpus, sans avertissement (le probleme du registre
    4.32.a). C'est pourtant ce que fait la branche qui traite un curseur non JSON.

    Le message nomme la cle fautive et le geste correct, ce que ne fait pas le
    ``ValueError`` brut de ``float(...)``.
    """


def _mtimes_du_curseur(brut: str) -> dict[str, str]:
    """Lit le curseur nominal : une cle de partition, un mtime, et rien d'autre.

    Ces trois curseurs sont du JSON valide sans etre des curseurs. Sans ce
    controle, chacun faisait echouer le tick sur une exception non rattrapee
    (mesure du 22 septembre 2026) :

    ===================================== ==========================================
    Curseur                               Ce que le tick levait
    ===================================== ==========================================
    ``{"captures/p.html": "reingerer:…"}`` ``ValueError`` — sur ``float(last_mtime)``
    ``[1, 2, 3]``                          ``TypeError`` — sur ``dict(cursor_data)``
    ``3``                                  ``TypeError`` — sur ``dict(cursor_data)``
    ===================================== ==========================================

    L'appelant ne rattrape que ``json.JSONDecodeError`` : ``json.loads`` sur une
    ``str`` ne leve pas ``TypeError``.

    Args:
        brut: Curseur tel que Dagster le rend, non vide et sans marqueur.

    Returns:
        Les mtimes, par cle de partition, normalises en chaines.

    Raises:
        json.JSONDecodeError: Si le curseur n'est pas du JSON ; l'appelant
            repart alors d'un curseur vide.
        CurseurIllisibleError: Si c'est du JSON qui n'est pas un curseur.
    """
    charge = json.loads(brut)
    if not isinstance(charge, dict):
        raise CurseurIllisibleError(
            f"Le curseur de ce capteur est du JSON bien forme, mais ce n'est pas un "
            f"objet : {type(charge).__name__}. Un curseur nominal associe une cle de "
            f"partition a son mtime. Le curseur n'est PAS touche par ce tick : "
            f"corrigez-le, le capteur repartira seul."
        )
    for cle, valeur in charge.items():
        try:
            float(valeur)
        except (TypeError, ValueError) as exc:
            raise CurseurIllisibleError(
                f"Le curseur de ce capteur est du JSON bien forme, mais la valeur de "
                f"la cle « {cle} » n'est pas un mtime : {valeur!r}. LE MARQUEUR DE "
                f"REINGESTION SE POSE A LA PLACE DU CURSEUR ENTIER, pas dans le JSON : "
                f"le curseur doit valoir « {PREFIXE_REINGESTION}<etiquette> » et rien "
                f"d'autre. Le curseur n'est PAS touche par ce tick : corrigez-le, le "
                f"capteur repartira seul."
            ) from exc
    return {str(cle): str(valeur) for cle, valeur in charge.items()}


def _etiquette_de_reingestion(curseur: str | None) -> str | None:
    """Lit l'ordre de reingestion pose dans le curseur, s'il y en a un.

    Le marqueur est du TEXTE et non du JSON, et c'est delibere : il se pose a la
    main, depuis l'interface Dagster ou en une ligne de commande, sans
    echappement de guillemets. Un curseur nominal est un objet JSON, qui ne
    commence jamais par ``reingerer:``.

    Deux details de cette lecture comptent :

    - ``startswith`` et non ``in`` : le marqueur n'est un ordre qu'en tete du
      curseur. Avec ``in``, un curseur contenant la chaine ailleurs deviendrait
      un ordre, et l'etiquette serait mal decoupee. Teste par
      ``test_le_marqueur_n_est_honore_qu_en_tete_du_curseur`` ;
    - le ``.strip()`` final normalise l'etiquette : « reingerer: 2026-09-22 »
      et « reingerer:2026-09-22 » donnent les memes cles de run. Sans lui,
      l'espace en trop rendrait les cles neuves, et un geste repete
      reingererait tout sans avertissement au lieu d'etre refuse et signale.
      Teste par
      ``test_l_etiquette_est_la_meme_avec_ou_sans_espace_apres_le_marqueur``.
      Sur un marqueur suivi de seuls espaces, il ne change rien :
      ``curseur.strip()`` les a deja retires, et l'etiquette est vide.

    Args:
        curseur: Curseur du capteur, tel que Dagster le rend.

    Returns:
        ``None`` s'il n'y a pas d'ordre — le cas nominal, et celui de tout
        deploiement. L'etiquette s'il y en a une. La chaine VIDE si le marqueur
        est la sans etiquette : c'est un ordre mal forme, distinct de l'absence
        d'ordre, et l'appelant le refuse en le disant.
    """
    if not curseur:
        return None
    texte = curseur.strip()
    if not texte.startswith(PREFIXE_REINGESTION):
        return None
    return texte[len(PREFIXE_REINGESTION) :].strip()


def _cles_deja_consommees(
    instance: DagsterInstance, nom_du_capteur: str, cles: Sequence[str]
) -> list[str]:
    """Rend, parmi ces cles de run, celles dont Dagster ne creera aucun run.

    Le daemon n'ecrit rien quand il ecarte une demande dont la cle est deja
    consommee : le tick porte ``skip_reason=None`` (registre 4.32.a ; 22 runs
    perdus ainsi le 2 septembre 2026). Le capteur verifie donc lui-meme, avant
    d'emettre.

    La regle est celle du daemon (``dagster/_daemon/sensor.py::fetch_existing_runs``) :
    une requete ``RunsFilter(tags={RUN_KEY_TAG: cle})`` par cle (Dagster note
    qu'une requete ``IN`` unique est plus lente), puis seuls les runs dont
    ``SENSOR_NAME_TAG`` est ce capteur sont retenus. Ce filtre evite une fausse
    alerte quand deux capteurs homonymes vivent dans des depots differents.

    Cout : une requete par demande emise dans ce tick, quelle que soit la taille
    de l'historique. Mesure du 22 septembre 2026, corpus reel :

    - corpus inchange : aucune requete (aucune demande, fonction non appelee) ;
    - un document ajoute ou modifie : 1 requete. C'est le cas courant ;
    - premier tick, curseur perdu ou reingestion : une requete par fichier
      retenu, soit 22 pour `livres_html` et 1 pour `pdfs`.

    Args:
        instance: Instance Dagster interrogee.
        nom_du_capteur: Nom du capteur, celui que le daemon tague.
        cles: Cles de run sur le point d'etre emises.

    Returns:
        Les cles deja consommees, dans l'ordre ou elles ont ete presentees.
    """
    consommees: list[str] = []
    for cle in cles:
        runs = instance.get_runs(filters=RunsFilter(tags={RUN_KEY_TAG: cle}))
        if any((run.tags or {}).get(SENSOR_NAME_TAG) == nom_du_capteur for run in runs):
            consommees.append(cle)
    return consommees


def _build_sensor(
    source: SourceConfig,
    partitions_name: str,
    partitions: DynamicPartitionsDefinition,
    job_name: str,
) -> SensorDefinition:
    """Sensor de detection de fichiers : une partition + un run par fichier nouveau/modifie.

    Il porte aussi le geste de reingestion, decrit au-dessus de
    :data:`PREFIXE_REINGESTION` : le chemin nominal detecte ce qui a change, le
    marqueur redemande tout, et les deux se distinguent par la forme de la cle
    de run.
    """
    nom_du_capteur = f"{source.name}_sensor"

    @sensor(
        name=nom_du_capteur,
        minimum_interval_seconds=30,
        job_name=job_name,
        default_status=DefaultSensorStatus.RUNNING,
    )
    def file_sensor(context: SensorEvaluationContext) -> SensorResult | SkipReason:
        etiquette = _etiquette_de_reingestion(context.cursor)
        if etiquette == "":
            # Marqueur sans etiquette : ne rien lancer et laisser le curseur en
            # place pour qu'il soit corrige. L'honorer donnerait une cle
            # constante, et le geste suivant serait refuse sans avertissement
            # (registre 4.32.a).
            context.log.warning(
                f"Curseur pose sur « {PREFIXE_REINGESTION} » SANS etiquette : aucune "
                "reingestion n'est lancee, et le curseur n'est pas touche. L'etiquette "
                "est ce qui distingue deux gestes successifs ; sans elle, le second "
                "porterait les memes cles de run et Dagster le refuserait sans un mot "
                f"(registre 4.32.a). Posez par exemple « {PREFIXE_REINGESTION}"
                "2026-09-22-apres-purge »."
            )
            return SensorResult(run_requests=[], dynamic_partitions_requests=[])

        if etiquette:
            # Refus du marqueur si une ingestion de la source est en cours
            # (registre 4.33.c ; point 4 du bloc au-dessus de
            # `PREFIXE_REINGESTION`). Un second marqueur d'etiquette neuve
            # produirait un second jeu complet de demandes, a cles neuves, que
            # Dagster accepterait. `dagster.yaml` fixe `max_concurrent_runs: 2`
            # sans cle de concurrence par partition : deux runs simultanes
            # pourraient alors ecrire le meme `Datas/.cleaned/<fichier>`.
            #
            # Le filtre porte sur le job, donc sur tout run de la source : le
            # risque est deux runs sur une meme partition, que le premier vienne
            # d'une reingestion ou du chemin nominal. Le message dit donc « une
            # ingestion est deja en vol », et non « une reingestion ».
            #
            # Le refus differe le geste sans le perdre : avec `SkipReason`,
            # `update_cursor` n'est pas atteint, et le tick suivant relit le
            # marqueur. `reindex_job` suit le meme principe.
            #
            # Un run bloque en `STARTED` (worker tue, daemon interrompu) n'est
            # jamais terminal : le marqueur serait refuse a chaque tick. Le
            # `run_monitoring` de `dagster.yaml` passe ce run en echec apres
            # `max_runtime_seconds`, soit 90 000 s (25 h). Ne pas confondre avec
            # les 86 400 s (24 h) d'`extraction_timeout_seconds`, plafond par
            # document ; l'arithmetique des deux est ecrite dans `dagster.yaml`.
            # Ce delai est gere par le daemon plutot que par chaque capteur. En
            # attendant, la raison de saut nomme le run et son age.
            #
            # Le chemin nominal n'est pas soumis a ce refus : un fichier modifie
            # pendant une reingestion produit une cle neuve sur une partition
            # en cours. Le bloquer empecherait de detecter un nouveau depot
            # pendant une reingestion de plusieurs heures ; ce cas reste ouvert
            # au registre.
            en_vol = context.instance.get_run_records(
                RunsFilter(job_name=job_name, statuses=list(STATUTS_EN_COURS)), limit=1
            )
            if en_vol:
                return SkipReason(
                    f"Une ingestion de {source.name} est deja en vol : le marqueur "
                    f"« {PREFIXE_REINGESTION}{etiquette} » n'est PAS consomme, et sera "
                    f"relu au prochain tick. Deux runs simultanes sur la meme partition "
                    f"reecriraient le meme HTML nettoye en meme temps (registre 4.33.c). "
                    f"{_decrire_le_run(en_vol[0])}"
                )

        source_dir = get_settings().source_dir
        pattern = str(Path(source_dir) / source.glob)
        discovered = sorted(globlib.glob(pattern, recursive=True))

        # Index, sommaire, page de copyright : aucune phrase a indexer, mais
        # tout le vocabulaire de l'ouvrage. Ecartes avant meme la partition,
        # ils ne coutent ni run, ni place, ni bruit dans les reponses.
        files = [f for f in discovered if not source.is_ignored(f)]
        if len(files) < len(discovered):
            ecartes = [os.path.relpath(f, source_dir) for f in discovered if f not in set(files)]
            context.log.info(f"Ignored (front/back matter): {', '.join(sorted(ecartes))}")

        cursor_data: dict[str, str] = {}
        if etiquette:
            context.log.info(
                f"Reingestion demandee, etiquette « {etiquette} » : les {len(files)} "
                "fichiers de la source sont redemandes, quel que soit leur mtime. "
                "Le marqueur est consomme par ce tick."
            )
        elif context.cursor:
            try:
                cursor_data = _mtimes_du_curseur(context.cursor)
            except json.JSONDecodeError:
                # Curseur qui n'est pas du JSON : repartir d'un curseur vide.
                # `CurseurIllisibleError` n'est volontairement pas rattrapee
                # (voir sa docstring).
                context.log.warning("Invalid cursor format, resetting.")

        run_requests: list[RunRequest] = []
        cles_demandees: list[str] = []
        partition_requests = []
        new_cursor = dict(cursor_data)

        for f in files:
            # Chemin relatif : cle de partition stable et lisible dans l'UI
            partition_key = os.path.relpath(f, source_dir)

            if not context.instance.has_dynamic_partition(partitions_name, partition_key):
                context.log.info(f"Adding new partition for file: {partition_key}")
                partition_requests.append(partitions.build_add_request([partition_key]))

            mtime = os.path.getmtime(f)
            last_mtime = cursor_data.get(partition_key)

            if not last_mtime or float(last_mtime) < mtime:
                # Sans marqueur, la cle garde sa forme historique : pour un
                # fichier inchange, elle est deja dans l'historique et rien ne
                # repart. Seul le marqueur produit une cle neuve, qui porte son
                # etiquette.
                run_key = (
                    f"{source.name}_{partition_key}_reingestion_{etiquette}"
                    if etiquette
                    else f"{source.name}_{partition_key}_{mtime}"
                )
                context.log.info(f"Requesting run for partition: {partition_key}")
                run_requests.append(RunRequest(run_key=run_key, partition_key=partition_key))
                cles_demandees.append(run_key)
                new_cursor[partition_key] = str(mtime)

        if cles_demandees:
            perdues = _cles_deja_consommees(context.instance, nom_du_capteur, cles_demandees)
            if perdues:
                context.log.warning(
                    f"{len(perdues)} demande(s) de run sur {len(cles_demandees)} portent un "
                    "run_key DEJA CONSOMME : Dagster n'en creera aucun run, et il ne le dira "
                    "pas — le tick portera skip_reason=None (registre 4.32.a). Le curseur, "
                    "lui, avance : le geste est a refaire avec une etiquette NEUVE, "
                    f"« {PREFIXE_REINGESTION}<etiquette> ». Premieres cles perdues : "
                    f"{', '.join(perdues[:3])}"
                )

        # `or etiquette` : sur une source au corpus vide, le curseur calcule
        # vaut `{}`, comme celui de depart. Sans cette condition, le marqueur ne
        # serait jamais consomme et chaque tick le rejouerait.
        if new_cursor != cursor_data or etiquette:
            context.update_cursor(json.dumps(new_cursor))

        return SensorResult(
            run_requests=run_requests,
            dynamic_partitions_requests=partition_requests,
        )

    return file_sensor


def build_source(source: SourceConfig) -> SourceDefinitions:
    """Genere l'ensemble des objets Dagster pour une source declaree.

    Args:
        source: Configuration de la source (voir ``sources.yaml``).

    Returns:
        Partitions, assets, job et sensor de la source.
    """
    partitions_name = f"{source.name}_files"
    partitions = DynamicPartitionsDefinition(name=partitions_name)

    if source.needs_cleaning:
        assets_list = _build_html_assets(source, partitions)
    else:
        assets_list = _build_direct_assets(source, partitions)

    job_name = f"{source.name}_job"
    job = define_asset_job(name=job_name, selection=AssetSelection.assets(*assets_list))
    sensor_def = _build_sensor(source, partitions_name, partitions, job_name)

    return SourceDefinitions(
        partitions=partitions,
        assets=assets_list,
        job=job,
        sensor=sensor_def,
    )
