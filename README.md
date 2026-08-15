# IMDb DuckLake

A reproducible local analytics lakehouse built from the official IMDb non-commercial datasets.

The project uses:

- **dlt** for lossless raw ingestion and load metadata
- **DuckDB and DuckLake** for query execution, cataloging, snapshots, and Parquet storage
- **dbt** for typed staging models, tests, and analytics-ready marts
- **Shiny for Python** in a later phase for interactive exploration

> IMDb permits these datasets for personal and non-commercial use. This repository contains code
> and miniature synthetic fixtures only; it does not redistribute IMDb data.

## Modules

- [`cli.py`](src/imdb_ducklake/cli.py) composes the command-line interface and maps expected errors
  to user-facing output.
- [`config.py`](src/imdb_ducklake/config.py) resolves immutable settings and repository-relative
  paths.
- [`datasets.py`](src/imdb_ducklake/datasets.py) is the authoritative registry for the seven IMDb
  sources, raw tables, and expected headers.
- [`acquisition/`](src/imdb_ducklake/acquisition/) downloads, resumes, verifies, and records source
  archives without depending on dlt or dbt.
- [`lakehouse/`](src/imdb_ducklake/lakehouse/) owns isolated build paths, locking, free-space
  validation, failure cleanup, and safe promotion.
- [`exceptions.py`](src/imdb_ducklake/exceptions.py) and
  [`observability.py`](src/imdb_ducklake/observability.py) provide shared error and logging
  conventions.

## Development

```powershell
uv sync --locked
uv run imdb-lakehouse --help
uv run ruff check .
uv run pytest
```

## Download IMDb datasets

Install the locked dependencies and download all seven IMDb source archives:

```powershell
uv sync --locked
uv run imdb-lakehouse download
```

Verified archives are reused on later runs. If a transfer was interrupted, the downloader keeps
its `.part` file and resumes from the last saved byte when the server supports range requests.

To download every archive again even when a verified local copy exists:

```powershell
uv run imdb-lakehouse download --force
```

To use a different repository-relative data directory:

```powershell
uv run imdb-lakehouse download --data-dir ./local-imdb-data
```

By default, archives are written to `data/raw/` and their source metadata, byte sizes, SHA-256
checksums, and acquisition batch IDs are recorded in `data/raw/manifest.json`.

## Data source

- [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/)
