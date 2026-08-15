# ADR 0001: Use dlt for lossless raw loading

- Status: Accepted
- Date: 2026-08-15

## Context

Seven gzip-compressed IMDb TSV snapshots must enter DuckLake without Python materializing complete
files, changing source strings, or losing file-level provenance.

## Decision

Use dlt filesystem resources backed by DuckDB chunked CSV parsing. Declare every IMDb source
column as `VARCHAR`, preserve literal `\N`, load sequentially into `raw`, and add an
`ingestion_files` resource linked through `_dlt_load_id`.

## Consequences

dlt owns extraction, normalization, load packages, and raw lineage. Python owns source contracts
and orchestration; dbt owns all semantic typing. Native dlt/DuckLake integration remains covered by
fixture-backed integration tests.
