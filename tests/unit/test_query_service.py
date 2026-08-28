from pathlib import Path

import duckdb
import pytest

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.query.service import (
    configured_attach_sql,
    connect_readonly,
    get_title_cast,
    search_titles,
)


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
            episode_count bigint,
            average_rating double,
            num_votes bigint,
            genres varchar[],
            directors varchar[],
            writers varchar[],
            dlt_load_id varchar
        )
        """
    )
    connection.execute("create table marts.mart_series_episodes (series_tconst varchar)")
    connection.execute(
        """
        create table marts.mart_top_titles (
            tconst varchar,
            title_type varchar,
            primary_title varchar,
            original_title varchar,
            start_year integer,
            end_year integer,
            runtime_minutes integer,
            episode_count bigint,
            average_rating double,
            num_votes bigint,
            genres varchar[],
            directors varchar[],
            writers varchar[],
            title_rank bigint
        )
        """
    )
    connection.execute(
        """
        insert into marts.mart_title_search values
            ('tt0000001', 'movie', 'The Matrix', 'The Matrix', false, 1999, NULL,
             136, NULL, 8.7, 2000000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Lana Wachowski', 'Lilly Wachowski'], 'load1'),
            ('tt0000002', 'movie', 'The Matrix Reloaded', 'The Matrix Reloaded', false,
             2003, NULL, 138, NULL, 7.2, 700000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Lana Wachowski', 'Lilly Wachowski'], 'load1'),
            ('tt0000003', 'movie', 'Inception', 'Inception', false, 2010, NULL,
             148, NULL, 8.8, 2300000, ['Action', 'Sci-Fi'], ['Christopher Nolan'],
             ['Christopher Nolan'], 'load1'),
            ('tt0000004', 'tvSeries', 'Breaking Bad', 'Breaking Bad', false, 2008, 2013,
             47, 62, 9.5, 2100000, ['Crime', 'Drama'], ['Vince Gilligan'],
             ['Vince Gilligan'], 'load1'),
            ('tt0000005', 'short', 'Bao', 'Bao', false, 2018, NULL,
             8, NULL, 8.1, 90000, ['Animation', 'Comedy', 'Drama'], ['Domee Shi'],
             ['Domee Shi'], 'load1')
        """
    )
    connection.execute(
        """
        insert into marts.mart_top_titles values
            ('tt0000003', 'movie', 'Inception', 'Inception', 2010, NULL,
             148, NULL, 8.8, 2300000, ['Action', 'Sci-Fi'], ['Christopher Nolan'],
             ['Christopher Nolan'], 1),
            ('tt0000001', 'movie', 'The Matrix', 'The Matrix', 1999, NULL,
             136, NULL, 8.7, 2000000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Lana Wachowski', 'Lilly Wachowski'], 2),
            ('tt0000002', 'movie', 'The Matrix Reloaded', 'The Matrix Reloaded',
             2003, NULL, 138, NULL, 7.2, 700000, ['Action', 'Sci-Fi'], ['Lana Wachowski'],
             ['Lana Wachowski', 'Lilly Wachowski'], 3),
            ('tt0000004', 'tvSeries', 'Breaking Bad', 'Breaking Bad', 2008, 2013,
             47, 62, 9.5, 2100000, ['Crime', 'Drama'], ['Vince Gilligan'],
             ['Vince Gilligan'], 1)
        """
    )
    connection.execute(
        """
        create table marts.mart_person_filmography (
            nconst varchar,
            primary_name varchar,
            tconst varchar,
            primary_title varchar,
            title_type varchar,
            start_year integer,
            ordering integer,
            category varchar,
            job varchar,
            characters varchar[],
            average_rating double,
            num_votes bigint,
            dlt_load_id varchar
        )
        """
    )
    connection.execute(
        """
        insert into marts.mart_person_filmography values
            ('nm0000401', 'Carrie-Anne Moss', 'tt0000001', 'The Matrix', 'movie',
             1999, 2, 'actress', NULL, ['Trinity'], 8.7, 2000000, 'load1'),
            ('nm0000206', 'Keanu Reeves', 'tt0000001', 'The Matrix', 'movie',
             1999, 1, 'actor', NULL, ['Neo'], 8.7, 2000000, 'load1'),
            ('nm0905154', 'Lana Wachowski', 'tt0000001', 'The Matrix', 'movie',
             1999, NULL, 'director', NULL, NULL, 8.7, 2000000, 'load1'),
            ('nm-missing', NULL, 'tt0000002', 'The Matrix Reloaded', 'movie',
             2003, 1, 'self', NULL, NULL, 7.2, 700000, 'load1')
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


def test_configured_attach_sql_uses_postgresql_catalog_and_shared_storage(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    storage_dir = settings.current_dir / "storage"
    storage_dir.mkdir(parents=True)

    sql = configured_attach_sql(settings)

    assert "ducklake:postgres:dbname=''ducklake_catalog'' host=''postgres''" in sql
    assert "user=''imdb'' password=''secret''" in sql
    assert f"DATA_PATH '{storage_dir.resolve().as_posix()}'" in sql
    assert "METADATA_SCHEMA 'imdb_lake'" in sql
    assert "OVERRIDE_DATA_PATH true, READ_ONLY" in sql


def test_shared_catalog_missing_storage_error_does_not_expose_credentials(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )

    with pytest.raises(NoPromotedBuildError) as captured:
        configured_attach_sql(settings)

    assert "postgresql://postgres:5432/ducklake_catalog#imdb_lake" in str(captured.value)
    assert "secret" not in str(captured.value)


def test_search_titles_filters_by_query(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "matrix", title_type="movie"))

    assert sorted(tconsts) == ["tt0000001", "tt0000002"]
    assert tconsts == ["tt0000001", "tt0000002"]  # ordered by num_votes desc


def test_search_titles_returns_person_names(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    frame = search_titles(connection, "matrix", title_type="movie").df()

    assert "Cast" not in frame.columns
    assert frame.loc[0, "Directors"] == "Lana Wachowski"
    assert frame.loc[0, "Writers"] == "Lana Wachowski, Lilly Wachowski"


def test_get_title_cast_returns_ordered_principal_credits(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    frame = get_title_cast(connection, "tt0000001").df()

    assert frame["IMDb Person ID"].tolist() == ["nm0000206", "nm0000401"]
    assert frame["Name"].tolist() == ["Keanu Reeves", "Carrie-Anne Moss"]
    assert frame["Role"].tolist() == ["actor", "actress"]
    assert frame["Characters"].tolist() == ["Neo", "Trinity"]


def test_get_title_cast_falls_back_to_person_id(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    frame = get_title_cast(connection, "tt0000002").df()

    assert frame.loc[0, "Name"] == "nm-missing"


def test_get_title_cast_returns_empty_relation_for_unknown_title(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    assert get_title_cast(connection, "tt-unknown").df().empty


def test_search_titles_empty_query_returns_top_by_votes(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "", title_type="movie"))

    assert tconsts == ["tt0000003", "tt0000001", "tt0000002"]


def test_search_titles_respects_limit(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "", title_type="movie", limit=1))

    assert tconsts == ["tt0000003"]


def test_search_titles_returns_tv_series(settings_with_fixture_build: Settings) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "", title_type="tvSeries"))

    assert tconsts == ["tt0000004"]


def test_search_titles_excludes_non_selected_title_types(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build)

    tconsts = _tconsts(search_titles(connection, "", title_type="movie"))

    assert "tt0000004" not in tconsts
    assert "tt0000005" not in tconsts
