.EXPORT_ALL_VARIABLES:

.PHONY: help lint ruff ruff-fix ruff-unsafe-fix black isort format \
        test check sync lock migrate makemigrations runserver \
        docker-build docker-up docker-down docker-logs docker-migrate docker-shell confirm

help:
	@echo "Common targets:"
	@echo "  make lint          Check style (ruff + black + isort, no writes)"
	@echo "  make format        Autoformat (black + isort + ruff --fix)"
	@echo "  make test          Run the Django test suite"
	@echo "  make check         lint + test"
	@echo "  make migrate       Apply migrations"
	@echo "  make runserver     Run the dev server"
	@echo "  make docker-build  Build the production image"
	@echo "  make docker-up     Start the prod stack (web + Caddy) detached"
	@echo "  make docker-down   Stop the prod stack"
	@echo "  make docker-logs   Tail prod logs"
	@echo "  make docker-migrate Run migrations inside the web container"

# --- Lint: check only, never rewrite files ---
lint: ruff black isort

ruff:
	uv run --all-extras ruff check .

black:
	uv run --all-extras black --check .

isort:
	uv run --all-extras isort --check-only .

# --- Format: rewrite files in place ---
format:
	uv run --all-extras isort .
	uv run --all-extras black .
	uv run --all-extras ruff check --fix .

ruff-fix:
	uv run --all-extras ruff check --fix .

ruff-unsafe-fix:
	uv run --all-extras ruff check --unsafe-fixes .

# --- Test ---
test:
	uv run python manage.py test

check: lint test

# --- Dependencies ---
sync:
	uv sync --all-extras --locked

lock:
	uv lock

# --- Django ---
migrate:
	uv run python manage.py migrate

makemigrations:
	uv run python manage.py makemigrations

runserver:
	uv run python manage.py runserver

# --- Docker (production) ---
COMPOSE := docker compose -f docker-compose.prod.yml

docker-build:
	$(COMPOSE) build

docker-up:
	$(COMPOSE) up -d

docker-down:
	$(COMPOSE) down

docker-logs:
	$(COMPOSE) logs -f

docker-migrate:
	$(COMPOSE) exec web python manage.py migrate

docker-shell:
	$(COMPOSE) exec web bash

confirm:
	@echo -n "Are you sure? [y/N] " && read ans && [ $${ans:-N} = y ]
