.PHONY: setup lint test up down migrate seed

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

up:  ## Start Postgres (more services join as they land)
	docker compose up -d postgres

down:
	docker compose down

migrate: up
	uv run alembic upgrade head

seed: migrate  ## Reset the LOCAL dev database to the deterministic synthetic seed
	uv run griddesk db seed
