FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY dbt/ dbt/
COPY apps/ apps/
COPY docker/entrypoint.sh /usr/local/bin/imdb-lakehouse-entrypoint
COPY README.md LICENSE ./

RUN uv sync --locked --no-dev --extra app

# Bake the extensions into the image so nothing is fetched at runtime (httpfs + aws: remote storage).
RUN uv run --no-sync python -c "import duckdb; con = duckdb.connect(':memory:'); con.execute('INSTALL ducklake; INSTALL postgres; INSTALL httpfs; INSTALL aws;')" \
    && chmod +x /usr/local/bin/imdb-lakehouse-entrypoint

ENV IMDB_LAKEHOUSE_DATA_DIR=/data

ENTRYPOINT ["imdb-lakehouse-entrypoint"]
CMD ["build"]
