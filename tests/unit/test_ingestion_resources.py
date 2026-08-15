import gzip
import hashlib
import warnings
from datetime import UTC, datetime

import pyarrow as pa
import pytest

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.acquisition.manifest import ManifestEntry
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import IngestionError
from imdb_ducklake.ingestion.resources import build_ingestion_resources

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
    )
    return VerifiedArtifact(SPEC, path, entry)


def test_raw_resource_preserves_headers_strings_and_null_literal(tmp_path) -> None:
    artifact = _artifact(tmp_path, (("id1", "\\N"), ("id2", "")))

    raw_resource, metadata_resource = build_ingestion_resources((artifact,), chunk_size=1)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        batches = list(raw_resource)
    metadata = list(metadata_resource)

    assert all(isinstance(batch, pa.RecordBatch) for batch in batches)
    assert [batch.schema.names for batch in batches] == [list(SPEC.headers)] * 2
    assert [batch.to_pylist()[0] for batch in batches] == [
        {"recordId": "id1", "optionalValue": "\\N"},
        {"recordId": "id2", "optionalValue": ""},
    ]
    assert metadata[0]["table_name"] == SPEC.table_name
    assert metadata[0]["batch_id"] == "batch-1"


def test_resources_require_unique_existing_verified_artifacts(tmp_path) -> None:
    artifact = _artifact(tmp_path)

    with pytest.raises(IngestionError, match="duplicate table names"):
        build_ingestion_resources((artifact, artifact))

    artifact.path.unlink()
    with pytest.raises(IngestionError, match="does not exist"):
        build_ingestion_resources((artifact,))


def test_resources_reject_empty_input_and_invalid_chunk_size() -> None:
    with pytest.raises(IngestionError, match="At least one"):
        build_ingestion_resources(())
    with pytest.raises(ValueError, match="at least one"):
        build_ingestion_resources((), chunk_size=0)
