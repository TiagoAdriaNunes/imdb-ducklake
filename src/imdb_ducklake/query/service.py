"""Read-only DuckLake connection and mart queries for the Shiny application."""

from __future__ import annotations

from pathlib import Path

import duckdb

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError

ATTACH_ALIAS = "imdb_lake"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    connection: duckdb.DuckDBPyConnection, query: str, limit: int = 50
) -> duckdb.DuckDBPyRelation:
    """Search mart_title_search by title substring, ordered by vote count descending."""
    if query.strip():
        return connection.sql(
            "select * from marts.mart_title_search "
            "where primary_title ilike '%' || ? || '%' "
            "order by num_votes desc nulls last "
            "limit ?",
            params=[query, limit],
        )
    return connection.sql(
        "select * from marts.mart_title_search order by num_votes desc nulls last limit ?",
        params=[limit],
    )
