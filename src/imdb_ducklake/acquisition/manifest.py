"""Persistent metadata for verified IMDb source archives."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from imdb_ducklake.exceptions import AcquisitionError

MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """Metadata proving how and when one source archive was acquired."""

    dataset: str
    file_name: str
    table_name: str
    url: str
    size_bytes: int
    sha256: str
    downloaded_at: str
    batch_id: str
    row_count: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> ManifestEntry:
        if not isinstance(value, dict):
            raise AcquisitionError("Manifest entries must be JSON objects")
        try:
            return cls(
                dataset=_required_string(value, "dataset"),
                file_name=_required_string(value, "file_name"),
                table_name=_required_string(value, "table_name"),
                url=_required_string(value, "url"),
                size_bytes=_required_non_negative_int(value, "size_bytes"),
                sha256=_required_string(value, "sha256"),
                downloaded_at=_required_string(value, "downloaded_at"),
                batch_id=_required_string(value, "batch_id"),
                row_count=_optional_non_negative_int(value, "row_count"),
                etag=_optional_string(value, "etag"),
                last_modified=_optional_string(value, "last_modified"),
                content_type=_optional_string(value, "content_type"),
            )
        except AcquisitionError:
            raise
        except (TypeError, ValueError) as error:
            raise AcquisitionError("Manifest entry has invalid fields") from error


@dataclass(frozen=True, slots=True)
class Manifest:
    """Immutable collection of the latest verified source artifacts."""

    entries: tuple[ManifestEntry, ...] = ()

    def get(self, table_name: str) -> ManifestEntry | None:
        return next((entry for entry in self.entries if entry.table_name == table_name), None)

    def upsert(self, replacement: ManifestEntry) -> Manifest:
        retained = tuple(
            entry for entry in self.entries if entry.table_name != replacement.table_name
        )
        return Manifest(
            entries=tuple(sorted((*retained, replacement), key=lambda item: item.table_name))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": MANIFEST_VERSION,
            "entries": [asdict(entry) for entry in self.entries],
        }


def load_manifest(path: Path) -> Manifest:
    """Read a manifest, returning an empty one when it does not exist."""
    if not path.exists():
        return Manifest()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"Could not read manifest {path}") from error
    if not isinstance(value, dict):
        raise AcquisitionError(f"Manifest {path} must contain a JSON object")
    if value.get("version") != MANIFEST_VERSION:
        raise AcquisitionError(f"Unsupported manifest version in {path}")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise AcquisitionError(f"Manifest {path} has no entries array")
    manifest = Manifest(tuple(ManifestEntry.from_dict(entry) for entry in entries))
    if len({entry.table_name for entry in manifest.entries}) != len(manifest.entries):
        raise AcquisitionError(f"Manifest {path} contains duplicate table names")
    return manifest


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Atomically replace a manifest with a durable JSON representation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".part",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(manifest.to_dict(), temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        raise AcquisitionError(f"Could not write manifest {path}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _required_string(value: dict[str, Any], key: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field:
        raise AcquisitionError(f"Manifest field {key!r} must be a non-empty string")
    return field


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    field = value.get(key)
    if field is not None and not isinstance(field, str):
        raise AcquisitionError(f"Manifest field {key!r} must be a string or null")
    return field


def _required_non_negative_int(value: dict[str, Any], key: str) -> int:
    field = value.get(key)
    if not isinstance(field, int) or isinstance(field, bool) or field < 0:
        raise AcquisitionError(f"Manifest field {key!r} must be a non-negative integer")
    return field


def _optional_non_negative_int(value: dict[str, Any], key: str) -> int | None:
    field = value.get(key)
    if field is None:
        return None
    if not isinstance(field, int) or isinstance(field, bool) or field < 0:
        raise AcquisitionError(f"Manifest field {key!r} must be a non-negative integer or null")
    return field
