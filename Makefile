.PHONY: setup lint test

setup:  ## Install Python and web dependencies and git hooks
	uv sync
	uv run pre-commit install
	@if [ -d web ]; then cd web && npm ci; fi

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing:skip-covered
