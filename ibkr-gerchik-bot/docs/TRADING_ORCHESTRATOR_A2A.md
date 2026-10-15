# Trading Orchestrator A2A

`trading-orchestrator` is a persistent, read-only Hermes coordinator. Its
repository source is `config/hermes/trading-orchestrator/`; its installed
profile is `C:\Users\Vitaly\AppData\Local\hermes\profiles\trading-orchestrator`.

The supported Hermes A2A tools are `a2a_list`, `a2a_discover`, `a2a_call`,
`a2a_history`, and `a2a_orchestrate`. This profile uses `a2a_call` for the
ordered Ops -> Daily Trader -> exact-scan Reviewer workflow. `a2a_orchestrate`
is not used for the critical path because fan-out does not preserve the
required preflight and scan-ID dependency.


Specialist peers are configured explicitly under `a2a_agents` with the
loopback URLs served by the shared Hermes A2A adapter. Profile names alone are
not resolvable peer addresses. The configured routes are:

- `trading-ops-supervisor` -> `http://127.0.0.1:9900/trading-ops-supervisor`
- `daily-trader` -> `http://127.0.0.1:9900/daily-trader`
- `pre-trade-reviewer` -> `http://127.0.0.1:9900/pre-trade-reviewer`

The adapter routes each path to the persistent profile named in the shared
gateway `platforms.a2a.extra.agents` map. Port `9900` is loopback-only and was
verified free during configuration; no public endpoint or bearer token is
needed for this local-only topology. The current gateway must be restarted by
an authorized operator before the listener and routes become live.

Local invocation after peer configuration:

```powershell
hermes -p trading-orchestrator chat
```

Example request:

```text
How is the market today? Check the system and find current trade opportunities.
```

Telegram integration is a separate step. This implementation does not copy bot
tokens, change `profile_routes`, enable schedules, or restart the shared
gateway.
