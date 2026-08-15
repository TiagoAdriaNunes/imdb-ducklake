# ADR 0004: Refresh IMDb data as full snapshots

- Status: Accepted
- Date: 2026-08-15

## Context

IMDb publishes replacement TSV snapshots rather than an ordered change stream. Cursor-based
incremental ingestion could miss deletions and make source reconciliation difficult.

## Decision

Treat the seven verified archives as one acquisition snapshot. Load every raw table with replace
semantics, rebuild all dbt relations, validate the complete candidate, and promote it as a new
immutable build.

## Consequences

Refresh cost scales with the complete dataset, so free-space gates and bounded parsing are required.
In exchange, retries are idempotent, source-to-raw reconciliation is direct, deletions are honored,
and no partially refreshed lakehouse becomes current.
