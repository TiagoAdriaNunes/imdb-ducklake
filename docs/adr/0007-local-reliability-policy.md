# ADR 0007: Local single-user reliability policy

- Status: Accepted
- Date: 2026-08-16

## Context

Generic data-platform reliability checklists assume a scheduled, multi-user, or multi-tenant
deployment: dead-letter queues for bad records, sensors and freshness SLAs, alert routing, and an
external DAG orchestrator. This project is intentionally a local, single-user, manually triggered
pipeline. Applying those controls unmodified would add operational surface with no operator to
benefit from it. This ADR records which controls apply now, in what form, and which are
deliberately deferred.

## Decision

### Fail-fast, not quarantine

IMDb publishes complete, lossless daily snapshots rather than an ordered change stream (see
[ADR 0004](0004-full-snapshot-refreshes.md)). A corrupt or truncated archive, a gzip failure, or a
header mismatch invalidates the entire candidate build: `acquisition/downloader.py::_validate_archive`
and the dbt test suite raise rather than skip or quarantine the offending rows. Quarantine and
dead-letter queues exist to let a stream keep flowing around bad records; they have no equivalent
value here, because there is no partial-record concept to isolate — the unit of correctness is one
complete archive matching its documented header, and the unit of promotion is one complete,
validated lakehouse build (`application/build.py::build_lakehouse`). A silently dropped or
quarantined row would make every downstream mart wrong in a way nothing would ever surface.

### Restart boundaries

Each stage defines its own safe restart point instead of one global retry:

- **Acquisition**: interrupted downloads resume from the on-disk `.part` file's byte offset via
  HTTP `Range` requests; a rejected or stale partial restarts from byte zero
  (`acquisition/downloader.py::_download_once`). Transient transport and 408/425/429/5xx errors
  retry with bounded exponential backoff (`_download_with_retries`), now logged with
  `event_code=acquisition_retry`/`acquisition_retry_exhausted`, `attempt`, and `attempts`.
- **Ingestion and transformation**: `imdb-lakehouse ingest` and `imdb-lakehouse transform` operate
  on one named staged build (`lakehouse/lifecycle.py::select_staged_build`). A failed `transform`
  can be re-run against the same staged build without re-downloading or re-ingesting.
  `imdb-lakehouse build` composes both automatically per run.
- **Full atomic build**: a failed `build_lakehouse` run never touches `data/ducklake/current/`.
  Every attempt gets a fresh, uniquely identified temporary build
  (`lakehouse/lifecycle.py::BuildPaths.create`/`temporary_build`); a retry is a new isolated build,
  not a resumed one, and `promote_build` only replaces `current` after ingestion, `dbt build`, and
  validation all succeed.

### Controls intentionally not applicable today

The following generic checklist controls are not implemented, by design, for the current
local/single-user/manual operating model:

- **Dead-letter queues / quarantine**: superseded by fail-fast (above).
- **External DAG orchestrator** (e.g. Airflow): the pipeline is five sequential stages triggered by
  one operator; `cron`/Task Scheduler calling the CLI in sequence covers this without an
  orchestrator's scheduling, sensor, and UI overhead. Reassess only if a scheduled or multi-user
  deployment becomes an active requirement.
- **Freshness SLAs, sensors, and alert routing**: there is no on-call and no consumer depending on
  a delivery deadline; acquisition/build timestamps are logged and locally inspectable instead.
- **Dashboards**: structured JSON logs (see [ADR 0006](0006-loguru-structured-logging.md)) are
  sufficient for a single operator reading their own run output.

### Review cadence

Once any of the above moves from "not applicable" toward automated or scheduled operation (a cron
job, a second operator, a hosted consumer), revisit this ADR and the project's quality-threshold
policy together, rather than adding controls piecemeal.

## Consequences

Operational surface stays proportional to one local operator. The tradeoff is explicit: a corrupt
snapshot blocks the *next* build entirely rather than degrading gracefully, and there is no
automated alert if a scheduled run is never actually run. Both are acceptable because promotion is
manually triggered and the previous valid build always remains current until a new one is fully
validated.
