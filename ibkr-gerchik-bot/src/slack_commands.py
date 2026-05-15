"""Safe Slack command polling and dispatch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS


@dataclass(frozen=True)
class SlackCommand:
    """Parsed Slack command."""

    kind: str
    job_name: str = ""


class SlackCommandProcessor:
    """Poll a Slack channel for whitelisted bot commands."""

    def __init__(self, alerter: SlackAlerter) -> None:
        self.alerter = alerter
        self.prefix = SETTINGS.slack_command_prefix or "ibkr"
        self.allowed_users = {item.strip() for item in SETTINGS.slack_allowed_user_ids if item.strip()}

    def process(
        self,
        state: Dict[str, object],
        run_job_callback: Callable[[str, Optional[bool]], Dict[str, object]],
    ) -> Dict[str, object]:
        if not SETTINGS.slack_commands_enabled:
            return {"enabled": False, "processed": 0}

        command_state = state.setdefault("slack_commands", {})
        if not isinstance(command_state, dict):
            command_state = {}
            state["slack_commands"] = command_state

        oldest = str(command_state.get("last_processed_ts", "0"))
        messages = self.alerter.fetch_channel_messages(limit=25, oldest=oldest)
        ordered_messages = sorted(messages, key=lambda item: float(str(item.get("ts", "0") or "0")))

        processed = 0
        last_ts = oldest
        for message in ordered_messages:
            ts = str(message.get("ts", "") or "")
            if ts:
                last_ts = ts
            if self._is_bot_message(message):
                continue

            user_id = str(message.get("user", "") or "")
            if self.allowed_users and user_id not in self.allowed_users:
                continue

            command = self.parse_command(str(message.get("text", "") or ""))
            if command is None:
                continue

            processed += 1
            self._dispatch(command, run_job_callback)

        command_state["last_processed_ts"] = last_ts
        return {"enabled": True, "processed": processed, "last_processed_ts": last_ts}

    def parse_command(self, text: str) -> Optional[SlackCommand]:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        if not normalized:
            return None

        body = self._strip_prefix(normalized)
        if body is None:
            return None

        if body in {"help", "commands"}:
            return SlackCommand(kind="help")
        if body == "status":
            return SlackCommand(kind="status")
        if body in {"latest report", "latest levels report", "levels report"}:
            return SlackCommand(kind="latest_report")

        for starter in ("rerun ", "run "):
            if body.startswith(starter):
                body = body[len(starter):].strip()
                break

        if body in {"premarket", "open", "intraday", "eod", "weekly"}:
            return SlackCommand(kind="job", job_name=body)
        return None

    def _dispatch(
        self,
        command: SlackCommand,
        run_job_callback: Callable[[str, Optional[bool]], Dict[str, object]],
    ) -> None:
        if command.kind == "help":
            self.alerter.send_channel_message(
                "Slack commands: `ibkr status`, `ibkr premarket`, `ibkr open`, "
                "`ibkr intraday`, `ibkr eod`, `ibkr weekly`, `ibkr latest report`."
            )
            return

        if command.kind == "status":
            latest_report = self._latest_report_path()
            report_name = latest_report.name if latest_report else "none"
            self.alerter.send_channel_message(
                f"Bot status: dry_run_default={'ON' if SETTINGS.dry_run_mode else 'OFF'} "
                f"paper_trading={'ON' if SETTINGS.paper_trading else 'OFF'} latest_report={report_name}"
            )
            return

        if command.kind == "latest_report":
            latest_report = self._latest_report_path()
            if latest_report is None:
                self.alerter.send_channel_message("No premarket levels report has been generated yet.")
                return

            uploaded = self.alerter.upload_file(
                latest_report,
                title="Premarket Strong Levels",
                initial_comment=f"Latest levels report: {latest_report.name}",
            )
            if not uploaded:
                self.alerter.send_channel_message(f"Latest report is available locally: {latest_report.name}")
            return

        if command.kind == "job" and command.job_name:
            self.alerter.send_channel_message(f"Starting `{command.job_name}` from Slack command.")
            result = run_job_callback(command.job_name, None)
            summary = self._summarize_job_result(command.job_name, result)
            self.alerter.send_channel_message(summary)

    def _strip_prefix(self, text: str) -> Optional[str]:
        if text.startswith(f"{self.prefix} "):
            return text[len(self.prefix) :].strip()
        return None

    @staticmethod
    def _is_bot_message(message: Dict[str, object]) -> bool:
        return bool(message.get("bot_id")) or bool(message.get("subtype"))

    @staticmethod
    def _summarize_job_result(job_name: str, result: Dict[str, object]) -> str:
        if job_name == "premarket":
            return (
                f"`premarket` finished: watchlist_count={result.get('watchlist_count')} "
                f"dry_run={result.get('dry_run')}"
            )
        if job_name == "open":
            executed = result.get("executed", [])
            executed_count = len(executed) if isinstance(executed, list) else 0
            return f"`open` finished: executed={executed_count} dry_run={result.get('dry_run')}"
        if job_name == "intraday":
            actions = result.get("actions", [])
            action_count = len(actions) if isinstance(actions, list) else 0
            return f"`intraday` finished: actions={action_count} dry_run={result.get('dry_run')}"
        if job_name == "eod":
            return f"`eod` finished: dry_run={result.get('dry_run')}"
        if job_name == "weekly":
            return f"`weekly` finished: dry_run={result.get('dry_run')}"
        return f"`{job_name}` finished."

    @staticmethod
    def _latest_report_path() -> Optional[Path]:
        reports_dir = SETTINGS.paths.reports_dir
        if not reports_dir.exists():
            return None
        candidates = sorted(reports_dir.glob("premarket_levels_*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None
