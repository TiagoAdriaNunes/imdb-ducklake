.PHONY: help sync format format-check lint typecheck dbt-parse test smoke coverage docs package ci \
	download ingest transform build validate promote checkpoint

help:
	@echo "Setup"
	@echo "  make sync            uv sync --locked"
	@echo ""
	@echo "Quality gates (matches .github/workflows/ci.yml)"
	@echo "  make format-check    ruff format --check ."
	@echo "  make format          ruff format . (rewrites files)"
	@echo "  make lint            ruff check ."
	@echo "  make typecheck       mypy src"
	@echo "  make dbt-parse       dbt parse --project-dir dbt --profiles-dir dbt"
	@echo "  make coverage        pytest -m 'not smoke' --cov=imdb_ducklake --cov-fail-under=85"
	@echo "  make package         uv build"
	@echo "  make ci              format-check + lint + typecheck + dbt-parse + coverage + package"
	@echo ""
	@echo "Tests"
	@echo "  make test            uv run pytest (all markers, no coverage gate)"
	@echo "  make smoke           uv run pytest -m smoke (needs the full local IMDb download)"
	@echo ""
	@echo "Docs"
	@echo "  make docs            dbt docs generate"
	@echo ""
	@echo "Lakehouse pipeline (imdb-lakehouse CLI)"
	@echo "  make download        download and verify all seven IMDb archives"
	@echo "  make ingest          load retained archives into an isolated raw build"
	@echo "  make transform       run dbt build against the staged build"
	@echo "  make build           full pipeline: acquire, ingest, dbt build, validate, promote"
	@echo "  make validate        validate the current or sole staged build"
	@echo "  make promote         promote a staged build (add ARGS=--build-id=... as needed)"
	@echo "  make checkpoint      compact and expire snapshots on the current build"

sync:
	uv sync --locked

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

dbt-parse:
	uv run dbt parse --project-dir dbt --profiles-dir dbt --no-partial-parse

docs:
	uv run dbt docs generate --project-dir dbt --profiles-dir dbt

test:
	uv run pytest

smoke:
	uv run pytest -m smoke

coverage:
	uv run pytest -m "not smoke" --cov=imdb_ducklake --cov-fail-under=85

package:
	uv build

ci: format-check lint typecheck dbt-parse coverage package

download:
	uv run imdb-lakehouse download $(ARGS)

ingest:
	uv run imdb-lakehouse ingest $(ARGS)

transform:
	uv run imdb-lakehouse transform $(ARGS)

build:
	uv run imdb-lakehouse build $(ARGS)

validate:
	uv run imdb-lakehouse validate $(ARGS)

promote:
	uv run imdb-lakehouse promote $(ARGS)

checkpoint:
	uv run imdb-lakehouse checkpoint $(ARGS)
