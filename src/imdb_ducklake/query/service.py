"""Read-only DuckLake connection and app-schema queries for the Shiny application."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Literal

import duckdb
from jinja2 import Template
from loguru import logger

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import ConfigurationError, NoPromotedBuildError
from imdb_ducklake.lakehouse.catalog import CatalogTarget
from imdb_ducklake.lakehouse.validation import REQUIRED_RELATIONS

ATTACH_ALIAS = "imdb_lake"
APP_SCHEMA = "app"


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


def _remote_secret_sql(storage_url: str, s3_endpoint: str | None, s3_region: str | None) -> str:
    """CREATE SECRET for a remote DATA_PATH. Credentials come from the environment, never SQL."""
    if storage_url.startswith("hf://datasets/"):
        # HF_TOKEN, or the local `hf auth login` cache (ADR 0013).
        return "CREATE SECRET hf_token (TYPE huggingface, PROVIDER credential_chain);\n"
    if storage_url.startswith("s3://"):
        if s3_endpoint is None or s3_region is None:
            raise ConfigurationError("s3:// storage needs an endpoint and region (ADR 0017)")
        # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, against e.g. Backblaze B2 (ADR 0017).
        return (
            "CREATE SECRET s3_storage (TYPE s3, PROVIDER credential_chain, CHAIN 'env', "
            f"ENDPOINT {_sql_string(s3_endpoint)}, REGION {_sql_string(s3_region)});\n"
        )
    raise ConfigurationError(f"Unsupported remote storage: {storage_url!r}")


def attach_sql(
    metadata_path: Path | str,
    storage_location: Path | str,
    *,
    metadata_schema: str = "main",
    s3_endpoint: str | None = None,
    s3_region: str | None = None,
) -> str:
    """Build the read-only ATTACH statements for a DuckLake catalog/storage pair.

    ``storage_location`` is a local directory, or a remote DATA_PATH: ``hf://datasets/<repo>``
    (never ``hf://buckets/...``, ADR 0013) or ``s3://<bucket>`` with ``s3_endpoint`` and
    ``s3_region`` (ADR 0017). A remote location also emits the matching credential secret.
    """
    metadata = metadata_path.as_posix() if isinstance(metadata_path, Path) else metadata_path
    catalog = _sql_string(f"ducklake:{metadata}")
    if isinstance(storage_location, Path):
        storage = _sql_string(storage_location.resolve().as_posix())
        secret_sql = ""
    else:
        storage = _sql_string(storage_location)
        secret_sql = _remote_secret_sql(storage_location, s3_endpoint, s3_region)
    schema = _sql_string(metadata_schema)
    return (
        "INSTALL ducklake;\n"
        "LOAD ducklake;\n"
        f"{secret_sql}"
        f"ATTACH {catalog} AS {ATTACH_ALIAS} "
        f"(DATA_PATH {storage}, METADATA_SCHEMA {schema}, "
        "OVERRIDE_DATA_PATH true, READ_ONLY);\n"
        f"USE {ATTACH_ALIAS};\n"
    )


def configured_attach_sql(settings: Settings) -> str:
    """Resolve the configured shared catalog or the local promoted build."""
    if settings.catalog_url is not None:
        target = CatalogTarget(settings.catalog_url, settings.current_dir / "storage")
        storage_location: Path | str = settings.storage_url or target.storage_dir
        if isinstance(storage_location, Path) and not storage_location.is_dir():
            raise NoPromotedBuildError(
                f"No promoted build found in the shared DuckLake catalog: {storage_location} "
                f"({target.safe_identity})"
            )
        return attach_sql(
            target.duckdb_metadata_path,
            storage_location,
            metadata_schema=target.metadata_schema,
            s3_endpoint=settings.s3_endpoint,
            s3_region=settings.s3_region,
        )

    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    if not catalog_path.is_file() or not storage_dir.is_dir():
        raise NoPromotedBuildError(f"No promoted build found at {catalog_path}")
    return attach_sql(catalog_path, storage_dir)


def connect_readonly(
    settings: Settings, *, preload: bool | None = None
) -> duckdb.DuckDBPyConnection:
    """Attach read-only to the configured shared catalog or local promoted build.

    With ``preload`` (default ``settings.app_preload``) the app tables are copied into memory and
    the lake is detached, so later queries never touch the catalog or (remote) storage.
    """
    sql = configured_attach_sql(settings)
    connection = duckdb.connect(":memory:")
    try:
        for statement in sql.strip().split(";"):
            if statement.strip():
                connection.execute(statement)
        if settings.app_preload if preload is None else preload:
            _preload_app_schema(connection)
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


def _preload_app_schema(connection: duckdb.DuckDBPyConnection) -> None:
    started = time.perf_counter()
    tables = sorted(REQUIRED_RELATIONS[APP_SCHEMA])
    connection.execute(f"CREATE SCHEMA memory.{APP_SCHEMA}")
    for table in tables:
        connection.execute(
            f"CREATE TABLE memory.{APP_SCHEMA}.{table} AS FROM {ATTACH_ALIAS}.{APP_SCHEMA}.{table}"
        )
    connection.execute("USE memory")
    connection.execute(f"DETACH {ATTACH_ALIAS}")
    logger.info(
        "App tables preloaded into memory",
        event_code="app_preload_completed",
        table_count=len(tables),
        duration_seconds=round(time.perf_counter() - started, 2),
    )


class SharedConnection:
    """One read-only connection per process, opened on first use; safe to call from threads."""

    def __init__(self, open_connection: Callable[[], duckdb.DuckDBPyConnection]) -> None:
        self._open_connection = open_connection
        self._lock = threading.Lock()
        self._connection: duckdb.DuckDBPyConnection | None = None

    def get(self) -> duckdb.DuckDBPyConnection:
        """Return the connection, opening (and preloading) it if this is the first call."""
        with self._lock:
            if self._connection is None:
                self._connection = self._open_connection()
            return self._connection


def search_titles(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    title_type: Literal["movie", "tvSeries"],
    limit: int = 50,
) -> duckdb.DuckDBPyRelation:
    """Search or rank one application title type from the app schema."""
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
