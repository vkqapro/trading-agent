"""Immutable, human-readable JSON artifact writer for scanner runs."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_clean(payload), indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")


def write_run_artifacts(
    runs_root: Path,
    run_id: str,
    *,
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
    candidates: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    symbols: Mapping[str, dict[str, Any]],
) -> Path:
    """Create one new run directory; refusal to reuse a run id enforces immutability."""
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    symbols_dir = run_dir / "symbols"
    symbols_dir.mkdir()
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "candidates.json", {"run_id": run_id, "candidates": candidates})
    _write_json(run_dir / "errors.json", {"run_id": run_id, "errors": errors})
    for symbol, payload in symbols.items():
        filename = re.sub(r"[^A-Z0-9._-]", "_", symbol.upper()) + ".json"
        _write_json(symbols_dir / filename, payload)
    return run_dir
