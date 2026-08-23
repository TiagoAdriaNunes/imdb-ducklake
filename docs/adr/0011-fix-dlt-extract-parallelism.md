# ADR 0011: Fix dlt extract-stage parallelism and expose ingestion tuning knobs

- Status: Accepted
- Date: 2026-08-23
- Amends: [ADR 0009](0009-bounded-ingestion-parallelism.md)

## Context

ADR 0009 set `ingestion/resources.py::read_csv_duckdb_arrow` as a
`dlt.transformer(parallelized=True)` piped from a `dlt.sources.filesystem.filesystem()` resource
(`files | read_csv_duckdb_arrow(...)`), and `ingestion/pipeline.py` set `extract.workers: 2`,
believing this delivered concurrent extraction across the seven IMDb archives. That belief was
based on a benchmark of `_read_csv_duckdb_arrow` called directly from a manual
`ThreadPoolExecutor(2)` - it proved the function is thread-safe with a per-call DuckDB connection,
but never verified that dlt's actual `pipeline.run()` / `PipeIterator` scheduling achieved the same
concurrency for this specific resource shape.

It didn't. Reproduced directly against the real `ingestion/resources.py` code (two real archives,
`title.crew.tsv.gz` and `title.episode.tsv.gz`, through `build_ingestion_resources()` and
`pipeline.extract()`): `title_crew` ran to near-completion (~2500 of its batches) before
`title_episode` pulled its first item at all. Total wall time for the two files: 64.34s, identical
in shape to fully serial extraction. This matches what was observed live in a real
`docker-ingest` run: only one archive's progress bar advanced at a time despite
`extract.workers: 2`.

Root cause, isolated with three progressively narrower reproductions:

1. Two plain top-level `@dlt.resource(parallelized=True)` generators (synthetic, no filesystem
   piping) genuinely overlap: both interleave item-for-item, 5.75s wall time for ten total
   `sleep(1)` calls that would take ~10s serial.
2. The same two real archives, restructured as plain top-level parallelized resources instead of
   `filesystem() | transformer(parallelized=True)`, also genuinely overlap (both start within
   0.1s of each other and progress in lockstep throughout).
3. The original `filesystem() | transformer(parallelized=True)` fork pattern does not, regardless
   of `extract.workers`.

dlt's `PipeIterator` only adds a *forked* pipe's wrapped, poolable generator to its round-robin
source list after the parent resource's single item has been forked into it mid-run. A top-level
parallelized resource's wrapped generator is present in that list from the very first iteration.
That timing difference is enough to starve round-robin scheduling across sibling archives in the
fork case - the newly-discovered generator dominates the scheduler's attention while the other
archives' root file-listing pipes are still waiting their turn.

Separately, `chunk_size` (the Arrow batch size handed from DuckDB's CSV reader into dlt's
normalize stage, `relation.to_arrow_reader(batch_size=chunk_size)`) was hardcoded at 5,000 rows
and never exposed to configuration. Benchmarked on `title.crew.tsv.gz` alone (12.7M rows,
single-threaded extract): 5,000 rows/batch took 20.09s, 50,000 took 11.00s, 200,000 took 9.53s -
smaller batches cost real, measurable overhead from more Python-level `yield`/normalize-item
processing per row, with diminishing returns past roughly 50,000.

## Decision

- `ingestion/resources.py::_raw_resource` no longer pipes a `filesystem()` source into a
  `dlt.transformer(parallelized=True)`. Since each archive's exact local path is already known
  (`VerifiedArtifact.path` - the file was already downloaded and verified), it builds one plain
  top-level `@dlt.resource(parallelized=True)` generator per artifact directly. `filesystem()` and
  `FileItemDict` are no longer used; `_read_csv_duckdb_arrow` now takes one resolved `Path` instead
  of an `Iterable[FileItemDict]`.
- `chunk_size`'s default rises from 5,000 to 50,000 rows per Arrow batch
  (`ingestion/pipeline.py`, `ingestion/resources.py`).
- Both extract/normalize/load worker count and Arrow batch size are now configurable instead of
  hardcoded, following the same `Settings`-driven pattern as the rest of the application:
  `IMDB_DUCKLAKE_INGEST_WORKERS` (default `2`, unchanged from ADR 0009) and
  `IMDB_DUCKLAKE_INGEST_CHUNK_SIZE` (default `50000`), threaded through
  `Settings.ingest_workers` / `Settings.ingest_chunk_size` into
  `ingest_snapshot(workers=..., chunk_size=...)`. Both are exposed in `.env.example` and
  `compose.yaml`, matching `IMDB_DUCKLAKE_DBT_THREADS`/`IMDB_DUCKLAKE_QUERY_THREADS`.

Verified: the full unit suite, `mypy`, and `ruff` pass unchanged (`build_ingestion_resources`'s
public contract - one resource per artifact, plus `ingestion_files` - is unchanged; only its
internal pipe shape changed). Re-running the same two-archive reproduction against the fixed code
(through the real `build_ingestion_resources()`, not a synthetic stand-in) shows both resources
starting within 0.1s of each other and progressing in lockstep: 64.34s -> 24.98s wall time for the
same two files. A real `docker-ingest` run against all seven archives (rebuilt image, so the fix
was actually running) shows all seven progress bars advancing concurrently rather than one at a
time.

## Consequences

- Real ingestion wall-clock time drops meaningfully, from both genuine cross-archive concurrency
  and fewer/larger normalize-stage batches. No change to what lands in the raw schema: every
  source row is still loaded exactly once, per-table schema contracts are still frozen (ADR 0001),
  the raw layer remains lossless.
- `ingestion/resources.py` no longer depends on `dlt.sources.filesystem`; one fewer moving part
  between "verified local file" and "read it."
- ADR 0009's benchmark numbers (extraction time for the raw function under manual threading) are
  still accurate as a description of `_read_csv_duckdb_arrow`'s thread-safety; its implication that
  the *pipeline* achieved that concurrency was not correct, and this ADR's numbers supersede it for
  end-to-end extract timing.
- `IMDB_DUCKLAKE_INGEST_WORKERS` and `IMDB_DUCKLAKE_INGEST_CHUNK_SIZE` are new tunables an operator
  can raise on a larger machine or lower under memory pressure, without a code change.
