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

## Consequences

- No time travel to earlier snapshots.
- No directory rollback in PostgreSQL mode (it couldn't roll back PostgreSQL metadata anyway).
