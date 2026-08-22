import gzip
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.acquisition.manifest import ManifestEntry
from imdb_ducklake.application.build import build_lakehouse
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.ingestion.pipeline import ingest_snapshot
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build
from imdb_ducklake.transformation.dbt_runner import run_dbt

ROWS = {
    "title_akas": ("tt0001", "1", "Exemplo", "BR", "pt", "\\N", "\\N", "1"),
    "title_crew": ("tt0001", "nm0001", "nm0001"),
    "title_episode": ("tt0002", "tt0001", "1", "1"),
    "title_principals": ("tt0001", "1", "nm0001", "actor", "\\N", '["Hero"]'),
    "title_ratings": ("tt0001", "7.5", "100"),
    "name_basics": ("nm0001", "Example Person", "1980", "\\N", "actor", "tt0001"),
}


def _snapshot(raw_dir, *, title_rows) -> tuple[VerifiedArtifact, ...]:
    raw_dir.mkdir(exist_ok=True)
    artifacts = []
    for dataset in DATASETS:
        path = raw_dir / dataset.file_name
        rows = title_rows if dataset.table_name == "title_basics" else (ROWS[dataset.table_name],)
        content = "\t".join(dataset.headers) + "\n"
        content += "".join("\t".join(row) + "\n" for row in rows)
        with gzip.open(path, "wb") as archive:
            archive.write(content.encode("utf-8"))
        compressed = path.read_bytes()
        artifacts.append(
            VerifiedArtifact(
                dataset=dataset,
                path=path,
                manifest_entry=ManifestEntry(
                    dataset=dataset.name,
                    file_name=dataset.file_name,
                    table_name=dataset.table_name,
                    url=dataset.url,
                    size_bytes=len(compressed),
                    sha256=hashlib.sha256(compressed).hexdigest(),
                    downloaded_at=datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
                    batch_id="fixture-batch",
                    content_type="application/gzip",
                ),
            )
        )
    return tuple(artifacts)


@pytest.mark.integration
def test_loads_complete_lossless_snapshot_into_ducklake(tmp_path) -> None:
    first_title_rows = (
        ("tt0001", "movie", "Exámple 東京", "Example", "0", "2020", "\\N", "90", "Drama"),
        ("tt-old", "short", "Old row", "", "0", "1999", "2000", "5", "Comedy"),
    )
    artifacts = _snapshot(tmp_path / "raw", title_rows=first_title_rows)
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="fixture-build")
    initialize_build(paths)

    result = ingest_snapshot(
        artifacts,
        build_paths=paths,
        pipelines_dir=tmp_path / "pipelines",
        chunk_size=1,
    )

    assert result.load_ids
    assert result.catalog_path.is_file()
    assert result.storage_dir.is_dir()

    catalog = result.catalog_path.as_posix().replace("'", "''")
    storage = result.storage_dir.as_posix().replace("'", "''")
    with duckdb.connect(":memory:") as connection:
        connection.execute(f"ATTACH 'ducklake:{catalog}' AS imdb_lake (DATA_PATH '{storage}')")
        raw_tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = 'imdb_lake' AND table_schema = 'raw'"
            ).fetchall()
        }
        expected_tables = {dataset.table_name for dataset in DATASETS} | {"ingestion_files"}
        assert expected_tables <= raw_tables

        titles = connection.execute(
            'SELECT "tconst", "primaryTitle", "originalTitle", "endYear", "_dlt_load_id" '
            'FROM imdb_lake.raw."title_basics" ORDER BY "tconst"'
        ).fetchall()
        assert len(titles) == 2
        title = next(row for row in titles if row[0] == "tt0001")
        assert title[0:4] == ("tt0001", "Exámple 東京", "Example", "\\N")
        assert next(row for row in titles if row[0] == "tt-old")[2] == ""
        assert (
            connection.execute(
                'SELECT count(*) FROM imdb_lake.raw."ingestion_files" '
                'WHERE "_dlt_load_id" = ? AND "table_name" = ?',
                [title[4], "title_basics"],
            ).fetchone()[0]
            == 1
        )

        source_types = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_catalog = 'imdb_lake' AND table_schema = 'raw' "
                "AND table_name = 'title_basics'"
            ).fetchall()
            if not row[0].startswith("_dlt")
        }
        assert set(source_types) == set(
            next(d.headers for d in DATASETS if d.table_name == "title_basics")
        )
        assert set(source_types.values()) == {"VARCHAR"}

    replacement_rows = (
        ("tt0001", "movie", "Exámple 東京", "Example", "0", "2020", "\\N", "90", "Drama"),
        ("tt0002", "tvEpisode", "Episode", "Episode", "0", "2021", "\\N", "45", "Drama"),
        ("tt-new", "movie", "Replacement", "Replacement", "0", "2026", "\\N", "91", "Drama"),
    )
    replacement = _snapshot(tmp_path / "raw", title_rows=replacement_rows)
    replacement_result = ingest_snapshot(
        replacement,
        build_paths=paths,
        pipelines_dir=tmp_path / "pipelines",
        chunk_size=1,
    )

    assert replacement_result.load_ids != result.load_ids
    with duckdb.connect(":memory:") as connection:
        connection.execute(f"ATTACH 'ducklake:{catalog}' AS imdb_lake (DATA_PATH '{storage}')")
        assert set(
            connection.execute('SELECT "tconst" FROM imdb_lake.raw."title_basics"').fetchall()
        ) == {("tt0001",), ("tt0002",), ("tt-new",)}

    repository_root = Path(__file__).parents[2]
    dbt_executable = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    dbt_result = run_dbt(
        ("build",),
        build_paths=paths,
        project_dir=repository_root / "dbt",
        profiles_dir=repository_root / "dbt",
        controller_path=tmp_path / "dbt" / "controller.duckdb",
        executable=str(dbt_executable),
        environment=os.environ,
    )
    assert "Completed successfully" in dbt_result.stdout
    run_dbt(
        ("docs", "generate"),
        build_paths=paths,
        project_dir=repository_root / "dbt",
        profiles_dir=repository_root / "dbt",
        controller_path=tmp_path / "dbt" / "controller.duckdb",
        executable=str(dbt_executable),
        environment=os.environ,
    )
    assert (repository_root / "dbt" / "target" / "catalog.json").is_file()

    with duckdb.connect(":memory:") as connection:
        connection.execute(f"ATTACH 'ducklake:{catalog}' AS imdb_lake (DATA_PATH '{storage}')")
        transformed = connection.execute(
            "SELECT tconst, start_year, end_year, genres, dlt_load_id "
            "FROM imdb_lake.staging.stg_imdb__title_basics WHERE tconst = 'tt-new'"
        ).fetchone()
        assert transformed is not None
        assert transformed[0:4] == ("tt-new", 2026, None, ["Drama"])
        assert transformed[4]
        assert connection.execute(
            "SELECT characters FROM imdb_lake.staging.stg_imdb__title_principals"
        ).fetchone()[0] == ["Hero"]
        title_search = connection.execute(
            "SELECT primary_title, average_rating, genres, directors "
            "FROM imdb_lake.marts.mart_title_search WHERE tconst = 'tt0001'"
        ).fetchone()
        assert title_search == (
            "Exámple 東京",
            7.5,
            ["Drama"],
            ["Example Person"],
        )
        assert connection.execute(
            "SELECT title_count, rated_title_count, total_votes "
            "FROM imdb_lake.marts.mart_genre_year_summary "
            "WHERE start_year = 2020 AND genre = 'Drama'"
        ).fetchone() == (1, 1, 100)
        person_filmography_sql = (
            "SELECT nconst, tconst, category, characters "
            "FROM imdb_lake.marts.mart_person_filmography "
            "WHERE category = 'actor'"
        )
        assert connection.execute(person_filmography_sql).fetchone() == (
            "nm0001",
            "tt0001",
            "actor",
            ["Hero"],
        )
        assert connection.execute(
            "SELECT category FROM imdb_lake.marts.mart_person_filmography "
            "WHERE nconst = 'nm0001' AND tconst = 'tt0001' ORDER BY category"
        ).fetchall() == [("actor",), ("director",), ("writer",)]
        assert connection.execute(
            "SELECT series_tconst, episode_tconst, season_number, episode_number "
            "FROM imdb_lake.marts.mart_series_episodes"
        ).fetchone() == ("tt0001", "tt0002", 1, 1)


@pytest.mark.integration
def test_one_command_builds_validates_and_promotes_fixture_snapshot(tmp_path) -> None:
    title_rows = (
        ("tt0001", "movie", "Exámple 東京", "Example", "0", "2020", "\\N", "90", "Drama"),
        ("tt0002", "tvEpisode", "Episode", "Episode", "0", "2021", "\\N", "45", "Drama"),
    )
    artifacts = _snapshot(tmp_path / "raw", title_rows=title_rows)
    repository_root = Path(__file__).parents[2]
    settings = Settings(repository_root=repository_root, data_dir=tmp_path / "data")
    settings.current_dir.mkdir(parents=True)
    (settings.current_dir / "previous.txt").write_text("known-good", encoding="utf-8")

    class FixtureDownloader:
        def download_all(self, _datasets, **_kwargs):
            return artifacts

    dbt_executable = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    result = build_lakehouse(
        settings=settings,
        downloader=FixtureDownloader(),
        dbt_executable=str(dbt_executable),
        python_executable=sys.executable,
        environment=os.environ,
        reserve_bytes=0,
        temporary_size_factor=1,
        show_progress=False,
    )

    assert result.promoted.current_dir == settings.current_dir
    assert result.validation.relation_count == 30
    assert result.validation.mart_row_counts["mart_title_search"] == 2
    assert result.promoted.previous_dir is not None
    assert (result.promoted.previous_dir / "previous.txt").read_text(encoding="utf-8") == (
        "known-good"
    )
    assert result.promoted.catalog_path.is_file()
    assert result.promoted.storage_dir.is_dir()
    promoted_validation = subprocess.run(
        (
            sys.executable,
            "-m",
            "imdb_ducklake.lakehouse.validation",
            "--catalog",
            str(result.promoted.catalog_path),
            "--storage",
            str(result.promoted.storage_dir),
            "--build-id",
            result.build_id,
        ),
        cwd=repository_root,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert promoted_validation.returncode == 0, promoted_validation.stderr
