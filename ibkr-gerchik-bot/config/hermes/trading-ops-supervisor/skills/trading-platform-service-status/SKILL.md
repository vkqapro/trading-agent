---
name: trading-platform-service-status
description: Inspect the deterministic status of the existing local trading services.
---

Use the same repository-scoped client as `trading-platform-health` with the
`service-status` command. It is diagnostic only and must not start, restart,
stop, or kill a process. If a named service is not represented by the client,
report that fact rather than improvising a command.
