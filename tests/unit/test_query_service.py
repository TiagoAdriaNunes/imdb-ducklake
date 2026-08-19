from pathlib import Path

import duckdb
import pytest

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.query.service import connect_readonly, search_titles


def _build_fixture_lakehouse(current_dir: Path) -> None:
    storage_dir = current_dir / "storage"
    storage_dir.mkdir(parents=True)
    catalog_path = current_dir / "catalog.duckdb"
    connection = duckdb.connect(":memory:")
    connection.execute("INSTALL ducklake")
    connection.execute("LOAD ducklake")
    connection.execute(
        f"ATTACH 'ducklake:{catalog_path.as_posix()}' AS imdb_lake "
        f"(DATA_PATH '{storage_dir.as_posix()}', OVERRIDE_DATA_PATH true)"
    )
    connection.execute("USE imdb_lake")
    connection.execute("create schema marts")
    connection.execute(
        """
        create table marts.mart_title_search (
            tconst varchar,
            title_type varchar,
            primary_title varchar,
            original_title varchar,
            is_adult boolean,
            start_year integer,
            end_year integer,
            runtime_minutes integer,
            average_rating double,
            num_votes bigint,
            genres varchar[],
            directors varchar[],
            principal_cast varchar[],
            dlt_load_id varchar
        )
        """
    )
    connection.execute(
        """
        insert into marts.mart_title_search values
            ('tt0000001', 'movie', 'The Matrix', 'The Matrix', false, 1999, NULL,
             136, 8.7, 2000000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Keanu Reeves'], 'load1'),
            ('tt0000002', 'movie', 'The Matrix Reloaded', 'The Matrix Reloaded', false,
             2003, NULL, 138, 7.2, 700000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Keanu Reeves'], 'load1'),
            ('tt0000003', 'movie', 'Inception', 'Inception', false, 2010, NULL,
             148, 8.8, 2300000, ['Action', 'Sci-Fi'], ['Christopher Nolan'],
             ['Leonardo DiCaprio'], 'load1')
        """
    )
    connection.close()


def _tconsts(relation: duckdb.DuckDBPyRelation) -> list[str]:
    return [row[0] for row in relation.fetchall()]


@pytest.fixture
def settings_with_fixture_build(tmp_path: Path) -> Settings:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    settings = Settings.load(repository_root=root)
    _build_fixture_lakehouse(settings.current_dir)
    return settings


def test_connect_readonly_raises_when_no_promoted_build(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").touch()
    settings = Settings.load(repository_root=root)

    with pytest.raises(NoPromotedBuildError, match="No promoted build found"):
        connect_readonly(settings)


def test_search_titles_filters_by_query(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "matrix"))

    assert sorted(tconsts) == ["tt0000001", "tt0000002"]
    assert tconsts == ["tt0000001", "tt0000002"]  # ordered by num_votes desc


def test_search_titles_empty_query_returns_top_by_votes(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, ""))

    assert tconsts == ["tt0000003", "tt0000001", "tt0000002"]


def test_search_titles_respects_limit(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "", limit=1))

    assert tconsts == ["tt0000003"]
