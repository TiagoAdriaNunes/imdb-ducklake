# IMDb DuckLake

A reproducible local analytics lakehouse built from the official IMDb non-commercial datasets.

The project uses:

- **dlt** for lossless raw ingestion and load metadata
- **DuckDB and DuckLake** for query execution, cataloging, snapshots, and Parquet storage
- **dbt** for typed staging models, tests, and analytics-ready marts
- **Shiny for Python** in a later phase for interactive exploration

> IMDb permits these datasets for personal and non-commercial use. This repository contains code
> and miniature synthetic fixtures only; it does not redistribute IMDb data.

## Development

```powershell
uv sync
uv run imdb-lakehouse --help
uv run ruff check .
uv run pytest
```

## Data source

- [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/)
