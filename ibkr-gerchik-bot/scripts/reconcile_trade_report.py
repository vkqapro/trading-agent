"""Import the latest TWS trade report without requiring a live IBKR socket."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.journal.flex_statement import (
    commit_flex_checkpoint,
    reconcile_statement_trades,
    run_flex_catch_up,
)


def main() -> int:
    result = run_flex_catch_up(commit=False)
    details = (
        reconcile_statement_trades(result.get("trades", []), return_details=True)
        if result.get("status") == "success"
        else {"added": 0, "unmatched": 0}
    )
    if result.get("status") == "success" and details.get("unmatched", 0) == 0:
        commit_flex_checkpoint(result)
    print(json.dumps({
        "status": result.get("status"),
        "source": result.get("source"),
        "archive": result.get("archive"),
        "reconciled": details.get("added", 0),
        "unmatched": details.get("unmatched", 0),
        "reason": result.get("reason"),
    }, indent=2))
    return 0 if result.get("status") in {"success", "disabled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
