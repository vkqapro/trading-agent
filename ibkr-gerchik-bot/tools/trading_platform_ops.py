"""Hermes-safe entry point for the repository operational supervisor."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.trading_platform_ops import main


if __name__ == "__main__":
    raise SystemExit(main())
