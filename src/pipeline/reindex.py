"""L'appel ``POST /reindex`` sur rag-agent-chat : ce qu'il fait, et rien d'autre.

QUAND il part est une autre question, et elle a son module : ``reindex_job.py``.
Les avoir confondus est ce qui a fait poster une fois par document une route
que le contrat veut en fin d'ingestion.

C'est **l'une des exigences dures** du contrat d'interface, et la seule que ce
module-ci porte. Ce n'est pas la seule que le contrat impose au pipeline : le
modele d'embedding en est une autre, verifiee au demarrage du service par
``main.py:93`` via ``embedding.verify_model_name``, et le contrat en enonce
d'autres encore, portees ailleurs dans la chaine. La liste qui fait foi est
tenue hors du code, dans le registre du chantier : elle n'est pas recopiee ici,
parce qu'une enumeration recopiee se ferme et que personne ne la rouvre.

Ce qui etait vrai et qui justifie ce module : cette exigence-la etait absente,
aucun appel, aucune configuration, nulle part.

Ce qu'elle repare. L'agent tient son index lexical BM25 **en memoire**,
construit au premier appel. La recherche dense, elle, part a ChromaDB a chaque
requete et suit donc le corpus sans effort. Un document ingere apres le
demarrage de l'agent etait donc trouvable en dense et invisible en lexical
jusqu'au prochain redemarrage : la recherche devenait silencieusement
asymetrique, ce qui ne se voit dans aucune sonde.

**Le filet de l'agent ne nous couvre pas.** L'agent compare le nombre de chunks
de sa collection au nombre qu'il a indexe, et se reconstruit s'ils different.
Mais une re-ingestion qui retire autant de chunks qu'elle en ajoute affiche le
meme compte : le filet ne voit rien, et c'est exactement ce que produit une
re-ingestion d'un corpus deja present. D'ou un contrat, et non une option.

Trois choix, tous les trois deliberes :

1. **Cette fonction-ci ne leve jamais.** Elle rend ce qu'il est advenu de
   l'appel, y compris l'echec, et laisse son appelant decider ce qu'il en fait.
   La separation compte : quand l'appel vivait dans le run d'une partition,
   rougir aurait declenche des reprises qui reconvertissent des centaines de
   pages. Il vit desormais dans son propre run, ou une reprise coute UN appel
   HTTP — et c'est ``reindex_job.py`` qui tranche, en connaissance de sa
   hauteur. Dans les deux cas, une ingestion reussie reste verte.
2. **Un echec ne passe pas inapercu.** L'appelant a tout ce qu'il faut pour le
   dire : ``ok``, ``detail``, et un rendu court pour les metadonnees. Ce que
   ``reindex_job.py`` en fait — faire rougir son run et le retenter jusqu'a ce
   qu'il passe — est decrit la-bas.
3. **L'absence d'URL est un choix explicite, annonce au chargement**, pas une
   surprise en fin de course. L'URL a une valeur par defaut qui marche sur le
   reseau ``rag_network`` ; la vider revient a desactiver l'appel, et
   ``definitions.py`` le dit alors au demarrage. ``called`` distingue ce choix
   d'une panne : un appel non tente n'est pas un appel echoue.
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
# `pragma: allowlist secret` : c'est un NOM d'en-tete HTTP, pas une valeur.
# `detect-secrets` leve un « Secret Keyword » parce que le nom de la constante
# contient `API_KEY`, sans regarder ce qu'elle vaut. La cle elle-meme n'est
# jamais ecrite ici : elle arrive par `settings`.
API_KEY_HEADER = "X-API-Key"  # pragma: allowlist secret


@dataclass(frozen=True)
class ReindexOutcome:
    """Ce qu'il est advenu de l'appel, sans jamais lever.

    Attributes:
        called: L'appel a-t-il ete tente. Faux si l'appel est desactive.
        ok: L'agent a-t-il reconstruit son index.
        chunks_indexed: Taille de l'index APRES reconstruction, telle que
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
        """Rendu court pour les metadonnees d'asset Dagster."""
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

    Ne leve jamais : une ingestion reussie ne doit pas rougir parce que l'agent
    est arrete. Tout echec ressort dans l'objet rendu.

    Args:
        base_url: Racine de l'API de l'agent. Vide, l'appel est desactive.
        api_key: Cle d'API de l'agent, si le sien en exige une.
        timeout: Plafond de l'appel. La reconstruction parcourt tout le corpus
            et l'agent la fait de maniere synchrone : elle est lente par nature.
        post: Fonction d'envoi. Injectee par les tests. Laissee vide, elle est
            resolue A L'APPEL sur ``requests.post`` — et non figee en valeur par
            defaut a l'import. Une valeur par defaut capture l'objet fonction :
            aucun test ne peut alors intercepter l'appel sans se substituer a
            ``request_reindex`` elle-meme, c'est-a-dire sans bouchonner
            au-dessus de ce qu'il pretend verifier.

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
