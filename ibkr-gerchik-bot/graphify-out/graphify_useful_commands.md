# Graphify Useful Commands

This project uses Graphify to build and query a knowledge graph of the codebase.

## Use Graphify in Codex

Type these commands in Codex. Codex uses `$graphify` for the project-installed skill.

```text
$graphify query "what connects IBKRClient to OrderManager?"
$graphify path "IBKRClient" "OrderManager"
$graphify explain "TradeSignal"
```

Build or refresh the full graph, including documentation and images:

```text
$graphify .
$graphify . --update
```

## Use Graphify from PowerShell

If `graphify` is on your `PATH`:

```powershell
graphify --version
graphify query "what connects IBKRClient to OrderManager?"
graphify path "IBKRClient" "OrderManager"
graphify explain "TradeSignal"
```

If PowerShell cannot find the command, use the installed executable directly:

```powershell
$graphify = Join-Path (uv tool dir --bin) 'graphify.exe'
& $graphify --version
```

## Build and update the graph

Code-only extraction is local and does not require an API key:

```powershell
& $graphify extract . --code-only --timing
```

Update only files changed since the previous build:

```powershell
& $graphify update .
```

Rebuild communities and the report from the existing graph:

```powershell
& $graphify cluster-only . --no-label
```

## Reports and visualization

Regenerate the interactive HTML graph:

```powershell
& $graphify export html
```

Open the visualization in your default browser:

```powershell
Start-Process .\graphify-out\graph.html
```

The generated files are:

- `graph.json` — complete graph data.
- `GRAPH_REPORT.md` — summary, god nodes, communities, and suggested questions.
- `graph.html` — interactive graph visualization.
- `manifest.json` — file manifest used for incremental updates.

## Maintenance

Check the automatic Git hook status:

```powershell
& $graphify hook status
```

Install the hook when running from the Git repository root:

```powershell
& $graphify hook install
```

Show all available commands:

```powershell
& $graphify --help
```

## Notes

- In PowerShell, use `graphify .` without a leading `/`.
- Run `graphify update .` after code changes.
- Use `--code-only` for a fully local, no-API-key graph build.
- The current project graph was initially generated with `--code-only`; run `$graphify .` in Codex to include docs and images.
