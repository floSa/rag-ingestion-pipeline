# Politique de securite

## Gestion des secrets

- Tous les secrets sont dans `.env` (ignore par git)
- `.env.example` documente les cles attendues sans valeurs sensibles
- Les mots de passe sont generes avec `openssl rand -base64 24`
- `detect-secrets` tourne en hook `pre-commit`, **et il faut l'installer** :
  `make install && uv run pre-commit install`. Ce geste n'etait fait nulle part
  avant le lot 0b — la ligne qui precedait celle-ci annoncait un garde-fou
  « integre au pre-commit » que rien n'executait, avec une baseline
  `.secrets.baseline` desormais supprimee (registre §5.5)
- **Ce hook ne protege PAS le `.env`**, et il ne faut pas le croire : un hook
  `pre-commit` ne voit que les fichiers **indexes**, et `.env` est ignore par
  git, donc jamais indexe. Ce qu'il protege, c'est le depot **versionne** —
  empecher qu'un secret y parte un jour. Detail au `README.md`, section
  « Les garde-fous du depot »
- Un faux positif se declare **au site**, avec sa justification, par un
  commentaire `# pragma: allowlist secret` — jamais dans une baseline, qui est
  un etat que rien ne reconcilie avec le code

## Audit des dependances

```bash
# Depuis le venv
pip-audit -r requirements.txt
```

Les versions sont pinnees dans `requirements.txt` avec `==`.
Mettre a jour regulierement et re-auditer.

## Isolation reseau

- Les services internes (ChromaDB, MinIO, NebulaGraph, PostgreSQL, Docling) ne sont
  pas exposes sur l'hote (`expose:` au lieu de `ports:`)
- Seuls Dagster (3000) et Nebula Studio (7001) sont accessibles depuis l'hote
- `docker-compose.override.yml` (non commite) permet d'exposer les ports en debug

## Containers

- Images de base pinnees (`python:3.12-slim`)
- Utilisateur non-root dans les Dockerfiles custom (`dagster`, `docling`)
- `--no-install-recommends` pour minimiser la surface d'attaque

## Rotation des secrets

1. Generer de nouveaux secrets : `openssl rand -base64 24`
2. Mettre a jour `.env`
3. Redemarrer les services : `docker compose down && docker compose up -d`

## Preparation future (couche LLM/agent)

Quand la couche RAG agent sera ajoutee :

- **Presidio** ou **NeMo Guardrails** pour la detection/anonymisation de PII
  dans les prompts et les reponses
- **Rate limiting** sur les endpoints exposes
- **Audit logging** des requetes LLM (prompts, tokens, latence)
- Ne jamais stocker de cles API LLM en dur — utiliser les settings pydantic
