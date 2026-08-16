# ADR 0006: Use Loguru for structured application logging

- Status: Accepted
- Date: 2026-08-16

## Context

The project needed one centralized, tested logging configuration that could emit concise console
output for interactive local runs and newline-delimited JSON for CI, schedulers, and log
processors, while routing standard-library records from `dlt`, `dbt`'s subprocess output, and
other dependencies through the same sinks. The project's original design proposed `structlog` for
this role.

## Decision

Use `loguru` as the application event API instead of `structlog`. `loguru` ships a built-in JSON
renderer (`serialize=True`), a single global logger with `bind()`-based context propagation, and a
small `logging.Handler` shim (`observability._InterceptHandler`) that redirects standard-library
records into the same sinks. This removed the need to hand-configure `structlog` processors,
renderers, and a stdlib bridge separately, and kept `observability.py` to one small module.

Adopt the following stable event fields, bound once per scope and merged into every record's
`extra` mapping:

- `run_id`: bound once per CLI invocation (`start_run_context`), correlates every event emitted by
  one process run.
- `build_id`: bound once a DuckLake build workspace exists (`logger.bind(build_id=...)` in
  `application/build.py`), correlates acquisition through promotion for one build.
- `dlt_load_id`: extracted from dlt's numeric load-package identifier by
  `ingestion/progress.py`; always reported under this explicit field name, never as an unlabeled
  value such as `Load raw in 1786839514.1750643`.
- `event_code`, `stage`, `status`: every application-owned log call in `application/build.py`,
  `cli.py`, and `transformation/dbt_runner.py` passes a stable machine-matchable `event_code`
  (e.g. `acquisition_completed`), the pipeline `stage`, and a `status` of `waiting`, `started`, or
  `completed`.
- Typed optional fields added only when relevant: `dataset`, `file_name`, `rows`, `*_bytes`,
  `*_seconds`, `dbt_stream`, `dbt_message`, `mart_row_counts`.

The console renderer (`observability._console_format`) hides machine-oriented fields
(`event_code`, `stage`, `status`, `dbt_stream`, `dbt_message`, `mart_row_counts`) and renders
`*_bytes`/`*_seconds` fields in human units; JSON mode (`serialize=True`) emits every bound field
verbatim so it stays independently parseable per line.

## Consequences

`loguru` is now a direct runtime dependency instead of `structlog`. Context binding uses `loguru`'s
global logger and `bind()`/`configure(extra=...)` rather than `structlog`'s per-call bound loggers,
which is slightly less explicit about scope but requires far less setup code. Any future switch
back to `structlog` would need a new ADR and would replace `observability.py` and the
`_InterceptHandler` shim, but would not change the event field contract above, since `dlt`, `dbt`,
and the CLI only depend on that contract, not on `loguru` itself.
