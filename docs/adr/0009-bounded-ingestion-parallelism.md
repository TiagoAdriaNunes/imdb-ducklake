# ADR 0009: Bounded parallelism for dlt extraction and load

- Status: Accepted
- Date: 2026-08-22
- Amends: [ADR 0001](0001-dlt-raw-loader.md)

## Context

[ADR 0001](0001-dlt-raw-loader.md) decided to "load sequentially into raw," and
`ingestion/pipeline.py` originally pinned `load.workers: 1` accordingly. Extraction was never
explicitly configured, but was sequential in practice anyway: dlt's `PipeIterator` pulls plain
generator/iterator resources cooperatively via `next(gen)` on a single thread, so the seven
per-file CSV-reading resources were never actually read concurrently regardless of any `workers`
setting.

Re-examining both assumptions:

- **Load**: dlt's DuckLake destination gives every concurrent load job its own DuckDB connection
  (`destinations/impl/ducklake/sql_client.py`: "connection pool creates a separate connection for
  each sql_client"), and DuckLake itself is a multi-writer catalog format with optimistic
  concurrency at commit time (like Delta/Iceberg), not a single-writer DuckDB file. The strict
  serialization ADR 0001 assumed is not a hard technical requirement of the destination.
- **Extraction**: dlt supports `parallelized=True` on `@dlt.transformer()`/`@dlt.resource()`,
  which runs each resource's generator in its own thread via the extract-stage worker pool
  (`extract.workers`), instead of the cooperative single-thread pull. This was previously unused.

`ingestion/resources.py::_read_csv_duckdb_arrow` read each archive via DuckDB's implicit shared
default connection (module-level `duckdb.read_csv(...)`, no explicit `connect()`). Reproduced
empirically that this is not safe for concurrent extraction: two threads calling it concurrently on
the same file set raised `InvalidInputException: Attempting to execute an unsuccessful or closed
pending query result` on one of the two. An explicit `duckdb.connect(":memory:")` per call
(verified with the same two-thread reproduction) removed the failure entirely.

## Decision

- `ingestion/resources.py`: `_read_csv_duckdb_arrow` now opens its own DuckDB connection per call
  instead of using the implicit shared default connection. `read_csv_duckdb_arrow` is now
  `dlt.transformer(parallelized=True)`.
- `ingestion/pipeline.py`: `dlt.config.values` now sets `extract.workers: 2` and `load.workers: 2`
  (previously unset/default and `1`, respectively). `normalize.workers` is unchanged at `2`.

Both values were chosen conservatively (2, not higher) without exhausting the local machine's
resources (12 logical CPUs, 16GB RAM); this is a starting point, not a measured optimum.

Verified end to end: the full unit suite, `mypy`, and
`tests/integration/test_ingestion_pipeline.py` (a real lossless load → `dbt build` → validate →
promote run) all pass unchanged with these settings.

Benchmarked on the two largest real archives (`title.principals.tsv.gz`, 743MB/101M rows;
`title.akas.tsv.gz`, 487MB/59M rows): sequential extraction took 102.8s; concurrent extraction
(two threads, separate connections, mirroring `extract.workers: 2`) took 62.1s — a ~40% reduction.
Not a full 2x, because DuckDB already parallelizes a single file's CSV parsing internally across
cores, so there is some contention between the two concurrently-extracting files; the benefit is
still substantial across the full seven-file ingest.

## Consequences

Ingestion is faster without changing what lands in the raw schema: every source row is still
loaded exactly once, per-table schema contracts are still frozen (see ADR 0001), and the raw layer
remains lossless. The tradeoff is a small increase in mechanism complexity — a per-call DuckDB
connection instead of the implicit default one, and reliance on dlt's `parallelized` resource
wrapping, whose behavior under direct resource iteration (bypassing a full pipeline run, as
`tests/unit/test_ingestion_resources.py` does) was not obvious in advance and had to be verified
rather than assumed.
