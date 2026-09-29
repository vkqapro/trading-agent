"""Versioned, editable analytical prompt presets.

Built-ins are code-owned and immutable.  Custom entries are append-only JSON
records in the runtime directory; saving creates a new record rather than
mutating a historical version used by an earlier run.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


_FORBIDDEN_KEYS = re.compile(r"(?:api[_ -]?key|authorization|account[_ -]?id|password|passphrase|secret|access[_ -]?token)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_prompt_text(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    if not text:
        raise ValueError("prompt_text is required")
    if len(text) > 4000:
        raise ValueError("prompt_text exceeds the 4000 character limit")
    if _FORBIDDEN_KEYS.search(text):
        raise ValueError("prompt_text contains a forbidden secret or account field")
    return text


def _preset(prompt_id: str, source: str, name: str, description: str, prompt_text: str) -> dict[str, object]:
    text = validate_prompt_text(prompt_text)
    version = "v1"
    return {
        "prompt_id": prompt_id,
        "strategy_source": source,
        "name": name,
        "description": description,
        "version": version,
        "prompt_text": text,
        "built_in": True,
        "editable": False,
        "enabled": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "parent_prompt_id": None,
        "content_hash": _hash(text),
    }


_BUILT_INS = (
    _preset("builtin-gaussian-balanced-v1", "gaussian", "Gaussian Balanced", "Balanced analysis of a Gaussian watch signal.", "Evaluate the source-defined Gaussian signal conservatively. Distinguish a near trigger from a confirmed cross. Prefer WAIT when evidence is incomplete."),
    _preset("builtin-bmsb-balanced-v1", "bmsb", "BMSB Balanced", "Balanced analysis of a BMSB monitor signal.", "Evaluate the source-defined BMSB cross or near-cross. Do not infer a trade plan that the source did not provide."),
    _preset("builtin-stock-screener-balanced-v1", "stock_screener", "Stock Screener Balanced", "Balanced review of a deterministic screener plan.", "Evaluate the quality and completeness of the supplied deterministic plan. Do not alter its prices, quantity, or risk."),
    _preset("builtin-gerchik-router-balanced-v1", "gerchik_router", "Gerchik Router Balanced", "Balanced review of persisted Gerchik Router output.", "Evaluate the persisted router evidence and its existing deterministic plan. Do not invent missing levels or execution details."),
    _preset("builtin-all-balanced-v1", "all", "All Strategies Balanced", "Balanced source-aware analysis.", "Use the selected candidate's source definition and evidence. Do not substitute generic strategy assumptions."),
)


def preset_store_path(runtime_dir: Path | None = None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir) / "decision_lab_prompt_presets.json"
    from src.config import SETTINGS

    return Path(SETTINGS.paths.runtime_dir) / "decision_lab_prompt_presets.json"


def _load_custom(runtime_dir: Path | None = None) -> list[dict[str, object]]:
    path = preset_store_path(runtime_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(item) for item in payload if isinstance(item, Mapping) and not item.get("built_in")]


def _write_custom(items: list[dict[str, object]], runtime_dir: Path | None = None) -> None:
    path = preset_store_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def list_presets(strategy_source: str | None = None, *, runtime_dir: Path | None = None) -> list[dict[str, object]]:
    source = str(strategy_source or "").strip().lower()
    items = [dict(item) for item in _BUILT_INS] + _load_custom(runtime_dir)
    if source:
        items = [item for item in items if str(item.get("strategy_source")) in {source, "all"}]
    return items


def default_preset(strategy_source: str, *, runtime_dir: Path | None = None) -> dict[str, object]:
    source = str(strategy_source or "all").strip().lower()
    candidates = [item for item in list_presets(source, runtime_dir=runtime_dir) if item.get("enabled")]
    for item in candidates:
        if item.get("strategy_source") == source and item.get("built_in"):
            return item
    for item in candidates:
        if item.get("strategy_source") == "all" and item.get("built_in"):
            return item
    raise ValueError(f"No enabled prompt preset for strategy source: {strategy_source}")


def get_preset(prompt_id: str, *, runtime_dir: Path | None = None) -> dict[str, object]:
    for item in list_presets(runtime_dir=runtime_dir):
        if str(item.get("prompt_id")) == str(prompt_id):
            return item
    raise ValueError(f"Unknown prompt preset: {prompt_id}")


def duplicate_preset(prompt_id: str, *, name: str, runtime_dir: Path | None = None) -> dict[str, object]:
    parent = get_preset(prompt_id, runtime_dir=runtime_dir)
    custom_name = str(name or "").strip()
    if not custom_name:
        raise ValueError("name is required")
    text = validate_prompt_text(str(parent.get("prompt_text") or ""))
    now = _now()
    item = {
        **parent,
        "prompt_id": f"custom-{uuid.uuid4().hex}",
        "name": custom_name,
        "version": "v1",
        "built_in": False,
        "editable": True,
        "created_at": now,
        "updated_at": now,
        "parent_prompt_id": parent.get("prompt_id"),
        "content_hash": _hash(text),
    }
    items = _load_custom(runtime_dir)
    items.append(item)
    _write_custom(items, runtime_dir)
    return item


def save_preset(
    *,
    strategy_source: str,
    name: str,
    prompt_text: str,
    description: str = "",
    parent_prompt_id: str | None = None,
    as_new_version: bool = False,
    runtime_dir: Path | None = None,
) -> dict[str, object]:
    text = validate_prompt_text(prompt_text)
    source = str(strategy_source or "").strip().lower()
    if not source:
        raise ValueError("strategy_source is required")
    base = get_preset(parent_prompt_id, runtime_dir=runtime_dir) if parent_prompt_id else None
    if as_new_version and base is None:
        raise ValueError("parent_prompt_id is required for a new version")
    version = "v1"
    if base is not None:
        match = re.match(r"v(\d+)$", str(base.get("version") or "v1"))
        version = f"v{int(match.group(1)) + 1}" if match else "v2"
    now = _now()
    item = {
        "prompt_id": f"custom-{uuid.uuid4().hex}",
        "strategy_source": source,
        "name": str(name or "").strip() or (str(base.get("name")) if base else "Custom Prompt"),
        "description": str(description or (base.get("description") if base else "")),
        "version": version,
        "prompt_text": text,
        "built_in": False,
        "editable": True,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
        "parent_prompt_id": base.get("prompt_id") if base else parent_prompt_id,
        "content_hash": _hash(text),
    }
    items = _load_custom(runtime_dir)
    items.append(item)
    _write_custom(items, runtime_dir)
    return item


def disable_preset(prompt_id: str, *, runtime_dir: Path | None = None) -> dict[str, object]:
    item = get_preset(prompt_id, runtime_dir=runtime_dir)
    if item.get("built_in"):
        raise ValueError("built-in presets cannot be disabled")
    items = _load_custom(runtime_dir)
    for current in items:
        if current.get("prompt_id") == prompt_id:
            current["enabled"] = False
            current["updated_at"] = _now()
            _write_custom(items, runtime_dir)
            return current
    raise ValueError(f"Unknown prompt preset: {prompt_id}")
