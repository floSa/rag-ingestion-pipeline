"""L'appel ``POST /reindex`` sur rag-agent-chat : ce qu'il fait, et rien d'autre.

Le moment ou il part est decide ailleurs, dans ``reindex_job.py`` (une fois par
rafale d'ingestion, pas une fois par document).

C'est l'une des exigences dures du contrat d'interface, et la seule que ce
module porte. Le contrat en impose d'autres au pipeline, par exemple le modele
d'embedding, verifie au demarrage du service Docling (``main.py``, via
``embedding.verify_model_name``). La liste qui fait foi est tenue dans le
registre du chantier et n'est pas recopiee ici.

Pourquoi cet appel. L'agent tient son index lexical BM25 en memoire, construit
au premier appel. La recherche dense, elle, interroge ChromaDB a chaque requete
et suit donc le corpus. Sans cet appel, un document ingere apres le demarrage de
l'agent est trouvable en dense mais invisible en lexical jusqu'au prochain
redemarrage, et aucune sonde ne le voit.

**Le filet de l'agent ne suffit pas.** L'agent compare le nombre de chunks de sa
collection au nombre qu'il a indexe, et se reconstruit s'ils different. Une
re-ingestion qui retire autant de chunks qu'elle en ajoute garde le meme compte,
et le filet ne voit rien. C'est le cas de toute re-ingestion d'un corpus deja
present.

Trois choix deliberes :

1. **Cette fonction ne leve jamais.** Elle rend ce qu'il est advenu de l'appel,
   echec compris, et laisse l'appelant decider. ``reindex_job.py``, qui fait
   l'appel dans son propre run, choisit de lever en cas d'echec : une reprise
   n'y coute qu'un appel HTTP. Une ingestion reussie reste reussie.
2. **Un echec ne passe pas inapercu.** L'appelant dispose de ``ok``, de
   ``detail`` et d'un rendu court pour les metadonnees. ``reindex_job.py`` fait
   alors echouer son run et le retente jusqu'a ce qu'il reussisse.
3. **L'absence d'URL est un choix explicite, annonce au chargement.** L'URL a
   une valeur par defaut valable sur le reseau ``rag_network`` ; la vider
   desactive l'appel, et ``definitions.py`` l'annonce au demarrage. ``called``
   distingue ce choix d'une panne : un appel non tente n'est pas un appel echoue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

# Route du contrat, cote agent.
REINDEX_PATH = "/reindex"

# En-tete attendu par l'agent quand il est protege par une cle. Sans cle
# configuree de son cote, la dependance ne fait rien et l'en-tete est ignore.
#
# `pragma: allowlist secret` : c'est un nom d'en-tete HTTP, pas une valeur.
# `detect-secrets` signale un « Secret Keyword » des que le nom de la constante
# contient `API_KEY`. La cle elle-meme arrive par `settings`.
API_KEY_HEADER = "X-API-Key"  # pragma: allowlist secret


@dataclass(frozen=True)
class ReindexOutcome:
    """Ce qu'il est advenu de l'appel, sans jamais lever.

    Attributes:
        called: L'appel a-t-il ete tente. Faux si l'appel est desactive.
        ok: L'agent a-t-il reconstruit son index.
        chunks_indexed: Taille de l'index apres reconstruction, telle que
            l'agent la rapporte. ``None`` si l'appel n'a pas abouti. C'est le
            nombre a confronter aux chunks que l'ingestion vient d'ecrire.
        detail: Message lisible, destine au journal et aux metadonnees.
    """

    called: bool
    ok: bool
    chunks_indexed: int | None
    detail: str

    @property
    def metadata_value(self) -> str:
        """Rendu court pour les metadonnees d'asset Dagster.

        La branche « ECHEC » n'est pas atteinte en production, et elle reste
        volontairement (registre 5.7). `reindex_job.lexical_index` leve quand
        l'appel a ete tente sans aboutir, donc n'appelle jamais
        `add_output_metadata` avec `called=True, ok=False`.

        L'etat existe pourtant : `request_reindex` le construit sur exception,
        et cet objet doit dire ce qui s'est passe quel que soit son
        consommateur. Sans cette branche, un echec s'afficherait comme un
        succes :

            ReindexOutcome(called=True, ok=False, chunks_indexed=None,
                           detail="agent injoignable")
              avec la branche  ->  "ECHEC — agent injoignable"
              sans la branche  ->  "ok — None chunks indexes"
        """
        if not self.called:
            return f"non appele — {self.detail}"
        if self.ok:
            return f"ok — {self.chunks_indexed} chunks indexes"
        return f"ECHEC — {self.detail}"


def request_reindex(
    base_url: str,
    api_key: str = "",
    timeout: float = 300.0,
    post: Callable[..., Any] | None = None,
) -> ReindexOutcome:
    """Demande a l'agent de reconstruire son index lexical.

    Ne leve jamais : l'appelant decide quoi faire d'un echec. Tout echec
    ressort dans l'objet rendu.

    Args:
        base_url: Racine de l'API de l'agent. Vide, l'appel est desactive.
        api_key: Cle d'API de l'agent, si le sien en exige une.
        timeout: Plafond de l'appel. La reconstruction parcourt tout le corpus
            et l'agent la fait de maniere synchrone : elle est lente par nature.
        post: Fonction d'envoi, injectee par les tests. Laissee vide, elle est
            resolue a l'appel sur ``requests.post``, et non figee en valeur par
            defaut a l'import : un test peut ainsi remplacer ``requests.post``
            sans remplacer ``request_reindex`` elle-meme.

    Returns:
        Le resultat de l'appel.
    """
    envoyer = post if post is not None else requests.post
    url = base_url.strip().rstrip("/")
    if not url:
        return ReindexOutcome(
            called=False,
            ok=False,
            chunks_indexed=None,
            detail=(
                "AGENT_SERVICE_URL est vide : l'index lexical de rag-agent-chat ne sera "
                "pas reconstruit et les documents ingeres resteront invisibles en "
                "recherche lexicale jusqu'a son redemarrage."
            ),
        )

    headers = {API_KEY_HEADER: api_key} if api_key else {}
    try:
        reponse = envoyer(f"{url}{REINDEX_PATH}", headers=headers, timeout=timeout)
        reponse.raise_for_status()
        charge = reponse.json()
    except Exception as exc:
        # Volontairement large : requests leve une famille entiere d'exceptions
        # reseau, et une reponse illisible en leve d'autres encore. Aucune ne
        # doit remonter jusqu'a l'asset.
        return ReindexOutcome(
            called=True,
            ok=False,
            chunks_indexed=None,
            detail=f"{type(exc).__name__} : {exc}",
        )

    return ReindexOutcome(
        called=True,
        ok=True,
        chunks_indexed=_lire_compte(charge),
        detail="index lexical reconstruit",
    )


def _lire_compte(charge: Any) -> int | None:
    """Extrait ``chunks_indexed`` de la reponse, ``None`` si elle n'en porte pas.

    Un agent d'une version anterieure peut rendre autre chose : l'appel a
    quand meme eu lieu, seul le compte manque.
    """
    if isinstance(charge, dict):
        valeur = charge.get("chunks_indexed")
        if isinstance(valeur, int):
            return valeur
    return None
