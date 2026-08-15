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
- [`ingestion/`](src/imdb_ducklake/ingestion/) defines explicit lossless dlt resources and loads a
  complete verified snapshot into an isolated local DuckLake build.
- [`transformation/`](src/imdb_ducklake/transformation/) invokes dbt with explicit paths and
  environment while dbt owns all typing, analytical SQL, tests, and model documentation.
- [`application/`](src/imdb_ducklake/application/) coordinates acquisition, ingestion,
  transformation, fresh-process validation, and promotion as one atomic build use case.
- [`dbt/`](dbt/) defines raw sources, typed staging views, reusable intermediate views, four
  analytics-ready marts, and their data-quality tests.
- [`lakehouse/`](src/imdb_ducklake/lakehouse/) owns isolated build paths, locking, free-space
  validation, failure cleanup, crash recovery, retention pruning, read-only validation, and safe
  promotion.
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

## Build the complete lakehouse

Run the complete safe workflow with one command:

```powershell
uv run imdb-lakehouse build
```

The command acquires the single-writer lock, reuses or downloads all seven verified archives,
checks free space, creates an isolated build, runs dlt ingestion and `dbt build`, and validates all
31 required relations through a new process with a read-only DuckLake attachment. Only then does
it atomically replace `data/ducklake/current/`; any earlier failure leaves the existing current
build unchanged. The previous current build is retained for rollback, and older retired or
crash-orphaned workspaces are pruned on the next run.

Running the command again reuses verified source archives but deliberately creates a fresh full
snapshot. To reacquire every archive as well, use:

```powershell
uv run imdb-lakehouse build --force-download
```

The data location is independent of where the repository is cloned:

```powershell
uv run imdb-lakehouse build --data-dir ./local-imdb-data
```

## Validate a lakehouse

Validate the active `current/` build without supplying catalog or storage paths:

```powershell
uv run imdb-lakehouse validate
```

When no current build exists, the command automatically validates the sole staged build instead.
It prints the required-relation count and row count for each mart. If multiple staged builds exist,
select one explicitly with `--build-id`; `--data-dir` uses the same override as the other commands.

## Ingest a raw snapshot

Load all seven retained archives into a new isolated DuckLake build:

```powershell
uv run imdb-lakehouse ingest
```

The command revalidates every archive against the manifest before loading it. It prints the build
ID and catalog path, and leaves the result under `data/ducklake/builds/` for inspection. This
stage-only command does not replace `data/ducklake/current/`; use the full `build` command when the
result should be validated and promoted. During the load it logs each queued dataset and
dlt's extraction, normalization, and destination progress every few seconds.

Use the same data-directory override as the download command when the archives are elsewhere:

```powershell
uv run imdb-lakehouse ingest --data-dir ./local-imdb-data
```

If a staged ingestion already exists, the command preserves it and stops instead of repeating the
load. Discard that unpromoted build and ingest a fresh snapshot explicitly with:

```powershell
uv run imdb-lakehouse ingest --replace-staged
```

## Transform and test a staged build

After ingestion, run every dbt model and data-quality test against the staged DuckLake build:

```powershell
uv run imdb-lakehouse transform
```

The command builds the `staging`, `intermediate`, and `marts` schemas and prints dbt's complete
result. Known sparse IMDb relationship gaps are measured as ratios and fail the build if they
exceed 0.01%; they are not patched in the source data. Large title-search arrays are materialized
as separate rollups to keep peak DuckDB memory bounded. If more than one staged build exists,
select the build ID printed by ingestion:

```powershell
uv run imdb-lakehouse transform --build-id 20260815T123000Z-abc123
```

Use `--data-dir` when ingestion used a custom data directory. A successful transformation remains
staged for inspection; the full `build` command performs its own isolated end-to-end run before
promotion.

## Data source

- [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/)
