"""Read-only DuckLake connection and mart queries for the Shiny application."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Literal

import duckdb
from jinja2 import Template

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError

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


def attach_sql(catalog_path: Path, storage_dir: Path) -> str:
    """Build the read-only ATTACH statements for a DuckLake catalog/storage pair."""
    catalog = _sql_string(f"ducklake:{catalog_path.as_posix()}")
    storage = _sql_string(storage_dir.as_posix())
    return (
        "INSTALL ducklake;\n"
        "LOAD ducklake;\n"
        f"ATTACH {catalog} AS {ATTACH_ALIAS} "
        f"(DATA_PATH {storage}, OVERRIDE_DATA_PATH true, READ_ONLY);\n"
        f"USE {ATTACH_ALIAS};\n"
    )


def connect_readonly(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Attach read-only to the current promoted DuckLake build."""
    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    if not catalog_path.is_file():
        raise NoPromotedBuildError(f"No promoted build found at {catalog_path}")
    connection = duckdb.connect(":memory:")
    for statement in attach_sql(catalog_path, storage_dir).strip().split(";"):
        if statement.strip():
            connection.execute(statement)
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
