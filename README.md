# IMDb DuckLake

[![CI](https://github.com/TiagoAdriaNunes/imdb-ducklake/actions/workflows/ci.yml/badge.svg)](https://github.com/TiagoAdriaNunes/imdb-ducklake/actions/workflows/ci.yml)

A reproducible local analytics lakehouse built from the official IMDb non-commercial datasets.

The project uses:

- **dlt** for lossless raw ingestion and load metadata
- **DuckDB and DuckLake** for query execution, cataloging, snapshots, and Parquet storage
- **dbt** for typed staging models, tests, and analytics-ready marts
- **Shiny for Python** in a later phase for interactive exploration

> IMDb permits these datasets for personal and non-commercial use. This repository contains code
> and miniature synthetic fixtures only. It never redistributes IMDb source archives, extracted
> source rows, generated catalogs, or Parquet data.

## Architecture

The end-to-end data flow, package dependency rules, failure boundaries, and dbt lineage are
documented in [`docs/architecture.md`](docs/architecture.md). Architectural decisions are recorded
under [`docs/adr/`](docs/adr/README.md).

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

```console
uv sync --locked
uv run imdb-lakehouse --help
uv run ruff check .
uv run pytest
```

Ordinary CI excludes tests marked `smoke`, because those tests require the complete local IMDb
download. Run them deliberately with `uv run pytest -m smoke` after acquiring all seven archives.

`uv sync` may print a Windows-only warning that it could not create hardlinks and copied files
instead; this is an unrelated Windows/antivirus filesystem limitation, not a project issue. Set
`$env:UV_LINK_MODE = "copy"` (PowerShell) to silence it.

## Logging and run correlation

Interactive commands emit concise console events by default. Use the global option before the
command name to emit newline-delimited JSON for CI, schedulers, or log processors:

```console
uv run imdb-lakehouse --log-format json build
```

`IMDB_LAKEHOUSE_LOG_FORMAT=console|json` provides the equivalent environment configuration, and
`IMDB_LAKEHOUSE_LOG_LEVEL` controls verbosity. Progress is reported every 10 seconds by default;
set `IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS` to a positive number to change the interval.

Interactive terminals render live ingestion counters with Rich. Known totals include a percentage
and ETA; unknown row totals show the processed count, rate, and elapsed time. JSON and redirected
output use throttled structured progress events instead of terminal animation.

Every command receives a `run_id`. A staged DuckLake artifact receives a `build_id`, and dlt's
internal load package is reported explicitly as `dlt_load_id`. This hierarchy keeps acquisition,
build lifecycle, and dlt progress events correlated without presenting timestamp-like identifiers
as elapsed time.

A representative console event:

```console
14:32:07 | INFO | Free-space check passed | required=1.2GiB | available=57.5GiB | run=1f2e3a4b | build=20260816T143206Z-ab12cd
```

The same event in `--log-format json` mode, one self-contained JSON object per line:

```json
{"record": {"time": {"repr": "2026-08-16T14:32:07"}, "level": {"name": "INFO"}, "message": "Free-space check passed", "extra": {"event_code": "free_space_gate_passed", "stage": "lifecycle", "status": "completed", "run_id": "1f2e3a4b-...", "build_id": "20260816T143206Z-ab12cd", "required_bytes": 1288490188, "available_bytes": 61782441984}}}
```

## Download IMDb datasets

Install the locked dependencies and download all seven IMDb source archives:

```console
uv sync --locked
uv run imdb-lakehouse download
```

Verified archives are reused on later runs. If a transfer was interrupted, the downloader keeps
its `.part` file and resumes from the last saved byte when the server supports range requests.

To download every archive again even when a verified local copy exists:

```console
uv run imdb-lakehouse download --force
```

To use a different repository-relative data directory:

```console
uv run imdb-lakehouse download --data-dir ./local-imdb-data
```

By default, archives are written to `data/raw/` and their source metadata, byte sizes, SHA-256
checksums, and acquisition batch IDs are recorded in `data/raw/manifest.json`.

## Build the complete lakehouse

Run the complete safe workflow with one command:

```console
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

```console
uv run imdb-lakehouse build --force-download
```

The data location is independent of where the repository is cloned:

```console
uv run imdb-lakehouse build --data-dir ./local-imdb-data
```

## Validate a lakehouse

Validate the active `current/` build without supplying catalog or storage paths:

```console
uv run imdb-lakehouse validate
```

When no current build exists, the command automatically validates the sole staged build instead.
It prints the required-relation count and row count for each mart. If multiple staged builds exist,
select one explicitly with `--build-id`; `--data-dir` uses the same override as the other commands.

## Promote a staged build

Promote an already transformed staged build without repeating acquisition, ingestion, or dbt:

```console
uv run imdb-lakehouse promote --build-id 20260815T123000Z-abc123
```

After a successful promotion and current-catalog validation, optionally remove other staged builds
and older retired versions while retaining the newest rollback build:

```console
uv run imdb-lakehouse promote --build-id 20260815T123000Z-abc123 --prune
```

The command holds the single-writer lock, validates the staged catalog through a fresh read-only
process, atomically moves it to `data/ducklake/current/`, and then reattaches and validates the
promoted catalog from another fresh process. Omit `--build-id` when exactly one staged build exists.
Use `--data-dir` for a non-default data root.

## Checkpoint the current lakehouse

Compact the active DuckLake build and expire obsolete snapshots without rerunning acquisition,
ingestion, dbt, validation, or promotion:

```console
uv run imdb-lakehouse checkpoint
```

The command acquires the lakehouse build lock and operates only on `data/ducklake/current/`. It
emits structured start, completion, and failure logs. Use `--data-dir` when the lakehouse uses a
non-default data root.

## Exit codes

Expected failures use stable, category-specific process exit codes so local scripts and schedulers
can distinguish the failed stage:

| Code | Failure category |
| ---: | --- |
| 10 | Configuration |
| 11 | Acquisition or retained-archive verification |
| 12 | dlt ingestion |
| 13 | dbt transformation |
| 14 | Post-build validation |
| 15 | Promotion of a validated build |
| 16 | Other lakehouse lifecycle operations, including locks and free-space gates |

Typer continues to own command-line parsing errors. An otherwise expected application error that
does not yet have a more specific subtype exits with code 1.

## Ingest a raw snapshot

Load all seven retained archives into a new isolated DuckLake build:

```console
uv run imdb-lakehouse ingest
```

The command revalidates every archive against the manifest before loading it. It prints the build
ID and catalog path, and leaves the result under `data/ducklake/builds/` for inspection. This
stage-only command does not replace `data/ducklake/current/`; use `promote` after transformation or
use the full `build` command for one end-to-end operation. During the load it logs each queued
dataset and emits throttled extraction, normalization, and destination progress with explicit
schema and dlt load-package identifiers.

Use the same data-directory override as the download command when the archives are elsewhere:

```console
uv run imdb-lakehouse ingest --data-dir ./local-imdb-data
```

If a staged ingestion already exists, the command preserves it and stops instead of repeating the
load. Discard that unpromoted build and ingest a fresh snapshot explicitly with:

```console
uv run imdb-lakehouse ingest --replace-staged
```

## Transform and test a staged build

After ingestion, run every dbt model and data-quality test against the staged DuckLake build:

```console
uv run imdb-lakehouse transform
```

The command builds the `staging`, `intermediate`, and `marts` schemas and prints dbt's complete
result. Known sparse IMDb relationship gaps are measured as ratios and fail the build if they
exceed 0.01%; they are not patched in the source data. Large title-search arrays are materialized
as separate rollups to keep peak DuckDB memory bounded. If more than one staged build exists,
select the build ID printed by ingestion:

```console
uv run imdb-lakehouse transform --build-id 20260815T123000Z-abc123
```

Use `--data-dir` when ingestion used a custom data directory. A successful transformation remains
staged for inspection; run `promote` to validate and activate it, or use the full `build` command to
perform an isolated end-to-end run before promotion.

## Query the analytical marts

Attach a promoted catalog read-only from DuckDB. Replace the two paths when `--data-dir` was used:

```sql
LOAD ducklake;
ATTACH 'ducklake:D:/path/to/imdb-ducklake/data/ducklake/current/catalog.duckdb'
    AS imdb_lake (
        DATA_PATH 'D:/path/to/imdb-ducklake/data/ducklake/current/storage',
        OVERRIDE_DATA_PATH true,
        READ_ONLY
    );
```

Search well-rated titles without reading raw tables:

```sql
SELECT tconst, primary_title, start_year, average_rating, num_votes, genres
FROM imdb_lake.marts.mart_title_search
WHERE primary_title ILIKE '%matrix%'
ORDER BY num_votes DESC NULLS LAST
LIMIT 25;
```

Summarize a genre over time:

```sql
SELECT start_year, title_count, rated_title_count, average_rating, total_votes
FROM imdb_lake.marts.mart_genre_year_summary
WHERE genre = 'Documentary' AND start_year BETWEEN 2000 AND 2025
ORDER BY start_year;
```

Inspect a person's filmography or navigate a series:

```sql
SELECT primary_name, primary_title, start_year, category, average_rating
FROM imdb_lake.marts.mart_person_filmography
WHERE nconst = 'nm0000206'
ORDER BY start_year DESC NULLS LAST, ordering;

SELECT series_title, season_number, episode_number, episode_title, average_rating
FROM imdb_lake.marts.mart_series_episodes
WHERE series_tconst = 'tt0944947'
ORDER BY season_number NULLS LAST, episode_number NULLS LAST;
```

## Data source

- [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/)
