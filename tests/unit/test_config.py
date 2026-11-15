from pathlib import Path

import pytest

from imdb_ducklake.config import Settings, find_repository_root
from imdb_ducklake.exceptions import ConfigurationError


def test_finds_repository_root_from_nested_directory(tmp_path) -> None:
    root = tmp_path / "project"
    nested = root / "one" / "two"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    assert find_repository_root(nested) == root.resolve()


def test_repository_root_must_contain_project_metadata(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda _path: False)

    with pytest.raises(ConfigurationError, match=r"Could not find pyproject\.toml"):
        find_repository_root(Path.cwd())


def test_settings_resolve_environment_values_from_root(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_LAKEHOUSE_DATA_DIR", "local-data")
    monkeypatch.setenv("IMDB_LAKEHOUSE_LOG_LEVEL", "debug")
    monkeypatch.setenv("IMDB_LAKEHOUSE_LOG_FORMAT", "json")
    monkeypatch.setenv("IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("IMDB_DUCKLAKE_ENV", "docker")
    monkeypatch.setenv(
        "IMDB_DUCKLAKE_CATALOG_URL",
        "postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )

    settings = Settings.load(repository_root=root)

    assert settings.repository_root == root.resolve()
    assert settings.data_dir == (root / "local-data").resolve()
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"
    assert settings.progress_interval_seconds == 15.0
    assert settings.raw_dir == settings.data_dir / "raw"
    assert settings.manifest_path == settings.raw_dir / "manifest.json"
    assert settings.lakehouse_dir == settings.data_dir / "ducklake"
    assert settings.current_dir == settings.lakehouse_dir / "current"
    assert settings.dlt_pipelines_dir == settings.data_dir / ".dlt" / "pipelines"
    assert settings.dbt_project_dir == root.resolve() / "dbt"
    assert settings.environment == "docker"
    assert settings.catalog_url == "postgresql://imdb:secret@postgres:5432/ducklake_catalog"


def test_scratch_dir_moves_dbt_and_dlt_working_files(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_SCRATCH_DIR", "scratch")

    settings = Settings.load(repository_root=root)

    assert settings.dbt_state_dir == root.resolve() / "scratch" / ".dbt"
    assert settings.dlt_pipelines_dir == root.resolve() / "scratch" / ".dlt" / "pipelines"
    assert settings.current_dir == settings.data_dir / "ducklake" / "current"


def test_environment_defaults_to_local(tmp_path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()

    settings = Settings.load(repository_root=root)

    assert settings.environment == "local"
    assert settings.catalog_url is None
    assert settings.storage_url is None


def test_invalid_environment_is_rejected(tmp_path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()

    with pytest.raises(ConfigurationError, match="Unsupported IMDB_DUCKLAKE_ENV"):
        Settings.load(repository_root=root, environment="staging")


def test_local_environment_rejects_stray_catalog_url(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv(
        "IMDB_DUCKLAKE_CATALOG_URL", "postgresql://imdb:secret@postgres:5432/ducklake_catalog"
    )

    with pytest.raises(ConfigurationError, match="IMDB_DUCKLAKE_ENV is 'local'"):
        Settings.load(repository_root=root)


def test_local_environment_ignores_remote_storage(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_REMOTE_STORAGE", "hf")

    settings = Settings.load(repository_root=root)

    assert settings.environment == "local"
    assert settings.storage_url is None


def test_local_environment_ignores_empty_string_env_vars(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    # Mirrors compose.yaml's `${IMDB_DUCKLAKE_REMOTE_STORAGE:-}` - present but empty, not absent.
    monkeypatch.setenv("IMDB_DUCKLAKE_REMOTE_STORAGE", "")

    settings = Settings.load(repository_root=root)

    assert settings.storage_url is None


def test_docker_environment_computes_default_catalog_url(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.delenv("IMDB_DUCKLAKE_CATALOG_URL", raising=False)
    for name in ("POSTGRES_USER", "POSTGRES_PORT", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "shh")

    settings = Settings.load(repository_root=root, environment="docker")

    assert settings.catalog_url == "postgresql://imdb:shh@localhost:5432/ducklake_catalog"
    assert settings.storage_url is None


def test_docker_environment_ignores_remote_storage(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_REMOTE_STORAGE", "b2")

    settings = Settings.load(repository_root=root, environment="docker")

    assert settings.environment == "docker"
    assert settings.storage_url is None


@pytest.mark.parametrize("value", [None, "gcs"])
def test_docker_cloud_requires_hf_or_b2(tmp_path, monkeypatch, value) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.delenv("IMDB_DUCKLAKE_REMOTE_STORAGE", raising=False)

    with pytest.raises(ConfigurationError, match="REMOTE_STORAGE=hf or b2"):
        Settings.load(repository_root=root, environment="docker-cloud", remote_storage=value)


def test_docker_cloud_hf_reads_the_upload_dataset_repo(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_REMOTE_STORAGE", "HF")
    monkeypatch.setenv("HF_DATASET_REPO", "you/name")
    # Both remotes configured at once: only the selected one is used.
    monkeypatch.setenv("B2_BUCKET", "bucket")
    monkeypatch.setenv("BLAZE_ENDPOINT", "not-an-endpoint")

    settings = Settings.load(repository_root=root, environment="docker-cloud")

    assert settings.environment == "docker-cloud"
    assert settings.storage_url == "hf://datasets/you/name"
    assert settings.s3_endpoint is None


def test_docker_cloud_hf_requires_dataset_repo(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.delenv("HF_DATASET_REPO", raising=False)

    with pytest.raises(ConfigurationError, match="requires HF_DATASET_REPO"):
        Settings.load(repository_root=root, environment="docker-cloud", remote_storage="hf")


def test_docker_cloud_b2_reads_the_upload_bucket(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("B2_BUCKET", "imdb-ducklake")
    monkeypatch.setenv("BLAZE_ENDPOINT", "https://s3.us-east-005.backblazeb2.com/")

    settings = Settings.load(repository_root=root, environment="docker-cloud", remote_storage="b2")

    assert settings.storage_url == "s3://imdb-ducklake"
    assert settings.s3_endpoint == "s3.us-east-005.backblazeb2.com"
    assert settings.s3_region == "us-east-005"


@pytest.mark.parametrize("missing", ["B2_BUCKET", "BLAZE_ENDPOINT"])
def test_docker_cloud_b2_requires_bucket_and_endpoint(tmp_path, monkeypatch, missing) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("B2_BUCKET", "imdb-ducklake")
    monkeypatch.setenv("BLAZE_ENDPOINT", "s3.us-east-005.backblazeb2.com")
    monkeypatch.delenv(missing)

    with pytest.raises(ConfigurationError, match=f"requires {missing}"):
        Settings.load(repository_root=root, environment="docker-cloud", remote_storage="b2")


def test_docker_cloud_b2_rejects_endpoint_without_region(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("B2_BUCKET", "imdb-ducklake")
    monkeypatch.setenv("BLAZE_ENDPOINT", "storage.example.com")

    with pytest.raises(ConfigurationError, match=r"s3\.<region>\.backblazeb2\.com"):
        Settings.load(repository_root=root, environment="docker-cloud", remote_storage="b2")


def test_explicit_settings_override_environment(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    absolute_data = tmp_path / "absolute-data"
    monkeypatch.setenv("IMDB_LAKEHOUSE_DATA_DIR", "ignored")
    monkeypatch.setenv("IMDB_LAKEHOUSE_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("IMDB_LAKEHOUSE_LOG_FORMAT", "json")
    monkeypatch.setenv("IMDB_LAKEHOUSE_PROGRESS_INTERVAL_SECONDS", "20")

    settings = Settings.load(
        repository_root=root,
        data_dir=absolute_data,
        log_level="warning",
        log_format="console",
        progress_interval_seconds=5,
    )

    assert settings.data_dir == absolute_data.resolve()
    assert settings.log_level == "WARNING"
    assert settings.log_format == "console"
    assert settings.progress_interval_seconds == 5


def test_invalid_log_level_is_rejected(tmp_path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()

    with pytest.raises(ConfigurationError, match="Unsupported log level"):
        Settings.load(repository_root=root, log_level="verbose")


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"log_format": "xml"}, "Unsupported log format"),
        ({"progress_interval_seconds": 0}, "Progress interval must be greater than zero"),
    ],
)
def test_invalid_observability_settings_are_rejected(tmp_path, values, message) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()

    with pytest.raises(ConfigurationError, match=message):
        Settings.load(repository_root=root, **values)


def test_app_preload_defaults_to_true(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.delenv("IMDB_DUCKLAKE_APP_PRELOAD", raising=False)

    assert Settings.load(repository_root=root).app_preload is True


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("0", False), (" Yes ", True)])
def test_app_preload_reads_environment(tmp_path, monkeypatch, raw, expected) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_APP_PRELOAD", raw)

    assert Settings.load(repository_root=root).app_preload is expected


def test_invalid_app_preload_is_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    monkeypatch.setenv("IMDB_DUCKLAKE_APP_PRELOAD", "sometimes")

    with pytest.raises(ConfigurationError, match="IMDB_DUCKLAKE_APP_PRELOAD"):
        Settings.load(repository_root=root)
