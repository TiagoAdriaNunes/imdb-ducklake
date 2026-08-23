# ADR 0010: Use PostgreSQL as the authoritative DuckLake catalog

- Status: Accepted
- Date: 2026-08-23
- Amends: [ADR 0003](0003-ducklake-storage-snapshots.md) and
  [ADR 0007](0007-local-reliability-policy.md)

## Context

ADR 0003 selected a DuckDB file as DuckLake's metadata catalog and kept that file beside each
build's Parquet directory. ADR 0007 consequently treated atomic directory promotion to
`data/ducklake/current/` as the publication boundary.

That design makes the metadata identity depend on a filesystem path. A host process and a Linux
container see different absolute paths, separate file catalogs can silently diverge, and every
consumer must agree which promoted directory is current. It also couples the Shiny application to
a local `catalog.duckdb` even when ingestion and dbt wrote metadata somewhere else.

DuckLake supports PostgreSQL as an external catalog database. A shared database gives ingestion,
dbt, validation, maintenance, and the application one stable metadata identity while Parquet data
continues to live in the bind-mounted data directory.

## Decision

The supported Docker Compose runtime uses PostgreSQL as the single authoritative database for
DuckLake metadata:

- Database: `ducklake_catalog` by default.
- DuckLake metadata schema: `imdb_lake`.
- Configuration: `IMDB_DUCKLAKE_CATALOG_URL`.
- Durable Parquet storage: `/data/ducklake/storage` in containers and
  `data/ducklake/storage` on the host.
- PostgreSQL data: the Compose `postgres-data` named volume.

PostgreSQL replaces the durable `catalog.duckdb` file; it does **not** replace DuckDB as the SQL
engine. dlt, dbt, validation, checkpointing, the SQL shell, and Shiny each create an embedded DuckDB
connection and attach DuckLake using the PostgreSQL catalog. Those processes execute SQL in DuckDB,
read and write table data as Parquet, and read or commit DuckLake metadata through PostgreSQL.

The shared runtime has no file promotion step. A successful operation publishes DuckLake snapshots
to the PostgreSQL-backed catalog directly. The application reads the same catalog read-only. One
application-level build lock serializes mutating pipeline commands; dlt and dbt own any internal
parallelism. No queue, orchestration schema, persistent worker, or manually managed worker process
is part of this design.

The original DuckDB-file workflow remains available through the non-Docker commands for isolated
development, compatibility, and fixture tests. It is a separate catalog and is not synchronized
with PostgreSQL. It must not be treated as the source of truth for the Compose pipeline or Shiny
application.

## Alternatives considered

### Keep promoting `catalog.duckdb` directories

Rejected for the shared runtime because host/container path translation and multiple independently
created catalogs can make a successful build invisible to another process.

### Store IMDb rows directly in PostgreSQL

Rejected because PostgreSQL is only the DuckLake metadata database. Keeping analytical table data
in Parquet preserves DuckLake snapshots and DuckDB's columnar analytical execution model.

### Add a job queue or persistent pipeline worker

Rejected because pipeline commands are bounded one-shot operations. Compose starts PostgreSQL and
disposable command containers; concurrency is an internal dlt/dbt concern.

## Consequences

- Every Compose command and the Shiny application use one catalog and one Parquet storage root.
- There is no normal `catalog.duckdb` promotion, `current/` pointer, or retired-directory rollback
  in the shared runtime.
- PostgreSQL availability is required for ingestion, transformation, validation, maintenance, and
  application queries.
- Backup and restore must keep the PostgreSQL catalog and Parquet storage consistent; preserving
  only one of them is insufficient.
- Logs and user-facing errors must use the credential-free catalog identity and never print the
  PostgreSQL password.
- Local file-catalog commands can still be useful for tests, but their output is intentionally
  independent from the PostgreSQL-backed application data.
