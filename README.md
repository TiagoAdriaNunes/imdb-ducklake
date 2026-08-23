# IMDb DuckLake

[![CI](https://github.com/TiagoAdriaNunes/imdb-ducklake/actions/workflows/ci.yml/badge.svg)](https://github.com/TiagoAdriaNunes/imdb-ducklake/actions/workflows/ci.yml)

A reproducible local analytics lakehouse built from the official IMDb non-commercial datasets.

The project uses:

- **dlt** for lossless raw ingestion and load metadata
- **PostgreSQL** as the authoritative DuckLake metadata catalog
- **DuckLake and Parquet** for table metadata, snapshots, and analytical storage
- **DuckDB** as the embedded execution engine used by dlt, dbt, validation, and the application
- **dbt** for typed staging models, tests, and analytics-ready marts
- **Shiny for Python** for interactive exploration of the analytical marts

> IMDb permits these datasets for personal and non-commercial use. This repository contains code
> and miniature synthetic fixtures only. It never redistributes IMDb source archives, extracted
> source rows, generated catalogs, or Parquet data.

## Architecture

The end-to-end data flow, package dependency rules, failure boundaries, and dbt lineage are
documented in [`docs/architecture.md`](docs/architecture.md). Architectural decisions are recorded
under [`docs/adr/`](docs/adr/README.md). Generated dbt docs (model/column reference, lineage graph,
compiled SQL) are published at
**[tiagoadrianunes.github.io/imdb-ducklake](https://tiagoadrianunes.github.io/imdb-ducklake/)**.

The runtime catalog contract is defined by
[ADR 0010](docs/adr/0010-postgresql-authoritative-ducklake-catalog.md):

| Responsibility | Authoritative component |
| --- | --- |
| DuckLake metadata and snapshot state | PostgreSQL (`ducklake_catalog`, schema `imdb_lake`) |
| Analytical table data | Parquet under `data/ducklake/storage/` |
| SQL execution | Embedded DuckDB connections attaching the PostgreSQL-backed DuckLake catalog |

**PostgreSQL replaces `catalog.duckdb` as the durable catalog in the Compose workflow; it does not
replace DuckDB as the query engine.** The non-Docker file-catalog commands remain an isolated local
fallback and are not synchronized with the PostgreSQL catalog used by Docker and Shiny.

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
make sync
uv run pre-commit install
uv run pre-commit run --all-files
uv run imdb-lakehouse --help
make lint
make test
```

Ordinary CI excludes tests marked `smoke`, because those tests require the complete local IMDb
download. Run them deliberately with `make smoke` after acquiring all seven archives.

Every command above, every quality gate, and every `imdb-lakehouse` pipeline stage is also
available as an explicit `make` target; run `make help` for the full list, or `make ci` to run
every check `.github/workflows/ci.yml` runs, in the same order.

`uv sync` may print a Windows-only warning that it could not create hardlinks and copied files
instead; this is an unrelated Windows/antivirus filesystem limitation, not a project issue. Set
`$env:UV_LINK_MODE = "copy"` (PowerShell) to silence it.

## Run the pipeline in Docker

Docker Compose is the supported shared runtime with a PostgreSQL-backed DuckLake metadata catalog.
It does not run a queue, scheduler, or persistent worker. The original local commands remain
available for the file-catalog/staged-build fallback; use the explicit `docker-*` commands for the
authoritative PostgreSQL workflow:

```console
make docker-image
make docker-build
make docker-download
make docker-ingest
make docker-transform
make docker-validate
make docker-checkpoint
make docker-app
```

`make docker-up` is idempotent: it only ensures PostgreSQL is running and healthy. It never rebuilds
the image or runs the pipeline. `make docker-app` similarly ensures PostgreSQL and Shiny are healthy;
calling it again leaves the existing containers in place. Use `make docker-image` explicitly after
changing application code. Pipeline targets check that an image exists, build it only when missing,
and then run only their named one-shot operation.

The PostgreSQL service retains DuckLake metadata in its named volume; the `./data:/data` bind mount
retains the raw archives and Parquet data under `data/ducklake/storage/` on the host.
`docker-transform`, `docker-validate`, and `docker-checkpoint` reuse that existing catalog and
storage without repeating ingestion. dlt and dbt concurrency remain internal settings; callers do
not create or coordinate process workers.

`make docker-app` starts the Shiny application at <http://localhost:8000>. The app executes queries
with an in-process DuckDB connection, reads DuckLake metadata from the same PostgreSQL service, and
reads the existing Parquet files from the read-only `/data/ducklake/storage` mount. Stop the app and
PostgreSQL with `make docker-down`; this preserves the stopped containers and all data so the next
`make docker-app` starts the same containers. Use `make docker-app-logs` to follow its logs.

The Docker defaults are sized for an 8 GB Docker Desktop VM: one concurrent dbt node, two DuckDB
query threads, and a 6 GB DuckDB memory limit. Override `IMDB_DUCKLAKE_DBT_THREADS`,
`IMDB_DUCKLAKE_QUERY_THREADS`, or `IMDB_DUCKLAKE_DUCKDB_MEMORY_LIMIT` in `.env` on a larger host.

## Logging and run correlation

Interactive commands emit concise console events by default. Use the global option before the
command name to emit newline-delimited JSON for CI, schedulers, or log processors:

```console
uv run imdb-lakehouse --log-format json build
```

That global option must precede the subcommand, so it cannot be passed through `make ... ARGS=...`
(which appends after the subcommand); with `make`, set `LOG_FORMAT` instead:

```console
make build LOG_FORMAT=json
```

`IMDB_LAKEHOUSE_LOG_LEVEL` controls verbosity. Progress is reported every 10 seconds by default; set
`IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS` to a positive number to change the interval.

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

> Unprefixed pipeline targets in the sections below (`make build`, `make ingest`, `make transform`,
> and related commands) describe the isolated local file-catalog fallback. Use the corresponding
> `docker-*` targets when working with the authoritative PostgreSQL catalog used by Shiny.

## Download IMDb datasets

Install the locked dependencies and download all seven IMDb source archives:

```console
make sync
make download
```

Verified archives are reused on later runs. If a transfer was interrupted, the downloader keeps
its `.part` file and resumes from the last saved byte when the server supports range requests.

To download every archive again even when a verified local copy exists:

```console
make download ARGS=--force
```

To use a different repository-relative data directory:

```console
make download ARGS="--data-dir ./local-imdb-data"
```

By default, archives are written to `data/raw/` and their source metadata, byte sizes, SHA-256
checksums, and acquisition batch IDs are recorded in `data/raw/manifest.json`.

## Build the complete lakehouse

Run the complete safe workflow with one command:

```console
make build
```

The command acquires the single-writer lock, reuses or downloads all seven verified archives,
checks free space, creates an isolated build, runs dlt ingestion and `dbt build`, and validates all
31 required relations through a new process with a read-only DuckLake attachment. Only then does
it atomically replace `data/ducklake/current/`; any earlier failure leaves the existing current
build unchanged. The previous current build is retained for rollback, and older retired or
crash-orphaned workspaces are pruned on the next run.

`build` is intentionally all-or-nothing: any failure anywhere in the pipeline, including a late
`dbt build` test, discards the whole isolated workspace it created — even the already-ingested raw
data. That is the right behavior for the one true reproducible build path, but it makes `build` the
wrong tool for iterating on a dbt change: every retry re-runs the full multi-minute dlt ingestion
even though the source archives never changed. Use `make ingest` once, then `make transform`
repeatedly, instead — see
["Ingest a raw snapshot"](#ingest-a-raw-snapshot) below.

Running the command again reuses verified source archives but deliberately creates a fresh full
snapshot. To reacquire every archive as well, use:

```console
make build ARGS=--force-download
```

The data location is independent of where the repository is cloned:

```console
make build ARGS="--data-dir ./local-imdb-data"
```

## Validate a lakehouse

Validate the active `current/` build without supplying catalog or storage paths:

```console
make validate
```

When no current build exists, the command automatically validates the sole staged build instead.
It prints the required-relation count and row count for each mart. If multiple staged builds exist,
select one explicitly with `--build-id`; `--data-dir` uses the same override as the other commands.

## Promote a staged build

Promote an already transformed staged build without repeating acquisition, ingestion, or dbt:

```console
make promote ARGS="--build-id 20260815T123000Z-abc123"
```

After a successful promotion and current-catalog validation, optionally remove other staged builds
and older retired versions while retaining the newest rollback build:

```console
make promote ARGS="--build-id 20260815T123000Z-abc123 --prune"
```

The command holds the single-writer lock, validates the staged catalog through a fresh read-only
process, atomically moves it to `data/ducklake/current/`, and then reattaches and validates the
promoted catalog from another fresh process. Omit `--build-id` when exactly one staged build exists.
Use `--data-dir` for a non-default data root.

## Checkpoint the current lakehouse

Compact the active DuckLake build and expire obsolete snapshots without rerunning acquisition,
ingestion, dbt, validation, or promotion:

```console
make checkpoint
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
make ingest
```

The command revalidates every archive against the manifest before loading it. It prints the build
ID and catalog path, and leaves the result under `data/ducklake/builds/` for inspection. This
stage-only command does not replace `data/ducklake/current/`; use `promote` after transformation or
use the full `build` command for one end-to-end operation. During the load it logs each queued
dataset and emits throttled extraction, normalization, and destination progress with explicit
schema and dlt load-package identifiers.

Use the same data-directory override as the download command when the archives are elsewhere:

```console
make ingest ARGS="--data-dir ./local-imdb-data"
```

If a staged ingestion already exists, the command preserves it and stops instead of repeating the
load. Discard that unpromoted build and ingest a fresh snapshot explicitly with:

```console
make ingest ARGS=--replace-staged
```

## Transform and test a staged build

After ingestion, run every dbt model and data-quality test against the staged DuckLake build:

```console
make transform
```

The command builds the `staging`, `intermediate`, and `marts` schemas and prints dbt's complete
result. Known sparse IMDb relationship gaps are measured as ratios and fail the build if they
exceed 0.01%; they are not patched in the source data. Large title-search arrays are materialized
as separate rollups to keep peak DuckDB memory bounded. If more than one staged build exists,
select the build ID printed by ingestion:

```console
make transform ARGS="--build-id 20260815T123000Z-abc123"
```

Use `--data-dir` when ingestion used a custom data directory. A successful transformation remains
staged for inspection; run `promote` to validate and activate it, or use the full `build` command to
perform an isolated end-to-end run before promotion.

Unlike `build`, `transform` never deletes the staged build it runs against, even when a dbt model
or test fails. That makes `ingest` once + `transform` on repeat the right workflow when iterating on
a dbt change: fix the model, rerun `make transform`, and only the (fast) dbt stage re-executes — the
multi-minute dlt ingestion of the seven raw archives is not repeated.

## Query the analytical marts

DuckDB remains the query engine. Attach it read-only to the authoritative PostgreSQL-backed
DuckLake catalog, using the configured PostgreSQL credentials and the host Parquet path:

```sql
LOAD ducklake;
ATTACH 'ducklake:postgres:dbname=''ducklake_catalog'' host=''localhost'' port=5432 user=''imdb'' password=''imdb-local-dev'''
    AS imdb_lake (
        DATA_PATH 'D:/path/to/imdb-ducklake/data/ducklake/storage',
        METADATA_SCHEMA 'imdb_lake',
        OVERRIDE_DATA_PATH true,
        READ_ONLY
    );
```

The shown password is the Compose development default; use the values configured in `.env`. From
inside Compose, the PostgreSQL host is `postgres` and the data path is `/data/ducklake/storage`.

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
