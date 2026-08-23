"""Shared PostgreSQL-backed DuckLake catalog configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from imdb_ducklake.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class CatalogTarget:
    """One DuckLake catalog expressed for dlt and DuckDB consumers."""

    url: str
    storage_dir: Path
    metadata_schema: str = "imdb_lake"

    @property
    def storage_url(self) -> str:
        return self.storage_dir.resolve().as_uri()

    @property
    def duckdb_metadata_path(self) -> str:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ConfigurationError("DuckLake catalog URL must use postgres or postgresql")
        if not parsed.hostname or not parsed.path.lstrip("/"):
            raise ConfigurationError("DuckLake catalog URL requires a host and database")
        values = [
            f"dbname={_quote_parameter(unquote(parsed.path.lstrip('/')))}",
            f"host={_quote_parameter(parsed.hostname)}",
        ]
        if parsed.port:
            values.append(f"port={parsed.port}")
        if parsed.username:
            values.append(f"user={_quote_parameter(unquote(parsed.username))}")
        if parsed.password:
            values.append(f"password={_quote_parameter(unquote(parsed.password))}")
        return "postgres:" + " ".join(values)

    @property
    def safe_identity(self) -> str:
        """Credential-free catalog identity suitable for logs and errors."""
        parsed = urlsplit(self.url)
        host = parsed.hostname or "unknown"
        port = f":{parsed.port}" if parsed.port else ""
        database = unquote(parsed.path.lstrip("/")) or "unknown"
        return f"postgresql://{host}{port}/{database}#{self.metadata_schema}"


def _quote_parameter(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
