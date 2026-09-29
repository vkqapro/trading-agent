"""Durable Decision Lab control, snapshot, request, and run state."""

from __future__ import annotations

import json
import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from src.config import SETTINGS
from src.decision.strategy_sources import DEFAULT_REGISTRY, StrategySourceRegistry


DEFAULT_ANALYSIS_MODE = "manual"
DEFAULT_STRATEGY_SOURCE = "gerchik_router"
RUN_ACTIVE_STATES = {"QUEUED", "RUNNING"}
MANUAL_QUEUE_STALE_SECONDS = 30 * 60
_LOCAL_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_dir(runtime_dir: Path | None = None) -> Path:
    path = Path(runtime_dir or SETTINGS.paths.runtime_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def control_path(runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(runtime_dir) / "decision_lab_control.json"


def snapshot_path(runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(runtime_dir) / "decision_lab_snapshots.json"


def snapshot_archive_path(scan_id: str, runtime_dir: Path | None = None) -> Path:
    digest = hashlib.sha256(str(scan_id).encode("utf-8")).hexdigest()[:32]
    return _runtime_dir(runtime_dir) / "decision_lab_snapshots" / f"{digest}.json"


def runs_path(runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(runtime_dir) / "decision_lab_runs.json"


def requests_path(runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(runtime_dir) / "decision_lab_requests.json"


def _read(path: Path, default: object) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _write_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _file_lock(path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    """Use a small sidecar lock so API and worker processes update state safely."""
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout_seconds
    handle: int | None = None
    while handle is None:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()}|{_now()}".encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"decision lab state lock timeout: {lock.name}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            lock.unlink()
        except OSError:
            pass


def load_control(*, runtime_dir: Path | None = None, registry: StrategySourceRegistry = DEFAULT_REGISTRY) -> dict[str, object]:
    default = {
        "analysis_mode": DEFAULT_ANALYSIS_MODE,
        "strategy_source": DEFAULT_STRATEGY_SOURCE,
        "prompt_preset_id": None,
        "updated_at": None,
        "updated_by": "default",
    }
    with _LOCAL_LOCK:
        raw = _read(control_path(runtime_dir), {})
    if not isinstance(raw, Mapping):
        return default
    mode = str(raw.get("analysis_mode") or DEFAULT_ANALYSIS_MODE).strip().lower()
    source = str(raw.get("strategy_source") or DEFAULT_STRATEGY_SOURCE).strip().lower()
    if mode not in {"manual", "auto"} or source not in registry.selection_ids():
        return default
    return {
        "analysis_mode": mode,
        "strategy_source": source,
        "prompt_preset_id": str(raw.get("prompt_preset_id") or "") or None,
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by") or "decision_lab",
    }


def update_control(
    *,
    analysis_mode: object,
    strategy_source: object,
    prompt_preset_id: object | None = None,
    updated_by: str = "decision_lab",
    runtime_dir: Path | None = None,
    registry: StrategySourceRegistry = DEFAULT_REGISTRY,
) -> dict[str, object]:
    mode = str(analysis_mode or "").strip().lower()
    source = str(strategy_source or "").strip().lower()
    if mode not in {"manual", "auto"}:
        raise ValueError("analysis_mode must be manual or auto")
    registry.validate_selection(source)
    payload = {
        "analysis_mode": mode,
        "strategy_source": source,
        "prompt_preset_id": str(prompt_preset_id or "") or None,
        "updated_at": _now(),
        "updated_by": str(updated_by or "decision_lab")[:80],
    }
    path = control_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        _write_atomic(path, payload)
    return payload


def load_snapshot(
    *,
    runtime_dir: Path | None = None,
    scan_id: str | None = None,
) -> dict[str, object] | None:
    path = snapshot_archive_path(scan_id, runtime_dir) if scan_id else snapshot_path(runtime_dir)
    value = _read(path, None)
    return value if isinstance(value, dict) else None


def save_snapshot(payload: Mapping[str, object], *, runtime_dir: Path | None = None) -> dict[str, object]:
    path = snapshot_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        _write_atomic(path, dict(payload))
        scan_id = str(payload.get("scan_id") or "").strip()
        if scan_id:
            archive = snapshot_archive_path(scan_id, runtime_dir)
            _write_atomic(archive, dict(payload))
    return dict(payload)


def _load_runs(runtime_dir: Path | None = None) -> list[dict[str, object]]:
    value = _read(runs_path(runtime_dir), [])
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _timestamp_age_seconds(value: object, *, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((now - parsed.astimezone(timezone.utc)).total_seconds(), 0.0)


def _recover_queued_runs(
    runs: list[dict[str, object]],
    *,
    now: datetime | None = None,
    queue_stale_seconds: float = MANUAL_QUEUE_STALE_SECONDS,
) -> bool:
    """Fail closed on queued requests that cannot be consumed safely."""
    current_time = now or datetime.now(timezone.utc)
    changed = False
    for item in runs:
        if item.get("status") != "QUEUED":
            continue
        reason: str | None = None
        if not str(item.get("scan_id") or "").strip():
            reason = "NO_COMPLETED_SNAPSHOT"
        else:
            age = _timestamp_age_seconds(item.get("requested_at"), now=current_time)
            if age is not None and age > max(float(queue_stale_seconds), 1.0):
                reason = "STALE_QUEUE_REQUEST"
        if reason:
            item.update(
                {
                    "status": "FAILED",
                    "completed_at": current_time.isoformat(),
                    "error": reason,
                    "failure_reason": reason,
                }
            )
            changed = True
    return changed


def recover_stale_runs(
    *,
    runtime_dir: Path | None = None,
    queue_stale_seconds: float = MANUAL_QUEUE_STALE_SECONDS,
) -> list[dict[str, object]]:
    """Recover impossible/orphaned queued requests under the shared file lock."""
    path = runs_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        runs = _load_runs(runtime_dir)
        if _recover_queued_runs(runs, queue_stale_seconds=queue_stale_seconds):
            _write_runs(runs, runtime_dir=runtime_dir)
        return runs


def list_runs(*, runtime_dir: Path | None = None, limit: int = 50) -> list[dict[str, object]]:
    runs = recover_stale_runs(runtime_dir=runtime_dir)
    return runs[: max(1, min(int(limit), 200))]


def _write_runs(runs: list[dict[str, object]], *, runtime_dir: Path | None = None) -> None:
    path = runs_path(runtime_dir)
    _write_atomic(path, runs[:200])


def create_run(
    *,
    analysis_mode: str,
    strategy_source: str,
    scan_id: str | None,
    prompt_preset_id: str | None = None,
    snapshot_timestamp: str | None = None,
    runtime_dir: Path | None = None,
    registry: StrategySourceRegistry = DEFAULT_REGISTRY,
) -> dict[str, object]:
    mode = str(analysis_mode).strip().lower()
    source = str(strategy_source).strip().lower()
    if mode not in {"manual", "auto"}:
        raise ValueError("analysis_mode must be manual or auto")
    registry.validate_selection(source)
    normalized_scan_id = str(scan_id or "").strip()
    normalized_snapshot_timestamp = str(snapshot_timestamp or "").strip()
    if mode == "manual" and not normalized_scan_id:
        raise ValueError("scan_id is required for a manual Decision Lab run")
    if mode == "manual" and not normalized_snapshot_timestamp:
        raise ValueError("snapshot_timestamp is required for a manual Decision Lab run")
    path = runs_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        runs = _load_runs(runtime_dir)
        _recover_queued_runs(runs)
        if mode == "auto" and scan_id:
            for item in runs:
                if (
                    item.get("analysis_mode") == "auto"
                    and item.get("strategy_selection") == source
                    and item.get("scan_id") == normalized_scan_id
                    and item.get("status") in RUN_ACTIVE_STATES | {"COMPLETED", "PARTIAL"}
                ):
                    return dict(item)
        if mode == "manual" and any(
            item.get("analysis_mode") == "manual" and item.get("status") in RUN_ACTIVE_STATES
            for item in runs
        ):
            raise RuntimeError("another Decision Lab analysis is already active")
        now = _now()
        run = {
            "run_id": f"llm-run-{uuid.uuid4().hex}",
            "scan_id": normalized_scan_id or None,
            "analysis_snapshot_id": normalized_scan_id or None,
            "snapshot_timestamp": normalized_snapshot_timestamp or None,
            "strategy_selection": source,
            "strategy_source": source,
            "prompt_preset_id": str(prompt_preset_id or "") or None,
            "analysis_mode": mode,
            "manual_or_auto": mode,
            "status": "QUEUED",
            "started_at": None,
            "completed_at": None,
            "requested_at": now,
            "candidate_count": 0,
            "applicable_candidate_count": 0,
            "not_applicable_count": 0,
            "decision_count": 0,
            "provider_success_count": 0,
            "provider_failure_count": 0,
            "enter_count": 0,
            "wait_count": 0,
            "reject_count": 0,
            "execution_count": 0,
            "model_enter_count": 0,
            "model_wait_count": 0,
            "model_reject_count": 0,
            "system_veto_count": 0,
            "risk_veto_count": 0,
            "error": None,
            "failure_reason": None,
        }
        runs.insert(0, run)
        _write_runs(runs, runtime_dir=runtime_dir)
        return run


def update_run(run_id: str, *, runtime_dir: Path | None = None, **updates: object) -> dict[str, object] | None:
    path = runs_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        runs = _load_runs(runtime_dir)
        for index, item in enumerate(runs):
            if item.get("run_id") == run_id:
                item.update(updates)
                runs[index] = item
                _write_runs(runs, runtime_dir=runtime_dir)
                return dict(item)
    return None


def claim_queued_run(run_id: str, *, runtime_dir: Path | None = None) -> dict[str, object] | None:
    """Atomically transition exactly one QUEUED run to RUNNING."""
    path = runs_path(runtime_dir)
    with _LOCAL_LOCK, _file_lock(path):
        runs = _load_runs(runtime_dir)
        for index, item in enumerate(runs):
            if item.get("run_id") != run_id or item.get("status") != "QUEUED":
                continue
            item.update(
                {
                    "status": "RUNNING",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            runs[index] = item
            _write_runs(runs, runtime_dir=runtime_dir)
            return dict(item)
    return None


def pending_manual_runs(*, runtime_dir: Path | None = None) -> list[dict[str, object]]:
    return [item for item in list_runs(runtime_dir=runtime_dir, limit=200) if item.get("analysis_mode") == "manual" and item.get("status") == "QUEUED"]


def manual_status_payload(*, runtime_dir: Path | None = None) -> dict[str, object]:
    runs = list_runs(runtime_dir=runtime_dir, limit=200)
    manual_runs = [item for item in runs if item.get("analysis_mode") == "manual"]
    active = next((item for item in manual_runs if item.get("status") in RUN_ACTIVE_STATES), None)
    last = manual_runs[0] if manual_runs else None
    return {
        "manual_queue_depth": sum(item.get("status") == "QUEUED" for item in manual_runs),
        "active_manual_run_id": active.get("run_id") if active else None,
        "active_manual_strategy": active.get("strategy_selection") if active else None,
        "active_manual_started_at": active.get("started_at") if active else None,
        "last_manual_run_state": last.get("status") if last else "IDLE",
        "last_manual_run_id": last.get("run_id") if last else None,
    }


def control_payload(
    *,
    runtime_dir: Path | None = None,
    registry: StrategySourceRegistry = DEFAULT_REGISTRY,
    strategy_source: str | None = None,
) -> dict[str, object]:
    control = load_control(runtime_dir=runtime_dir, registry=registry)
    snapshot = load_snapshot(runtime_dir=runtime_dir)
    runs = list_runs(runtime_dir=runtime_dir, limit=20)
    source = str(strategy_source or control.get("strategy_source") or DEFAULT_STRATEGY_SOURCE).strip().lower()
    registry.validate_selection(source)
    source_statuses = snapshot.get("source_status", {}) if isinstance(snapshot, Mapping) else {}
    source_counts = snapshot.get("source_counts", {}) if isinstance(snapshot, Mapping) else {}
    source_metadata = snapshot.get("source_metadata", {}) if isinstance(snapshot, Mapping) else {}
    sources = snapshot.get("sources", {}) if isinstance(snapshot, Mapping) else {}
    source_available = False
    source_count = 0
    source_status = "NO_COMPLETED_SNAPSHOT"
    if isinstance(snapshot, Mapping) and isinstance(sources, Mapping):
        if source == "all":
            source_available = bool(sources) and any(
                bool(value.get("available", True))
                for value in (source_metadata.values() if isinstance(source_metadata, Mapping) and source_metadata else ())
            )
            if not source_metadata:
                source_available = bool(sources) and any(
                    str(value).upper() != "SOURCE_UNAVAILABLE"
                    for value in (source_statuses.values() if isinstance(source_statuses, Mapping) else ())
                )
            source_count = sum(
                int(value or 0) for value in source_counts.values()
            ) if isinstance(source_counts, Mapping) else 0
            source_status = "READY" if source_available and source_count else "NO_CANDIDATES" if source_available else "SOURCE_UNAVAILABLE"
        elif source in sources:
            metadata = source_metadata.get(source, {}) if isinstance(source_metadata, Mapping) else {}
            source_available = bool(metadata.get("available", True)) if isinstance(metadata, Mapping) else True
            if not source_metadata:
                source_available = not isinstance(source_statuses, Mapping) or str(source_statuses.get(source, "READY")).upper() != "SOURCE_UNAVAILABLE"
            source_count = int(source_counts.get(source, len(sources.get(source, [])))) if isinstance(source_counts, Mapping) else len(sources.get(source, []))
            source_status = str(metadata.get("status") or (source_statuses.get(source, "READY" if source_count else "NO_CANDIDATES") if isinstance(source_statuses, Mapping) else ("READY" if source_count else "NO_CANDIDATES"))) if isinstance(metadata, Mapping) else ("READY" if source_count else "NO_CANDIDATES")
            if not source_available:
                source_status = "SOURCE_UNAVAILABLE"
    selected_metadata = source_metadata.get(source, {}) if isinstance(source_metadata, Mapping) else {}
    selected_metadata = selected_metadata if isinstance(selected_metadata, Mapping) else {}
    if source == "all" and isinstance(source_metadata, Mapping):
        source_items = [item for item in source_metadata.values() if isinstance(item, Mapping) and item.get("available")]
        timestamps = [str(item.get("source_timestamp")) for item in source_items if item.get("source_timestamp")]
        bar_timestamps = [str(item.get("latest_bar_timestamp")) for item in source_items if item.get("latest_bar_timestamp")]
        ages = [float(item.get("data_age_seconds")) for item in source_items if item.get("data_age_seconds") is not None]
        selected_metadata = {
            "source_timestamp": max(timestamps) if timestamps else None,
            "latest_bar_timestamp": max(bar_timestamps) if bar_timestamps else None,
            "data_age_seconds": max(ages) if ages else None,
            "freshness": "STALE" if any(str(item.get("freshness")) == "STALE" for item in source_items) else "FRESH",
        }
    selected_snapshot = {
        "available": source_available,
        "source": source,
        "analysis_snapshot_id": snapshot.get("analysis_snapshot_id", snapshot.get("scan_id")) if source_available and snapshot else None,
        "scan_id": snapshot.get("scan_id") if source_available and snapshot else None,
        "snapshot_timestamp": selected_metadata.get("source_timestamp") if source_available else None,
        "source_timestamp": selected_metadata.get("source_timestamp") if source_available else None,
        "latest_bar_timestamp": selected_metadata.get("latest_bar_timestamp") if source_available else None,
        "data_age_seconds": selected_metadata.get("data_age_seconds") if source_available else None,
        "freshness": selected_metadata.get("freshness") if source_available else "UNKNOWN",
        "status": source_status,
        "candidate_count": source_count,
    }
    active_runs = [item for item in runs if item.get("status") in RUN_ACTIVE_STATES]
    source_runs = [item for item in runs if str(item.get("strategy_selection") or "").lower() == source]
    return {
        **control,
        "latest_scan": {
            "scan_id": snapshot.get("scan_id"),
            "analysis_snapshot_id": snapshot.get("analysis_snapshot_id", snapshot.get("scan_id")),
            "completed_at": snapshot.get("completed_at"),
            "source_counts": snapshot.get("source_counts", {}),
            "source_metadata": snapshot.get("source_metadata", {}),
        } if snapshot else None,
        "latest_run": runs[0] if runs else None,
        "active_run": active_runs[0] if active_runs else None,
        "last_run": source_runs[0] if source_runs else None,
        "selected_source_snapshot": selected_snapshot,
        **manual_status_payload(runtime_dir=runtime_dir),
        "runs": runs,
    }
