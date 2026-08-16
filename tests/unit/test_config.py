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
