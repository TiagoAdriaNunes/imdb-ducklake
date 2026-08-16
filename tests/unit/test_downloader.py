import gzip
import hashlib
import json
from datetime import UTC, datetime
from io import StringIO

import httpx
import pytest

from imdb_ducklake.acquisition.downloader import Downloader, load_verified_artifacts
from imdb_ducklake.acquisition.manifest import load_manifest
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import AcquisitionError
from imdb_ducklake.observability import configure_logging

SPEC = DatasetSpec(
    name="example",
    file_name="example.tsv.gz",
    table_name="example",
    headers=("id", "value"),
)
NOW = datetime(2026, 8, 15, 6, 30, tzinfo=UTC)


class InterruptingStream(httpx.SyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __iter__(self):
        yield self._content
        raise httpx.ReadError("connection interrupted")


def _archive(header: str = "id\tvalue", row: str = "1\texample") -> bytes:
    return gzip.compress(f"{header}\n{row}\n".encode())


def _paths(tmp_path):
    raw_dir = tmp_path / "raw"
    return raw_dir, raw_dir / "manifest.json"


def test_downloads_valid_archive_and_records_manifest(tmp_path) -> None:
    body = _archive()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == SPEC.url
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={
                "content-length": str(len(body)),
                "content-type": "application/gzip",
                "etag": '"abc"',
            },
        )

    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifacts = Downloader(client, clock=lambda: NOW).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path, batch_id="batch-1"
        )

    assert len(artifacts) == 1
    assert artifacts[0].path.read_bytes() == body
    entry = load_manifest(manifest_path).get(SPEC.table_name)
    assert entry is not None
    assert entry.size_bytes == len(body)
    assert entry.sha256 == hashlib.sha256(body).hexdigest()
    assert entry.downloaded_at == NOW.isoformat()
    assert entry.batch_id == "batch-1"
    assert entry.row_count == 1
    assert entry.etag == '"abc"'
    assert not list(raw_dir.glob("*.part"))


def test_reuses_archive_when_manifest_and_digest_match(tmp_path) -> None:
    body = _archive()
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, stream=httpx.ByteStream(body))

    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = Downloader(client, clock=lambda: NOW)
        first = downloader.download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)
        second = downloader.download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    assert requests == 1
    assert second[0].manifest_entry == first[0].manifest_entry

    retained = load_verified_artifacts(
        [SPEC],
        raw_dir=raw_dir,
        manifest_path=manifest_path,
    )
    assert retained == first


def test_reports_one_durable_event_per_dataset_downloaded_and_reused(tmp_path) -> None:
    body = _archive()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(body))

    raw_dir, manifest_path = _paths(tmp_path)
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = Downloader(client, clock=lambda: NOW)
        downloader.download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)
        stream.truncate(0)
        stream.seek(0)
        downloader.download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    events = [json.loads(line)["record"] for line in stream.getvalue().splitlines()]
    acquired = [event for event in events if event["extra"]["event_code"] == "dataset_acquired"]
    assert len(acquired) == 1
    assert acquired[0]["extra"]["dataset"] == SPEC.table_name
    assert acquired[0]["extra"]["file_name"] == SPEC.file_name
    assert acquired[0]["extra"]["reused"] is True
    assert acquired[0]["extra"]["bytes"] == len(body)


def test_loading_legacy_manifest_backfills_row_count(tmp_path) -> None:
    body = _archive()
    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=httpx.ByteStream(body))
        )
    ) as client:
        Downloader(client, clock=lambda: NOW).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["entries"][0]["row_count"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    retained = load_verified_artifacts([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    assert retained[0].manifest_entry.row_count == 1
    assert load_manifest(manifest_path).get(SPEC.table_name).row_count == 1


def test_retained_artifacts_require_manifest_and_matching_checksum(tmp_path) -> None:
    raw_dir, manifest_path = _paths(tmp_path)
    with pytest.raises(AcquisitionError, match="no verified entry"):
        load_verified_artifacts([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    body = _archive()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=httpx.ByteStream(body))
        )
    ) as client:
        Downloader(client, clock=lambda: NOW).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )
    (raw_dir / SPEC.file_name).write_bytes(body + b"corrupt")

    with pytest.raises(AcquisitionError, match="Checksum does not match"):
        load_verified_artifacts([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)


def test_retries_transient_server_error(tmp_path) -> None:
    body = _archive()
    requests = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            503 if requests == 1 else 200,
            stream=httpx.ByteStream(body),
        )

    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        Downloader(client, sleeper=delays.append).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )

    assert requests == 2
    assert delays == [0.5]


def test_retry_events_report_attempt_number_and_final_outcome(tmp_path) -> None:
    body = _archive()
    requests = 0

    def flaky_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            503 if requests == 1 else 200,
            stream=httpx.ByteStream(body),
        )

    raw_dir, manifest_path = _paths(tmp_path)
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    with httpx.Client(transport=httpx.MockTransport(flaky_handler)) as client:
        Downloader(client, sleeper=lambda _seconds: None).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )

    events = [json.loads(line)["record"] for line in stream.getvalue().splitlines()]
    retries = [event for event in events if event["extra"].get("event_code") == "acquisition_retry"]
    assert len(retries) == 1
    assert retries[0]["extra"]["dataset"] == SPEC.table_name
    assert retries[0]["extra"]["attempt"] == 1
    assert retries[0]["extra"]["attempts"] == 3
    assert body.decode(errors="ignore") not in stream.getvalue()

    def failing_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    stream.truncate(0)
    stream.seek(0)
    other_raw_dir, other_manifest_path = _paths(tmp_path / "other")
    with (
        httpx.Client(transport=httpx.MockTransport(failing_handler)) as client,
        pytest.raises(AcquisitionError),
    ):
        Downloader(client, attempts=2, sleeper=lambda _seconds: None).download_all(
            [SPEC], raw_dir=other_raw_dir, manifest_path=other_manifest_path
        )

    exhausted_events = [json.loads(line)["record"] for line in stream.getvalue().splitlines()]
    exhausted = [
        event
        for event in exhausted_events
        if event["extra"].get("event_code") == "acquisition_retry_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0]["extra"]["dataset"] == SPEC.table_name
    assert exhausted[0]["extra"]["attempts"] == 2
    assert exhausted[0]["extra"]["status"] == "failed"


def test_resumes_interrupted_download_from_saved_byte_offset(tmp_path) -> None:
    body = _archive(row="1\ta value long enough to split")
    split_at = len(body) // 2
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            assert "range" not in request.headers
            return httpx.Response(200, stream=InterruptingStream(body[:split_at]))
        assert request.headers["range"] == f"bytes={split_at}-"
        remainder = body[split_at:]
        return httpx.Response(
            206,
            stream=httpx.ByteStream(remainder),
            headers={
                "content-length": str(len(remainder)),
                "content-range": f"bytes {split_at}-{len(body) - 1}/{len(body)}",
            },
        )

    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifacts = Downloader(client, chunk_size=1, sleeper=lambda _delay: None).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )

    assert requests == 2
    assert artifacts[0].path.read_bytes() == body
    assert artifacts[0].manifest_entry.sha256 == hashlib.sha256(body).hexdigest()
    assert not list(raw_dir.glob("*.part"))


def test_keeps_partial_file_when_all_attempts_are_interrupted(tmp_path) -> None:
    body = _archive()
    split_at = len(body) // 2

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=InterruptingStream(body[:split_at]))

    raw_dir, manifest_path = _paths(tmp_path)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="after 1 attempts"),
    ):
        Downloader(client, attempts=1, chunk_size=1).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )

    assert (raw_dir / f"{SPEC.file_name}.part").read_bytes() == body[:split_at]


def test_restarts_when_server_ignores_range_request(tmp_path) -> None:
    body = _archive()
    split_at = len(body) // 2
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    (raw_dir / f"{SPEC.file_name}.part").write_bytes(body[:split_at])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == f"bytes={split_at}-"
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={"content-length": str(len(body))},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifact = Downloader(client).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )[0]

    assert artifact.path.read_bytes() == body
    assert not list(raw_dir.glob("*.part"))


def test_discards_stale_partial_and_restarts_from_zero(tmp_path) -> None:
    body = _archive()
    stale_prefix = b"not-gzip"
    requests = 0
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    (raw_dir / f"{SPEC.file_name}.part").write_bytes(stale_prefix)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            offset = len(stale_prefix)
            remainder = body[offset:]
            assert request.headers["range"] == f"bytes={offset}-"
            return httpx.Response(
                206,
                stream=httpx.ByteStream(remainder),
                headers={"content-range": f"bytes {offset}-{len(body) - 1}/{len(body)}"},
            )
        assert "range" not in request.headers
        return httpx.Response(200, stream=httpx.ByteStream(body))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifact = Downloader(client, sleeper=lambda _delay: None).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )[0]

    assert requests == 2
    assert artifact.path.read_bytes() == body


def test_rejects_mismatched_content_range_without_appending(tmp_path) -> None:
    body = _archive()
    split_at = len(body) // 2
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    (raw_dir / f"{SPEC.file_name}.part").write_bytes(body[:split_at])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            stream=httpx.ByteStream(body[split_at:]),
            headers={"content-range": f"bytes 0-{len(body) - 1}/{len(body)}"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="Invalid Content-Range"),
    ):
        Downloader(client).download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    assert not list(raw_dir.glob("*.part"))


def test_rejects_corrupt_gzip_received_from_byte_zero(tmp_path) -> None:
    invalid_body = b"not a gzip archive"
    raw_dir, manifest_path = _paths(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(invalid_body))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="Invalid gzip archive"),
    ):
        Downloader(client).download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)

    assert not list(raw_dir.glob("*.part"))


def test_resumes_after_content_length_mismatch(tmp_path) -> None:
    body = _archive(row="1\tcontent length retry")
    split_at = len(body) // 2
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            assert "range" not in request.headers
            return httpx.Response(
                200,
                stream=httpx.ByteStream(body[:split_at]),
                headers={"content-length": str(len(body))},
            )
        assert request.headers["range"] == f"bytes={split_at}-"
        remainder = body[split_at:]
        return httpx.Response(
            206,
            stream=httpx.ByteStream(remainder),
            headers={
                "content-length": str(len(remainder)),
                "content-range": f"bytes {split_at}-{len(body) - 1}/{len(body)}",
            },
        )

    raw_dir, manifest_path = _paths(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifact = Downloader(client, sleeper=lambda _delay: None).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )[0]

    assert requests == 2
    assert artifact.path.read_bytes() == body


def test_restarts_after_server_rejects_partial_range(tmp_path) -> None:
    body = _archive()
    partial = body[: len(body) // 2]
    requests = 0
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    (raw_dir / f"{SPEC.file_name}.part").write_bytes(partial)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            assert request.headers["range"] == f"bytes={len(partial)}-"
            return httpx.Response(416)
        assert "range" not in request.headers
        return httpx.Response(200, stream=httpx.ByteStream(body))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        artifact = Downloader(client, sleeper=lambda _delay: None).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path
        )[0]

    assert requests == 2
    assert artifact.path.read_bytes() == body


def test_archive_write_failure_preserves_existing_target(tmp_path, monkeypatch) -> None:
    body = _archive(row="1\treplacement")
    old_body = _archive(row="1\texisting")
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    target = raw_dir / SPEC.file_name
    target.write_bytes(old_body)
    original_open = type(target).open

    def fail_part_write(path, mode="r", *args, **kwargs):
        if path.name.endswith(".part") and mode == "wb":
            raise OSError("simulated disk failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(type(target), "open", fail_part_write)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(body))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="Could not store"),
    ):
        Downloader(client).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path, force=True
        )

    assert target.read_bytes() == old_body


def test_bad_header_does_not_replace_existing_archive(tmp_path) -> None:
    old_body = _archive(row="1\told")
    invalid_body = _archive(header="unexpected\theader")
    raw_dir, manifest_path = _paths(tmp_path)
    raw_dir.mkdir()
    target = raw_dir / SPEC.file_name
    target.write_bytes(old_body)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(invalid_body))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="Header mismatch"),
    ):
        Downloader(client).download_all(
            [SPEC], raw_dir=raw_dir, manifest_path=manifest_path, force=True
        )

    assert target.read_bytes() == old_body
    assert not manifest_path.exists()
    assert not list(raw_dir.glob("*.part"))


def test_non_retryable_http_error_has_context(tmp_path) -> None:
    raw_dir, manifest_path = _paths(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(AcquisitionError, match="HTTP 404"),
    ):
        Downloader(client).download_all([SPEC], raw_dir=raw_dir, manifest_path=manifest_path)
