# Full-data smoke report — 2026-08-15

This report records local validation of the complete IMDb non-commercial snapshot retained on
2026-08-15. The archives, catalogs, Parquet files, and source rows remain ignored local data and are
not part of the repository.

## Commands

```console
uv run pytest -m smoke tests/smoke/test_full_snapshot.py -q -s
uv run dbt build --project-dir dbt --profiles-dir dbt
uv run imdb-lakehouse promote --build-id 20260815T080036Z-bd40cace79aa
```

The smoke suite completed with four passing tests in 47.91 seconds. The retained dbt run artifact
records 23 successful models and 93 passing data tests.

## Verified source archives

The downloader's retained-artifact path re-read every gzip stream, checked the exact header, byte
size, and SHA-256 digest, and reconstructed seven typed artifacts without another HTTP request.

| Archive | Compressed bytes | SHA-256 |
| --- | ---: | --- |
| `title.akas.tsv.gz` | 510,512,701 | `20cd3908debbf25e42da50fc5b3d112e89ce39ca3f9b384f679590dc225128e3` |
| `title.basics.tsv.gz` | 225,506,758 | `6f99314e58cf54c168fb113ff293b59f0ecba276100ffa09981e89c51c0c68a1` |
| `title.crew.tsv.gz` | 82,694,702 | `dda99740c4deb4ed8e74acdc971d3e6d8e1c088b5d092f2f219b6f00a970838f` |
| `title.episode.tsv.gz` | 54,334,091 | `88c47026e376662e88514099166a3b46959e1e7ac382f2659331ffb1f174562b` |
| `title.principals.tsv.gz` | 778,757,964 | `b5ac6b99be8cd9f7babab8ccb108ebb7fbf64a23ce656c86b88fbe966c3e7354` |
| `title.ratings.tsv.gz` | 8,612,123 | `67c060b1019e50e856c95c2937f3511ea947ef911668408ec409cd3873898d91` |
| `name.basics.tsv.gz` | 308,159,447 | `a7930dfbee94f475dcab0948bcb213490f9b16c77d6296e4a0288edf7fedc9a5` |
| **Total** | **1,968,577,786** | — |

Range resume and fresh-download behavior remain covered by fault-injected automated downloader
tests. This smoke run exercised checksum-backed reuse: all local archives were accepted without a
network transfer.

## Raw ingestion and lineage

Every declared source column was present as `VARCHAR`, every raw table was non-empty, and every raw
row's `_dlt_load_id` resolved to the matching `raw.ingestion_files.table_name` record.

| Raw table | Rows |
| --- | ---: |
| `raw.title_akas` | 58,943,401 |
| `raw.title_basics` | 12,717,779 |
| `raw.title_crew` | 12,716,620 |
| `raw.title_episode` | 9,830,770 |
| `raw.title_principals` | 101,214,175 |
| `raw.title_ratings` | 1,705,427 |
| `raw.name_basics` | 15,573,835 |

## Catalog, dbt, and marts

Build `20260815T080036Z-bd40cace79aa` passed the fresh-process read-only validation gate, was
atomically promoted to `data/ducklake/current/`, and then passed a second fresh-process read-only
attachment from its promoted paths. All 31 required raw, staging, intermediate, and mart relations
were present before and after promotion.

| Mart | Rows |
| --- | ---: |
| `mart_title_search` | 12,717,779 |
| `mart_genre_year_summary` | 19,911 |
| `mart_person_filmography` | 101,207,138 |
| `mart_series_episodes` | 9,830,753 |

Representative title search, genre/year summary, people/credit, and series/episode queries each
returned rows through the marts without raw-table access.

## Disk usage

| Artifact group | Bytes |
| --- | ---: |
| Retained compressed archives | 1,968,577,786 |
| DuckLake catalog | 7,352,320 |
| DuckLake Parquet storage | 10,618,121,555 |
| Complete staged DuckLake build | 10,625,473,875 |

## Repository audit

The full archives and DuckLake build are excluded by Git ignore rules. The tracked tree contains no
IMDb production archive, generated catalog, Parquet file, local environment file, dlt secret, or
machine-specific data path. Only code, documentation, configuration without credentials, and
miniature synthetic fixtures are eligible for version control.
