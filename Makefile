.PHONY: install lint format typecheck test test-cov audit all

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

format:
	uv run ruff format src/

typecheck:
	uv run mypy src/

test:
	uv run pytest tests/

test-cov:
	uv run pytest tests/ --cov=src --cov-report=term-missing

audit:
	uv run pip-audit -r requirements.txt -r src/docling_service/requirements.txt

all: format lint typecheck test
