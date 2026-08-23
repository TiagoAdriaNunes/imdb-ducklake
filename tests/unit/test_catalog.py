from pathlib import Path

import pytest

from imdb_ducklake.exceptions import ConfigurationError
from imdb_ducklake.lakehouse.catalog import CatalogTarget


def test_postgresql_url_is_rendered_for_duckdb() -> None:
    target = CatalogTarget(
        "postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        Path("/data/ducklake/storage"),
    )

    assert target.duckdb_metadata_path == (
        "postgres:dbname='ducklake_catalog' host='postgres' port=5432 user='imdb' password='secret'"
    )


def test_non_postgres_catalog_is_rejected() -> None:
    target = CatalogTarget("duckdb:///catalog.duckdb", Path("storage"))

    with pytest.raises(ConfigurationError, match="postgres"):
        _ = target.duckdb_metadata_path
