import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import ConfigurationError, NoPromotedBuildError
from imdb_ducklake.query.service import (
    SharedConnection,
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
    # A mart the app must never read or preload.
    connection.execute("create schema marts")
    connection.execute("create table marts.mart_title_search as select 'tt0000001' as tconst")
    connection.execute("create schema app")
    connection.execute(
        """
        create table app.app_title_search (
            tconst varchar,
            title_type varchar,
            primary_title varchar,
            original_title varchar,
            start_year smallint,
            end_year smallint,
            runtime_minutes integer,
            episode_count bigint,
            average_rating double,
            num_votes bigint,
            genres_text varchar,
            directors_text varchar,
            writers_text varchar
        )
        """
    )
    connection.execute(
        """
        insert into app.app_title_search values
            ('tt0000001', 'movie', 'The Matrix', 'The Matrix', 1999, NULL, 136, NULL, 8.7,
             2000000, 'Action, Sci-Fi', 'Lana Wachowski', 'Lana Wachowski, Lilly Wachowski'),
            ('tt0000002', 'movie', 'The Matrix Reloaded', 'The Matrix Reloaded', 2003, NULL,
             138, NULL, 7.2, 700000, 'Action, Sci-Fi', 'Lana Wachowski',
             'Lana Wachowski, Lilly Wachowski'),
            ('tt0000003', 'movie', 'Inception', 'Inception', 2010, NULL, 148, NULL, 8.8,
             2300000, 'Action, Sci-Fi', 'Christopher Nolan', 'Christopher Nolan'),
            ('tt0000004', 'tvSeries', 'Breaking Bad', 'Breaking Bad', 2008, 2013, 47, 62, 9.5,
             2100000, 'Crime, Drama', 'Vince Gilligan', 'Vince Gilligan'),
            ('tt0000006', 'tvSeries', 'The Matrix Animated', 'The Matrix Animated', 2020, NULL,
             20, 10, 6.0, 1000, 'Animation', NULL, NULL)
        """
    )
    connection.execute(
        """
        create table app.app_top_movies (
            tconst varchar,
            primary_title varchar,
            original_title varchar,
            start_year smallint,
            runtime_minutes integer,
            average_rating double,
            num_votes bigint,
            title_rank bigint,
            genres_text varchar,
            directors_text varchar,
            writers_text varchar
        )
        """
    )
    connection.execute(
        """
        insert into app.app_top_movies values
            ('tt0000003', 'Inception', 'Inception', 2010, 148, 8.8, 2300000, 1,
             'Action, Sci-Fi', 'Christopher Nolan', 'Christopher Nolan'),
            ('tt0000001', 'The Matrix', 'The Matrix', 1999, 136, 8.7, 2000000, 2,
             'Action, Sci-Fi', 'Lana Wachowski', 'Lana Wachowski, Lilly Wachowski'),
            ('tt0000002', 'The Matrix Reloaded', 'The Matrix Reloaded', 2003, 138, 7.2, 700000, 3,
             'Action, Sci-Fi', 'Lana Wachowski', 'Lana Wachowski, Lilly Wachowski')
        """
    )
    connection.execute(
        """
        create table app.app_top_tv_series (
            tconst varchar,
            primary_title varchar,
            original_title varchar,
            start_year smallint,
            end_year smallint,
            runtime_minutes integer,
            episode_count bigint,
            average_rating double,
            num_votes bigint,
            title_rank bigint,
            genres_text varchar,
            directors_text varchar,
            writers_text varchar
        )
        """
    )
    connection.execute(
        """
        insert into app.app_top_tv_series values
            ('tt0000004', 'Breaking Bad', 'Breaking Bad', 2008, 2013, 47, 62, 9.5, 2100000, 1,
             'Crime, Drama', 'Vince Gilligan', 'Vince Gilligan')
        """
    )
    connection.execute(
        """
        create table app.app_title_cast (
            tconst varchar,
            nconst varchar,
            primary_name varchar,
            category varchar,
            ordering integer,
            characters_text varchar
        )
        """
    )
    connection.execute(
        """
        insert into app.app_title_cast values
            ('tt0000001', 'nm0000401', 'Carrie-Anne Moss', 'actress', 2, 'Trinity'),
            ('tt0000001', 'nm0000206', 'Keanu Reeves', 'actor', 1, 'Neo'),
            ('tt0000002', 'nm-missing', NULL, 'self', 1, NULL)
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


@pytest.fixture(params=[True, False], ids=["preloaded", "lake"])
def connection(
    request: pytest.FixtureRequest, settings_with_fixture_build: Settings
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Every query test runs against the in-memory copy and directly against the lake."""
    opened = connect_readonly(settings_with_fixture_build, preload=request.param)
    yield opened
    opened.close()


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


def test_configured_attach_sql_reads_hf_dataset_with_hf_secret(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        storage_url="hf://datasets/you/name",
    )

    sql = configured_attach_sql(settings)

    assert "CREATE SECRET hf_token (TYPE huggingface, PROVIDER credential_chain);" in sql
    assert "DATA_PATH 'hf://datasets/you/name'" in sql
    assert "TYPE s3" not in sql


def test_configured_attach_sql_reads_s3_bucket_with_env_credentials(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        storage_url="s3://imdb-ducklake",
        s3_endpoint="s3.us-east-005.backblazeb2.com",
    )

    sql = configured_attach_sql(settings)

    assert (
        "CREATE SECRET s3_storage (TYPE s3, PROVIDER credential_chain, CHAIN 'env', "
        "ENDPOINT 's3.us-east-005.backblazeb2.com', REGION 'us-east-005');"
    ) in sql
    assert "DATA_PATH 's3://imdb-ducklake'" in sql
    assert "KEY_ID" not in sql
    assert "huggingface" not in sql


def test_attach_sql_rejects_s3_without_endpoint(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        storage_url="s3://imdb-ducklake",
    )

    with pytest.raises(ConfigurationError, match="endpoint and region"):
        configured_attach_sql(settings)


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


def test_search_titles_filters_by_query(connection: duckdb.DuckDBPyConnection) -> None:

    tconsts = _tconsts(search_titles(connection, "matrix", title_type="movie"))

    assert sorted(tconsts) == ["tt0000001", "tt0000002"]
    assert tconsts == ["tt0000001", "tt0000002"]  # ordered by num_votes desc


def test_search_titles_returns_person_names(connection: duckdb.DuckDBPyConnection) -> None:

    frame = search_titles(connection, "matrix", title_type="movie").df()

    assert "Cast" not in frame.columns
    assert frame.loc[0, "Directors"] == "Lana Wachowski"
    assert frame.loc[0, "Writers"] == "Lana Wachowski, Lilly Wachowski"


def test_get_title_cast_returns_ordered_principal_credits(
    connection: duckdb.DuckDBPyConnection,
) -> None:

    frame = get_title_cast(connection, "tt0000001").df()

    assert frame["IMDb Person ID"].tolist() == ["nm0000206", "nm0000401"]
    assert frame["Name"].tolist() == ["Keanu Reeves", "Carrie-Anne Moss"]
    assert frame["Role"].tolist() == ["actor", "actress"]
    assert frame["Characters"].tolist() == ["Neo", "Trinity"]


def test_get_title_cast_falls_back_to_person_id(
    connection: duckdb.DuckDBPyConnection,
) -> None:

    frame = get_title_cast(connection, "tt0000002").df()

    assert frame.loc[0, "Name"] == "nm-missing"


def test_get_title_cast_returns_empty_relation_for_unknown_title(
    connection: duckdb.DuckDBPyConnection,
) -> None:

    assert get_title_cast(connection, "tt-unknown").df().empty


def test_search_titles_empty_query_returns_top_by_votes(
    connection: duckdb.DuckDBPyConnection,
) -> None:

    tconsts = _tconsts(search_titles(connection, "", title_type="movie"))

    assert tconsts == ["tt0000003", "tt0000001", "tt0000002"]


def test_search_titles_top_query_ignores_limit(connection: duckdb.DuckDBPyConnection) -> None:
    """The empty-query path reads app_top_movies/app_top_tv_series, already bounded to 500 rows
    per title type by dbt; the limit only applies to searches."""

    tconsts = _tconsts(search_titles(connection, "", title_type="movie", limit=1))

    assert tconsts == ["tt0000003", "tt0000001", "tt0000002"]


def test_search_titles_search_query_respects_limit(connection: duckdb.DuckDBPyConnection) -> None:

    tconsts = _tconsts(search_titles(connection, "matrix", title_type="movie", limit=1))

    assert tconsts == ["tt0000001"]


def test_search_titles_returns_tv_series(connection: duckdb.DuckDBPyConnection) -> None:

    tconsts = _tconsts(search_titles(connection, "", title_type="tvSeries"))

    assert tconsts == ["tt0000004"]


def test_search_titles_excludes_non_selected_title_types(
    connection: duckdb.DuckDBPyConnection,
) -> None:

    tconsts = _tconsts(search_titles(connection, "", title_type="movie"))

    assert "tt0000004" not in tconsts
    assert "tt0000005" not in tconsts


def test_search_titles_tv_search_excludes_movies(connection: duckdb.DuckDBPyConnection) -> None:
    assert _tconsts(search_titles(connection, "matrix", title_type="tvSeries")) == ["tt0000006"]


def test_preload_copies_only_app_tables_and_detaches_the_lake(
    settings_with_fixture_build: Settings,
) -> None:
    connection = connect_readonly(settings_with_fixture_build, preload=True)

    databases = {
        row[0]
        for row in connection.execute("select database_name from duckdb_databases()").fetchall()
    }
    tables = connection.execute(
        "select schema_name, table_name from duckdb_tables() order by all"
    ).fetchall()

    assert "imdb_lake" not in databases
    assert tables == [
        ("app", "app_title_cast"),
        ("app", "app_title_search"),
        ("app", "app_top_movies"),
        ("app", "app_top_tv_series"),
    ]


def test_preload_follows_settings_by_default(settings_with_fixture_build: Settings) -> None:
    lake_settings = replace(settings_with_fixture_build, app_preload=False)

    preloaded = connect_readonly(settings_with_fixture_build)
    attached = connect_readonly(lake_settings)

    assert preloaded.execute("select current_database()").fetchone() == ("memory",)
    assert attached.execute("select current_database()").fetchone() == ("imdb_lake",)


def test_preload_missing_app_table_raises_no_promoted_build(
    settings_with_fixture_build: Settings,
) -> None:
    catalog_path = settings_with_fixture_build.current_dir / "catalog.duckdb"
    storage_dir = settings_with_fixture_build.current_dir / "storage"
    writer = duckdb.connect(":memory:")
    writer.execute("LOAD ducklake")
    writer.execute(
        f"ATTACH 'ducklake:{catalog_path.as_posix()}' AS imdb_lake "
        f"(DATA_PATH '{storage_dir.as_posix()}', OVERRIDE_DATA_PATH true)"
    )
    writer.execute("drop table imdb_lake.app.app_title_cast")
    writer.close()

    with pytest.raises(NoPromotedBuildError, match="Could not attach"):
        connect_readonly(settings_with_fixture_build, preload=True)


def test_shared_connection_opens_once_across_threads() -> None:
    opened: list[duckdb.DuckDBPyConnection] = []

    def open_connection() -> duckdb.DuckDBPyConnection:
        opened.append(duckdb.connect(":memory:"))
        return opened[-1]

    shared = SharedConnection(open_connection)
    results: list[duckdb.DuckDBPyConnection] = []
    threads = [threading.Thread(target=lambda: results.append(shared.get())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(opened) == 1
    assert all(result is opened[0] for result in results)


def test_shared_connection_retries_after_a_failed_open() -> None:
    attempts: list[int] = []

    def open_connection() -> duckdb.DuckDBPyConnection:
        attempts.append(1)
        if len(attempts) == 1:
            raise NoPromotedBuildError("not yet")
        return duckdb.connect(":memory:")

    shared = SharedConnection(open_connection)

    with pytest.raises(NoPromotedBuildError):
        shared.get()
    assert shared.get() is shared.get()
    assert len(attempts) == 2
