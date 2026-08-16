import json
import logging
import re
from io import StringIO

import pytest
from loguru import logger

from imdb_ducklake.observability import (
    configure_logging,
    get_console,
    rich_progress_enabled,
    start_run_context,
)


class _InteractiveStream(StringIO):
    def isatty(self) -> bool:
        return True


def test_json_logging_includes_bound_context_and_typed_fields() -> None:
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    start_run_context("run-123")

    logger.bind(component="test.logger").info(
        "Dataset completed",
        event_code="dataset_completed",
        stage="ingestion",
        dataset="title_basics",
        rows=42,
    )

    payload = json.loads(stream.getvalue())
    record = payload["record"]
    assert record["message"] == "Dataset completed"
    assert record["level"]["name"] == "INFO"
    assert record["extra"]["component"] == "test.logger"
    assert record["extra"]["event_code"] == "dataset_completed"
    assert record["extra"]["run_id"] == "run-123"
    assert record["extra"]["stage"] == "ingestion"
    assert record["extra"]["dataset"] == "title_basics"
    assert record["extra"]["rows"] == 42
    assert record["time"]["timestamp"] > 0


def test_standard_library_records_use_the_same_json_renderer() -> None:
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    start_run_context("run-stdlib")

    logging.getLogger("dependency").warning("dependency warning", extra={"stage": "load"})

    payload = json.loads(stream.getvalue())
    record = payload["record"]
    assert record["message"] == "dependency warning"
    assert record["level"]["name"] == "WARNING"
    assert record["extra"]["logger_name"] == "dependency"
    assert record["extra"]["run_id"] == "run-stdlib"
    assert record["extra"]["stage"] == "load"


def test_console_logging_is_human_readable() -> None:
    stream = StringIO()
    configure_logging("INFO", "console", stream=stream)
    start_run_context("run-console")

    logger.bind(component="test.logger").info(
        "Free-space check passed",
        event_code="free_space_gate_passed",
        stage="lifecycle",
        status="completed",
        build_id="build-123456789",
        available_bytes=61_782_441_984,
        required_bytes=17_299_953_907,
    )

    output = stream.getvalue()
    assert re.match(r"\d{2}:\d{2}:\d{2} \| INFO \|", output)
    assert "Free-space check passed" in output
    assert "run=run-cons" in output
    assert "build=build-12" in output
    assert "available=57.5GiB" in output
    assert "required=16.1GiB" in output
    assert "component=test.logger" in output
    assert "stage=" not in output
    assert "status=" not in output
    assert "_bytes=" not in output


def test_invalid_log_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported log format"):
        configure_logging("INFO", "xml")


def test_rich_progress_is_enabled_only_for_interactive_console_output() -> None:
    stream = _InteractiveStream()

    configure_logging("INFO", "console", stream=stream)

    assert rich_progress_enabled()
    assert get_console().file is stream

    configure_logging("INFO", "json", stream=stream)

    assert not rich_progress_enabled()
