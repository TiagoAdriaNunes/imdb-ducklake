"""Tests for concise, correlated dlt progress events."""

import json
from io import StringIO

from rich.console import Console

from imdb_ducklake.ingestion.progress import RichProgressCollector, StructuredLogCollector
from imdb_ducklake.observability import configure_logging, start_run_context


def test_load_progress_labels_schema_and_dlt_load_id() -> None:
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    start_run_context("run-progress")
    collector = StructuredLogCollector(build_id="build-progress", log_period=10)
    times = iter((0.0, 0.0, 0.0, 12.0, 12.0, 12.0))
    collector._clock = lambda: next(times)  # type: ignore[method-assign]

    with collector("Load raw in 1786839514.1750643"):
        collector.update("Jobs", inc=0, total=9)
        collector.update("Jobs", inc=4)

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    first_record = events[0]["record"]
    last_record = events[-1]["record"]
    assert first_record["message"] == "DLT progress"
    assert first_record["extra"]["event_code"] == "dlt_progress"
    assert first_record["extra"]["run_id"] == "run-progress"
    assert first_record["extra"]["build_id"] == "build-progress"
    assert first_record["extra"]["stage"] == "load"
    assert first_record["extra"]["schema"] == "raw"
    assert first_record["extra"]["dlt_load_id"] == "1786839514.1750643"
    assert last_record["extra"]["metrics"]["jobs"]["completed"] == 4
    assert last_record["extra"]["metrics"]["jobs"]["total"] == 9
    assert "rate_per_second" in last_record["extra"]["metrics"]["jobs"]


def test_rich_progress_formats_known_and_unknown_totals_compactly() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, color_system=None, width=160)
    collector = RichProgressCollector(
        console=console,
        refresh_per_second=100,
        transient=False,
    )
    collector.set_expected_totals({"title_akas": 58_070_000})

    with collector("Extract raw"):
        collector.update("Resources", inc=1, total=2)
        collector.update("title_akas", inc=29_035_000)

    output = stream.getvalue()
    assert "Extract raw/Resources" in output
    assert "1/2 resources" in output
    assert "Extract raw/title_akas" in output
    assert "50%" in output
    assert "29.0M/58.1M rows" in output
