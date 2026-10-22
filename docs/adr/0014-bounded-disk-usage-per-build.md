# ADR 0014: Bound disk usage per build

- Status: Accepted
- Date: 2026-10-18
- Amends: [ADR 0012](0012-restore-build-staging-for-postgresql-catalog.md)

## Context

`data/` grew to 62 GB for 7.5 GB of live data: PostgreSQL mode never pruned `retired/` or failed
builds, snapshots were never expired, and the free-space gate counted `current/` and downloaded
archives that were already on disk.

## Decision

- Checkpoint keeps only the latest snapshot and deletes the files it no longer references.
- In PostgreSQL mode, retired and unpromoted builds are removed after a successful promotion and
  checkpoint, never before.
- The free-space gate counts only new writes: downloads, the estimated new build, and a reserve.
- `build` empties the scratch directory (dbt spill, dlt load packages) before it starts and when it
  ends, even on failure. A killed build's leftovers go at the next build.
- In Docker, scratch is `data/.scratch` on the host disk, not a Docker volume: a volume lives in
  Docker's disk file, which grows with each spill and never shrinks after deletes.

## Consequences

- No time travel to earlier snapshots.
- No directory rollback in PostgreSQL mode (it couldn't roll back PostgreSQL metadata anyway).
