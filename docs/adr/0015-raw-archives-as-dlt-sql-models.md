# ADR 0015: Load raw archives as dlt SQL models

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0011](0011-fix-dlt-extract-parallelism.md)

## Context

A full local ingest took 272s: extract 205s, normalize 5s, load 61s. DuckDB alone parses all seven
archives in 41s. dlt's extract re-encoded every Arrow batch to parquet on one main thread, then
the load read that parquet back and wrote DuckLake files: two full writes, one single-threaded.

## Decision

Each raw resource yields a dlt SQL model (`file_format="model"`): a `SELECT ... FROM
read_csv('<archive>', all_varchar = true, ...)` relation. dlt's loader runs it inside the DuckLake
connection as `INSERT INTO raw.<table> SELECT ...`. dlt still creates and replaces the tables,
enforces the frozen column contract, adds `_dlt_load_id`, and records the load.

`ingest_snapshot` no longer forces `loader_file_format="parquet"`, which would override the model
format. `IMDB_DUCKLAKE_INGEST_CHUNK_SIZE` is removed: there are no Arrow batches.

dlt 1.31 passes `infer_schema` to sqlglot's `qualify`, removed in sqlglot 30.25, when it builds a
model. dlt 1.32 fixes it but requires DuckDB < 2, so `_drop_removed_sqlglot_qualify_argument`
drops the argument until a dlt release supports both.

## Consequences

- Same benchmark: 79s total (extract 3s, load 70s), same row counts and storage size.
- The load reads archives from local disk inside the DuckLake connection, so ingestion must run
  where the archives are (true locally and in Docker).
- Progress shows one item per table during extract, not rows.
- Rejected: DuckDB-staged parquet with `dlt.mark.with_file_import` (160s, still writes twice);
  `ducklake_add_data_files` (no rewrite, but bypasses dlt's loader).
