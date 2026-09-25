# Politique de securite

## Gestion des secrets

- Tous les secrets sont dans `.env` (ignore par git).
- `.env.example` documente les cles attendues sans valeurs sensibles.
- Les mots de passe sont generes avec `openssl rand -base64 24`.
- `detect-secrets` tourne en hook `pre-commit`. Il est installe, avec les autres
  hooks du depot, par `make install` (qui lance
  `scripts/installer-les-garde-fous.sh`).
- **Ce hook ne protege pas le `.env`** : un hook `pre-commit` ne voit que les
  fichiers **indexes**, et `.env`, ignore par git, n'est jamais indexe. Il
  empeche qu'un secret parte dans un fichier **versionne**. Detail dans
  [guide_du_depot.md](guide_du_depot.md), section « Ce que `detect-secrets`
  protège, et ce qu'il ne protège pas ».
- Le corpus (`Datas/`) est exclu de tous les hooks : un secret depose sous
  `Datas/` ne serait pas detecte.
- Un faux positif se declare **a l'endroit concerne**, avec sa justification,
  par un commentaire `# pragma: allowlist secret` sur la ligne qui porte la
  valeur. Il n'y a pas de fichier `.secrets.baseline` (registre §5.5).

## Stockage objet : deux jeux d'identifiants

La passerelle S3 declare deux identites (`SEAWEEDFS_RW_*` et `SEAWEEDFS_RO_*`,
dans `.env`) :

- le jeu **RW** sert au pipeline (`docling-service`, `dagster-webserver`,
  `dagster-daemon`), qui ecrit ; `docker-compose.yml` en derive `S3_ACCESS_KEY`
  et `S3_SECRET_KEY` ;
- le jeu **RO** (lecture et listage seulement) sert a `rag-agent-chat`.

Le fichier d'identites du serveur est ecrit au demarrage dans un tmpfs du
conteneur, a partir de ces variables : aucune cle n'est ecrite dans le depot ni
passee en argument de commande. Detail :
[services/stockage_objet.md](services/stockage_objet.md).

## Audit des dependances

```bash
make audit
```

La cible lance `uv run pip-audit -r requirements.txt -r src/docling_service/requirements.txt`.
Les versions sont epinglees avec `==` dans les deux fichiers. Mettre a jour
regulierement et re-auditer.

## Isolation reseau

- Les services internes (ChromaDB, le stockage d'objets, NebulaGraph,
  PostgreSQL, Docling) ne sont pas exposes sur l'hote (`expose:` au lieu de
  `ports:`).
- Seuls Dagster (port hote 3002) et Nebula Studio (7001) sont accessibles
  depuis l'hote.
- Pour joindre un service interne en debug, passer par un conteneur du reseau
  (`docker compose exec …`), ou ajouter un `docker-compose.override.yml` local,
  non versionne, qui publie le port voulu.

## Containers

- Images de base epinglees (`python:3.12-slim`).
- Utilisateur non-root dans les Dockerfiles du depot (`dagster`, `docling`).
- `--no-install-recommends` pour reduire la surface d'attaque.

## Rotation des secrets

1. Generer de nouveaux secrets : `openssl rand -base64 24`.
2. Mettre a jour `.env`.
3. Redemarrer les services : `docker compose down && docker compose up -d`.
4. Si le jeu `SEAWEEDFS_RO_*` a change, reporter les nouvelles valeurs dans la
   configuration de `rag-agent-chat`.

## Couche LLM/agent

La couche agent vit dans le projet `rag-agent-chat`. Mesures a y prevoir :

- **Presidio** ou **NeMo Guardrails** pour la detection et l'anonymisation de
  PII dans les prompts et les reponses ;
- **rate limiting** sur les endpoints exposes ;
- **journal d'audit** des requetes LLM (prompts, tokens, latence) ;
- aucune cle API LLM en dur : passer par des reglages pydantic.
