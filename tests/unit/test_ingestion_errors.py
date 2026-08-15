"""Fast failure-boundary tests for the dlt ingestion pipeline."""

from datetime import UTC, datetime

import pytest

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.acquisition.manifest import ManifestEntry
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import IngestionError
from imdb_ducklake.ingestion import pipeline as ingestion_pipeline
from imdb_ducklake.ingestion.pipeline import ingest_snapshot
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build


def _artifacts(raw_dir) -> tuple[VerifiedArtifact, ...]:
    raw_dir.mkdir()
    artifacts = []
    for dataset in DATASETS:
        path = raw_dir / dataset.file_name
        path.write_bytes(b"fixture")
        artifacts.append(
            VerifiedArtifact(
                dataset=dataset,
                path=path,
                manifest_entry=ManifestEntry(
                    dataset=dataset.name,
                    file_name=dataset.file_name,
                    table_name=dataset.table_name,
                    url=dataset.url,
                    size_bytes=7,
                    sha256="digest",
                    downloaded_at=datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
                    batch_id="fixture",
                ),
            )
        )
    return tuple(artifacts)


def test_ingestion_requires_complete_snapshot_and_initialized_workspace(tmp_path) -> None:
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="unit-build")

    with pytest.raises(IngestionError, match="requires all seven datasets"):
        ingest_snapshot((), build_paths=paths, pipelines_dir=tmp_path / "pipelines")

    with pytest.raises(IngestionError, match="workspace is not initialized"):
        ingest_snapshot(
            _artifacts(tmp_path / "raw"),
            build_paths=paths,
            pipelines_dir=tmp_path / "pipelines",
        )


def test_dlt_startup_failure_is_wrapped_with_catalog_context(tmp_path, monkeypatch) -> None:
    artifacts = _artifacts(tmp_path / "raw")
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="build.with-symbols")
    initialize_build(paths)
    monkeypatch.setattr(ingestion_pipeline, "build_ingestion_resources", lambda *_a, **_kw: ())

    def fail_pipeline(**kwargs):
        assert kwargs["pipeline_name"] == "imdb_build_with_symbols"
        raise RuntimeError("dlt unavailable")

    monkeypatch.setattr(ingestion_pipeline.dlt, "pipeline", fail_pipeline)

    with pytest.raises(IngestionError, match="dlt could not load IMDb snapshot") as raised:
        ingest_snapshot(
            artifacts,
            build_paths=paths,
            pipelines_dir=tmp_path / "pipelines",
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(paths.catalog_path) in str(raised.value)
