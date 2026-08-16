"""Interactive and structured dlt progress collectors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from threading import RLock
from typing import Any

from dlt.common.runtime.collector import LogCollector
from dlt.common.runtime.collector_base import Collector
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Column
from rich.text import Text

_STEP_PATTERN = re.compile(
    r"^(?P<stage>Extract|Normalize|Load) (?P<schema>\S+)(?: in (?P<load_id>\S+))?$"
)


class _CompactCountColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        unit = str(task.fields["unit"])
        completed = _compact_number(task.completed)
        if task.total is None:
            return Text(f"{completed} {unit}")
        return Text(f"{completed}/{_compact_number(task.total)} {unit}")


class _CompactRateColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        if task.speed is None:
            return Text("")
        return Text(f"{_compact_number(task.speed)} {task.fields['unit']}/s")


class RichProgressCollector(Collector):
    """Render dlt counters as a compact Rich live display."""

    def __init__(
        self,
        *,
        console: Console,
        refresh_per_second: float = 4.0,
        transient: bool = True,
    ) -> None:
        self.step = ""
        self._console = console
        self._refresh_per_second = refresh_per_second
        self._transient = transient
        self._progress: Progress | None = None
        self._tasks: dict[str, TaskID] = {}
        self._totals: dict[str, float | None] = {}
        self._expected_totals: dict[str, int] = {}
        self._lock = RLock()

    def set_expected_totals(self, totals: Mapping[str, int]) -> None:
        """Provide totals for dlt row counters that do not declare their own."""
        with self._lock:
            self._expected_totals = dict(totals)

    def _start(self, step: str) -> None:
        with self._lock:
            self._tasks = {}
            self._totals = {}
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn(
                    "[progress.description]{task.description}",
                    table_column=Column(ratio=1),
                ),
                BarColumn(bar_width=None, table_column=Column(ratio=2)),
                TaskProgressColumn(),
                _CompactCountColumn(),
                _CompactRateColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(compact=True, elapsed_when_finished=True),
                console=self._console,
                refresh_per_second=self._refresh_per_second,
                transient=self._transient,
                redirect_stdout=False,
                redirect_stderr=False,
                expand=True,
            )
            self._progress.start()

    def _stop(self) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress.refresh()
                self._progress.stop()
            self._progress = None
            self._tasks.clear()
            self._totals.clear()

    def update(
        self,
        name: str,
        inc: int = 1,
        total: int | None = None,
        inc_total: int | None = None,
        message: str | None = None,
        label: str | None = None,
    ) -> None:
        del message
        with self._lock:
            if self._progress is None:
                raise RuntimeError("Rich progress collector has not been started")

            key = f"{name}_{label}" if label else name
            task_id = self._tasks.get(key)
            effective_total = total if total is not None else self._expected_totals.get(name)
            if task_id is None:
                selected_total = float(effective_total) if effective_total is not None else None
                task_id = self._progress.add_task(
                    _task_description(self.step, name, label),
                    total=selected_total,
                    unit=_counter_unit(name),
                )
                self._tasks[key] = task_id
                self._totals[key] = selected_total

            update_fields: dict[str, Any] = {"advance": inc}
            selected_total = self._totals[key]
            if selected_total is None and effective_total is not None:
                selected_total = float(effective_total)
            if inc_total is not None:
                selected_total = (selected_total or 0.0) + inc_total
            if selected_total != self._totals[key]:
                self._totals[key] = selected_total
                update_fields["total"] = selected_total
            self._progress.update(task_id, **update_fields)


class StructuredLogCollector(LogCollector):
    """Render dlt counters as throttled structured events."""

    def __init__(self, *, build_id: str, log_period: float = 10.0) -> None:
        super().__init__(log_period=log_period, logger="stdout", dump_system_stats=False)
        self._event_logger = logger.bind(build_id=build_id)
        self._last_snapshot: tuple[tuple[str, int, int | None, str | None], ...] | None = None

    def _start(self, step: str) -> None:
        super()._start(step)
        self._last_snapshot = None

    def dump_counters(self) -> None:
        """Emit one event containing every current dlt counter."""
        snapshot = tuple(
            (
                name,
                count,
                self.counter_info[name].total,
                self.messages[name],
            )
            for name, count in self.counters.items()
        )
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot

        now = self._clock()
        metrics: dict[str, dict[str, Any]] = {}
        for name, count in self.counters.items():
            info = self.counter_info[name]
            elapsed = max(0.0, now - info.start_time)
            metric: dict[str, Any] = {
                "completed": count,
                "elapsed_seconds": round(elapsed, 2),
            }
            if info.total is not None:
                metric["total"] = info.total
            if elapsed >= 1.0 and count > 0:
                metric["rate_per_second"] = round(count / elapsed, 2)
            if self.messages[name] is not None:
                metric["message"] = self.messages[name]
            metrics[_metric_name(info.description)] = metric

        fields: dict[str, Any] = {"metrics": metrics}
        match = _STEP_PATTERN.fullmatch(self.step)
        if match:
            fields["stage"] = match.group("stage").lower()
            fields["schema"] = match.group("schema")
            if match.group("load_id"):
                fields["dlt_load_id"] = match.group("load_id")
        else:
            fields["dlt_step"] = self.step
        self._event_logger.info("DLT progress", event_code="dlt_progress", **fields)


def _metric_name(description: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")


def _task_description(step: str, name: str, label: str | None) -> str:
    selected_name = f"{name}[{label}]" if label else name
    if not step:
        return selected_name
    match = _STEP_PATTERN.fullmatch(step)
    selected_step = f"{match.group('stage')} {match.group('schema')}" if match else step
    return f"{selected_step}/{selected_name}"


def _counter_unit(name: str) -> str:
    normalized = _metric_name(name)
    if "resource" in normalized:
        return "resources"
    if "file" in normalized:
        return "files"
    if "job" in normalized:
        return "jobs"
    return "rows"


def _compact_number(value: float) -> str:
    magnitude = abs(value)
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if magnitude >= scale:
            return f"{value / scale:.1f}{suffix}"
    return f"{value:,.0f}"
