"""Version-aware, analysis-only strategy documentation registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LIBRARY_ROOT = Path(__file__).with_name("strategy_library")


class StrategyLibrary:
    """Load strategy descriptors without importing or changing strategy code."""

    def __init__(self, root: Path | str = LIBRARY_ROOT):
        self.root = Path(root)

    def _registry(self) -> dict[str, Any]:
        with (self.root / "registry.json").open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("strategies"), dict):
            raise ValueError("Invalid Trading Research strategy registry")
        return data

    def list(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for strategy_id, definition in self._registry()["strategies"].items():
            if isinstance(definition, dict):
                entries.append({"strategy_id": strategy_id.upper(), **definition})
        return sorted(entries, key=lambda item: item["strategy_id"])

    def get(self, strategy_id: str, *, include_documentation: bool = False) -> dict[str, Any]:
        normalized = str(strategy_id or "").strip().upper()
        definition = self._registry()["strategies"].get(normalized)
        if not isinstance(definition, dict):
            raise KeyError(f"Strategy is not in the research library: {normalized}")
        result = {"strategy_id": normalized, **definition}
        if include_documentation:
            library_root = self.root.resolve()
            documentation = (library_root / str(definition["documentation"])).resolve()
            try:
                documentation.relative_to(library_root)
            except ValueError as exc:
                raise ValueError(f"Strategy documentation escapes the library root: {normalized}") from exc
            result["documentation_markdown"] = documentation.read_text(encoding="utf-8")
        return result

