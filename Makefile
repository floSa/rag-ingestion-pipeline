.PHONY: install lint format format-check typecheck test test-cov audit all

# `uv sync` installe les dependances de production ET le groupe `dev` declare
# dans pyproject.toml. C'est la seule etape d'installation de la porte qualite :
# il n'y a pas de liste annexe a se rappeler.
install:
	uv sync

# Chaque outil passe par `uv run` : la porte tourne immediatement apres
# `make install`, sans activation d'environnement, et toujours aux versions
# epinglees par uv.lock. Un `ruff` ou un `mypy` trouve au hasard du PATH rendrait
# un verdict qui n'est celui d'aucune version declaree.
lint:
	uv run ruff check src/

# `format` ECRIT dans le depot. C'est le geste volontaire du developpeur qui
# decide de reformater, et il n'entre dans aucune porte.
format:
	uv run ruff format src/

# `format-check` ne fait que CONSTATER, et c'est lui qui entre dans `all`. Une
# porte qualite qui reecrit l'arbre qu'elle controle ne controle rien : elle
# rend vrai ce qu'elle allait verifier. Avant cette separation, `make all`
# reecrivait trois fichiers a chaque execution, et chaque developpeur devait se
# souvenir de les revoquer avant chaque commit — celui de la reparation du lot 0
# l'a fait six fois parce qu'il le savait. Un garde-fou qui repose sur la
# memoire du suivant n'est pas un garde-fou.
format-check:
	uv run ruff format --check src/

typecheck:
	uv run mypy src/

test:
	uv run pytest tests/

test-cov:
	uv run pytest tests/ --cov=src --cov-report=term-missing

audit:
	uv run pip-audit -r requirements.txt -r src/docling_service/requirements.txt

# `format-check` passe EN DERNIER, et ce n'est pas cosmetique. Il est rouge sur
# `main` — trois fichiers plies a la main, que le lot de la hierarchie reecrira
# (registre 5.4) — et l'ordre ne change pas le verdict de la porte, seulement ce
# qu'un humain apprend avant qu'elle ne s'arrete. Place en premier, il priverait
# tous les lots a venir du signal de `lint`, `typecheck` et `test` sur `main`.
# Place en dernier, la porte dit tout ce qu'elle sait avant de s'arreter.
all: lint typecheck test format-check
