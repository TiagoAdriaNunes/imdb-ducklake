# ADR 0008: Schema compatibility policy

- Status: Accepted
- Date: 2026-08-16

## Context

The pipeline has three schema layers with different stability expectations: raw (`imdb_raw`,
mirrors IMDb's published TSV headers exactly), staging/intermediate (internal, dbt-owned typed
views), and marts (the four consumer-facing relations the future Shiny app queries). Until now
none of these had an explicit compatibility policy or an enforcement mechanism — a silent column
rename or type change in a mart could reach a promoted build undetected.

## Decision

### Raw layer

Governed by IMDb's own header contract, already enforced at acquisition: `acquisition/downloader.py`
rejects any archive whose header doesn't exactly match `datasets.py`'s registered `DatasetSpec.headers`
(see `_validate_archive`). A source header change is a hard acquisition failure, not a silent
schema drift — the build never starts.

### Staging and intermediate layers

Additive changes (a new column, a new model) are always safe and require no process beyond the
normal doc-audit expectation (every column documented with a grain-stating description, per
`docs/adr/0007-local-reliability-policy.md`'s general documentation bar). These layers are internal
to dbt — nothing outside `dbt/models/` references them directly — so renames and type changes are
also low-risk as long as every downstream model that consumes the change is updated in the same
commit; `dbt build`'s dependency graph will fail loudly on a broken `ref()` either way.

### Marts (`mart_title_search`, `mart_genre_year_summary`, `mart_person_filmography`,
`mart_series_episodes`)

These are the actual compatibility boundary: the future Shiny app, and any other consumer, reads
only these four relations. All four now declare `config: {contract: {enforced: true}}` with an
explicit `data_type` on every column (`dbt/models/marts/marts.yml`). This makes the contract
mechanical, not just documented:

- **Additive** (a new column): safe, contract still enforces the existing columns unchanged.
- **Breaking** (a column renamed, removed, or retyped): `dbt build` fails immediately with a
  contract-mismatch error, before validation or promotion ever run. The build simply cannot
  produce a promotable candidate with a broken mart contract.

A breaking mart change is deliberate work, not an accident: update `marts.yml`'s contract, update
every consumer, and bump the version per the branch-per-version convention already in use
(`feature/<topic>-v<next-version>`, recorded in `CLAUDE.md`) so the change is traceable to a
specific build.

### Deprecation

There's exactly one consumer today, so deprecation is currently a documentation step, not a
compatibility shim: mark the column's description as deprecated and note its replacement, keep it
for at least one full version bump so any local script depending on it has a build to adapt
against, then remove it (a breaking change, per above) in a later version.

## Consequences

Mart schema drift is now caught by `dbt build` itself instead of relying on manual review or a
downstream consumer noticing. The cost is that every mart column addition requires touching
`marts.yml`'s `data_type` alongside the SQL — a small, deliberate friction that matches the point
of a contract. Staging/intermediate layers stay lightweight since dbt's own dependency graph
already provides their safety net; adding contracts there would be enforcement with no real
consumer to protect.
