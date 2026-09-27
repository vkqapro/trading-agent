# LLM Shadow runtime validation

This is the reproducible, stock-only preparation and validation procedure for
the autonomous LLM Shadow path. It is deliberately conservative:

- no `.env` file is edited by this procedure;
- the preflight is read-only and does not create the Decision Lab database;
- no production worker, Live mode, order request, or broker order is started by
  the preparation work;
- the safest first runtime uses `PAPER_TRADING=true` and `DRY_RUN_MODE=true`;
- Shadow observations are isolated from broker execution, but the legacy scan
  still connects to the configured paper broker for market data and can reach
  its normal execution code. `DRY_RUN_MODE=true` is therefore required for the
  first run.

The status of this preparation is recorded in
`docs/SHADOW_RUNTIME_PREPARATION_REPORT.md`.

## 1. Scope and prerequisites

Use a Windows PowerShell session in the repository root:

```powershell
Set-Location C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot
```

The repository dependencies and the project virtual environment must already
be installed. If the project uses its shared virtual environment, activate it
before the commands below:

```powershell
.\.venv\Scripts\Activate.ps1
```

The first runtime observation is stocks only. Use the existing `open` or
`intraday` jobs; do not use crypto, FX, IRS, `manual_watch --execute`, the
dashboard order endpoints, or any Live/autonomous mode.

The job needs TWS or IB Gateway only for the existing market-data path. Use the
paper account and the broker host/port already configured for this checkout.
The repository default is `IBKR_PORT=7497`, but the effective environment must
be checked locally rather than assumed. Do not use a live-account socket.

The dashboard may be used as a read-only observer. Do not use
`run_react_dashboard.cmd` or `run_dashboard.cmd` for this procedure: those
launch additional workers, including an execution worker. If the data-only API
is needed, start only the existing FastAPI server in a separate session:

```powershell
python -m uvicorn dashboard_react.server:app --host 127.0.0.1 --port 8550
```

That command is optional and is not part of the first preflight.

## 2. Exact process-scoped Shadow configuration

Set the following in the PowerShell process that will run the preflight and
the later one-shot job. These assignments do not write `.env`; closing the
PowerShell session removes them.

```powershell
$env:LLM_AGENT_MODE = "shadow"
$env:ALLOW_LLM_LIVE_TRADING = "false"
$env:LLM_AGENT_USE_NEWS = "false"
$env:LLM_DECISION_PROVIDER = "deepseek"
$env:LLM_DECISION_MODEL = "<configured model>"
$env:LLM_MULTI_PROVIDER_SHADOW = "false"
$env:LLM_DECISION_TIMEOUT_SECONDS = "15"
$env:LLM_DECISION_WORKERS = "2"
$env:LLM_DECISION_QUEUE_DEPTH = "16"
$env:LLM_CANDIDATE_EXPIRY_SECONDS = "45"
$env:LLM_DECISION_DATABASE_PATH = "memory/decision_lab.db"
$env:PAPER_TRADING = "true"
$env:DRY_RUN_MODE = "true"
```

Set the provider credential only if it is already available through the
machine's approved secret handling. Never paste a real key into this document,
the repository, or chat:

```powershell
$env:LLM_DEEPSEEK_API_KEY = "<secret supplied through the approved local mechanism>"
```

The model placeholder must be replaced with the exact model configured for the
DeepSeek account. The preflight only checks that a model is present; it does
not infer or invent a model name. `LLM_DEEPSEEK_BASE_URL` remains the provider
default unless the local configuration intentionally overrides it.

The relevant values are intentionally explicit:

| Setting | Required first-run value |
| --- | --- |
| `LLM_AGENT_MODE` | `shadow` |
| `ALLOW_LLM_LIVE_TRADING` | `false` |
| `LLM_AGENT_USE_NEWS` | `false` |
| `LLM_DECISION_PROVIDER` | `deepseek` |
| `LLM_DECISION_MODEL` | exact configured model; no placeholder at runtime |
| `LLM_MULTI_PROVIDER_SHADOW` | `false` |
| `PAPER_TRADING` | `true` |
| `DRY_RUN_MODE` | `true` |

`PAPER_TRADING` and `DRY_RUN_MODE` are not substitutes for the LLM live guard:
all three safety boundaries must remain in force.

## 3. Read-only preflight

Run the preflight before starting any job:

```powershell
python scripts/check_llm_shadow_ready.py --allow-uninitialized-db
```

This checks the effective mode, LLM live permission, autonomous-News flag,
paper/dry-run broker flags, legacy live-trading flag, legacy paper mode,
provider/model, single-provider Shadow, worker count, queue depth, candidate
expiry, Decision Lab parent-directory access, and (when present) the required
read-only SQLite schema. It does not create the database, start workers,
connect to IBKR, submit an order, or mutate `.env`.

On a clean checkout, `--allow-uninitialized-db` permits one warning that
`memory/decision_lab.db` does not yet exist. It does not permit a missing
parent directory or an unreadable database. After the first safe job has
initialized the schema, rerun the strict form and require the schema check to
pass:

```powershell
python scripts/check_llm_shadow_ready.py
```

The strict form is the required gate for an already-initialized runtime. Do
not treat a preflight with a missing model, `DRY_RUN_MODE=false`,
`PAPER_TRADING=false`, `ALLOW_LLM_LIVE_TRADING=true`, or
`LLM_AGENT_USE_NEWS=true` as ready.

Provider connectivity is intentionally separate and opt-in:

```powershell
python scripts/check_llm_shadow_ready.py --check-provider
```

This makes one synthetic stock `WAIT` request directly to the configured LLM
provider. It does not write Decision Lab state or contact IBKR. Run it only
after the key and exact model are already configured; the provider request is
not required to prove that the local configuration is structurally safe.

## 4. Exact first-run sequence

The preparation work stops before this sequence. When a deliberate first
runtime observation is authorized, use the same PowerShell process in this
order:

1. Start TWS or IB Gateway on the paper account and verify its API listener
   and market-data entitlement locally.
2. Run the preflight with `--allow-uninitialized-db` if the Decision Lab file
   is new. Resolve every `FAIL`; the only allowed fresh-checkout warning is
   the uninitialized database schema.
3. Optionally run the provider-only check. Do not substitute it for the
   preflight.
4. Run the existing stock open scan as a one-shot dry run:

   ```powershell
   python -m src.main --job open
   ```

   The job may connect to the paper broker for quotes and existing account
   context. With `DRY_RUN_MODE=true`, the legacy order manager must not submit
   broker orders. No `--execute` flag is used.
5. After the job exits, rerun the strict preflight:

   ```powershell
   python scripts/check_llm_shadow_ready.py
   ```

6. If an intraday observation is separately authorized, use the existing
   stock job in another deliberate session:

   ```powershell
   python -m src.main --job intraday
   ```

   Stop it through its normal operator mechanism. Do not create a second
   worker, scheduler, or production service as part of Shadow preparation.

The `open` and `intraday` jobs are existing operational entry points, not
preflight commands. The preparation procedure itself does not invoke them.

## 5. Expected News timing and isolation

When an autonomous agent is enabled and `LLM_AGENT_USE_NEWS=false`:

- the autonomous candidate receives an explicit neutral News context with
  `risk_level=DISABLED` and no fabricated headlines;
- the autonomous path does not wait for macro News, symbol News, News timeout,
  News exception, no-data, or provider-unavailable state;
- in `shadow`, the neutral technical candidate is submitted to the AI path
  before the legacy News lookup is allowed to run;
- after that submission, the legacy strategy route may perform its existing
  real News lookup and may be blocked by legacy News risk; that result must not
  reject or erase the already-submitted AI observation;
- in `paper_autonomous`, the legacy News path is not needed for the AI handoff;
- with the agent off, the existing legacy macro/symbol News behavior remains
  the control path.

The relevant timing regression tests are in
`tests/test_news_timing_isolation.py`. They cover a blocking News call, a News
exception, a HIGH legacy News result, and the off-mode legacy macro gate.

## 6. Read-only observation points

If the data-only dashboard server is running, use these existing endpoints:

```text
GET /api/decision-lab/status
GET /api/decision-lab/decisions?mode=shadow
GET /api/decision-lab/providers
GET /api/decision-lab/positions
GET /api/decision-lab/performance
```

The API is an observation surface. Do not call the order-placement or close
endpoints during this experiment.

For direct SQLite inspection, use a read-only connection. The preflight's
strict schema set is the minimum expected Decision Lab surface:

```text
candidates
decision_snapshots
model_decisions
risk_decisions
execution_links
execution_reservations
provider_health
```

The first-day review should record actual values, not estimates:

- number of candidates and their freshness/expiry outcomes;
- number of Shadow submissions and final `WAIT`, `ENTER`, or `REJECT` actions;
- provider health, error/timeout count, and observed latency;
- queue depth, duplicate suppression, and any candidate drops;
- Decision Lab token/cost fields if the provider returns them;
- `execution_links` and reservations, which should show no live broker order
  path for this Shadow preparation;
- the timestamps around candidate submission and legacy News lookup, using
  logs plus the persisted decision timestamps.

No first-day metric is claimed by this document. Results belong in a separate
operator record after an authorized run.

## 7. Stop conditions

Stop before starting or stop the one-shot observation if any of the following
is true:

- the effective mode is not `shadow`;
- `ALLOW_LLM_LIVE_TRADING` is true;
- `PAPER_TRADING` or `DRY_RUN_MODE` is false;
- the provider/model is not the explicitly selected single configuration;
- the strict Decision Lab schema is unavailable after initialization;
- the broker connection is to a live account or unexpected client/port;
- a dashboard launcher starts an execution worker unexpectedly;
- any code path requests a live order, an order endpoint is invoked, or a
  preflight unexpectedly writes state.

This runbook prepares and validates the safe boundary. It does not authorize
Live mode, order placement, production workers, multi-provider Shadow, or a
performance conclusion.
