# ADR 0002: Make dbt the transformation owner

- Status: Accepted
- Date: 2026-08-15

## Context

IMDb source strings require NULL normalization, casting, array and JSON parsing, relationship
checks, and documented analytical grains. Splitting these rules between Python and SQL would make
lineage and test ownership ambiguous.

## Decision

Keep all analytical SQL, typing, model documentation, and data-quality rules in the committed dbt
project. Python invokes `dbt build` with explicit catalog, storage, controller, project, and profile
paths but does not reproduce transformation logic.

## Consequences

dbt's graph is the authoritative transformation lineage. Staging and lightweight intermediate
models are views; Shiny-facing marts are DuckLake tables. A dbt test failure blocks promotion.
