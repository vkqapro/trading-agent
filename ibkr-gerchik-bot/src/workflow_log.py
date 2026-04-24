"""Structured workflow logging and recovery helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import append_markdown_log, ensure_directories


def append_workflow_snapshot(path: Path, stage: str, payload: Dict[str, Any]) -> None:
    """Append a human-readable section plus a machine-readable JSON snapshot."""
    summary = {"stage": stage}
    summary.update(_summarize_payload(payload))
    append_markdown_log(path, f"Workflow {stage}", summary)
    ensure_directories()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("```json\n")
        json.dump({"stage": stage, "payload": payload}, handle, indent=2)
        handle.write("\n```\n")


def read_latest_workflow_snapshot(path: Path, stage: str) -> Optional[Dict[str, Any]]:
    """Read the most recent JSON snapshot for the requested workflow stage."""
    if not path.exists():
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    current_stage: Optional[str] = None
    collecting = False
    json_lines: list[str] = []
    latest_payload: Optional[Dict[str, Any]] = None

    for line in lines:
        if line.startswith("## Workflow "):
            header = line[3:]
            current_stage = header.split(" (", 1)[0].replace("Workflow ", "", 1).strip()
            collecting = False
            json_lines = []
            continue

        if line.strip() == "```json":
            collecting = True
            json_lines = []
            continue

        if collecting and line.strip() == "```":
            collecting = False
            if current_stage == stage:
                try:
                    data = json.loads("\n".join(json_lines))
                except json.JSONDecodeError:
                    continue
                payload = data.get("payload")
                if isinstance(payload, dict):
                    latest_payload = payload
            json_lines = []
            continue

        if collecting:
            json_lines.append(line)

    return latest_payload


def _summarize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    if "watchlist" in payload and isinstance(payload["watchlist"], dict):
        summary["watchlist_symbols"] = list(payload["watchlist"].keys())
    if "executed" in payload and isinstance(payload["executed"], list):
        summary["executed_symbols"] = [item.get("symbol") for item in payload["executed"] if isinstance(item, dict)]
    if "tracked_positions" in payload and isinstance(payload["tracked_positions"], list):
        summary["tracked_symbols"] = [item.get("symbol") for item in payload["tracked_positions"] if isinstance(item, dict)]
    if "actions" in payload:
        summary["actions"] = payload["actions"]
    if "summary" in payload:
        summary["summary"] = payload["summary"]
    return summary
