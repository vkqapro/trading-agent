"""Slack webhook notifications."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List

import requests

from src.config import LOGGER, SETTINGS

try:
    from slack_sdk import WebClient
    from slack_sdk.webhook import WebhookClient
except ImportError:  # pragma: no cover - optional dependency path.
    WebClient = None  # type: ignore[assignment]
    WebhookClient = None  # type: ignore[assignment]


class SlackAlerter:
    """Send alerts to Slack with graceful degradation when disabled."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or SETTINGS.slack_webhook
        self.client = WebhookClient(self.webhook_url) if self.webhook_url and WebhookClient else None
        self.bot_client = WebClient(token=SETTINGS.slack_bot_token) if SETTINGS.slack_bot_token and WebClient else None

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            LOGGER.info("Slack webhook not configured. Message skipped: %s", message)
            return False
        try:
            if self.client is not None:
                response = self.client.send(text=message)
                return response.status_code == 200
            response = requests.post(self.webhook_url, json={"text": message}, timeout=10)
            response.raise_for_status()
            return True
        except Exception:
            LOGGER.exception("Failed to send Slack alert.")
            return False

    def send_channel_message(self, message: str) -> bool:
        if self.bot_client is not None and SETTINGS.slack_channel:
            try:
                response = self.bot_client.chat_postMessage(channel=SETTINGS.slack_channel, text=message)
                return bool(response.get("ok", False))
            except Exception:
                LOGGER.exception("Failed to send Slack channel message.")
                return False
        return self.send(message)

    def fetch_channel_messages(self, *, limit: int = 20, oldest: str | None = None) -> List[Dict[str, object]]:
        if self.bot_client is None or not SETTINGS.slack_channel:
            return []
        try:
            response = self.bot_client.conversations_history(
                channel=SETTINGS.slack_channel,
                limit=limit,
                oldest=oldest,
                inclusive=False,
            )
            messages = response.get("messages", [])
            return messages if isinstance(messages, list) else []
        except Exception:
            LOGGER.exception("Failed to fetch Slack channel history.")
            return []

    def send_trade_executed(self, trade: Dict[str, object]) -> bool:
        return self.send_channel_message(
            f"🟢 Trade executed\n"
            f"{trade['symbol']} {trade['signal']} qty={trade['quantity']}\n"
            f"Entry: {trade['entry']} Stop: {trade['stop_loss']} Target: {trade['target']}"
        )

    def send_manual_candidate(self, candidate: Dict[str, object]) -> bool:
        return self.send_channel_message(
            "Manual candidate\n"
            f"{candidate.get('symbol')} {candidate.get('signal_side', candidate.get('direction', '?'))} qty={candidate.get('quantity')}\n"
            f"Entry: {candidate.get('entry')} Stop: {candidate.get('stop_loss')} Target: {candidate.get('target')}\n"
            "Auto-submit blocked: quote subscription required"
        )

    def send_stop_triggered(self, symbol: str, stop_price: float) -> bool:
        return self.send_channel_message(f"Stop triggered: {symbol} at {stop_price}")

    def send_error(self, message: str) -> bool:
        return self.send_channel_message(f"Error: {message}")

    @staticmethod
    def _format_price(value: object) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "-"
        if not math.isfinite(numeric):
            return "-"
        decimals = 4 if abs(numeric) < 1 else 2
        return f"{numeric:.{decimals}f}".rstrip("0").rstrip(".")

    @staticmethod
    def _compact_text(value: object, *, max_length: int = 80) -> str:
        text = str(value or "").strip()
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

    def _format_signal_detail(self, item: Dict[str, object]) -> str:
        symbol = str(item.get("symbol", "?") or "?").upper()
        side = str(item.get("signal", item.get("direction", "?")) or "?").upper()
        strategy = str(item.get("strategy", "unknown") or "unknown")
        entry = self._format_price(item.get("entry"))
        signal_level = self._format_price(item.get("signal_level"))
        signal_level_type = str(item.get("signal_level_type", "") or "").strip()
        nearest_level = self._format_price(item.get("nearest_level"))
        nearest_level_type = str(item.get("nearest_level_type", "") or "").strip()
        status = str(item.get("status", "not_entered") or "not_entered")
        reason = self._compact_text(item.get("reason", status))
        reward_risk = self._format_price(item.get("reward_risk"))

        parts = [f"{symbol}: {side} {strategy}", f"entry {entry}"]
        if signal_level != "-":
            level_label = f" {signal_level_type}" if signal_level_type else ""
            parts.append(f"signal level {signal_level}{level_label}")
        if nearest_level != "-":
            nearest_label = f" {nearest_level_type}" if nearest_level_type else ""
            parts.append(f"nearest {nearest_level}{nearest_label}")
        if reward_risk != "-":
            parts.append(f"R:R {reward_risk}")
        parts.append(f"{status}: {reason}" if reason != status else status)
        return " | ".join(parts)

    def send_intraday_heartbeat(self, summary: Dict[str, object]) -> bool:
        tracked_symbols = summary.get("tracked_symbols")
        tracked_count = len(tracked_symbols) if isinstance(tracked_symbols, list) else 0
        actions = summary.get("actions")
        action_count = len(actions) if isinstance(actions, list) else 0
        executed = summary.get("executed")
        executed_count = len(executed) if isinstance(executed, list) else 0
        skipped = summary.get("skipped")
        skipped_count = len(skipped) if isinstance(skipped, list) else 0
        manual_candidates = summary.get("manual_candidates")
        manual_candidate_count = len(manual_candidates) if isinstance(manual_candidates, list) else 0
        macro_risk = bool(summary.get("macro_risk", False))
        entries_enabled = bool(summary.get("entries_enabled", False))
        interval_seconds = int(summary.get("interval_seconds", 0) or 0)
        symbols_scanned = int(summary.get("symbols_scanned", 0) or 0)
        signals_detected = int(summary.get("signals_detected", 0) or 0)
        signal_details = summary.get("signal_details")
        skip_reason_summary = summary.get("skip_reason_summary", {})
        timestamp = str(summary.get("timestamp", ""))
        action_label = "actions taken" if action_count else "no action"
        lines = [
            "Intraday heartbeat",
            f"Time: {timestamp}" if timestamp else "Time: n/a",
            f"Tracked: {tracked_count}",
            f"Macro risk: {'ON' if macro_risk else 'OFF'}",
            f"Entries: {'ON' if entries_enabled else 'OFF'}",
            f"Scan interval: {interval_seconds // 60 if interval_seconds else 0} min",
            f"Scanned: {symbols_scanned} | Signals: {signals_detected} | Executed: {executed_count} | Skipped: {skipped_count}",
            f"Manual candidates: {manual_candidate_count}",
            f"Status: {action_label}",
        ]
        if isinstance(tracked_symbols, list) and tracked_symbols:
            preview = ", ".join(str(symbol) for symbol in tracked_symbols[:8])
            if len(tracked_symbols) > 8:
                preview = f"{preview}, ..."
            lines.append(f"Universe: {preview}")
        if executed_count and isinstance(executed, list):
            executed_preview = ", ".join(
                f"{item.get('symbol', '?')} {item.get('signal', '?')}"
                for item in executed[:3]
                if isinstance(item, dict)
            )
            if executed_preview:
                lines.append(f"Executed: {executed_preview}")
        if signals_detected and isinstance(signal_details, list) and signal_details:
            lines.append("Signals:")
            formatted_signals = [
                self._format_signal_detail(item)
                for item in signal_details[:5]
                if isinstance(item, dict)
            ]
            for detail in formatted_signals:
                lines.append(f"- {detail}")
            remaining = len(signal_details) - len(formatted_signals)
            if remaining > 0:
                lines.append(f"- ... {remaining} more")
        if skipped_count and isinstance(skip_reason_summary, dict) and skip_reason_summary:
            top_reasons = sorted(skip_reason_summary.items(), key=lambda item: (-int(item[1]), str(item[0])))
            formatted = ", ".join(f"{reason}={count}" for reason, count in top_reasons[:3])
            lines.append(f"Skipped: {formatted}")
        if manual_candidate_count and isinstance(manual_candidates, list):
            preview = ", ".join(
                f"{item.get('symbol', '?')} {item.get('signal_side', item.get('direction', '?'))}"
                for item in manual_candidates[:3]
                if isinstance(item, dict)
            )
            if preview:
                lines.append(f"Manual: {preview}")
        if action_count and isinstance(actions, list):
            latest = actions[0]
            if isinstance(latest, dict):
                symbol = latest.get("symbol", "portfolio")
                event = latest.get("event", "action")
                lines.append(f"Latest action: {symbol} {event}")
            else:
                lines.append(f"Latest action: {latest}")
        return self.send_channel_message("\n".join(lines))

    def send_premarket_summary(self, summary: Dict[str, object]) -> bool:
        macro_risk = summary.get("macro_risk", {})
        blocked = bool(macro_risk.get("blocked")) if isinstance(macro_risk, dict) else False
        ideas = summary.get("ideas")
        research_symbols = summary.get("research_symbols")
        lines = [
            "Premarket summary",
            f"Macro risk: {'ON' if blocked else 'OFF'}",
        ]

        if isinstance(research_symbols, list) and research_symbols:
            universe_preview = ", ".join(str(symbol) for symbol in research_symbols[:8])
            if len(research_symbols) > 8:
                universe_preview = f"{universe_preview}, ..."
            lines.append(f"Universe: {universe_preview}")

        if isinstance(ideas, list) and ideas:
            for idea in ideas[:3]:
                symbol = idea.get("symbol", "?")
                decision = idea.get("decision", "WATCH")
                entry = idea.get("entry", "-")
                stop = idea.get("stop", "-")
                target = idea.get("target", "-")
                lines.append(f"{symbol}: {decision} entry {entry} stop {stop} target {target}")
        else:
            lines.append("Ideas: none")

        if blocked and isinstance(macro_risk, dict):
            matched = macro_risk.get("matched_headlines")
            if isinstance(matched, list) and matched:
                lines.append(f"Risk: {matched[0]}")

        return self.send_channel_message("\n".join(lines))

    def upload_file(self, file_path: Path, title: str, initial_comment: str = "") -> bool:
        if self.bot_client is None or not SETTINGS.slack_channel:
            LOGGER.info("Slack bot token/channel not configured. File upload skipped: %s", file_path)
            return False
        try:
            with file_path.open("rb") as handle:
                response = self.bot_client.files_upload_v2(
                    channel=SETTINGS.slack_channel,
                    file=handle,
                    filename=file_path.name,
                    title=title,
                    initial_comment=initial_comment,
                )
            return bool(response.get("ok", False))
        except Exception:
            LOGGER.exception("Failed to upload Slack file: %s", file_path)
            return False

    @staticmethod
    def _parse_ticker_section(section: str) -> Dict[str, str]:
        lines = str(section).splitlines()
        ticker = next((line.replace("Ticker: ", "") for line in lines if line.startswith("Ticker:")), "?")
        pattern = next((line.replace("Pattern: ", "") for line in lines if line.startswith("Pattern:")), "NONE")
        decision = next((line for line in lines if line in {"TRADE TAKEN", "VALID SETUP", "NO TRADE"}), "NO TRADE")
        trade = next((line.replace("Trade: ", "") for line in lines if line.startswith("Trade: ")), "")
        rr = next((line.replace("R:R: ", "") for line in lines if line.startswith("R:R: ")), "")
        confidence = next((line.replace("Confidence: ", "") for line in lines if line.startswith("Confidence: ")), "")

        reasons_index = next((idx for idx, line in enumerate(lines) if line == "Reasons:"), None)
        first_reason = "no recorded reasons"
        if reasons_index is not None:
            reason_lines = [line.replace("- ", "") for line in lines[reasons_index + 1 :] if line.startswith("- ")]
            if reason_lines:
                first_reason = reason_lines[0]

        return {
            "ticker": ticker,
            "pattern": pattern,
            "decision": decision,
            "trade": trade,
            "rr": rr,
            "confidence": confidence,
            "reason": first_reason,
        }

    def send_daily_summary(self, summary: Dict[str, object]) -> bool:
        positions = summary.get("positions")
        open_position_count = len(positions) if isinstance(positions, list) else 0
        decision_summary = summary.get("decision_summary", {})
        top_rejections = decision_summary.get("top_rejection_reasons", []) if isinstance(decision_summary, dict) else []
        ticker_sections = decision_summary.get("ticker_sections", []) if isinstance(decision_summary, dict) else []
        status_notes = decision_summary.get("status_notes", []) if isinstance(decision_summary, dict) else []
        daily_pnl = float(summary.get("daily_pnl", 0.0) or 0.0)
        daily_pnl_pct = float(summary.get("daily_pnl_pct", 0.0) or 0.0)
        blocked_reasons = summary.get("reasons", [])

        if daily_pnl > 0:
            pnl_emoji = "🟢"
        elif daily_pnl < 0:
            pnl_emoji = "🔴"
        else:
            pnl_emoji = "⚪"

        lines = [
            "End of day report",
            f"{pnl_emoji} PnL: {daily_pnl:.2f} ({daily_pnl_pct:.2f}%)",
            "Summary:",
            f"- Open positions: {open_position_count}",
            f"- Total tickers scanned: {decision_summary.get('total_tickers_scanned', 0) if isinstance(decision_summary, dict) else 0}",
            f"- Valid setups: {decision_summary.get('valid_setups', 0) if isinstance(decision_summary, dict) else 0}",
            f"- Trades taken: {decision_summary.get('trades_taken', 0) if isinstance(decision_summary, dict) else 0}",
            f"- Skipped: {decision_summary.get('skipped', 0) if isinstance(decision_summary, dict) else 0}",
        ]

        if top_rejections:
            lines.append("Top rejection reasons:")
            for reason, count in top_rejections[:3]:
                lines.append(f"🔴 {reason}: {count}")

        if isinstance(status_notes, list) and status_notes:
            lines.append("Status:")
            for note in status_notes[:3]:
                lines.append(f"- {note}")

        if isinstance(ticker_sections, list) and ticker_sections:
            lines.append("Ticker decisions:")
            for section in ticker_sections[:3]:
                parsed = self._parse_ticker_section(str(section))
                emoji = "🟢" if parsed["decision"] == "TRADE TAKEN" else ("⚠️" if parsed["decision"] == "VALID SETUP" else "🔴")
                detail_parts = [parsed["decision"].lower(), parsed["pattern"].lower()]
                if parsed["trade"]:
                    detail_parts.append(parsed["trade"])
                if parsed["rr"]:
                    detail_parts.append(f"R:R {parsed['rr']}")
                if parsed["confidence"]:
                    detail_parts.append(f"conf {parsed['confidence']}")
                lines.append(f"{emoji} {parsed['ticker']}: {' | '.join(detail_parts)}")
                lines.append(f"   Reason: {parsed['reason']}")

        if summary.get("blocked"):
            if isinstance(blocked_reasons, list):
                blocked_text = ", ".join(str(reason) for reason in blocked_reasons)
            else:
                blocked_text = str(blocked_reasons)
            lines.append(f"⚠️ Blocked: {blocked_text}")

        return self.send_channel_message("\n".join(lines))
