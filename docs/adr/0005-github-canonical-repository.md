# ADR 0005: Use GitHub as the canonical source repository

- Status: Accepted
- Date: 2026-08-15

## Context

The project needs one auditable source for code, CI, decisions, and synthetic test fixtures while
large IMDb archives and generated lakehouse data remain local and ignored.

## Decision

Use the GitHub repository as the canonical code and documentation source. Run locked, fixture-backed
quality gates for pushes and pull requests. Never commit IMDb source archives, generated catalogs,
Parquet data, local state, credentials, or machine-specific paths.

## Consequences

Changes are reviewable and CI-reproducible without redistributing licensed data. Full-data smoke
validation remains an explicit local operation whose summarized evidence may be documented, while
the underlying data stays outside Git.
