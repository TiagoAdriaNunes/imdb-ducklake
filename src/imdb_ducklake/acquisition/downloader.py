"""Atomic, resumable acquisition of IMDb source archives."""

from __future__ import annotations

import gzip
import hashlib
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from imdb_ducklake.acquisition.manifest import (
    ManifestEntry,
    load_manifest,
    write_manifest,
)
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import AcquisitionError

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    """A local archive whose digest, gzip stream, and header were verified."""

    dataset: DatasetSpec
    path: Path
    manifest_entry: ManifestEntry


class _RetryableResponseError(Exception):
    pass


class Downloader:
    """Download and validate source archives without mutating good files in place."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        attempts: int = 3,
        chunk_size: int = 1024 * 1024,
        clock: Clock | None = None,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least one")
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least one")
        self._client = client
        self._attempts = attempts
        self._chunk_size = chunk_size
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper

    def download_all(
        self,
        datasets: Iterable[DatasetSpec],
        *,
        raw_dir: Path,
        manifest_path: Path,
        force: bool = False,
        batch_id: str | None = None,
    ) -> tuple[VerifiedArtifact, ...]:
        """Acquire every dataset and persist progress after each verified archive."""
        raw_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(manifest_path)
        acquisition_batch_id = batch_id or uuid4().hex
        artifacts: list[VerifiedArtifact] = []

        for dataset in datasets:
            target = raw_dir / dataset.file_name
            current_entry = manifest.get(dataset.table_name)
            if (
                not force
                and current_entry is not None
                and self._can_reuse(dataset, target, current_entry)
            ):
                artifacts.append(VerifiedArtifact(dataset, target, current_entry))
                continue

            entry = self._download_with_retries(
                dataset,
                target=target,
                batch_id=acquisition_batch_id,
            )
            manifest = manifest.upsert(entry)
            write_manifest(manifest_path, manifest)
            artifacts.append(VerifiedArtifact(dataset, target, entry))

        return tuple(artifacts)

    def _can_reuse(
        self,
        dataset: DatasetSpec,
        target: Path,
        entry: ManifestEntry,
    ) -> bool:
        if (
            entry.file_name != dataset.file_name
            or entry.url != dataset.url
            or entry.dataset != dataset.name
            or not target.is_file()
        ):
            return False
        size_bytes, sha256 = _hash_file(target, self._chunk_size)
        if size_bytes != entry.size_bytes or sha256 != entry.sha256:
            return False
        try:
            _validate_archive(target, dataset)
        except AcquisitionError:
            return False
        return True

    def _download_with_retries(
        self,
        dataset: DatasetSpec,
        *,
        target: Path,
        batch_id: str,
    ) -> ManifestEntry:
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                return self._download_once(dataset, target=target, batch_id=batch_id)
            except (httpx.TransportError, _RetryableResponseError) as error:
                last_error = error
                if attempt < self._attempts:
                    self._sleeper(0.5 * (2 ** (attempt - 1)))
        raise AcquisitionError(
            f"Could not download {dataset.file_name} after {self._attempts} attempts"
        ) from last_error

    def _download_once(
        self,
        dataset: DatasetSpec,
        *,
        target: Path,
        batch_id: str,
    ) -> ManifestEntry:
        temporary = target.with_name(f"{target.name}.part")
        resume_offset = temporary.stat().st_size if temporary.exists() else 0
        request_headers = {"Range": f"bytes={resume_offset}-"} if resume_offset else None
        digest = hashlib.sha256()
        size_bytes = resume_offset
        response_bytes = 0
        resumed = False
        expected_total: int | None = None
        try:
            with self._client.stream("GET", dataset.url, headers=request_headers) as response:
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    raise _RetryableResponseError(
                        f"{dataset.url} returned HTTP {response.status_code}"
                    )
                if resume_offset and response.status_code == 416:
                    temporary.unlink(missing_ok=True)
                    raise _RetryableResponseError(
                        f"Server rejected the partial download for {dataset.file_name}"
                    )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    temporary.unlink(missing_ok=True)
                    raise AcquisitionError(
                        f"{dataset.url} returned HTTP {response.status_code}"
                    ) from error

                if resume_offset and response.status_code == 206:
                    try:
                        expected_total = _validate_content_range(
                            response.headers.get("content-range"),
                            expected_start=resume_offset,
                            dataset=dataset,
                        )
                    except AcquisitionError:
                        temporary.unlink(missing_ok=True)
                        raise
                    resumed = True
                    size_bytes = 0
                    with temporary.open("rb") as existing:
                        while chunk := existing.read(self._chunk_size):
                            digest.update(chunk)
                            size_bytes += len(chunk)
                    output_mode = "ab"
                elif response.status_code == 200:
                    size_bytes = 0
                    output_mode = "wb"
                else:
                    temporary.unlink(missing_ok=True)
                    raise AcquisitionError(
                        f"Unexpected HTTP {response.status_code} while downloading "
                        f"{dataset.file_name}"
                    )

                with temporary.open(output_mode) as output:
                    for chunk in response.iter_raw(chunk_size=self._chunk_size):
                        output.write(chunk)
                        digest.update(chunk)
                        size_bytes += len(chunk)
                        response_bytes += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())

                content_length = response.headers.get("content-length")
                if (
                    content_length is not None
                    and content_length.isdigit()
                    and response_bytes != int(content_length)
                ):
                    raise _RetryableResponseError(
                        f"Incomplete download for {dataset.file_name}: "
                        f"expected {content_length} response bytes, received {response_bytes}"
                    )
                if expected_total is not None and size_bytes != expected_total:
                    raise _RetryableResponseError(
                        f"Incomplete resumed download for {dataset.file_name}: "
                        f"expected {expected_total} total bytes, received {size_bytes}"
                    )

                try:
                    _validate_archive(temporary, dataset)
                except AcquisitionError as error:
                    temporary.unlink(missing_ok=True)
                    if resumed:
                        raise _RetryableResponseError(
                            f"Resumed archive for {dataset.file_name} was invalid"
                        ) from error
                    raise
                os.replace(temporary, target)
                return ManifestEntry(
                    dataset=dataset.name,
                    file_name=dataset.file_name,
                    table_name=dataset.table_name,
                    url=dataset.url,
                    size_bytes=size_bytes,
                    sha256=digest.hexdigest(),
                    downloaded_at=self._clock().astimezone(UTC).isoformat(),
                    batch_id=batch_id,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    content_type=response.headers.get("content-type"),
                )
        except OSError as error:
            raise AcquisitionError(f"Could not store {dataset.file_name} at {target}") from error


def _validate_content_range(
    value: str | None,
    *,
    expected_start: int,
    dataset: DatasetSpec,
) -> int | None:
    try:
        unit, range_and_total = (value or "").split(" ", maxsplit=1)
        byte_range, total_value = range_and_total.split("/", maxsplit=1)
        start_value, end_value = byte_range.split("-", maxsplit=1)
        start = int(start_value)
        end = int(end_value)
        total = None if total_value == "*" else int(total_value)
    except (ValueError, TypeError) as error:
        raise AcquisitionError(
            f"Invalid Content-Range for {dataset.file_name}: {value!r}"
        ) from error
    if (
        unit != "bytes"
        or start != expected_start
        or end < start
        or (total is not None and (end >= total or total <= expected_start))
    ):
        raise AcquisitionError(f"Invalid Content-Range for {dataset.file_name}: {value!r}")
    return total


def _hash_file(path: Path, chunk_size: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
                size_bytes += len(chunk)
    except OSError as error:
        raise AcquisitionError(f"Could not read source archive {path}") from error
    return size_bytes, digest.hexdigest()


def _validate_archive(path: Path, dataset: DatasetSpec) -> None:
    try:
        with gzip.open(path, "rb") as source:
            header_bytes = source.readline()
            actual_header = tuple(header_bytes.rstrip(b"\r\n").decode("utf-8").split("\t"))
            if actual_header != dataset.headers:
                raise AcquisitionError(
                    f"Header mismatch for {dataset.file_name}: "
                    f"expected {dataset.headers!r}, received {actual_header!r}"
                )
            while source.read(1024 * 1024):
                pass
    except AcquisitionError:
        raise
    except (gzip.BadGzipFile, EOFError, OSError, UnicodeError) as error:
        raise AcquisitionError(f"Invalid gzip archive for {dataset.file_name}") from error
