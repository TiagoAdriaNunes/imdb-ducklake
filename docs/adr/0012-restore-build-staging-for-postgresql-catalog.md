# ADR 0012: Restore isolated build staging for the PostgreSQL-backed catalog

- Status: Accepted
- Date: 2026-08-23
- Amends: [ADR 0010](0010-postgresql-authoritative-ducklake-catalog.md)

## Context

ADR 0010 made PostgreSQL the authoritative DuckLake metadata catalog and, as part of that,
declared "no file promotion step": every PostgreSQL-mode command attached directly to one
permanent, shared `data/ducklake/storage` and wrote into it in place.

That traded away a property ADR 0007's `builds/<id>/` → `current/` promotion existed specifically
to provide: a killed writer's partial output stays confined to a directory nothing else reads,
so it can be inspected or discarded without touching live data. With one shared, permanently-live
storage directory, ingestion or dbt writing when the process is interrupted leaves partial Parquet
files mixed directly into the same tree every reader (`validate`, `checkpoint`, the Shiny app,
the next build) already depends on. There is no directory boundary separating "this build's
in-progress output" from "the data everything else is reading right now."

This stopped being theoretical: a killed dbt run left several tables' physical directories
retaining their dbt materialization `__dbt_tmp` suffix — DuckLake's `ALTER TABLE ... RENAME`
updates the catalog's table name but not the on-disk directory a table was created under, so a
table's live path can permanently differ from its current name. Judging "is this directory still
referenced" by name or by querying `ducklake_table` for that literal name is unreliable for
exactly that reason. Distinguishing genuine crash debris from a live table's actual backing
storage requires cross-referencing `ducklake_data_file` paths against the filesystem, which is
easy to get wrong under pressure - and getting it wrong deletes live data, not garbage.

## Decision

A PostgreSQL-backed build now stages every write into an isolated `builds/<id>/storage`,
identical in shape to the pre-ADR-0010 local-catalog layout, and reaches `current/storage` only
through the same atomic `promote_build` local builds already used (`os.replace`, journal-guarded,
previous build retired for rollback). PostgreSQL remains the authoritative metadata catalog
throughout - this only changes where the Parquet files backing that metadata physically live.

- `promote_build` accepts `require_catalog_file=False`: a PostgreSQL-mode build has no local
  `catalog.duckdb` (metadata lives in PostgreSQL), but still promotes its `storage/` directory the
  same way.
- `CatalogTarget.storage_dir` is constructed per-command against the directory that command
  actually needs: a build's own `builds/<id>/storage` while writing, `current/storage` for
  read-oriented commands (`validate`, `checkpoint`, the Shiny app) once nothing more will be
  promoted into it this run.
- Every DuckLake `ATTACH` in this codebase already passed `OVERRIDE_DATA_PATH true` except one:
  dlt's `DuckLakeCredentials`/`ducklake` destination used for ingestion, which defaults
  `override_data_path` to `False`. Under ADR 0010's single static storage directory this never
  mattered - the attach path never changed between runs. Once ingestion attaches at a fresh
  `builds/<id>/storage` on every build, the catalog's last-recorded path is the previous build's
  *promoted* `current/storage`, which never matches; without the override DuckLake refuses the
  attach outright. Fixed by passing `override_data_path=True` to the `ducklake()` destination
  factory in `ingestion/pipeline.py`.
- A post-ingestion failure (dbt, validation, promotion) now leaves the staged build in place for
  both catalog modes, exactly as ADR 0007 already did for local builds: retrying acquisition and
  ingestion to re-test a dbt fix wastes real time when the raw archives never changed, and the
  staged directory is isolated, so leaving it costs nothing. The DuckLake orphan-file maintenance
  added alongside this fix (`ducklake_delete_orphaned_files`, wired into `checkpoint`) remains
  useful for reclaiming files from `retired/` generations over time, but is no longer load-bearing
  for crash safety - directory-level isolation now provides that directly.

## Consequences

- `data/ducklake/current/`, `retired/`, and `builds/` now behave identically in both catalog
  modes; only the metadata backing them (a local `catalog.duckdb` file vs. PostgreSQL) differs.
- A build's Parquet output is trustworthy to delete-on-sight exactly when `builds/<id>/` was never
  promoted - the same rule local mode has always used, and one that no longer requires querying
  the catalog first to be sure.
- `_check_space`'s `current_build_bytes` estimate, previously near-meaningless in PostgreSQL mode
  (it measured an almost-always-empty `current/`), now measures real, live data.
- `ingest`/`transform` standalone commands stage without promoting in PostgreSQL mode too,
  matching local mode; only `build` (and `promote`, once fixed - see known gaps) promotes.
- Host-run tooling and container-run tooling must not both write through the same PostgreSQL
  catalog using different absolute-path conventions for the *same* logical storage location in
  the same session sequence. `OVERRIDE_DATA_PATH true` lets a later attach succeed at a different
  path than the catalog last recorded, but doing so from a path convention (host, Windows) other
  than the one long-running writers use (container, Linux bind mount) risks exactly the kind of
  confusion this ADR exists to eliminate. Prefer `docker compose run --rm lakehouse <command>` for
  anything that writes, even for ad hoc maintenance.

## Known gaps (not addressed here)

`promote_command` (`imdb-lakehouse promote` / `make promote`) is still not PostgreSQL-aware: it
unconditionally calls `select_staged_build`/`promote_build`/`checkpoint_lakehouse` with the local
single-file-catalog assumptions baked in as if this ADR did not exist. In PostgreSQL mode it
currently only ever hits "No staged DuckLake build exists" - `build` already promotes internally,
so nothing currently depends on this path, but it should be brought in line with `transform`'s
fix in the same pass that revisits it.
