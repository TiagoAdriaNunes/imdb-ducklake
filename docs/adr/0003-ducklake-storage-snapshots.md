# ADR 0003: Use DuckLake for storage and snapshots

- Status: Accepted
- Date: 2026-08-15

## Context

The project needs a local analytical catalog, columnar storage, reproducible snapshots, and a
read-only attachment path without operating a separate database service.

## Decision

Use a DuckDB-backed DuckLake catalog with local Parquet storage. Give every build absolute catalog
and storage paths in an isolated directory. Validate through a new read-only DuckDB process before
atomically promoting the directory to `current/`.

## Consequences

Catalog and Parquet files move together as one build. A single-user lock serializes mutations.
Failed builds are disposable, the prior current build can be retained for rollback, and consumers
query marts through a read-only DuckLake attachment.
