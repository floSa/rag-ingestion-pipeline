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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nebula3.gclient.net import ConnectionPool

from src.docling_service.elements import (
    ROOT_REFERENCE,
    TAG_MAP,
    DocumentFacts,
    DocumentIdentity,
    tag_for_label,
)
from src.docling_service.ngql import (
    DOCUMENT_PROPERTIES,
    VERTEX_PROPERTIES,
    VID_MAX_BYTES,
    compter_les_textes_coupes,
    document_vid,
    edge_value,
    element_vertex_value,
    insert_edge_statements,
    insert_vertex_statements,
    missing_vertex_columns,
    quote,
    tag_schema_statements,
    vertex_value,
)
from src.docling_service.settings import get_settings

logger = logging.getLogger(__name__)

SPACE = "rag_space"

# VERTEX_PROPERTIES et DOCUMENT_PROPERTIES vivaient ici ET dans ngql.py, avec
# des valeurs differentes, celle d'ici etant la vraie. Elles n'ont plus qu'un
# site, ngql.py, qui est aussi le seul des deux modules testable sans graphd.


class NebulaError(RuntimeError):
    """Echec d'une operation NebulaGraph."""


class NebulaWriter:
    """Acces a NebulaGraph : pool partage, sessions courtes, ecritures groupees."""

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()

    # ── Connexion ────────────────────────────────────────────────────────────

    def _connect(self, max_attempts: int, wait_seconds: float) -> ConnectionPool:
        """Ouvre un pool, avec retry (le graphd met du temps a etre pret).

        ``nebula3`` est importe ICI et non au niveau du module, et ce n'est pas
        un detail de style. Il n'est pas dans le venv du depot — les deps
        lourdes d'extraction vivent dans ``Dockerfile.docling`` — donc un import
        de module rendait ``src.docling_service.nebula`` INIMPORTABLE cote hote,
        et tout ce qu'il porte intestable : c'est ainsi que la mutation
        ``document_vid(identity.key)`` -> ``document_vid(identity.filename)``
        laissait la suite entierement verte (registre 4.28.d). C'est le meme
        geste que ``vectors.get_collection``, sur le cinquieme et dernier module
        dans ce cas. *Ce qu'un test n'importe pas, il ne teste pas.*
        """
        from nebula3.Config import Config
        from nebula3.gclient.net import ConnectionPool

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
        settings = get_settings()
        session = self._get_pool().get_session(settings.nebula_user, settings.nebula_password)
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
        identity: DocumentIdentity,
        facts: DocumentFacts,
    ) -> None:
        """Ecrit un lot d'elements et leurs relations dans le graphe.

        Args:
            elements: Elements produits par ``DocumentAccumulator``.
            identity: Identite du document (chemin, ouvrage, nom).
            facts: Format, pagination, langue et empreinte du document.

        Raises:
            NebulaError: Si une requete est rejetee par le graphd.
        """
        if not elements:
            return

        max_chars = get_settings().graph_text_max_chars
        doc_vid = document_vid(identity.key)

        vertices_by_tag: dict[str, list[str]] = {}
        parent_edges: list[str] = []
        caption_edges: list[str] = []
        last_visual_id: str | None = None

        for element in elements:
            label = str(element["label"])
            tag = tag_for_label(label)
            vertices_by_tag.setdefault(tag, []).append(element_vertex_value(element, max_chars))

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
                [
                    vertex_value(
                        doc_vid,
                        (
                            identity.filename,
                            facts.type_file,
                            facts.total_pages,
                            identity.collection,
                            identity.source_path,
                            facts.language,
                            facts.content_hash,
                        ),
                    )
                ],
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
            identity.key,
            len(vertices_by_tag),
        )

        # La coupe a graph_text_max_chars ne disait rien, et ChromaDB n'est pas
        # coupe : le graphe et les vecteurs divergent en silence sur ces
        # elements-la. Un avertissement plutot qu'un info — c'est une perte de
        # texte, meme bornee et voulue.
        coupes = compter_les_textes_coupes(elements, max_chars)
        if coupes:
            logger.warning(
                "Nebula: %d element(s) sur %d coupes a %d caracteres pour %s. "
                "ChromaDB garde le texte entier : le graphe et les vecteurs "
                "divergent sur ces elements",
                coupes,
                len(elements),
                max_chars,
                identity.key,
            )

    def find_duplicate(self, content_hash: str, doc_vid_exclu: str) -> str:
        """Cherche un document deja ingere ayant exactement le meme fichier.

        Sur une bibliotheque constituee au fil des annees, le meme ouvrage
        revient sous deux noms — une copie de sauvegarde, un telechargement
        refait. Sans ce controle, il occupe deux fois la place et remonte
        deux fois dans les reponses.

        Le test porte sur l'empreinte du fichier : c'est exact, jamais
        approximatif. Deux editions differentes du meme livre ne sont pas
        des doublons et restent toutes les deux.

        Args:
            content_hash: Empreinte SHA-256 du fichier a ingerer.
            doc_vid_exclu: Identifiant du document courant, ignore dans la
                recherche — reingerer le meme chemin n'est pas un doublon.

        Returns:
            Le chemin du document deja present, ou une chaine vide.
        """
        if not content_hash:
            return ""

        with self.session() as session:
            resultat = session.execute(
                "MATCH (d:Document) "
                f"WHERE d.Document.content_hash == {quote(content_hash)} "
                f"AND id(d) != {quote(doc_vid_exclu)} "
                "RETURN d.Document.source_path AS chemin LIMIT 1;"
            )
            if not resultat.is_succeeded() or resultat.is_empty():
                return ""
            valeur = resultat.rows()[0].values[0].get_sVal()
        return valeur.decode() if isinstance(valeur, bytes) else str(valeur or "")

    def delete_document(self, document_key: str) -> None:
        """Supprime les vertices d'un document (re-ingestion propre)."""
        with self.session() as session:
            execute(session, f"DELETE VERTEX {quote(document_vid(document_key))} WITH EDGE;")

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
                "CREATE TAG IF NOT EXISTS Document(filename string, type_file string, "
                "total_pages int, collection string, source_path string, "
                "language string, content_hash string);",
            )
            # Deploiements anterieurs : le tag existe sans total_pages, et
            # CREATE TAG IF NOT EXISTS ne l'ajoute pas. Tolere si deja present.
            for ajout in (
                "total_pages int",
                "collection string",
                "source_path string",
                "language string",
                "content_hash string",
            ):
                execute(session, f"ALTER TAG Document ADD ({ajout});", required=False)

            # Les CREATE puis les ALTER : le second est ce qui fait migrer un
            # space DEJA PEUPLE, ou CREATE TAG IF NOT EXISTS ne fait rien. Un
            # ALTER dont la colonne existe deja echoue en « Existed! » : c'est
            # attendu, d'ou required=False, exactement comme pour Document.
            for statement in tag_schema_statements(sorted(set(TAG_MAP.values()))):
                execute(session, statement, required=statement.startswith("CREATE"))

            execute(session, "CREATE EDGE IF NOT EXISTS PARENT_OF(sequence int);")
            execute(session, "CREATE EDGE IF NOT EXISTS LINKED_TO(relation string);")
            execute(session, "CREATE TAG INDEX IF NOT EXISTS doc_index ON Document(filename(20));")

        # NebulaGraph propage les changements de schema de maniere asynchrone :
        # ecrire immediatement apres un CREATE/ALTER expose a un rejet pour tag
        # inconnu. On laisse passer quelques heartbeats.
        time.sleep(10)
        self._verifier_les_tags(sorted(set(TAG_MAP.values())))
        logger.info("Schema semantique NebulaGraph pret.")

    def _verifier_les_tags(self, tags: Sequence[str]) -> None:
        """Constate que la migration a eu lieu, au lieu de la supposer.

        Un ALTER est tolere en echec — « la colonne existe deja » est son cas
        nominal — donc une migration REELLEMENT refusee ne dit rien. `mesure` le
        31 aout 2026 : onze tags sur douze avaient migre, le douzieme avait ete
        refuse avec « Schema exisited before! », et cette methode rendait la
        main au vert. Le defaut ne se serait vu qu'a la premiere ecriture, sur
        un rejet du graphd pour colonne inconnue — un document a moitie ecrit.

        Raises:
            NebulaError: Si un tag ne porte pas toutes les colonnes du schema.
                L'appelant journalise et rend ``False`` : le service refuse
                alors de se declarer pret, ce qui se voit.
        """
        with self.session() as session:
            for tag in tags:
                result = session.execute(f"DESCRIBE TAG {tag};")
                if not result.is_succeeded():
                    raise NebulaError(f"DESCRIBE TAG {tag} rejete : {result.error_msg()}")
                lues = [
                    result.row_values(ligne)[0].as_string() for ligne in range(result.row_size())
                ]
                manquantes = missing_vertex_columns(lues)
                if manquantes:
                    raise NebulaError(
                        f"le tag {tag} ne porte pas {list(manquantes)} apres migration "
                        f"(colonnes lues : {lues}). Nebula n'autorise pas une colonne "
                        "supprimee a revenir : ce space doit etre recree."
                    )


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
