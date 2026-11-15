# ADR 0017: Pick the app's remote storage, Hugging Face or Backblaze B2, with one setting

- Status: Accepted
- Date: 2026-11-14
- Amends: [ADR 0013](0013-hf-dataset-repo-for-remote-storage.md)

## Context

`make upload-b2` already published the `app/` tables to a private B2 bucket, but the app could
only read Hugging Face: `attach_sql` always created a Hugging Face secret. Switching meant editing
`IMDB_DUCKLAKE_STORAGE_URL` and commenting values in and out of `.env`.

## Decision

- `IMDB_DUCKLAKE_REMOTE_STORAGE=hf|b2` replaces `IMDB_DUCKLAKE_STORAGE_URL`. It builds the URL from
  the values the upload targets use: `hf://datasets/$HF_DATASET_REPO` or `s3://$B2_BUCKET`. It is
  required under `docker-cloud` and ignored otherwise, so both remotes can stay configured.
- `b2` creates an S3 secret against `BLAZE_ENDPOINT`, with the region taken from it
  (`s3.<region>.backblazeb2.com`), using `PROVIDER credential_chain, CHAIN 'env'`. The key comes
  from `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, which compose maps from `BLAZE_KEY_ID`/
  `BLAZE_TOKEN`, so it never appears in SQL.
- The image bakes the `httpfs` and `aws` extensions so the app fetches none at runtime.

## Consequences

- Switching storage means changing one value and recreating the app container. No new image.
- Both uploads use the same `app/` layout, so the PostgreSQL catalog's relative paths resolve on
  either remote.
- B2's S3 API needs an application key; the master key is rejected.
- The app gets whatever B2 key `.env` holds. A read-only key is safer than the upload key.
