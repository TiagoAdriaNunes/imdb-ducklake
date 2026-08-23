# ADR 0013: Use a Hugging Face Dataset repo, not a Bucket, for storage DuckLake reads directly

- Status: Accepted
- Date: 2026-08-23

## Context

The Shiny application reads marts through a DuckLake `ATTACH` (`query/service.py`), which needs a
`DATA_PATH` it can both list and read files under. We wanted an option to serve that `DATA_PATH`
from Hugging Face instead of a local directory, so the app could run without a local copy of the
lakehouse's Parquet output.

Hugging Face Buckets looked like the natural fit - it is HF's general-purpose object storage
product, and this repo already uploads to one (`make upload-bucket`) as a distribution artifact.

## Decision

DuckDB's `hf://` filesystem support only recognizes `hf://datasets/...` and `hf://spaces/...`.
Attaching (or even a bare `read_parquet`) against `hf://buckets/...` fails outright:

```
IO Error: Failed to parse: 'hf://buckets/<ns>/<name>/...'. Currently DuckDB only supports
querying datasets or spaces, so the url should start with 'hf://datasets' or 'hf://spaces'
```

This is a hard client-side restriction, not a permissions or path issue - there is no DuckDB
configuration that makes a Bucket URL attachable. A Hugging Face **Dataset** repo is required for
any storage DuckLake/DuckDB will read directly.

Verified end-to-end against a private Dataset repo (`TiagoAdriaNunes/imdb-ducklake`) with the
existing shared PostgreSQL catalog:

```sql
CREATE SECRET hf_token (TYPE huggingface, PROVIDER credential_chain);
ATTACH 'ducklake:postgres:dbname=...' AS imdb_lake
  (DATA_PATH 'hf://datasets/TiagoAdriaNunes/imdb-ducklake',
   METADATA_SCHEMA 'imdb_lake', OVERRIDE_DATA_PATH true, READ_ONLY);
```

`PROVIDER credential_chain` picks up the token from the local `hf auth login` cache (or `HF_TOKEN`
in the environment) with no further configuration. `OVERRIDE_DATA_PATH true` is required for the
same reason it is everywhere else in this codebase (see ADR 0012): the catalog's last-recorded
path never matches a freshly-attached remote location.

The repo is kept **private**. IMDb's non-commercial dataset terms restrict redistribution;
publishing the derived Parquet output as a public Hugging Face dataset would very likely violate
those terms regardless of DuckDB mechanics. A private repo read via an authenticated token keeps
the data scoped to the account that already has rights to it, the same way local disk or the
private Bucket did.

The publish-side layout matters too: the app's `configured_attach_sql` always expects `DATA_PATH`
to be the *parent* of `marts/` (mirroring local `current/storage/`, which also contains `raw/` and
`intermediate/` as siblings). Uploading only `marts/`'s *contents* to a repo's root - as
`make upload-bucket` currently does via `hf sync ... --include 'marts/*'` - drops that prefix, so
the repo must contain `marts/<table>__dbt_tmp/...`, not `<table>__dbt_tmp/...` at the root.

## Consequences

- Any future "read storage from Hugging Face" support must target `hf://datasets/...`, never
  `hf://buckets/...`.
- Buckets remain useful as plain file storage/transfer (what they were already used for before
  this ADR), just not as something DuckLake can attach to directly.
- `make upload-bucket` (`Makefile`) still uploads to a Bucket via `hf buckets sync` and is not yet
  updated to publish to the Dataset repo instead - it works for moving files around, but its
  output is not directly attachable the way a Dataset repo upload is. Bringing it in line (an
  `hf upload ... --type dataset` equivalent, preserving the `marts/` prefix) is a follow-up, not
  done as part of this ADR.
- The app itself (`query/service.py`) does not yet have a code path that attaches at an
  `hf://datasets/...` `DATA_PATH` - this ADR only records that the mechanism works and what it
  requires; wiring it into `Settings`/`configured_attach_sql` is separate, deliberately deferred
  work.
