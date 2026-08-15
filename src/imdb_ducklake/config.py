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

    @classmethod
    def load(
        cls,
        *,
        repository_root: Path | None = None,
        data_dir: Path | None = None,
        log_level: str | None = None,
    ) -> "Settings":
        root = find_repository_root(repository_root)
        configured_data = data_dir or Path(environ.get("IMDB_LAKEHOUSE_DATA_DIR", "data"))
        resolved_data = configured_data if configured_data.is_absolute() else root / configured_data
        configured_level = (log_level or environ.get("IMDB_LAKEHOUSE_LOG_LEVEL", "INFO")).upper()
        if configured_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError(f"Unsupported log level: {configured_level}")
        return cls(
            repository_root=root, data_dir=resolved_data.resolve(), log_level=configured_level
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
