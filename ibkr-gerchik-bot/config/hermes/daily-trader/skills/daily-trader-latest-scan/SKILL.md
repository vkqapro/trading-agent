---
name: daily-trader-latest-scan
description: Retrieve the latest completed persisted Daily Trader scan without running a scan or refreshing Radar.
---

# Daily Trader latest persisted scan

Use this skill when a caller requests the latest completed persisted scan, its
exact `scan_id`, or the candidates belonging to that scan. This is a distinct
read-only operation from `daily-trader-scan`, which is used only for an
explicit new scan request.

Run only this command through the terminal tool, preserving the caller's
workspace:

```text
cmd.exe /d /c C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot\tools\daily_trader_api.py latest --format report
```

This command calls only `GET /api/daily-trader/scans/latest`. The endpoint
returns only a completed scan with a valid authoritative persisted result. It
must never call `POST /api/daily-trader/scan`, run scanner code, refresh Radar,
import repository trading code, or call broker/order/approval/execution APIs.

Preserve the exact rendered `scan_id`, `status`, `radar_as_of`, timestamps,
scanner requested/completed/failed counts, active candidates, terminal
candidates, lifecycle fields, warnings, and zero-side-effect counters. If the
endpoint returns no completed scan, report that explicit condition; do not
silently substitute an older scan or start a new one.
