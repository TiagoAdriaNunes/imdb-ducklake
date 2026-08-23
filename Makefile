# GNU Make on Windows defaults recipes to cmd.exe unless it can auto-find sh.exe/bash.exe on
# PATH, which depends on which terminal launched `make` (works from Git Bash, not from a plain
# PowerShell/cmd session whose PATH only has Git\cmd, not Git\bin). publish-docs' POSIX `if [ ]`
# syntax needs a real shell, so pin one explicitly rather than depend on the invoking terminal.
ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/bin/bash.exe
endif

GH_PAGES_DIR := ../imdb-ducklake-gh-pages
REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
DBT_ENV := \
	IMDB_DUCKLAKE_DBT_CONTROLLER="$(REPO_ROOT)/data/.dbt/controller.duckdb" \
	IMDB_DUCKLAKE_CATALOG="$(REPO_ROOT)/data/ducklake/current/catalog.duckdb" \
	IMDB_DUCKLAKE_STORAGE="$(REPO_ROOT)/data/ducklake/current/storage"
# Same defaults as compose.yaml/.env.example, so this points at the PostgreSQL-backed catalog
# the docker-* pipeline targets promote into (published to localhost by `docker compose up postgres`).
DOCKER_CATALOG_ENV := IMDB_DUCKLAKE_CATALOG_URL="postgresql://$${POSTGRES_USER:-imdb}:$${POSTGRES_PASSWORD:-imdb-local-dev}@localhost:$${POSTGRES_PORT:-5432}/$${POSTGRES_DB:-ducklake_catalog}"
# dbt-duckdb's `attach.path` (dbt/profiles.yml) needs the raw duckdb postgres DSN, not a
# postgresql:// URL, and the docker-* pipeline targets write to a flat data/ducklake/storage (no
# "current" promotion dir) under the imdb_lake metadata schema - see CatalogTarget in
# src/imdb_ducklake/lakehouse/catalog.py, which src/imdb_ducklake/transformation/dbt_runner.py
# uses to derive the same values for the imdb-lakehouse CLI's own dbt invocations.
DOCKER_DBT_ENV := \
	IMDB_DUCKLAKE_DBT_CONTROLLER="$(REPO_ROOT)/data/.dbt/controller.duckdb" \
	IMDB_DUCKLAKE_CATALOG="postgres:dbname='$${POSTGRES_DB:-ducklake_catalog}' host='localhost' port=$${POSTGRES_PORT:-5432} user='$${POSTGRES_USER:-imdb}' password='$${POSTGRES_PASSWORD:-imdb-local-dev}'" \
	IMDB_DUCKLAKE_STORAGE="$(REPO_ROOT)/data/ducklake/storage" \
	IMDB_DUCKLAKE_METADATA_SCHEMA=imdb_lake

# LOG_FORMAT=json (add to any pipeline target below) sets IMDB_LAKEHOUSE_LOG_FORMAT for that run,
# e.g. `make build LOG_FORMAT=json`. The CLI's own --log-format flag is a global option that must
# precede the subcommand, so it cannot be passed through ARGS (which is appended after it).
ifdef LOG_FORMAT
PIPELINE_ENV := IMDB_LAKEHOUSE_LOG_FORMAT=$(LOG_FORMAT)
DOCKER_PIPELINE_ARGS := -e IMDB_LAKEHOUSE_LOG_FORMAT=$(LOG_FORMAT)
endif

.PHONY: help sync format format-check lint sql-lint typecheck dbt-parse test smoke coverage docs \
	publish-docs package ci download ingest transform build validate promote checkpoint shell \
	shell-ui clean-dlt-pipelines upload-bucket docker-image docker-up docker-build docker-down \
	docker-status docker-download docker-ingest docker-transform docker-validate docker-checkpoint \
	docker-app docker-app-logs docker-image-ready docker-docs docker-publish-docs

help:
	@echo "Setup"
	@echo "  make sync            uv sync --locked"
	@echo "  make docker-image    explicitly rebuild the Linux pipeline/app image"
	@echo "  make docker-up       ensure PostgreSQL is running; never runs the pipeline"
	@echo "  make docker-build    ensure prerequisites + run the full pipeline once"
	@echo "  make docker-download download archives through the Linux container"
	@echo "  make docker-ingest   ingest raw data into the PostgreSQL-backed DuckLake catalog"
	@echo "  make docker-transform run dbt against the PostgreSQL-backed DuckLake catalog"
	@echo "  make docker-validate validate the PostgreSQL-backed DuckLake catalog"
	@echo "  make docker-checkpoint checkpoint the PostgreSQL-backed DuckLake catalog"
	@echo "  make docker-app      ensure PostgreSQL + Shiny are running; no pipeline run"
	@echo "  make docker-app-logs follow Shiny app logs"
	@echo "  make docker-status   show PostgreSQL and one-shot pipeline containers"
	@echo "  make docker-down     stop services without deleting containers or data"
	@echo ""
	@echo "Quality gates (matches .github/workflows/ci.yml)"
	@echo "  make format-check    ruff format --check ."
	@echo "  make format          ruff format . (rewrites files)"
	@echo "  make lint            ruff check ."
	@echo "  make sql-lint        sqlfluff lint dbt/models dbt/tests"
	@echo "  make typecheck       mypy src"
	@echo "  make dbt-parse       dbt parse --project-dir dbt --profiles-dir dbt"
	@echo "  make coverage        unit tests only; excludes smoke and integration"
	@echo "  make package         uv build"
	@echo "  make ci              format-check + lint + sql-lint + typecheck + dbt-parse + coverage"
	@echo "                       + package"
	@echo ""
	@echo "Tests"
	@echo "  make test            uv run pytest (all markers, no coverage gate)"
	@echo "  make smoke           uv run pytest -m smoke (needs the full local IMDb download)"
	@echo ""
	@echo "Docs"
	@echo "  make docs            dbt docs generate against the local file-based catalog"
	@echo "  make publish-docs    generate (local) + stage docs on gh-pages branch (review, then push)"
	@echo "  make docker-docs     dbt docs generate against the PostgreSQL-backed catalog"
	@echo "  make docker-publish-docs  generate (postgres) + stage docs on gh-pages branch"
	@echo ""
	@echo "Lakehouse pipeline (imdb-lakehouse CLI; add LOG_FORMAT=json to any of these for JSON logs)"
	@echo "  make download        download and verify all seven IMDb archives"
	@echo "  make ingest          load retained archives into an isolated raw build"
	@echo "  make transform       run dbt build against the staged build"
	@echo "  make build           full pipeline: acquire, ingest, dbt build, validate, promote"
	@echo "  make validate        validate the current or sole staged build"
	@echo "  make promote         promote a staged build (add ARGS=--build-id=... as needed)"
	@echo "  make checkpoint      compact and expire snapshots on the current build"
	@echo "  make shell           interactive SQL shell read-only attached to the current build"
	@echo "  make shell-ui        same, and opens DuckDB's local web UI in your browser"
	@echo "  make docker-shell    same, attached to the PostgreSQL-backed catalog (needs docker-up)"
	@echo "  make docker-shell-ui same, and opens DuckDB's local web UI in your browser"
	@echo "  make clean-dlt-pipelines  delete old dlt working dirs, keep the newest KEEP=3"
	@echo "  make upload-bucket   hf sync data/ducklake/current to \$$HF_BUCKET (required, e.g. hf://buckets/<you>/<name>)"

sync:
	uv sync --locked

docker-image:
	docker compose build lakehouse

docker-up:
	docker compose up -d --wait postgres

docker-image-ready: docker-up
	@docker image inspect imdb-ducklake:latest >/dev/null 2>&1 || docker compose build lakehouse

docker-build: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse build $(ARGS)

docker-download: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse download $(ARGS)

docker-ingest: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse ingest $(ARGS)

docker-transform: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse transform $(ARGS)

docker-validate: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse validate $(ARGS)

docker-checkpoint: docker-image-ready
	docker compose run --rm $(DOCKER_PIPELINE_ARGS) lakehouse checkpoint $(ARGS)

docker-app:
	docker compose up -d --wait postgres app

docker-app-logs:
	docker compose logs --follow app

docker-status:
	docker compose ps -a

docker-down:
	docker compose stop

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

sql-lint:
	uv run sqlfluff lint dbt/models dbt/tests

typecheck:
	uv run mypy src

dbt-parse:
	$(DBT_ENV) uv run dbt parse --project-dir dbt --profiles-dir dbt --no-partial-parse

docs:
	$(DBT_ENV) uv run dbt docs generate --project-dir dbt --profiles-dir dbt

docker-docs:
	$(DOCKER_DBT_ENV) uv run dbt docs generate --project-dir dbt --profiles-dir dbt

publish-docs: docs
	@if [ ! -e "$(GH_PAGES_DIR)/.git" ]; then \
		git worktree add "$(GH_PAGES_DIR)" gh-pages; \
	fi
	cp dbt/target/index.html dbt/target/manifest.json dbt/target/catalog.json \
		dbt/target/graph_summary.json dbt/target/semantic_manifest.json "$(GH_PAGES_DIR)/"
	touch "$(GH_PAGES_DIR)/.nojekyll"
	cd "$(GH_PAGES_DIR)" && git add -A && git commit -m "docs: publish dbt docs site"
	@echo "Review the commit in $(GH_PAGES_DIR), then: git -C $(GH_PAGES_DIR) push origin gh-pages"

docker-publish-docs: docker-docs
	@if [ ! -e "$(GH_PAGES_DIR)/.git" ]; then \
		git worktree add "$(GH_PAGES_DIR)" gh-pages; \
	fi
	cp dbt/target/index.html dbt/target/manifest.json dbt/target/catalog.json \
		dbt/target/graph_summary.json dbt/target/semantic_manifest.json "$(GH_PAGES_DIR)/"
	touch "$(GH_PAGES_DIR)/.nojekyll"
	cd "$(GH_PAGES_DIR)" && git add -A && git commit -m "docs: publish dbt docs site"
	@echo "Review the commit in $(GH_PAGES_DIR), then: git -C $(GH_PAGES_DIR) push origin gh-pages"

test:
	uv run pytest

smoke:
	uv run pytest -m smoke

coverage:
	uv run pytest -m "not smoke and not integration" --cov=imdb_ducklake --cov-fail-under=85

package:
	uv build

ci: format-check lint sql-lint typecheck dbt-parse coverage package

shell:
	$(DOCKER_CATALOG_ENV) uv run python scripts/duckdb_shell.py

shell-ui:
	$(DOCKER_CATALOG_ENV) uv run python scripts/duckdb_shell.py --ui

download:
	$(PIPELINE_ENV) uv run imdb-lakehouse download $(ARGS)

ingest:
	$(PIPELINE_ENV) uv run imdb-lakehouse ingest $(ARGS)

transform:
	$(PIPELINE_ENV) uv run imdb-lakehouse transform $(ARGS)

build:
	$(PIPELINE_ENV) uv run imdb-lakehouse build $(ARGS)

validate:
	$(PIPELINE_ENV) uv run imdb-lakehouse validate $(ARGS)

promote:
	$(PIPELINE_ENV) uv run imdb-lakehouse promote $(ARGS)

checkpoint:
	$(PIPELINE_ENV) uv run imdb-lakehouse checkpoint $(ARGS)

KEEP ?= 3
clean-dlt-pipelines:
	@cd data/.dlt/pipelines 2>/dev/null || exit 0; \
	victims=$$(ls -t | tail -n +$$(($(KEEP) + 1))); \
	if [ -z "$$victims" ]; then echo "nothing to clean (KEEP=$(KEEP))"; exit 0; fi; \
	echo "$$victims"; \
	if [ "$(DRY)" = "1" ]; then echo "(dry run, nothing deleted; rerun without DRY=1)"; exit 0; fi; \
	echo "$$victims" | xargs -r rm -rf --

upload-bucket:
	@test -n "$(HF_BUCKET)" || { echo "set HF_BUCKET, e.g. HF_BUCKET=hf://buckets/<you>/<name> make upload-bucket"; exit 1; }
	hf sync ./data/ducklake/current $(HF_BUCKET) $(ARGS)
