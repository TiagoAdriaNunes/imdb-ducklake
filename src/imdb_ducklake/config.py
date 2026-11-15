"""Application configuration and repository-relative path resolution."""

import re
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


_ENVIRONMENTS = {"local", "docker", "docker-cloud"}
_BOOLEAN_VALUES = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
# IMDB_DUCKLAKE_REMOTE_STORAGE: a Hugging Face Dataset repo (ADR 0013) or a Backblaze B2 bucket
# (ADR 0017), named by the same .env values the upload targets use.
_REMOTE_STORAGES = {"hf", "b2"}
# s3.<region>.backblazeb2.com
_S3_ENDPOINT_REGION = re.compile(r"^s3\.([a-z0-9-]+)\.")


def _env_or_none(name: str) -> str | None:
    """`environ.get`, treating an empty string (e.g. compose's `${VAR:-}`) as unset."""
    return environ.get(name) or None


def _default_docker_catalog_url() -> str:
    """Same PostgreSQL default as `compose.yaml`, addressed from the host (not a container)."""
    user = environ.get("POSTGRES_USER", "imdb")
    password = environ.get("POSTGRES_PASSWORD", "imdb-local-dev")
    port = environ.get("POSTGRES_PORT", "5432")
    database = environ.get("POSTGRES_DB", "ducklake_catalog")
    return f"postgresql://{user}:{password}@localhost:{port}/{database}"


def _require_env(name: str, remote_storage: str, example: str) -> str:
    value = _env_or_none(name)
    if value is None:
        raise ConfigurationError(
            f"IMDB_DUCKLAKE_REMOTE_STORAGE={remote_storage} requires {name} (e.g. {example})"
        )
    return value.strip()


def _resolve_remote_storage(remote_storage: str) -> tuple[str, str | None]:
    """Return (storage_url, s3_endpoint) for IMDB_DUCKLAKE_REMOTE_STORAGE."""
    if remote_storage == "hf":
        repo = _require_env("HF_DATASET_REPO", remote_storage, "<you>/<name>")
        # Always a Dataset repo: DuckDB can't attach hf://buckets/... (ADR 0013).
        return f"hf://datasets/{repo}", None
    bucket = _require_env("B2_BUCKET", remote_storage, "imdb-ducklake")
    endpoint = _require_env("BLAZE_ENDPOINT", remote_storage, "s3.us-east-005.backblazeb2.com")
    endpoint = endpoint.removeprefix("https://").rstrip("/")
    if not _S3_ENDPOINT_REGION.match(endpoint):
        raise ConfigurationError(
            f"BLAZE_ENDPOINT must look like s3.<region>.backblazeb2.com, got {endpoint!r}"
        )
    return f"s3://{bucket}", endpoint


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved application settings, created once by the CLI."""

    repository_root: Path
    data_dir: Path
    log_level: str = "INFO"
    log_format: str = "console"
    progress_interval_seconds: float = 10.0
    # One of "local" (no shared catalog), "docker" (shared PostgreSQL catalog, local storage), or
    # "docker-cloud" (shared catalog, remote storage required). The single source of truth for
    # catalog_url/storage_url below - see IMDB_DUCKLAKE_ENV.
    environment: str = "local"
    catalog_url: str | None = None
    # Overrides current/storage as the app's read-only DATA_PATH: hf://datasets/<repo> or
    # s3://<bucket>, resolved from IMDB_DUCKLAKE_REMOTE_STORAGE (ADR 0013, 0017).
    storage_url: str | None = None
    # B2's S3 host for s3:// storage, e.g. s3.us-east-005.backblazeb2.com; the keys come from
    # AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.
    s3_endpoint: str | None = None
    ingest_workers: int = 2
    # Per-build scratch space: dbt's controller DuckDB file (DuckDB spills large sorts/joins into a
    # `.tmp` dir next to it) and dlt's load packages. Defaults to data_dir. `build` empties it
    # before and after each run.
    scratch_dir: Path | None = None
    # The app copies the `app` schema into memory at startup and then queries it locally, so remote
    # storage costs one download instead of seconds per interaction (ADR 0016).
    app_preload: bool = True

    @classmethod
    def load(
        cls,
        *,
        repository_root: Path | None = None,
        data_dir: Path | None = None,
        log_level: str | None = None,
        log_format: str | None = None,
        progress_interval_seconds: float | None = None,
        environment: str | None = None,
        catalog_url: str | None = None,
        remote_storage: str | None = None,
        ingest_workers: int | None = None,
        app_preload: bool | None = None,
    ) -> "Settings":
        root = find_repository_root(repository_root)
        raw_scratch = _env_or_none("IMDB_DUCKLAKE_SCRATCH_DIR")
        configured_scratch = Path(raw_scratch) if raw_scratch else None
        if configured_scratch is not None and not configured_scratch.is_absolute():
            configured_scratch = root / configured_scratch
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

        configured_environment = (environment or environ.get("IMDB_DUCKLAKE_ENV", "local")).lower()
        if configured_environment not in _ENVIRONMENTS:
            raise ConfigurationError(
                f"Unsupported IMDB_DUCKLAKE_ENV: {configured_environment!r} "
                f"(expected one of {sorted(_ENVIRONMENTS)})"
            )
        if configured_environment == "local":
            if catalog_url is None and _env_or_none("IMDB_DUCKLAKE_CATALOG_URL") is not None:
                raise ConfigurationError(
                    "IMDB_DUCKLAKE_CATALOG_URL is set but IMDB_DUCKLAKE_ENV is 'local' - "
                    "set IMDB_DUCKLAKE_ENV=docker or docker-cloud to use a shared catalog"
                )
            resolved_catalog_url = catalog_url
        else:
            resolved_catalog_url = (
                catalog_url
                or _env_or_none("IMDB_DUCKLAKE_CATALOG_URL")
                or _default_docker_catalog_url()
            )

        # The selector only applies under docker-cloud, so both remotes can stay configured.
        resolved_storage_url = resolved_s3_endpoint = None
        if configured_environment == "docker-cloud":
            raw_remote = remote_storage or _env_or_none("IMDB_DUCKLAKE_REMOTE_STORAGE")
            configured_remote = raw_remote.strip().lower() if raw_remote else None
            if configured_remote is None or configured_remote not in _REMOTE_STORAGES:
                raise ConfigurationError(
                    "IMDB_DUCKLAKE_ENV=docker-cloud requires IMDB_DUCKLAKE_REMOTE_STORAGE=hf or b2"
                    + (f", got {configured_remote!r}" if configured_remote else "")
                )
            resolved_storage_url, resolved_s3_endpoint = _resolve_remote_storage(configured_remote)

        configured_preload = app_preload
        if configured_preload is None:
            raw_preload = environ.get("IMDB_DUCKLAKE_APP_PRELOAD", "true").strip().lower()
            if raw_preload not in _BOOLEAN_VALUES:
                raise ConfigurationError("IMDB_DUCKLAKE_APP_PRELOAD must be true or false")
            configured_preload = _BOOLEAN_VALUES[raw_preload]

        return cls(
            repository_root=root,
            data_dir=resolved_data.resolve(),
            log_level=configured_level,
            log_format=configured_format,
            progress_interval_seconds=configured_interval,
            environment=configured_environment,
            catalog_url=resolved_catalog_url,
            storage_url=resolved_storage_url,
            s3_endpoint=resolved_s3_endpoint,
            ingest_workers=configured_workers,
            scratch_dir=configured_scratch,
            app_preload=configured_preload,
        )

    @property
    def s3_region(self) -> str | None:
        """Signing region, taken from the endpoint (s3.<region>.<host>)."""
        if self.s3_endpoint is None:
            return None
        match = _S3_ENDPOINT_REGION.match(self.s3_endpoint)
        return match.group(1) if match else None

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
        return (self.scratch_dir or self.data_dir) / ".dlt" / "pipelines"

    @property
    def dbt_project_dir(self) -> Path:
        return self.repository_root / "dbt"

    @property
    def dbt_state_dir(self) -> Path:
        return (self.scratch_dir or self.data_dir) / ".dbt"
