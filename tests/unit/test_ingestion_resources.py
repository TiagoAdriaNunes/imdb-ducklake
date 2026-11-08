import gzip
import hashlib
from datetime import UTC, datetime

import duckdb
import pytest

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.acquisition.manifest import ManifestEntry
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import IngestionError
from imdb_ducklake.ingestion.resources import _read_csv_select, build_ingestion_resources

SPEC = DatasetSpec(
    name="example",
    file_name="example.tsv.gz",
    table_name="example",
    headers=("recordId", "optionalValue"),
)


def _artifact(tmp_path, rows: tuple[tuple[str, str], ...] = (("id1", "\\N"),)):
    path = tmp_path / SPEC.file_name
    content = "\t".join(SPEC.headers) + "\n"
    content += "".join("\t".join(row) + "\n" for row in rows)
    with gzip.open(path, "wb") as archive:
        archive.write(content.encode("utf-8"))
    compressed = path.read_bytes()
    entry = ManifestEntry(
        dataset=SPEC.name,
        file_name=SPEC.file_name,
        table_name=SPEC.table_name,
        url=SPEC.url,
        size_bytes=len(compressed),
        sha256=hashlib.sha256(compressed).hexdigest(),
        downloaded_at=datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
        batch_id="batch-1",
        row_count=len(rows),
    )
    return VerifiedArtifact(SPEC, path, entry)


def test_raw_query_reads_archive_losslessly(tmp_path) -> None:
    quoted_dir = tmp_path / "it's"
    quoted_dir.mkdir()
    artifact = _artifact(quoted_dir, (("id1", "\\N"), ("id2", ""), ("id3", "Exámple 東京")))

    with duckdb.connect(":memory:") as connection:
        relation = connection.sql(_read_csv_select(SPEC, artifact.path))
        assert relation.columns == list(SPEC.headers)
        assert set(relation.types) == {"VARCHAR"}
        assert relation.fetchall() == [("id1", "\\N"), ("id2", ""), ("id3", "Exámple 東京")]


def test_raw_resource_loads_as_frozen_sql_model(tmp_path) -> None:
    artifact = _artifact(tmp_path)

    raw_resource, metadata_resource = build_ingestion_resources((artifact,))
    table = raw_resource.compute_table_schema()
    metadata = list(metadata_resource)

    assert table["name"] == SPEC.table_name
    assert table["file_format"] == "model"
    assert table["write_disposition"] == "replace"
    assert list(table["columns"]) == list(SPEC.headers)
    assert raw_resource.schema_contract == {"columns": "freeze", "data_type": "freeze"}
    assert metadata[0]["table_name"] == SPEC.table_name
    assert metadata[0]["batch_id"] == "batch-1"
    assert metadata[0]["row_count"] == 1


def test_resources_require_unique_existing_verified_artifacts(tmp_path) -> None:
    artifact = _artifact(tmp_path)

    with pytest.raises(IngestionError, match="duplicate table names"):
        build_ingestion_resources((artifact, artifact))

    artifact.path.unlink()
    with pytest.raises(IngestionError, match="does not exist"):
        build_ingestion_resources((artifact,))


def test_resources_reject_empty_input() -> None:
    with pytest.raises(IngestionError, match="At least one"):
        build_ingestion_resources(())
