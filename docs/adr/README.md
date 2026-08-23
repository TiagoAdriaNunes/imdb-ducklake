# Architecture decision records

Accepted decisions are immutable records of why the current design was chosen. A later change
should add a superseding ADR rather than rewriting history.

- [0001: Use dlt for lossless raw loading](0001-dlt-raw-loader.md)
- [0002: Make dbt the transformation owner](0002-dbt-transformation-owner.md)
- [0003: Use DuckLake for storage and snapshots](0003-ducklake-storage-snapshots.md)
- [0004: Refresh IMDb data as full snapshots](0004-full-snapshot-refreshes.md)
- [0005: Use GitHub as the canonical source repository](0005-github-canonical-repository.md)
- [0006: Use Loguru for structured application logging](0006-loguru-structured-logging.md)
- [0007: Local single-user reliability policy](0007-local-reliability-policy.md)
- [0008: Schema compatibility policy](0008-schema-compatibility-policy.md)
- [0009: Bounded parallelism for dlt extraction and load](0009-bounded-ingestion-parallelism.md)
- [0010: Use PostgreSQL as the authoritative DuckLake catalog](0010-postgresql-authoritative-ducklake-catalog.md)
- [0011: Fix dlt extract-stage parallelism and expose ingestion tuning knobs](0011-fix-dlt-extract-parallelism.md)
- [0012: Restore isolated build staging for the PostgreSQL-backed catalog](0012-restore-build-staging-for-postgresql-catalog.md)
