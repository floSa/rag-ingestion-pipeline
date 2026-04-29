.PHONY: lint format typecheck test audit all

lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	mypy src/

test:
	pytest tests/

test-cov:
	pytest tests/ --cov=src --cov-report=term-missing

audit:
	pip-audit -r requirements.txt -r src/docling_service/requirements.txt

all: format lint typecheck test
