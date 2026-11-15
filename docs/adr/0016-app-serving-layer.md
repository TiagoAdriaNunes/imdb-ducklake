# ADR 0016: Serve the app from a dbt `app` schema, preloaded into memory

- Status: Accepted
- Date: 2026-11-14
- Amends: [ADR 0013](0013-hf-dataset-repo-for-remote-storage.md)

## Context

With storage on Hugging Face or Backblaze B2, the app's queries took seconds to tens of seconds
(measured cold, real catalog):

| Query | Local | HF | B2 |
| --- | --- | --- | --- |
| movie search | 1.2s | 44s (418 requests) | 42s (412 requests, 326 MB) |
| cast for one title | 0.1s | 12s (7 requests) | 7.8s |

Two causes:

- Each remote range read costs 1-2s on HF and 0.25-0.9s on B2, whatever its size. A cast lookup
  is 3-4 sequential reads (footer, `tconst` column, remaining columns), so file layout can't fix it.
- `mart_title_search` holds all 12.8M titles in 2 files, and both files' catalog stats span every
  `title_type`, so DuckLake can't skip anything. The app only shows movies and TV series (8%).

## Decision

- A dbt `app` schema (`dbt/models/app/`) holds the only tables the app reads: `app_title_search`
  (movies and TV series, partitioned by `title_type`), `app_title_cast` (cast for those titles,
  sorted by `tconst`), `app_top_movies`, and `app_top_tv_series`. They only filter, project, and
  pre-format marts. `mart_title_cast`, `mart_top_movies`, and `mart_top_tv_series` moved there;
  `marts` keeps the complete, general-purpose tables.
- The `app` schema uses zstd and 32 MB target files, set as schema-scoped DuckLake options by a dbt
  pre-hook.
- At startup the app copies the `app` schema into an in-memory DuckDB database and detaches the
  lake (`query/service.py`). All sessions share that one connection. `IMDB_DUCKLAKE_APP_PRELOAD=false`
  queries storage directly instead.
- `make upload-dataset` and `make upload-b2` upload only `storage/app/`.

## Consequences

- Measured with `query/service.py` on app tables built from the real marts (163 MB), storage served
  with 500 ms per request: preload 5.4s (125 requests), then searches ~0.02s and cast lookups
  ~3 ms. Without preload: movie search 1.6s (8.2s on the old layout), cast lookup 1.5-2s.
  Locally, preload takes ~1s and a search without preload drops from 1.2s to 0.12s.
- Startup pays the load once per process; at the ~8 MB/s measured to B2 from a home connection
  that is ~20-30s. The app process uses up to about 1 GB more memory.
- `app_title_cast` builds from `mart_person_filmography` in ~10-19s instead of
  `mart_title_cast`'s ~104s, since it sorts 6.4M rows instead of 57.7M.
- Remote stores need a one-off cleanup of the old `marts/` prefix, which uploads no longer touch.
- Validation requires the `app` relations and reports row counts as `<schema>.<relation>`.
