"""Ecriture du graphe de connaissances dans NebulaGraph.

Deux changements structurants par rapport a la version initiale :

- **un pool partage** au lieu d'une connexion recreee a chaque flush (soit
  toutes les cinq pages, avec sa sequence de retry) ;
- **des INSERT groupes** au lieu de deux aller-retours par element : sur un
  livre de 400 pages, cela fait passer les dizaines de milliers de requetes a
  quelques centaines.

Les echecs d'ecriture ne sont plus silencieux : ils remontent et font echouer
le job, plutot que de laisser un run au vert sur un graphe incomplet.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from src.docling_service.elements import ROOT_REFERENCE, TAG_MAP, tag_for_label
from src.docling_service.ngql import (
    VID_MAX_BYTES,
    document_vid,
    edge_value,
    insert_edge_statements,
    insert_vertex_statements,
    quote,
    vertex_value,
)
from src.docling_service.settings import get_settings

logger = logging.getLogger(__name__)

SPACE = "rag_space"

VERTEX_PROPERTIES = ("label", "page_no", "text", "minio_url")
DOCUMENT_PROPERTIES = ("filename", "type_file", "total_pages")


class NebulaError(RuntimeError):
    """Echec d'une operation NebulaGraph."""


class NebulaWriter:
    """Acces a NebulaGraph : pool partage, sessions courtes, ecritures groupees."""

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()

    # ── Connexion ────────────────────────────────────────────────────────────

    def _connect(self, max_attempts: int, wait_seconds: float) -> ConnectionPool:
        """Ouvre un pool, avec retry (le graphd met du temps a etre pret)."""
        settings = get_settings()
        for attempt in range(1, max_attempts + 1):
            pool = ConnectionPool()
            try:
                if pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
                    return pool
                logger.warning("Nebula: init a renvoye False (%d/%d)", attempt, max_attempts)
            except Exception as exc:
                logger.warning(
                    "Nebula indisponible (%s), tentative %d/%d", exc, attempt, max_attempts
                )
            pool.close()
            if attempt < max_attempts:
                time.sleep(wait_seconds)
        raise NebulaError(f"connexion impossible apres {max_attempts} tentatives")

    def _get_pool(self) -> ConnectionPool:
        """Retourne le pool partage, en l'ouvrant au premier appel."""
        with self._lock:
            if self._pool is None:
                settings = get_settings()
                self._pool = self._connect(
                    settings.nebula_max_attempts, settings.nebula_retry_seconds
                )
            return self._pool

    def close(self) -> None:
        """Ferme le pool partage."""
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    @contextmanager
    def session(self, use_space: bool = True) -> Iterator[Any]:
        """Ouvre une session, la libere systematiquement.

        Args:
            use_space: Bascule sur ``rag_space`` a l'ouverture. Mis a False
                pendant l'initialisation du schema, avant que le space existe.

        Yields:
            La session NebulaGraph.

        Raises:
            NebulaError: Si la connexion echoue ou si ``USE rag_space`` echoue.
                Cet echec doit etre bruyant : sinon tous les INSERT suivants
                echouent en silence et le run se termine au vert sur un graphe vide.
        """
        session = self._get_pool().get_session("root", "nebula")
        try:
            if use_space:
                execute(session, f"USE {SPACE};")
            yield session
        finally:
            session.release()

    # ── Ecriture d'un document ───────────────────────────────────────────────

    def write_elements(
        self,
        elements: Sequence[dict[str, Any]],
        filename: str,
        type_file: str,
        total_pages: int = 0,
    ) -> None:
        """Ecrit un lot d'elements et leurs relations dans le graphe.

        Args:
            elements: Elements produits par ``DocumentAccumulator``.
            filename: Nom du document sans extension.
            type_file: ``pdf``, ``html`` ou ``md``.
            total_pages: Nombre de pages du document (0 si non pagine).

        Raises:
            NebulaError: Si une requete est rejetee par le graphd.
        """
        if not elements:
            return

        max_chars = get_settings().graph_text_max_chars
        doc_vid = document_vid(filename)

        vertices_by_tag: dict[str, list[str]] = {}
        parent_edges: list[str] = []
        caption_edges: list[str] = []
        last_visual_id: str | None = None

        for element in elements:
            label = str(element["label"])
            tag = tag_for_label(label)
            vertices_by_tag.setdefault(tag, []).append(
                vertex_value(
                    str(element["id"]),
                    (
                        label,
                        int(element["page_no"]),
                        str(element.get("text") or "")[:max_chars],
                        str(element.get("minio_url") or ""),
                    ),
                )
            )

            reference_id = str(element.get("reference_id") or ROOT_REFERENCE)
            parent_vid = doc_vid if reference_id == ROOT_REFERENCE else reference_id
            parent_edges.append(
                edge_value(parent_vid, str(element["id"]), (int(element["order"]),))
            )

            if tag == "Caption" and last_visual_id:
                caption_edges.append(edge_value(str(element["id"]), last_visual_id, ("describes",)))
            if tag in ("Table", "Picture"):
                last_visual_id = str(element["id"])

        with self.session() as session:
            for statement in insert_vertex_statements(
                "Document",
                DOCUMENT_PROPERTIES,
                [vertex_value(doc_vid, (filename, type_file, total_pages))],
            ):
                execute(session, statement)

            for tag, values in vertices_by_tag.items():
                for statement in insert_vertex_statements(tag, VERTEX_PROPERTIES, values):
                    execute(session, statement)

            for statement in insert_edge_statements("PARENT_OF", ("sequence",), parent_edges):
                execute(session, statement)

            if caption_edges:
                for statement in insert_edge_statements("LINKED_TO", ("relation",), caption_edges):
                    execute(session, statement)

        logger.info(
            "Nebula: %d elements ecrits pour %s (%d tags)",
            len(elements),
            filename,
            len(vertices_by_tag),
        )

    def delete_document(self, filename: str) -> None:
        """Supprime les vertices d'un document (re-ingestion propre)."""
        with self.session() as session:
            execute(session, f"DELETE VERTEX {quote(document_vid(filename))} WITH EDGE;")

    # ── Initialisation du schema ─────────────────────────────────────────────

    def init_schema(self) -> bool:
        """Cree le space, les tags et les edges. Idempotent.

        Ne leve pas : appelee au demarrage du service, elle journalise et rend
        la main pour que le service reste interrogeable — ``/health`` signale
        alors ``graph_ready: false`` et refuse de se declarer pret.

        Returns:
            True si le schema est en place.
        """
        try:
            self._create_space()
            self._create_tags()
        except Exception as exc:
            logger.error("Initialisation du schema NebulaGraph echouee : %s", exc)
            return False
        return True

    def _create_space(self) -> None:
        """Cree ``rag_space`` et attend qu'il soit reellement visible."""
        settings = get_settings()
        with self.session(use_space=False) as session:
            # Enregistrement manuel du storaged, requis hors orchestrateur.
            # Echoue si deja enregistre : tolere.
            execute(session, 'ADD HOSTS "storaged":9779;', required=False)
            time.sleep(3)

            # CREATE SPACE echoue tant que storaged n'a pas fini son heartbeat :
            # on retente jusqu'a ce que le space existe VRAIMENT, sinon tous les
            # flushs partent dans le vide en silence.
            for attempt in range(1, settings.nebula_space_attempts + 1):
                execute(
                    session,
                    f"CREATE SPACE IF NOT EXISTS {SPACE}(partition_num=10, "
                    f"replica_factor=1, vid_type=FIXED_STRING({VID_MAX_BYTES}));",
                    required=False,
                )
                time.sleep(5)
                result = session.execute("SHOW SPACES;")
                names = [result.row_values(i)[0].as_string() for i in range(result.row_size())]
                if SPACE in names:
                    time.sleep(5)  # propagation avant USE
                    return
                logger.warning(
                    "%s absent (tentative %d/%d)", SPACE, attempt, settings.nebula_space_attempts
                )
        raise NebulaError(f"{SPACE} n'a pas pu etre cree")

    def _create_tags(self) -> None:
        """Cree les tags, edges et index du schema semantique."""
        with self.session() as session:
            execute(
                session,
                "CREATE TAG IF NOT EXISTS Document"
                "(filename string, type_file string, total_pages int);",
            )
            # Deploiements anterieurs : le tag existe sans total_pages, et
            # CREATE TAG IF NOT EXISTS ne l'ajoute pas. Tolere si deja present.
            execute(session, "ALTER TAG Document ADD (total_pages int);", required=False)

            for tag in sorted(set(TAG_MAP.values())):
                execute(
                    session,
                    f"CREATE TAG IF NOT EXISTS {tag}"
                    "(label string, page_no int, text string, minio_url string);",
                )

            execute(session, "CREATE EDGE IF NOT EXISTS PARENT_OF(sequence int);")
            execute(session, "CREATE EDGE IF NOT EXISTS LINKED_TO(relation string);")
            execute(session, "CREATE TAG INDEX IF NOT EXISTS doc_index ON Document(filename(20));")

        # NebulaGraph propage les changements de schema de maniere asynchrone :
        # ecrire immediatement apres un CREATE/ALTER expose a un rejet pour tag
        # inconnu. On laisse passer quelques heartbeats.
        time.sleep(10)
        logger.info("Schema semantique NebulaGraph pret.")


def execute(session: Any, nql: str, required: bool = True) -> bool:
    """Execute une requete nGQL.

    Args:
        session: Session NebulaGraph.
        nql: Requete a executer.
        required: Si True, un echec leve. Mis a False pour les requetes dont
            l'echec est attendu (``ADD HOSTS`` deja enregistre, ``ALTER TAG``
            sur une colonne existante).

    Returns:
        True si la requete a reussi.

    Raises:
        NebulaError: Si la requete echoue et que ``required`` est True.
    """
    result = session.execute(nql)
    if result.is_succeeded():
        return True

    message = f"nGQL rejete : {result.error_msg()} -- {nql[:200]}"
    if required:
        raise NebulaError(message)
    logger.debug("%s (tolere)", message)
    return False


_writer: NebulaWriter | None = None
_writer_lock = threading.Lock()


def get_writer() -> NebulaWriter:
    """Retourne l'instance partagee du writer NebulaGraph."""
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = NebulaWriter()
        return _writer
