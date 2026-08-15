import json
from dataclasses import asdict

import pytest

from imdb_ducklake.acquisition.manifest import (
    Manifest,
    ManifestEntry,
    load_manifest,
    write_manifest,
)
from imdb_ducklake.exceptions import AcquisitionError


def _entry() -> ManifestEntry:
    return ManifestEntry(
        dataset="example",
        file_name="example.tsv.gz",
        table_name="example",
        url="https://example.test/example.tsv.gz",
        size_bytes=42,
        sha256="a" * 64,
        downloaded_at="2026-08-15T06:30:00+00:00",
        batch_id="batch-1",
        etag='"abc"',
    )


def test_manifest_round_trip(tmp_path) -> None:
    path = tmp_path / "raw" / "manifest.json"

    write_manifest(path, Manifest((_entry(),)))

    assert load_manifest(path) == Manifest((_entry(),))
    assert not list(path.parent.glob("*.part"))


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"version": 999, "entries": []},
        {"version": 1},
        {"version": 1, "entries": [{"dataset": "incomplete"}]},
    ],
)
def test_invalid_manifest_is_rejected(tmp_path, value) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(AcquisitionError):
        load_manifest(path)


def test_duplicate_table_names_are_rejected(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    entry = _entry()
    path.write_text(
        json.dumps({"version": 1, "entries": [asdict(entry), asdict(entry)]}),
        encoding="utf-8",
    )

    with pytest.raises(AcquisitionError, match="duplicate table names"):
        load_manifest(path)
