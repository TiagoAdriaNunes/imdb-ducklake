import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from imdb_ducklake.exceptions import LifecycleError, PromotionError
from imdb_ducklake.lakehouse import lifecycle
from imdb_ducklake.lakehouse.lifecycle import (
    BuildLock,
    BuildPaths,
    SpaceBudget,
    cleanup_build,
    ensure_free_space,
    initialize_build,
    new_build_id,
    promote_build,
    temporary_build,
)

NOW = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)


def _paths(tmp_path, build_id: str = "build-1") -> BuildPaths:
    return BuildPaths.create(tmp_path / "ducklake", build_id=build_id)


def _complete_build(paths: BuildPaths, content: str = "new") -> None:
    initialize_build(paths)
    paths.catalog_path.write_text(content, encoding="utf-8")
    (paths.storage_dir / "data.parquet").write_text(content, encoding="utf-8")


def test_build_paths_are_absolute_and_isolated(tmp_path) -> None:
    paths = _paths(tmp_path)

    assert paths.lakehouse_dir.is_absolute()
    assert paths.temporary_dir == paths.lakehouse_dir / "builds" / "build-1"
    assert paths.catalog_path == paths.temporary_dir / "catalog.duckdb"
    assert paths.storage_dir == paths.temporary_dir / "storage"
    assert paths.current_dir == paths.lakehouse_dir / "current"
    assert paths.lock_path == paths.lakehouse_dir / ".build.lock"


def test_build_id_is_sortable_and_rejects_unsafe_values(tmp_path) -> None:
    assert new_build_id(clock=lambda: NOW, token_factory=lambda: "abc123") == (
        "20260815T123000Z-abc123"
    )

    with pytest.raises(LifecycleError, match="Invalid build ID"):
        BuildPaths.create(tmp_path, build_id="../outside")


def test_build_id_requires_aware_clock_and_safe_token() -> None:
    with pytest.raises(LifecycleError, match="timezone-aware"):
        new_build_id(clock=lambda: datetime(2026, 8, 15), token_factory=lambda: "abc")
    with pytest.raises(LifecycleError, match="only letters and digits"):
        new_build_id(clock=lambda: NOW, token_factory=lambda: "not-safe!")


def test_initialize_and_cleanup_build_workspace(tmp_path) -> None:
    paths = _paths(tmp_path)

    initialize_build(paths)

    assert paths.temporary_dir.is_dir()
    assert paths.storage_dir.is_dir()
    with pytest.raises(LifecycleError, match="already exists"):
        initialize_build(paths)

    cleanup_build(paths)
    cleanup_build(paths)
    assert not paths.temporary_dir.exists()


def test_temporary_build_cleans_failure_without_touching_current(tmp_path) -> None:
    paths = _paths(tmp_path)
    paths.current_dir.mkdir(parents=True)
    marker = paths.current_dir / "marker"
    marker.write_text("current", encoding="utf-8")

    with pytest.raises(RuntimeError, match="build failed"), temporary_build(paths):
        (paths.temporary_dir / "partial").write_text("partial", encoding="utf-8")
        raise RuntimeError("build failed")

    assert marker.read_text(encoding="utf-8") == "current"
    assert not paths.temporary_dir.exists()


def test_space_budget_and_available_space_check(tmp_path, monkeypatch) -> None:
    budget = SpaceBudget(10, 20, 30, 40)
    usage = SimpleNamespace(total=1_000, used=900, free=100)
    monkeypatch.setattr(lifecycle.shutil, "disk_usage", lambda _path: usage)

    assert budget.required_bytes == 100
    assert ensure_free_space(tmp_path / "not-created", budget) == 100


def test_insufficient_or_unreadable_space_is_reported(tmp_path, monkeypatch) -> None:
    budget = SpaceBudget(10, 20, 30, 41)
    usage = SimpleNamespace(total=1_000, used=900, free=100)
    monkeypatch.setattr(lifecycle.shutil, "disk_usage", lambda _path: usage)
    with pytest.raises(LifecycleError, match="Insufficient free space"):
        ensure_free_space(tmp_path, budget)

    def fail_usage(_path):
        raise OSError("unavailable")

    monkeypatch.setattr(lifecycle.shutil, "disk_usage", fail_usage)
    with pytest.raises(LifecycleError, match="Could not inspect free space"):
        ensure_free_space(tmp_path, budget)


def test_space_budget_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SpaceBudget(0, 0, -1)


def test_build_lock_records_owner_and_blocks_contention(tmp_path) -> None:
    path = tmp_path / ".build.lock"
    first = BuildLock(path, clock=lambda: NOW, token_factory=lambda: "first")
    second = BuildLock(path, clock=lambda: NOW, token_factory=lambda: "second")

    with first:
        assert first.info is not None
        assert first.info.token == "first"
        assert first.info.acquired_at == NOW.isoformat()
        with pytest.raises(LifecycleError, match="Another build holds"):
            second.acquire()

    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["token"] == "first"

    with second:
        assert second.info is not None
        assert second.info.token == "second"


def test_stale_lock_file_does_not_block_new_process_owner(tmp_path) -> None:
    path = tmp_path / ".build.lock"
    path.write_text("stale metadata from crashed process", encoding="utf-8")

    lock = BuildLock(path, clock=lambda: NOW, token_factory=lambda: "replacement")
    with lock:
        assert lock.info is not None
        assert lock.info.token == "replacement"


def test_build_lock_rejects_double_acquire_and_requires_aware_clock(tmp_path) -> None:
    path = tmp_path / ".build.lock"
    lock = BuildLock(path, clock=lambda: NOW)
    with lock, pytest.raises(LifecycleError, match="already acquired"):
        lock.acquire()

    naive = BuildLock(path, clock=lambda: datetime(2026, 8, 15))
    with pytest.raises(LifecycleError, match="timezone-aware"):
        naive.acquire()


def test_promotes_first_build_to_current(tmp_path) -> None:
    paths = _paths(tmp_path)
    _complete_build(paths)

    promoted = promote_build(paths)

    assert promoted.build_id == paths.build_id
    assert promoted.current_dir == paths.current_dir
    assert promoted.catalog_path.read_text(encoding="utf-8") == "new"
    assert (promoted.storage_dir / "data.parquet").is_file()
    assert promoted.previous_dir is None
    assert not paths.temporary_dir.exists()


def test_promotes_new_build_and_preserves_previous_build(tmp_path) -> None:
    root = tmp_path / "ducklake"
    current = root / "current"
    current.mkdir(parents=True)
    (current / "catalog.duckdb").write_text("old", encoding="utf-8")
    (current / "storage").mkdir()
    paths = BuildPaths.create(root, build_id="build-2")
    _complete_build(paths, content="new")

    promoted = promote_build(paths, token_factory=lambda: "retired")

    assert promoted.catalog_path.read_text(encoding="utf-8") == "new"
    assert promoted.previous_dir is not None
    assert (promoted.previous_dir / "catalog.duckdb").read_text(encoding="utf-8") == "old"


def test_promotion_failure_rolls_previous_build_back(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ducklake"
    current = root / "current"
    current.mkdir(parents=True)
    marker = current / "marker"
    marker.write_text("old", encoding="utf-8")
    paths = BuildPaths.create(root, build_id="build-2")
    _complete_build(paths)
    real_replace = lifecycle.os.replace

    def fail_new_build(source, destination):
        if Path(source) == paths.temporary_dir:
            raise OSError("simulated promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(lifecycle.os, "replace", fail_new_build)

    with pytest.raises(PromotionError, match="Could not promote"):
        promote_build(paths, token_factory=lambda: "rollback")

    assert marker.read_text(encoding="utf-8") == "old"
    assert paths.temporary_dir.exists()
    assert not (root / "retired" / "build-2-previous-rollback").exists()


def test_promotion_rejects_unsafe_retirement_token_before_moving_current(tmp_path) -> None:
    root = tmp_path / "ducklake"
    current = root / "current"
    current.mkdir(parents=True)
    marker = current / "marker"
    marker.write_text("old", encoding="utf-8")
    paths = BuildPaths.create(root, build_id="build-2")
    _complete_build(paths)

    with pytest.raises(PromotionError, match="Retirement token"):
        promote_build(paths, token_factory=lambda: "../unsafe")

    assert marker.read_text(encoding="utf-8") == "old"
    assert paths.temporary_dir.exists()


def test_incomplete_build_cannot_be_promoted(tmp_path) -> None:
    paths = _paths(tmp_path)
    initialize_build(paths)

    with pytest.raises(PromotionError, match="catalog does not exist"):
        promote_build(paths)

    assert paths.temporary_dir.exists()
    assert not paths.current_dir.exists()
