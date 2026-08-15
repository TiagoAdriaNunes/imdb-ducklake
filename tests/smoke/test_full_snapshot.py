"""Explicit local smoke checks for the complete retained IMDb snapshot."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from imdb_ducklake.acquisition.downloader import load_verified_artifacts
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import LifecycleError
from imdb_ducklake.lakehouse.lifecycle import select_staged_build
from imdb_ducklake.lakehouse.validation import REQUIRED_RELATIONS, validate_catalog

pytestmark = pytest.mark.smoke


@dataclass(frozen=True, slots=True)
class SmokeBuild:
    build_id: str
    catalog_path: Path
    storage_dir: Path


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="module")
def smoke_build(settings: Settings) -> SmokeBuild:
    if settings.current_dir.is_dir():
        return SmokeBuild(
            build_id="current",
            catalog_path=settings.current_dir / "catalog.duckdb",
            storage_dir=settings.current_dir / "storage",
        )
    try:
        staged = select_staged_build(settings.lakehouse_dir)
    except LifecycleError as error:
        pytest.skip(f"No complete current or staged full-data build exists: {error}")
    return SmokeBuild(staged.build_id, staged.catalog_path, staged.storage_dir)


def test_full_archives_match_their_manifest(settings: Settings) -> None:
    artifacts = load_verified_artifacts(
        DATASETS,
        raw_dir=settings.raw_dir,
        manifest_path=settings.manifest_path,
    )

    assert len(artifacts) == len(DATASETS) == 7
    assert sum(artifact.manifest_entry.size_bytes for artifact in artifacts) > 0
    for artifact in artifacts:
        assert artifact.path.stat().st_size == artifact.manifest_entry.size_bytes
        print(
            f"{artifact.dataset.file_name}: {artifact.manifest_entry.size_bytes:,} bytes "
            f"sha256={artifact.manifest_entry.sha256}"
        )


def test_full_catalog_passes_fresh_process_read_only_validation(
    settings: Settings, smoke_build: SmokeBuild
) -> None:
    result = validate_catalog(
        catalog_path=smoke_build.catalog_path,
        storage_dir=smoke_build.storage_dir,
        build_id=smoke_build.build_id,
        executable=sys.executable,
        environment=os.environ,
        working_directory=settings.repository_root,
    )

    assert result.relation_count == sum(len(names) for names in REQUIRED_RELATIONS.values())
    assert set(result.mart_row_counts) == REQUIRED_RELATIONS["marts"]
    assert all(row_count > 0 for row_count in result.mart_row_counts.values())
    print(f"validated {result.build_id}: {result.mart_row_counts}")


def test_full_raw_tables_keep_string_columns_and_file_lineage(smoke_build: SmokeBuild) -> None:
    with _connect_read_only(smoke_build) as connection:
        for dataset in DATASETS:
            source_types = connection.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_catalog = 'imdb_lake'
                  AND table_schema = 'raw'
                  AND table_name = ?
                  AND column_name NOT LIKE '_dlt%'
                """,
                [dataset.table_name],
            ).fetchall()
            assert {str(name) for name, _data_type in source_types} == set(dataset.headers)
            assert {str(data_type) for _name, data_type in source_types} == {"VARCHAR"}

            quoted_table = dataset.table_name.replace('"', '""')
            row_count = connection.execute(
                f'SELECT count(*) FROM imdb_lake.raw."{quoted_table}"'
            ).fetchone()
            assert row_count is not None and int(row_count[0]) > 0
            orphan_count = connection.execute(
                f"""
                SELECT count(*)
                FROM imdb_lake.raw."{quoted_table}" AS source_rows
                LEFT JOIN imdb_lake.raw.ingestion_files AS files
                  ON source_rows._dlt_load_id = files._dlt_load_id
                 AND files.table_name = ?
                WHERE files._dlt_load_id IS NULL
                """,
                [dataset.table_name],
            ).fetchone()
            assert orphan_count == (0,)
            print(f"raw.{dataset.table_name}: {int(row_count[0]):,} rows, lineage complete")


def test_full_marts_answer_representative_ui_queries(smoke_build: SmokeBuild) -> None:
    queries = {
        "title search": """
            SELECT tconst, primary_title, average_rating, genres
            FROM imdb_lake.marts.mart_title_search
            WHERE primary_title IS NOT NULL
            ORDER BY num_votes DESC NULLS LAST
            LIMIT 10
        """,
        "genre summary": """
            SELECT genre, start_year, title_count, average_rating
            FROM imdb_lake.marts.mart_genre_year_summary
            WHERE genre IS NOT NULL
            ORDER BY total_votes DESC
            LIMIT 10
        """,
        "people and credits": """
            SELECT nconst, primary_name, tconst, category
            FROM imdb_lake.marts.mart_person_filmography
            WHERE primary_name IS NOT NULL
            ORDER BY num_votes DESC NULLS LAST
            LIMIT 10
        """,
        "series and episodes": """
            SELECT series_tconst, episode_tconst, season_number, episode_number
            FROM imdb_lake.marts.mart_series_episodes
            ORDER BY num_votes DESC NULLS LAST
            LIMIT 10
        """,
    }
    with _connect_read_only(smoke_build) as connection:
        for label, query in queries.items():
            rows = connection.execute(query).fetchall()
            assert rows, f"Representative {label} query returned no rows"
            print(f"{label}: {len(rows)} representative rows")


def _connect_read_only(smoke_build: SmokeBuild) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("LOAD ducklake")
    catalog = _sql_string(f"ducklake:{smoke_build.catalog_path.resolve().as_posix()}")
    storage = _sql_string(smoke_build.storage_dir.resolve().as_posix())
    connection.execute(
        f"ATTACH {catalog} AS imdb_lake (DATA_PATH {storage}, OVERRIDE_DATA_PATH true, READ_ONLY)"
    )
    return connection


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
