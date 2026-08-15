"""Crash-conscious local DuckLake build workspace lifecycle."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

from imdb_ducklake.exceptions import LifecycleError, PromotionError

Clock = Callable[[], datetime]
TokenFactory = Callable[[], str]

_BUILD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROMOTION_JOURNAL = ".promotion.json"
_PROMOTION_JOURNAL_VERSION = 1


def new_build_id(
    *,
    clock: Clock | None = None,
    token_factory: TokenFactory | None = None,
) -> str:
    """Create a sortable, collision-resistant identifier for one complete build."""
    now = _require_aware((clock or (lambda: datetime.now(UTC)))(), "Build ID clock")
    token = _require_alnum_token(
        (token_factory or _random_token)(),
        error_type=LifecycleError,
        label="Build ID token",
    )
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{token}"


@dataclass(frozen=True, slots=True)
class BuildPaths:
    """Absolute paths owned by one isolated DuckLake build."""

    lakehouse_dir: Path
    build_id: str
    temporary_dir: Path
    catalog_path: Path
    storage_dir: Path

    @classmethod
    def create(cls, lakehouse_dir: Path, *, build_id: str | None = None) -> BuildPaths:
        resolved_root = lakehouse_dir.resolve()
        resolved_id = build_id or new_build_id()
        if not _BUILD_ID_PATTERN.fullmatch(resolved_id):
            raise LifecycleError(f"Invalid build ID: {resolved_id!r}")
        temporary_dir = resolved_root / "builds" / resolved_id
        return cls(
            lakehouse_dir=resolved_root,
            build_id=resolved_id,
            temporary_dir=temporary_dir,
            catalog_path=temporary_dir / "catalog.duckdb",
            storage_dir=temporary_dir / "storage",
        )

    @property
    def current_dir(self) -> Path:
        return self.lakehouse_dir / "current"

    @property
    def lock_path(self) -> Path:
        return self.lakehouse_dir / ".build.lock"


@dataclass(frozen=True, slots=True)
class SpaceBudget:
    """Conservative free-space requirement for one build attempt."""

    raw_archives_bytes: int
    current_build_bytes: int
    temporary_build_bytes: int
    reserve_bytes: int = 0

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(value < 0 for value in values.values()):
            raise ValueError("Space budget values must be non-negative")

    @property
    def required_bytes(self) -> int:
        return (
            self.raw_archives_bytes
            + self.current_build_bytes
            + self.temporary_build_bytes
            + self.reserve_bytes
        )


def ensure_free_space(path: Path, budget: SpaceBudget) -> int:
    """Require enough free bytes on the filesystem containing ``path``."""
    probe = _nearest_existing_parent(path.resolve())
    try:
        free_bytes = shutil.disk_usage(probe).free
    except OSError as error:
        raise LifecycleError(f"Could not inspect free space for {probe}") from error
    if free_bytes < budget.required_bytes:
        raise LifecycleError(
            f"Insufficient free space at {probe}: required {budget.required_bytes:,} bytes, "
            f"available {free_bytes:,} bytes"
        )
    return free_bytes


def initialize_build(paths: BuildPaths) -> None:
    """Create a new isolated build directory and its local storage directory."""
    _validate_owned_build_path(paths)
    try:
        paths.temporary_dir.parent.mkdir(parents=True, exist_ok=True)
        paths.temporary_dir.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise LifecycleError(f"Build workspace already exists: {paths.temporary_dir}") from error
    except OSError as error:
        raise LifecycleError(
            f"Could not initialize build workspace {paths.temporary_dir}"
        ) from error
    try:
        paths.storage_dir.mkdir()
    except OSError as error:
        shutil.rmtree(paths.temporary_dir, ignore_errors=True)
        raise LifecycleError(f"Could not initialize build storage {paths.storage_dir}") from error


def cleanup_build(paths: BuildPaths) -> None:
    """Remove only the validated temporary directory owned by ``paths``."""
    _validate_owned_build_path(paths)
    if not paths.temporary_dir.exists():
        return
    try:
        shutil.rmtree(paths.temporary_dir)
    except OSError as error:
        raise LifecycleError(f"Could not clean build workspace {paths.temporary_dir}") from error


@contextmanager
def temporary_build(paths: BuildPaths) -> Iterator[BuildPaths]:
    """Create a build workspace and remove it automatically after any failure."""
    initialize_build(paths)
    try:
        yield paths
    except BaseException:
        try:
            cleanup_build(paths)
        except LifecycleError as cleanup_error:
            raise LifecycleError(
                f"Build failed and cleanup also failed for {paths.temporary_dir}"
            ) from cleanup_error
        raise


@dataclass(frozen=True, slots=True)
class PromotedBuild:
    """Paths for the active build and the safely retained prior build, if any."""

    build_id: str
    current_dir: Path
    catalog_path: Path
    storage_dir: Path
    previous_dir: Path | None


def promote_build(
    paths: BuildPaths,
    *,
    token_factory: TokenFactory | None = None,
) -> PromotedBuild:
    """Promote a validated build, rolling the previous current build back on failure."""
    recover_interrupted_promotion(paths.lakehouse_dir)
    _validate_promotable_layout(paths)
    current_dir = paths.current_dir
    retired_dir: Path | None = None
    moved_previous = False
    had_current = current_dir.exists()
    try:
        paths.lakehouse_dir.mkdir(parents=True, exist_ok=True)
        if had_current:
            token = _require_alnum_token(
                (token_factory or _random_token)(),
                error_type=PromotionError,
                label="Retirement token",
            )
            retired_root = paths.lakehouse_dir / "retired"
            retired_root.mkdir(exist_ok=True)
            retired_dir = retired_root / f"{paths.build_id}-previous-{token}"
        _write_promotion_journal(paths, retired_dir=retired_dir, had_current=had_current)
        if retired_dir is not None:
            os.replace(current_dir, retired_dir)
            moved_previous = True
        os.replace(paths.temporary_dir, current_dir)
    except OSError as promotion_error:
        if moved_previous and retired_dir is not None and retired_dir.exists():
            try:
                os.replace(retired_dir, current_dir)
            except OSError as rollback_error:
                raise PromotionError(
                    f"Promotion and rollback both failed for build {paths.build_id}"
                ) from rollback_error
        with suppress(OSError):
            _promotion_journal_path(paths.lakehouse_dir).unlink()
        raise PromotionError(f"Could not promote build {paths.build_id}") from promotion_error
    with suppress(OSError):
        _promotion_journal_path(paths.lakehouse_dir).unlink()

    return PromotedBuild(
        build_id=paths.build_id,
        current_dir=current_dir,
        catalog_path=current_dir / paths.catalog_path.name,
        storage_dir=current_dir / paths.storage_dir.name,
        previous_dir=retired_dir,
    )


def recover_interrupted_promotion(lakehouse_dir: Path) -> bool:
    """Reconcile an interrupted promotion journal, preferring the prior valid build."""
    root = lakehouse_dir.resolve()
    journal_path = _promotion_journal_path(root)
    if not journal_path.exists():
        return False
    build_id, retired_name, had_current = _read_promotion_journal(journal_path)
    temporary_dir = root / "builds" / build_id
    current_dir = root / "current"
    retired_dir = root / "retired" / retired_name if retired_name is not None else None

    current_exists = current_dir.is_dir()
    temporary_exists = temporary_dir.is_dir()
    retired_exists = retired_dir is not None and retired_dir.is_dir()
    if current_exists and not temporary_exists:
        journal_path.unlink()
        return True
    if current_exists and temporary_exists:
        if had_current and not retired_exists:
            journal_path.unlink()
            return True
        raise PromotionError(f"Ambiguous interrupted promotion state in {root}")
    if had_current and retired_exists and retired_dir is not None:
        try:
            os.replace(retired_dir, current_dir)
            journal_path.unlink()
        except OSError as error:
            raise PromotionError(f"Could not recover interrupted promotion in {root}") from error
        return True
    if not had_current and temporary_exists:
        journal_path.unlink()
        return True
    raise PromotionError(f"Unrecoverable interrupted promotion state in {root}")


def prune_obsolete_builds(
    lakehouse_dir: Path,
    *,
    keep_retired: int = 1,
    protected_build_ids: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    """Remove orphaned build workspaces and all but the newest retired builds.

    Callers must hold the lakehouse build lock while pruning.
    """
    if keep_retired < 0:
        raise ValueError("keep_retired must be non-negative")
    if any(not _BUILD_ID_PATTERN.fullmatch(build_id) for build_id in protected_build_ids):
        raise LifecycleError("Protected build IDs must be valid build identifiers")
    root = lakehouse_dir.resolve()
    builds_root = root / "builds"
    retired_root = root / "retired"
    try:
        orphaned = _safe_child_directories(builds_root)
        orphaned = [path for path in orphaned if path.name not in protected_build_ids]
        retired = sorted(
            _safe_child_directories(retired_root),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        removed = [*orphaned, *retired[keep_retired:]]
        for path in removed:
            shutil.rmtree(path)
    except OSError as error:
        raise LifecycleError(f"Could not prune obsolete builds in {root}") from error
    return tuple(removed)


@dataclass(frozen=True, slots=True)
class BuildLockInfo:
    """Human-readable metadata stored while an OS-level build lock is held."""

    process_id: int
    acquired_at: str
    token: str


class BuildLock:
    """Cross-platform non-blocking single-writer lock for local builds."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock | None = None,
        token_factory: TokenFactory | None = None,
    ) -> None:
        self._path = path.resolve()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._handle: BinaryIO | None = None
        self._info: BuildLockInfo | None = None

    @property
    def info(self) -> BuildLockInfo | None:
        return self._info

    def acquire(self) -> BuildLockInfo:
        if self._handle is not None:
            raise LifecycleError(f"Build lock is already acquired: {self._path}")
        now = _require_aware(self._clock(), "Build lock clock")
        info = BuildLockInfo(
            process_id=os.getpid(),
            acquired_at=now.astimezone(UTC).isoformat(),
            token=self._token_factory(),
        )
        handle: BinaryIO | None = None
        locked = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(exist_ok=True)
            handle = self._path.open("r+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            if not _try_lock(handle):
                handle.close()
                owner = _read_lock_description(self._path)
                raise LifecycleError(f"Another build holds {self._path}{owner}")
            locked = True
            handle.seek(0)
            handle.write(b"\0")
            handle.write(json.dumps(asdict(info), sort_keys=True).encode("utf-8"))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        except LifecycleError:
            raise
        except OSError as error:
            if handle is not None and not handle.closed:
                if locked:
                    with suppress(OSError):
                        _unlock(handle)
                handle.close()
            raise LifecycleError(f"Could not acquire build lock {self._path}") from error
        assert handle is not None
        self._handle = handle
        self._info = info
        return info

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            _unlock(handle)
        except OSError as error:
            raise LifecycleError(f"Could not release build lock {self._path}") from error
        finally:
            handle.close()
            self._handle = None
            self._info = None

    def __enter__(self) -> BuildLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


def _nearest_existing_parent(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    raise LifecycleError(f"No existing filesystem parent for {path}")


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None:
        raise LifecycleError(f"{label} must return a timezone-aware datetime")
    return value


def _random_token() -> str:
    return uuid4().hex[:12]


def _require_alnum_token(
    token: str,
    *,
    error_type: type[LifecycleError],
    label: str,
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]+", token):
        raise error_type(f"{label} must contain only letters and digits")
    return token


def _validate_owned_build_path(paths: BuildPaths) -> None:
    expected_parent = (paths.lakehouse_dir / "builds").resolve()
    if (
        paths.temporary_dir.resolve().parent != expected_parent
        or paths.temporary_dir.name != paths.build_id
        or not _BUILD_ID_PATTERN.fullmatch(paths.build_id)
    ):
        raise LifecycleError(f"Unsafe temporary build path: {paths.temporary_dir}")


def _validate_promotable_layout(paths: BuildPaths) -> None:
    _validate_owned_build_path(paths)
    if not paths.temporary_dir.is_dir():
        raise PromotionError(f"Build directory does not exist: {paths.temporary_dir}")
    if not paths.catalog_path.is_file():
        raise PromotionError(f"DuckLake catalog does not exist: {paths.catalog_path}")
    if not paths.storage_dir.is_dir():
        raise PromotionError(f"DuckLake storage does not exist: {paths.storage_dir}")


def _promotion_journal_path(lakehouse_dir: Path) -> Path:
    return lakehouse_dir.resolve() / _PROMOTION_JOURNAL


def _write_promotion_journal(
    paths: BuildPaths,
    *,
    retired_dir: Path | None,
    had_current: bool,
) -> None:
    journal_path = _promotion_journal_path(paths.lakehouse_dir)
    temporary_path = journal_path.with_name(f".{journal_path.name}.{_random_token()}.tmp")
    value = {
        "version": _PROMOTION_JOURNAL_VERSION,
        "build_id": paths.build_id,
        "retired_name": retired_dir.name if retired_dir is not None else None,
        "had_current": had_current,
    }
    try:
        with temporary_path.open("x", encoding="utf-8", newline="\n") as destination:
            json.dump(value, destination, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, journal_path)
    except OSError as error:
        raise PromotionError(f"Could not record promotion intent for {paths.build_id}") from error
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_promotion_journal(path: Path) -> tuple[str, str | None, bool]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        build_id = value["build_id"]
        retired_name = value["retired_name"]
        had_current = value["had_current"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PromotionError(f"Could not read promotion journal {path}") from error
    if (
        value.get("version") != _PROMOTION_JOURNAL_VERSION
        or not isinstance(build_id, str)
        or not _BUILD_ID_PATTERN.fullmatch(build_id)
        or not isinstance(had_current, bool)
        or (
            retired_name is not None
            and (
                not isinstance(retired_name, str)
                or not re.fullmatch(rf"{re.escape(build_id)}-previous-[A-Za-z0-9]+", retired_name)
            )
        )
    ):
        raise PromotionError(f"Invalid promotion journal {path}")
    return build_id, retired_name, had_current


def _safe_child_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []
    children: list[Path] = []
    for path in root.iterdir():
        if path.is_symlink():
            raise LifecycleError(f"Refusing to prune symbolic link {path}")
        if path.is_dir():
            children.append(path)
    return children


def _read_lock_description(path: Path) -> str:
    try:
        with path.open("rb") as source:
            source.seek(1)
            value = json.loads(source.read().decode("utf-8"))
        return f" (process {value['process_id']}, acquired {value['acquired_at']})"
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return ""


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, import_module("msvcrt"))

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    fcntl = cast(Any, import_module("fcntl"))

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, import_module("msvcrt"))

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl = cast(Any, import_module("fcntl"))

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
