import gzip
import hashlib
from datetime import UTC, datetime

import duckdb
import pytest

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.acquisition.manifest import ManifestEntry
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.ingestion.pipeline import ingest_snapshot
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build

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
        assert connection.execute(
            'SELECT "tconst" FROM imdb_lake.raw."title_basics"'
        ).fetchall() == [("tt-new",)]
