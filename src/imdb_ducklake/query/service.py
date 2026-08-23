"""Read-only DuckLake connection and mart queries for the Shiny application."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Literal

import duckdb
from jinja2 import Template

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.lakehouse.catalog import CatalogTarget

ATTACH_ALIAS = "imdb_lake"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_SQL_DIR = resources.files("imdb_ducklake.query") / "sql"
_TITLE_TEMPLATES = {
    ("movie", True): Template((_SQL_DIR / "search_movie_titles.sql").read_text(encoding="utf-8")),
    ("movie", False): Template((_SQL_DIR / "top_movie_titles.sql").read_text(encoding="utf-8")),
    ("tvSeries", True): Template((_SQL_DIR / "search_tv_series.sql").read_text(encoding="utf-8")),
    ("tvSeries", False): Template((_SQL_DIR / "top_tv_series.sql").read_text(encoding="utf-8")),
}
_TITLE_CAST_SQL = (_SQL_DIR / "title_cast.sql").read_text(encoding="utf-8")


def attach_sql(
    metadata_path: Path | str,
    storage_dir: Path,
    *,
    metadata_schema: str = "main",
) -> str:
    """Build the read-only ATTACH statements for a DuckLake catalog/storage pair."""
    metadata = metadata_path.as_posix() if isinstance(metadata_path, Path) else metadata_path
    catalog = _sql_string(f"ducklake:{metadata}")
    storage = _sql_string(storage_dir.resolve().as_posix())
    schema = _sql_string(metadata_schema)
    return (
        "INSTALL ducklake;\n"
        "LOAD ducklake;\n"
        f"ATTACH {catalog} AS {ATTACH_ALIAS} "
        f"(DATA_PATH {storage}, METADATA_SCHEMA {schema}, "
        "OVERRIDE_DATA_PATH true, READ_ONLY);\n"
        f"USE {ATTACH_ALIAS};\n"
    )


def configured_attach_sql(settings: Settings) -> str:
    """Resolve the configured shared catalog or the local promoted build."""
    if settings.catalog_url is not None:
        target = CatalogTarget(settings.catalog_url, settings.current_dir / "storage")
        if not target.storage_dir.is_dir():
            raise NoPromotedBuildError(
                f"No promoted build found in the shared DuckLake catalog: {target.storage_dir} "
                f"({target.safe_identity})"
            )
        return attach_sql(
            target.duckdb_metadata_path,
            target.storage_dir,
            metadata_schema=target.metadata_schema,
        )

    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    if not catalog_path.is_file() or not storage_dir.is_dir():
        raise NoPromotedBuildError(f"No promoted build found at {catalog_path}")
    return attach_sql(catalog_path, storage_dir)


def connect_readonly(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Attach read-only to the configured shared catalog or local promoted build."""
    sql = configured_attach_sql(settings)
    connection = duckdb.connect(":memory:")
    try:
        for statement in sql.strip().split(";"):
            if statement.strip():
                connection.execute(statement)
    except Exception as error:
        connection.close()
        identity = (
            CatalogTarget(
                settings.catalog_url,
                settings.current_dir / "storage",
            ).safe_identity
            if settings.catalog_url is not None
            else str(settings.current_dir / "catalog.duckdb")
        )
        raise NoPromotedBuildError(
            f"Could not attach the configured DuckLake catalog: {identity}"
        ) from error
    return connection


def search_titles(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    title_type: Literal["movie", "tvSeries"],
    limit: int = 50,
) -> duckdb.DuckDBPyRelation:
    """Search or rank one application title type from mart_title_search."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    sql = _TITLE_TEMPLATES[(title_type, bool(query.strip()))].render(query=query, limit=limit)
    return connection.sql(sql)


def get_title_cast(
    connection: duckdb.DuckDBPyConnection,
    tconst: str,
) -> duckdb.DuckDBPyRelation:
    """Return one ordered row per principal cast credit for a title."""
    return connection.sql(_TITLE_CAST_SQL, params={"tconst": tconst})
