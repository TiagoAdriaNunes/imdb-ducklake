"""Application configuration and repository-relative path resolution."""

from dataclasses import dataclass
from os import environ
from pathlib import Path

from imdb_ducklake.exceptions import ConfigurationError


def find_repository_root(start: Path | None = None) -> Path:
    """Find the nearest parent containing the project metadata."""
    candidate = (start or Path.cwd()).resolve()
    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    raise ConfigurationError(f"Could not find pyproject.toml from {candidate}")


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved application settings, created once by the CLI."""

    repository_root: Path
    data_dir: Path
    log_level: str = "INFO"
    log_format: str = "console"
    progress_interval_seconds: float = 10.0
    catalog_url: str | None = None
    ingest_workers: int = 2
    ingest_chunk_size: int = 50_000

    @classmethod
    def load(
        cls,
        *,
        repository_root: Path | None = None,
        data_dir: Path | None = None,
        log_level: str | None = None,
        log_format: str | None = None,
        progress_interval_seconds: float | None = None,
        catalog_url: str | None = None,
        ingest_workers: int | None = None,
        ingest_chunk_size: int | None = None,
    ) -> "Settings":
        root = find_repository_root(repository_root)
        configured_data = data_dir or Path(environ.get("IMDB_LAKEHOUSE_DATA_DIR", "data"))
        resolved_data = configured_data if configured_data.is_absolute() else root / configured_data
        configured_level = (log_level or environ.get("IMDB_LAKEHOUSE_LOG_LEVEL", "INFO")).upper()
        if configured_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError(f"Unsupported log level: {configured_level}")
        configured_format = (
            log_format or environ.get("IMDB_LAKEHOUSE_LOG_FORMAT", "console")
        ).lower()
        if configured_format not in {"console", "json"}:
            raise ConfigurationError(f"Unsupported log format: {configured_format}")
        configured_interval = progress_interval_seconds
        if configured_interval is None:
            raw_interval = environ.get("IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS", "10")
            try:
                configured_interval = float(raw_interval)
            except ValueError as error:
                raise ConfigurationError(
                    "IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS must be a number"
                ) from error
        if configured_interval <= 0:
            raise ConfigurationError("Progress interval must be greater than zero")
        configured_workers = ingest_workers
        if configured_workers is None:
            raw_workers = environ.get("IMDB_DUCKLAKE_INGEST_WORKERS", "2")
            try:
                configured_workers = int(raw_workers)
            except ValueError as error:
                raise ConfigurationError(
                    "IMDB_DUCKLAKE_INGEST_WORKERS must be an integer"
                ) from error
        if configured_workers <= 0:
            raise ConfigurationError("Ingest workers must be greater than zero")
        configured_chunk_size = ingest_chunk_size
        if configured_chunk_size is None:
            raw_chunk_size = environ.get("IMDB_DUCKLAKE_INGEST_CHUNK_SIZE", "50000")
            try:
                configured_chunk_size = int(raw_chunk_size)
            except ValueError as error:
                raise ConfigurationError(
                    "IMDB_DUCKLAKE_INGEST_CHUNK_SIZE must be an integer"
                ) from error
        if configured_chunk_size <= 0:
            raise ConfigurationError("Ingest chunk size must be greater than zero")
        return cls(
            repository_root=root,
            data_dir=resolved_data.resolve(),
            log_level=configured_level,
            log_format=configured_format,
            progress_interval_seconds=configured_interval,
            catalog_url=catalog_url or environ.get("IMDB_DUCKLAKE_CATALOG_URL"),
            ingest_workers=configured_workers,
            ingest_chunk_size=configured_chunk_size,
        )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.raw_dir / "manifest.json"

    @property
    def lakehouse_dir(self) -> Path:
        return self.data_dir / "ducklake"

    @property
    def current_dir(self) -> Path:
        return self.lakehouse_dir / "current"

    @property
    def dlt_pipelines_dir(self) -> Path:
        return self.data_dir / ".dlt" / "pipelines"

    @property
    def dbt_project_dir(self) -> Path:
        return self.repository_root / "dbt"

    @property
    def dbt_state_dir(self) -> Path:
        return self.data_dir / ".dbt"
