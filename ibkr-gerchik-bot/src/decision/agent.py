"""Autonomous Gerchik decision orchestrator.

The agent owns reasoning and provenance.  It has no broker/exchange client and
can only reach execution through the injected existing ``OrderManager``.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
from typing import Any, Mapping, Sequence

from src.config import SETTINGS
from src.strategy.signal_models import TradeSignal

from .audit import DecisionAudit
from .candidate_adapter import trade_signal_to_candidate
from .identity import broker_order_ref, order_fingerprint
from .market import quote_age_seconds, quote_last, quote_spread_pct
from .models import (
    AgentMode,
    DecisionAction,
    DecisionCandidate,
    DecisionRequest,
    DecisionResponse,
    DecisionSnapshot,
    PositionAction,
)
from .policy import AutonomousRiskGate, RiskDecision, evaluate_position_action
from .portfolio import PaperPortfolio
from .provider import DecisionProvider, DecisionProviderError, build_provider
from .shadow import MultiProviderShadowRunner


_DEFAULT_AGENTS: dict[tuple[str, str, int], "AutonomousGerchikAgent"] = {}


@dataclass(frozen=True)
class AgentRuntimeContext:
    account_id: str | None = None
    account_equity: float = 0.0
    cash_available: float = 0.0
    current_positions: Sequence[Mapping[str, object]] = tuple()
    open_risk_amount: float = 0.0
    spread_pct: float = 1.0
    broker_connected: bool = False
    account_synced: bool = False
    market_open: bool = False
    first_unstable_minutes: bool = False
    allow_first_unstable_minutes: bool = False
    symbol_news_risk: bool = False
    macro_risk: bool = False
    atr_has_room: bool = True
    data_age_seconds: float | None = None
    current_price: float | None = None
    daily_realized_pnl: float = 0.0
    quote: Mapping[str, object] = field(default_factory=dict)
    market_context: Mapping[str, object] = field(default_factory=dict)
    isolated_paper: bool = False
    decision_deadline: datetime | None = None
    broker_positions: Sequence[Mapping[str, object]] = tuple()
    broker_open_orders: Sequence[Mapping[str, object]] = tuple()
    broker_executions: Sequence[Mapping[str, object]] = tuple()


@dataclass(frozen=True)
class AgentResult:
    status: str
    action: str
    candidate_id: str | None = None
    decision_id: str | None = None
    reasons: tuple[str, ...] = tuple()
    risk: RiskDecision | None = None
    execution: dict[str, object] | None = None
    latency_ms: float | None = None


class AutonomousGerchikAgent:
    """Bounded reasoning layer for one configured agent identity."""

    def __init__(
        self,
        *,
        config: Any | None = None,
        provider: DecisionProvider | None = None,
        audit: DecisionAudit | None = None,
        portfolio: PaperPortfolio | None = None,
        risk_gate: AutonomousRiskGate | None = None,
        order_manager: Any | None = None,
        shadow_providers: Mapping[str, DecisionProvider] | None = None,
    ) -> None:
        self.config = config or SETTINGS.decision_agent
        self.mode = AgentMode.from_value(getattr(self.config, "mode", AgentMode.OFF.value))
        self.provider = provider
        self.audit = audit
        self.portfolio = portfolio
        self.risk_gate = risk_gate or AutonomousRiskGate(self.config)
        self.order_manager = order_manager
        self.shadow_providers = dict(shadow_providers or {})
        self.audit_error: str | None = None
        self._pending: set[Future[AgentResult]] = set()
        self._pending_keys: set[str] = set()
        self._shadow_cache: dict[str, float] = {}
        self._queue_lock = threading.Lock()
        workers = max(1, int(getattr(self.config, "decision_workers", 2)))
        queue_depth = max(1, int(getattr(self.config, "decision_queue_depth", 16)))
        self._decision_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"gerchik-llm-{self.mode.value}",
        )
        self._queue_slots = threading.BoundedSemaphore(workers + queue_depth)
        self._last_paper_protection = 0.0
        self._last_position_review: dict[str, float] = {}
        if self.mode is not AgentMode.OFF:
            if self.audit is None:
                try:
                    self.audit = DecisionAudit(self.config.database_path)
                except Exception:
                    self.audit_error = "decision_database_unavailable"
            if self.mode is AgentMode.PAPER_AUTONOMOUS and self.portfolio is None:
                try:
                    portfolio_path = self.config.database_path.with_name("decision_lab_portfolio.json")
                    self.portfolio = PaperPortfolio(portfolio_path)
                except Exception:
                    self.audit_error = self.audit_error or "paper_portfolio_unavailable"
            if self.mode is AgentMode.PAPER_AUTONOMOUS and self.audit and self.portfolio:
                self._recover_paper_reservations()

    def startup_guard(self, *, account_id: str | None = None) -> tuple[bool, tuple[str, ...]]:
        if self.mode is AgentMode.OFF:
            return True, tuple()
        reasons: list[str] = []
        if self.audit is None or self.audit_error:
            reasons.append(self.audit_error or "decision_database_unavailable")
        try:
            if hasattr(self.config, "validate"):
                identity = self._broker_account_identity() if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else None
                self.config.validate(
                    paper_trading=getattr(SETTINGS, "paper_trading", True),
                    dry_run=getattr(SETTINGS, "dry_run_mode", True),
                    account_id=account_id,
                    paper_account_verified=(identity or {}).get("paper_verified") if identity else None,
                    live_trading_enabled=getattr(getattr(SETTINGS, "inefficiency_reclaim", None), "allow_live_trading", False),
                )
        except Exception as exc:
            reasons.append(str(exc))
        if self.mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS}:
            identity = self._broker_account_identity()
            paper_verified = bool(identity and identity.get("paper_verified") is True)
            if self.mode is AgentMode.LIVE_AUTONOMOUS and not bool(getattr(self.config, "allow_live_trading", False)):
                reasons.append("live_ai_permission_missing")
            if self.mode is AgentMode.LIVE_AUTONOMOUS:
                if bool(getattr(SETTINGS, "paper_trading", True)):
                    reasons.append("paper_trading_mode_enabled")
                if bool(getattr(SETTINGS, "dry_run_mode", True)):
                    reasons.append("dry_run_mode_enabled")
            else:
                if not bool(getattr(self.config, "allow_ibkr_paper_trading", False)):
                    reasons.append("ibkr_paper_permission_missing")
                if not bool(getattr(SETTINGS, "paper_trading", True)):
                    reasons.append("paper_trading_mode_disabled")
                if bool(getattr(SETTINGS, "dry_run_mode", True)):
                    reasons.append("dry_run_mode_enabled")
                if bool(getattr(self.config, "allow_live_trading", False)) or bool(
                    getattr(getattr(SETTINGS, "inefficiency_reclaim", None), "allow_live_trading", False)
                ):
                    reasons.append("live_trading_permission_enabled")
                if str(getattr(getattr(SETTINGS, "inefficiency_reclaim", None), "trading_mode", "paper")).lower() != "paper":
                    reasons.append("legacy_trading_mode_not_paper")
            if self.order_manager is None:
                reasons.append("order_manager_missing")
            if not account_id:
                reasons.append("broker_account_id_unknown")
            elif self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS and not paper_verified:
                reasons.append("paper_account_not_verified")
            elif account_id not in tuple(getattr(self.config, "live_account_allowlist", ()) if self.mode is AgentMode.LIVE_AUTONOMOUS else getattr(self.config, "ibkr_paper_account_allowlist", ())):
                reasons.append("account_not_allowlisted")
            if self.provider is None:
                try:
                    self.provider = build_provider(self.config)
                except Exception:
                    reasons.append("provider_unavailable")
            if self.provider is not None and self.audit is not None and not self.audit.provider_health_is_recent(
                provider=str(getattr(self.provider, "provider_name", getattr(self.config, "provider", ""))),
                model=str(getattr(self.provider, "model", getattr(self.config, "model", ""))),
                max_age_seconds=float(getattr(self.config, "provider_health_max_age_seconds", 900.0)),
            ):
                try:
                    self._provider_health_probe()
                except Exception:
                    reasons.append("provider_health_unavailable")
            if not reasons and self.audit is not None and self.order_manager is not None:
                try:
                    self.reconcile_live_reservations()
                except Exception:
                    reasons.append("live_reconciliation_unavailable")
            if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS and self.audit is not None:
                start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                try:
                    used = self.audit.count_executions_since(
                        mode=self.mode.value,
                        agent_id=str(getattr(self.config, "agent_id", "GERCHIK_LLM_01")),
                        since=start_of_day,
                    )
                    if used >= int(getattr(self.config, "ibkr_paper_max_trades_per_day", 3)):
                        reasons.append("ibkr_paper_daily_trade_cap")
                except Exception:
                    reasons.append("execution_count_unavailable")
        return not reasons, tuple(dict.fromkeys(reasons))

    def _broker_account_identity(self) -> Mapping[str, object] | None:
        broker = getattr(self.order_manager, "broker", None)
        if broker is None or not bool(getattr(broker, "is_connected", False)):
            return None
        getter = getattr(broker, "get_account_identity", None)
        if not callable(getter):
            return None
        try:
            identity = getter()
        except Exception:
            return None
        return identity if isinstance(identity, Mapping) else None

    def _provider_health_probe(self) -> None:
        """Validate a provider without exposing broker/account data or trading."""
        if self.provider is None or self.audit is None:
            raise RuntimeError("provider health dependencies unavailable")
        now = datetime.now(timezone.utc)
        candidate = DecisionCandidate(
            candidate_id=f"health-{uuid.uuid4().hex}",
            created_at=now,
            asset_class="stock",
            symbol="HEALTH",
            strategy="provider_health",
            direction="long",
            entry=1.0,
            stop=0.99,
            target=1.02,
            metadata={"identity_status": "stable", "health_probe": True},
        )
        request = DecisionRequest(
            decision_id=f"health-decision-{uuid.uuid4().hex}",
            agent_id=str(getattr(self.config, "agent_id", "GERCHIK_LLM_01")),
            mode=self.mode,
            provider=str(getattr(self.provider, "provider_name", getattr(self.config, "provider", ""))),
            model=str(getattr(self.provider, "model", getattr(self.config, "model", ""))),
            snapshot=DecisionSnapshot.from_candidate(candidate, session="provider_health", context={"health_probe": True}),
            allowed_actions=(DecisionAction.WAIT.value,),
        )
        started = time.perf_counter()
        response = self.provider.decide(request)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if response.action is not DecisionAction.WAIT:
            raise RuntimeError("provider health probe returned a non-WAIT action")
        self.audit.record_provider_health(
            provider=request.provider,
            model=request.model,
            status="ok",
            latency_ms=latency_ms,
        )

    def _discover_account_id(self, context: AgentRuntimeContext) -> str | None:
        if self.mode not in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS}:
            return context.account_id
        broker = getattr(self.order_manager, "broker", None)
        if broker is None or not bool(getattr(broker, "is_connected", False)):
            return None
        if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS:
            identity = self._broker_account_identity()
            if not identity:
                return None
            discovered = str(identity.get("account_id", "") or "").strip()
            configured = str(context.account_id or "").strip()
            if configured and configured != discovered:
                return None
            return discovered or None
        configured = str(context.account_id or getattr(broker, "account_id", "") or "").strip()
        try:
            summary = broker.get_account_summary()
        except Exception:
            return None
        accounts = {
            str(item.get("account", "") or "").strip()
            for item in summary
            if isinstance(item, Mapping) and str(item.get("account", "") or "").strip()
        }
        if configured:
            return configured if configured in accounts else None
        return next(iter(accounts)) if len(accounts) == 1 else None

    @staticmethod
    def _candidate_state_key(candidate: DecisionCandidate, context: AgentRuntimeContext) -> str:
        payload = {
            "candidate": candidate.candidate_id,
            "price": context.current_price,
            "quote": dict(context.quote),
            "positions": [dict(item) for item in context.current_positions],
            "market": dict(context.market_context),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _deadline_context(self, context: AgentRuntimeContext) -> AgentRuntimeContext:
        if context.decision_deadline is not None:
            return context
        return replace(
            context,
            decision_deadline=datetime.now(timezone.utc)
            + timedelta(seconds=float(getattr(self.config, "candidate_expiry_seconds", 45.0))),
        )

    def _expired(self, context: AgentRuntimeContext) -> bool:
        return context.decision_deadline is not None and datetime.now(timezone.utc) >= context.decision_deadline

    def _queued_result(self, candidate: DecisionCandidate, *, status: str, reason: str) -> AgentResult:
        return AgentResult(status=status, action="NO_ACTION", candidate_id=candidate.candidate_id, reasons=(reason,))

    def _run_queued_signal(
        self,
        signal: TradeSignal,
        *,
        context: AgentRuntimeContext,
        source_signal_id: str | None,
        queue_key: str,
    ) -> AgentResult:
        try:
            return self.process_signal(signal, context=context, source_signal_id=source_signal_id)
        finally:
            with self._queue_lock:
                self._pending_keys.discard(queue_key)

    def _submit_queued(
        self,
        signal: TradeSignal,
        *,
        context: AgentRuntimeContext,
        source_signal_id: str | None,
    ) -> AgentResult:
        candidate = trade_signal_to_candidate(signal, source_signal_id=source_signal_id, market_context=context.market_context)
        if self.mode in {AgentMode.PAPER_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS, AgentMode.LIVE_AUTONOMOUS} and candidate.metadata.get("identity_status") == "unstable":
            return self._queued_result(candidate, status="unstable_candidate", reason="stable_setup_identity_unavailable")
        queue_key = self._candidate_state_key(candidate, context)
        now = time.monotonic()
        with self._queue_lock:
            if queue_key in self._pending_keys:
                return self._queued_result(candidate, status="deduplicated", reason="decision_already_queued")
            if self.mode is AgentMode.SHADOW:
                last = self._shadow_cache.get(queue_key)
                if last is not None and now - last < float(getattr(self.config, "shadow_dedup_minutes", 5.0)) * 60.0:
                    return self._queued_result(candidate, status="deduplicated", reason="shadow_state_already_evaluated")
            if not self._queue_slots.acquire(blocking=False):
                return self._queued_result(candidate, status="expired", reason="decision_queue_full")
            self._pending_keys.add(queue_key)
            if self.mode is AgentMode.SHADOW:
                self._shadow_cache[queue_key] = now
        try:
            future = self._decision_executor.submit(
                self._run_queued_signal,
                signal,
                context=self._deadline_context(context),
                source_signal_id=source_signal_id,
                queue_key=queue_key,
            )
        except Exception:
            with self._queue_lock:
                self._pending_keys.discard(queue_key)
                self._queue_slots.release()
            return self._queued_result(candidate, status="expired", reason="decision_queue_unavailable")
        future.add_done_callback(lambda _future: self._queue_slots.release())
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        return AgentResult(status="scheduled", action="PENDING", candidate_id=candidate.candidate_id)

    def _recover_paper_reservations(self) -> None:
        if self.audit is None or self.portfolio is None:
            return
        positions = {
            str(position.get("candidate_id", "")): position
            for position in self.portfolio.positions()
        }
        for reservation in self.audit.incomplete_reservations(mode=self.mode.value):
            candidate_id = str(reservation.get("candidate_id", ""))
            if candidate_id in positions:
                reservation_id = str(reservation["reservation_id"])
                try:
                    self.audit.record_execution(
                        decision_id=str(reservation.get("decision_id") or f"recovered-{reservation_id}"),
                        candidate_id=candidate_id,
                        status="paper_simulated",
                        order_ids={},
                        position_id=str(positions[candidate_id].get("position_id", "")),
                        mode=self.mode.value,
                        agent_id=str(reservation.get("agent_id", getattr(self.config, "agent_id", "GERCHIK_LLM_01"))),
                        reservation_id=reservation_id,
                    )
                except Exception:
                    self.audit.update_reservation(reservation_id, state="RECONCILIATION_REQUIRED", error="paper_audit_recovery_failed")
            elif str(reservation.get("state")) == "PAPER_MUTATING":
                self.audit.update_reservation(
                    str(reservation["reservation_id"]),
                    state="FAILED_PRE_SUBMIT",
                    error="paper_mutation_not_found_after_restart",
                )

    def reconcile_live_reservations(self) -> list[dict[str, object]]:
        """Resolve uncertain Live reservations without ever resubmitting."""
        if self.mode not in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS} or self.audit is None or self.order_manager is None:
            return []
        broker = getattr(self.order_manager, "broker", None)
        if broker is None:
            raise RuntimeError("broker_unavailable_for_reconciliation")
        open_orders = broker.get_open_orders()
        executions = broker.get_executions() if hasattr(broker, "get_executions") else []
        open_by_ref = {
            str(item.get("order_ref", "")): item
            for item in open_orders
            if str(item.get("order_ref", ""))
        }
        execution_order_ids = {
            str(item.get("order_id", ""))
            for item in executions
            if str(item.get("order_id", ""))
        }
        resolved: list[dict[str, object]] = []
        for reservation in self.audit.incomplete_reservations(mode=self.mode.value):
            reservation_id = str(reservation.get("reservation_id", ""))
            order_ref = str(reservation.get("order_ref", ""))
            if order_ref and order_ref in open_by_ref:
                order = open_by_ref[order_ref]
                order_ids = {"market_order_id": order.get("order_id")}
                self.audit.update_reservation(reservation_id, state="SUBMITTED", order_ids=order_ids)
                resolved.append({**reservation, "state": "SUBMITTED", "order_ids": order_ids})
                continue
            raw_order_ids = str(reservation.get("order_ids_json", "{}"))
            try:
                order_ids = json.loads(raw_order_ids)
            except json.JSONDecodeError:
                order_ids = {}
            if any(str(value) in execution_order_ids for value in order_ids.values()):
                self.audit.update_reservation(reservation_id, state="FILLED", order_ids=order_ids)
                resolved.append({**reservation, "state": "FILLED", "order_ids": order_ids})
            elif str(reservation.get("state")) == "SUBMITTING":
                self.audit.update_reservation(
                    reservation_id,
                    state="RECONCILIATION_REQUIRED",
                    error="broker_identity_not_found_no_retry",
                )
                resolved.append({**reservation, "state": "RECONCILIATION_REQUIRED"})
        return resolved

    def _refresh_live_context(self, context: AgentRuntimeContext) -> AgentRuntimeContext:
        """Fetch authoritative broker state and a fresh local quote."""
        if self.order_manager is None:
            raise RuntimeError("order_manager_missing")
        broker = getattr(self.order_manager, "broker", None)
        market_data = getattr(self.order_manager, "market_data", None)
        if broker is None or market_data is None or not bool(getattr(broker, "is_connected", False)):
            raise RuntimeError("broker_not_connected")
        account_id = self._discover_account_id(context)
        if not account_id:
            raise RuntimeError("broker_account_id_unknown")
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        executions = broker.get_executions()
        quote = market_data.get_quote(context.market_context.get("symbol", "") or "")
        # The candidate symbol is inserted by process_candidate before this call.
        quote_status = str(quote.get("quote_status", "") or "").lower() if quote else "missing"
        if not quote or ("quote_status" in quote and quote_status in {"missing", "unknown", "unavailable"}):
            raise RuntimeError("quote_unavailable")
        age = quote_age_seconds(quote)
        if age is None:
            raise RuntimeError("quote_age_unknown")
        return replace(
            context,
            account_id=account_id,
            current_positions=tuple(dict(item) for item in positions if float(item.get("position", 0.0) or 0.0) != 0.0),
            broker_positions=tuple(dict(item) for item in positions),
            broker_open_orders=tuple(dict(item) for item in open_orders),
            broker_executions=tuple(dict(item) for item in executions),
            broker_connected=True,
            account_synced=True,
            quote=quote,
            spread_pct=quote_spread_pct(quote),
            current_price=quote_last(quote),
            data_age_seconds=age,
            market_context={**context.market_context, "symbol": context.market_context.get("symbol", "")},
        )

    def _refresh_live_context_for_candidate(
        self,
        candidate: DecisionCandidate,
        context: AgentRuntimeContext,
    ) -> AgentRuntimeContext:
        refreshed = replace(context, market_context={**context.market_context, "symbol": candidate.symbol})
        refreshed = self._refresh_live_context(refreshed)
        symbol = candidate.symbol.upper()
        broker_positions = [
            item for item in refreshed.broker_positions
            if str(item.get("symbol", "")).upper() == symbol
            and abs(float(item.get("position", 0.0) or 0.0)) > 0
        ]
        if broker_positions:
            raise RuntimeError("broker_symbol_position_exists")
        broker_orders = [
            item for item in refreshed.broker_open_orders
            if str(item.get("symbol", "")).upper() == symbol
        ]
        if broker_orders:
            raise RuntimeError("broker_symbol_open_order_exists")
        return refreshed

    def _request(self, candidate: DecisionCandidate, context: AgentRuntimeContext) -> DecisionRequest:
        provider_name = str(getattr(self.provider, "provider_name", getattr(self.config, "provider", "")))
        model = str(getattr(self.provider, "model", getattr(self.config, "model", "")))
        market_context = dict(context.market_context)
        if not bool(getattr(self.config, "use_news", False)):
            for key in ("news", "news_risk", "macro_risk", "headlines", "macro_headlines"):
                market_context.pop(key, None)
        snapshot = DecisionSnapshot.from_candidate(
            candidate,
            session="intraday",
            current_price=context.current_price,
            data_age_seconds=context.data_age_seconds,
            open_positions=len(context.current_positions),
            open_risk_amount=context.open_risk_amount,
            daily_realized_pnl=context.daily_realized_pnl,
            context=market_context,
        )
        return DecisionRequest(
            decision_id=f"decision-{uuid.uuid4().hex}",
            agent_id=str(getattr(self.config, "agent_id", "GERCHIK_LLM_01")),
            mode=self.mode,
            provider=provider_name,
            model=model,
            snapshot=snapshot,
            allowed_actions=tuple(action.value for action in DecisionAction),
        )

    def process_signal(
        self,
        signal: TradeSignal,
        *,
        context: AgentRuntimeContext,
        source_signal_id: str | None = None,
    ) -> AgentResult:
        candidate = trade_signal_to_candidate(signal, source_signal_id=source_signal_id, market_context=context.market_context)
        return self.process_candidate(candidate, context=self._deadline_context(context), signal=signal)

    def submit_signal(
        self,
        signal: TradeSignal,
        *,
        context: AgentRuntimeContext,
        source_signal_id: str | None = None,
    ) -> AgentResult:
        if self.mode is AgentMode.OFF:
            candidate = trade_signal_to_candidate(signal, source_signal_id=source_signal_id)
            return self._queued_result(candidate, status="disabled", reason="agent_disabled")
        return self._submit_queued(
            signal,
            context=context,
            source_signal_id=source_signal_id,
        )

    def wait_for_shadow(self, timeout: float = 5.0) -> list[AgentResult]:
        results: list[AgentResult] = []
        deadline = time.monotonic() + timeout
        for future in list(self._pending):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results.append(future.result(timeout=remaining))
            except Exception:
                continue
        return results

    def drain_completed(self) -> list[AgentResult]:
        """Collect already-finished decision work without waiting on providers."""
        results: list[AgentResult] = []
        for future in list(self._pending):
            if not future.done():
                continue
            try:
                results.append(future.result())
            except Exception:
                continue
        return results

    def _legacy_process_candidate(
        self,
        candidate: DecisionCandidate,
        *,
        context: AgentRuntimeContext,
        signal: TradeSignal | None = None,
    ) -> AgentResult:
        if self.mode is AgentMode.OFF:
            return AgentResult(status="disabled", action="NO_ACTION", candidate_id=candidate.candidate_id)
        if self.audit is None:
            return AgentResult(status="audit_unavailable", action="NO_ACTION", candidate_id=candidate.candidate_id)
        if self.mode in {AgentMode.PAPER_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS, AgentMode.LIVE_AUTONOMOUS} and self.audit.has_active_execution(candidate.candidate_id):
            return AgentResult(
                status="idempotent_skip",
                action="NO_ACTION",
                candidate_id=candidate.candidate_id,
                reasons=("candidate_already_executed",),
            )
        if self.mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS}:
            allowed, reasons = self.startup_guard(account_id=self._discover_account_id(context))
            if not allowed:
                return AgentResult(
                    status="startup_veto",
                    action="NO_ACTION",
                    candidate_id=candidate.candidate_id,
                    reasons=reasons,
                )

        try:
            request = self._request(candidate, context)
            if not (self.mode is AgentMode.SHADOW and bool(getattr(self.config, "multi_provider_shadow", False))):
                self.audit.record_snapshot(request)
        except Exception:
            return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id)

        if self.mode is AgentMode.SHADOW and bool(getattr(self.config, "multi_provider_shadow", False)):
            providers = dict(self.shadow_providers)
            if not providers:
                for provider_name in tuple(getattr(self.config, "shadow_providers", ())):
                    try:
                        providers[provider_name] = build_provider(replace(self.config, provider=provider_name))
                    except Exception:
                        continue
            if not providers:
                return AgentResult(
                    status="provider_error", action="NO_ACTION", candidate_id=candidate.candidate_id,
                    reasons=("no_shadow_providers_available",),
                )
            results = MultiProviderShadowRunner(audit=self.audit, providers=providers).run(request)
            reasons: list[str] = []
            for result in results:
                if result.response is not None:
                    reasons.extend(f"{result.provider}:{code}" for code in result.response.reason_codes)
                    self.audit.record_risk(
                        decision_id=result.decision_id,
                        candidate_id=candidate.candidate_id,
                        approved=False,
                        reasons=("shadow_only",),
                    )
                else:
                    reasons.append(f"{result.provider}:provider_unavailable")
            return AgentResult(
                status="shadow", action="SHADOW", candidate_id=candidate.candidate_id,
                reasons=tuple(reasons),
            )

        provider = self.provider
        try:
            provider = provider or build_provider(self.config)
            self.provider = provider
            started = time.perf_counter()
            response = provider.decide(request)
            latency_ms = (time.perf_counter() - started) * 1000.0
            self.audit.record_decision(request, response, latency_ms=latency_ms)
            self.audit.record_provider_health(
                provider=request.provider,
                model=request.model,
                status="ok",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            self.audit.record_decision(request, error="provider_error", status="provider_error")
            self.audit.record_provider_health(
                provider=request.provider,
                model=request.model,
                status="error",
                error="provider_error",
            )
            return AgentResult(
                status="provider_error",
                action="NO_ACTION",
                candidate_id=candidate.candidate_id,
                decision_id=request.decision_id,
                reasons=("provider_unavailable",),
            )

        if self.mode is AgentMode.SHADOW:
            self.audit.record_risk(
                decision_id=request.decision_id,
                candidate_id=candidate.candidate_id,
                approved=False,
                reasons=("shadow_only",),
            )
            return AgentResult(
                status="shadow",
                action=response.action.value,
                candidate_id=candidate.candidate_id,
                decision_id=request.decision_id,
                reasons=response.reason_codes,
                latency_ms=latency_ms,
            )

        if response.action is not DecisionAction.ENTER:
            self.audit.record_risk(
                decision_id=request.decision_id,
                candidate_id=candidate.candidate_id,
                approved=False,
                reasons=(f"model_{response.action.value.lower()}",),
            )
            return AgentResult(
                status="no_action",
                action=response.action.value,
                candidate_id=candidate.candidate_id,
                decision_id=request.decision_id,
                reasons=response.reason_codes,
                latency_ms=latency_ms,
            )
        minimum_confidence = getattr(self.config, "minimum_confidence", None)
        if minimum_confidence is not None and response.confidence < float(minimum_confidence):
            risk = RiskDecision(False, ("model_confidence_below_minimum",), action=DecisionAction.REJECT.value)
        else:
            risk = self.risk_gate.evaluate_entry(
                candidate,
                account_equity=context.account_equity,
                cash_available=context.cash_available,
                current_positions=context.current_positions,
                open_risk_amount=context.open_risk_amount,
                spread_pct=context.spread_pct,
                broker_connected=context.broker_connected,
                account_synced=context.account_synced,
                market_open=context.market_open,
                first_unstable_minutes=context.first_unstable_minutes,
                allow_first_unstable_minutes=context.allow_first_unstable_minutes,
                symbol_news_risk=context.symbol_news_risk,
                macro_risk=context.macro_risk,
                atr_has_room=context.atr_has_room,
                daily_realized_pnl=context.daily_realized_pnl,
                data_age_seconds=context.data_age_seconds,
                current_price=context.current_price,
                isolated_paper=self.mode is AgentMode.PAPER_AUTONOMOUS or context.isolated_paper,
            )
        self.audit.record_risk(
            decision_id=request.decision_id,
            candidate_id=candidate.candidate_id,
            approved=risk.approved,
            reasons=risk.reasons,
            quantity=risk.quantity,
            risk_amount=risk.risk_amount,
        )
        if not risk.approved:
            return AgentResult(
                status="risk_veto",
                action=DecisionAction.REJECT.value,
                candidate_id=candidate.candidate_id,
                decision_id=request.decision_id,
                reasons=risk.reasons,
                risk=risk,
                latency_ms=latency_ms,
            )

        if self.mode is AgentMode.PAPER_AUTONOMOUS:
            try:
                if self.portfolio is None:
                    raise RuntimeError("paper portfolio unavailable")
                execution = self.portfolio.enter(
                    candidate,
                    quantity=risk.quantity,
                    quote=context.quote,
                    slippage_pct=float(getattr(self.config, "slippage_pct", 0.0)),
                    commission_per_share=float(getattr(self.config, "commission_per_share", 0.0)),
                )
                self.audit.record_execution(
                    decision_id=request.decision_id,
                    candidate_id=candidate.candidate_id,
                    status="paper_simulated",
                    order_ids={},
                    position_id=str(execution["position_id"]),
                )
                return AgentResult(
                    status="paper_simulated",
                    action=DecisionAction.ENTER.value,
                    candidate_id=candidate.candidate_id,
                    decision_id=request.decision_id,
                    reasons=response.reason_codes,
                    risk=risk,
                    execution=execution,
                    latency_ms=latency_ms,
                )
            except Exception:
                self.audit.record_execution(
                    decision_id=request.decision_id,
                    candidate_id=candidate.candidate_id,
                    status="paper_rejected",
                    order_ids={},
                )
                return AgentResult(
                    status="paper_rejected",
                    action="NO_ACTION",
                    candidate_id=candidate.candidate_id,
                    decision_id=request.decision_id,
                    reasons=("paper_execution_failed",),
                    risk=risk,
                    latency_ms=latency_ms,
                )

        if signal is None or self.order_manager is None:
            self.audit.record_execution(
                decision_id=request.decision_id,
                candidate_id=candidate.candidate_id,
                status="live_rejected",
                order_ids={},
            )
            return AgentResult(
                status="live_rejected",
                action="NO_ACTION",
                candidate_id=candidate.candidate_id,
                decision_id=request.decision_id,
                reasons=("live_execution_adapter_missing",),
                risk=risk,
                latency_ms=latency_ms,
            )
        signal.metadata = {
            **signal.metadata,
            "owner": "LLM_AGENT",
            "agent_id": request.agent_id,
            "decision_id": request.decision_id,
            "candidate_id": candidate.candidate_id,
        }
        success, payload = self.order_manager.execute_trade(
            signal,
            account_equity=context.account_equity,
            cash_available=context.cash_available,
            current_positions=[dict(item) for item in context.current_positions],
            open_risk_amount=context.open_risk_amount,
        )
        status = "live_executed" if success else "live_rejected"
        self.audit.record_execution(
            decision_id=request.decision_id,
            candidate_id=candidate.candidate_id,
            status=status,
            order_ids={key: payload.get(key) for key in ("market_order_id", "stop_order_id", "limit_order_id") if key in payload},
        )
        return AgentResult(
            status=status,
            action=DecisionAction.ENTER.value if success else "NO_ACTION",
            candidate_id=candidate.candidate_id,
            decision_id=request.decision_id,
            reasons=tuple(str(item) for item in payload.get("reasons", ())),
            risk=risk,
            execution=payload,
            latency_ms=latency_ms,
        )

    def review_position(
        self,
        position: Mapping[str, object],
        *,
        context: AgentRuntimeContext,
    ) -> AgentResult:
        """Optionally review an isolated paper position with a bounded menu.

        Protective stops and targets remain deterministic portfolio behavior;
        this method only adds an advisory/optional action review.
        """
        if self.mode is not AgentMode.PAPER_AUTONOMOUS or not bool(getattr(self.config, "position_review_enabled", False)):
            return AgentResult(status="position_review_disabled", action="NO_ACTION")
        if str(position.get("owner", "")) != "LLM_AGENT":
            return AgentResult(status="position_owner_veto", action="NO_ACTION", reasons=("position_not_owned_by_agent",))
        if self.audit is None or self.provider is None and not getattr(self.config, "model", None) and not getattr(self.config, "local_model", None):
            return AgentResult(status="provider_error", action="NO_ACTION", reasons=("provider_unavailable",))
        request: DecisionRequest | None = None
        try:
            from datetime import datetime, timezone

            candidate = DecisionCandidate(
                candidate_id=str(position.get("candidate_id") or f"position-{position.get('position_id')}"),
                created_at=datetime.now(timezone.utc),
                asset_class="stock",
                symbol=str(position.get("symbol", "")).upper(),
                strategy=str(position.get("strategy", "position_review")),
                direction=str(position.get("direction", "long")),
                entry=float(position.get("entry", 0.0) or 0.0),
                stop=float(position.get("stop_loss", 0.0) or 0.0),
                target=float(position.get("target", 0.0) or 0.0),
                risk_per_share=float(position.get("risk_per_share", 0.0) or 0.0),
                metadata={"position_id": str(position.get("position_id", ""))},
            )
            request = DecisionRequest(
                decision_id=f"position-decision-{uuid.uuid4().hex}",
                agent_id=str(getattr(self.config, "agent_id", "GERCHIK_LLM_01")),
                mode=self.mode,
                provider=str(getattr(self.provider, "provider_name", getattr(self.config, "provider", ""))),
                model=str(getattr(self.provider, "model", getattr(self.config, "model", ""))),
                snapshot=DecisionSnapshot.from_candidate(
                    candidate,
                    session="position_review",
                    current_price=context.current_price or float(position.get("current_price", position.get("entry", 0.0)) or 0.0),
                    open_positions=len(context.current_positions),
                    daily_realized_pnl=context.daily_realized_pnl,
                    context=context.market_context,
                    allowed_actions=tuple(action.value for action in PositionAction),
                ),
                allowed_actions=tuple(action.value for action in PositionAction),
            )
            self.audit.record_snapshot(request)
            provider = self.provider or build_provider(self.config)
            self.provider = provider
            started = time.perf_counter()
            response = provider.decide(request)
            latency_ms = (time.perf_counter() - started) * 1000.0
            self.audit.record_decision(request, response, latency_ms=latency_ms)
            decision = evaluate_position_action(
                response.action,
                position,
                current_price=context.current_price or float(position.get("current_price", position.get("entry", 0.0)) or 0.0),
            )
            self.audit.record_risk(
                decision_id=request.decision_id,
                candidate_id=candidate.candidate_id,
                approved=decision.approved,
                reasons=decision.reasons,
                quantity=decision.quantity,
            )
            if not decision.approved:
                return AgentResult(status="position_risk_veto", action=PositionAction.HOLD.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=decision.reasons, latency_ms=latency_ms)
            if decision.action in {PositionAction.CLOSE.value, PositionAction.TRIM_50.value}:
                execution = self.portfolio.close(
                    str(position["position_id"]),
                    price=float(context.current_price or position.get("current_price", position.get("entry", 0.0)) or 0.0),
                    reason=f"llm_position_review:{decision.action}",
                    quantity=decision.quantity,
                    commission_per_share=float(getattr(self.config, "commission_per_share", 0.0)),
                )
            elif decision.action == PositionAction.MOVE_STOP_TO_BREAKEVEN.value:
                execution = self.portfolio.move_stop_to_breakeven(str(position["position_id"]))
            else:
                execution = dict(position)
            self.audit.record_position_event(
                position_id=str(position["position_id"]), candidate_id=candidate.candidate_id,
                event_type=decision.action, payload=execution,
            )
            return AgentResult(status="position_reviewed", action=decision.action, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=response.reason_codes, execution=execution, latency_ms=latency_ms)
        except Exception:
            if self.audit is not None and request is not None:
                try:
                    self.audit.record_decision(request, error="provider_or_review_error", status="provider_or_review_error")
                except Exception:
                    pass
            return AgentResult(status="position_review_error", action="NO_ACTION", reasons=("position_review_failed",))


    def process_candidate(
        self,
        candidate: DecisionCandidate,
        *,
        context: AgentRuntimeContext,
        signal: TradeSignal | None = None,
    ) -> AgentResult:
        """Process one candidate through durable, fail-closed authorization."""
        if self.mode is AgentMode.OFF:
            return AgentResult(status="disabled", action="NO_ACTION", candidate_id=candidate.candidate_id)
        if self.audit is None:
            return AgentResult(status="audit_unavailable", action="NO_ACTION", candidate_id=candidate.candidate_id)
        context = self._deadline_context(context)
        if self.mode in {AgentMode.PAPER_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS, AgentMode.LIVE_AUTONOMOUS} and candidate.metadata.get("identity_status") == "unstable":
            return AgentResult(
                status="unstable_candidate",
                action="NO_ACTION",
                candidate_id=candidate.candidate_id,
                reasons=("stable_setup_identity_unavailable",),
            )

        agent_id = str(getattr(self.config, "agent_id", "GERCHIK_LLM_01"))
        account_id = context.account_id
        if self.mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS}:
            account_id = self._discover_account_id(context)
            allowed, reasons = self.startup_guard(account_id=account_id)
            if not allowed:
                return AgentResult(
                    status="startup_veto",
                    action="NO_ACTION",
                    candidate_id=candidate.candidate_id,
                    reasons=reasons,
                )

        try:
            request = self._request(candidate, context)
        except Exception:
            return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id)

        reservation = None
        if self.mode in {AgentMode.PAPER_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS, AgentMode.LIVE_AUTONOMOUS}:
            try:
                # The reservation references the durable candidate row through
                # SQLite foreign keys, so persist the candidate before claiming
                # the executable namespace.
                self.audit.record_candidate(candidate)
                reservation = self.audit.reserve_execution(
                    mode=self.mode.value,
                    agent_id=agent_id,
                    candidate_id=candidate.candidate_id,
                    account_id=account_id,
                    symbol=candidate.symbol,
                    side="BUY" if candidate.direction == "long" else "SELL",
                    entry=candidate.entry,
                    stop=candidate.stop,
                    target=candidate.target,
                    decision_id=request.decision_id,
                    order_ref=broker_order_ref(
                        agent_id=agent_id,
                        candidate_id=candidate.candidate_id,
                        decision_id=request.decision_id,
                    ) if self.mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS} else None,
                )
            except Exception:
                return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id)
            if not reservation.acquired:
                return AgentResult(
                    status="idempotent_skip",
                    action="NO_ACTION",
                    candidate_id=candidate.candidate_id,
                    reasons=(reservation.reason or "execution_identity_already_reserved",),
                )

        try:
            self.audit.record_snapshot(request)
        except Exception:
            if reservation:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="snapshot_persist_failed")
                except Exception:
                    pass
            return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id)

        if self._expired(context):
            try:
                self.audit.record_decision(request, error="candidate_expired", status="expired")
                if reservation:
                    self.audit.update_reservation(reservation.reservation_id, state="EXPIRED", error="candidate_expired")
            except Exception:
                pass
            return AgentResult(status="expired", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("candidate_expired",))

        if self.mode is AgentMode.SHADOW and bool(getattr(self.config, "multi_provider_shadow", False)):
            providers = dict(self.shadow_providers)
            if not providers:
                for provider_name in tuple(getattr(self.config, "shadow_providers", ())):
                    try:
                        providers[provider_name] = build_provider(replace(self.config, provider=provider_name))
                    except Exception:
                        continue
            if not providers:
                return AgentResult(status="provider_error", action="NO_ACTION", candidate_id=candidate.candidate_id, reasons=("no_shadow_providers_available",))
            try:
                results = MultiProviderShadowRunner(audit=self.audit, providers=providers).run(request)
                reasons: list[str] = []
                for result in results:
                    if result.response is not None:
                        reasons.extend(f"{result.provider}:{code}" for code in result.response.reason_codes)
                        self.audit.record_risk(
                            decision_id=result.decision_id,
                            candidate_id=candidate.candidate_id,
                            approved=False,
                            reasons=("shadow_only",),
                        )
                    else:
                        reasons.append(f"{result.provider}:provider_unavailable")
                return AgentResult(status="shadow", action="SHADOW", candidate_id=candidate.candidate_id, reasons=tuple(reasons))
            except Exception:
                return AgentResult(status="shadow_audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, reasons=("shadow_observability_failed",))

        provider = self.provider
        try:
            provider = provider or build_provider(self.config)
            self.provider = provider
            started = time.perf_counter()
            response = provider.decide(request)
            latency_ms = (time.perf_counter() - started) * 1000.0
            self.audit.record_decision(request, response, latency_ms=latency_ms)
            self.audit.record_provider_health(provider=request.provider, model=request.model, status="ok", latency_ms=latency_ms)
        except Exception:
            try:
                self.audit.record_decision(request, error="provider_error", status="provider_error")
                self.audit.record_provider_health(provider=request.provider, model=request.model, status="error", error="provider_error")
            except Exception:
                if reservation:
                    try:
                        self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="provider_and_audit_failed")
                    except Exception:
                        pass
                return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("critical_state_persist_failed",))
            if reservation:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="provider_unavailable")
                except Exception:
                    pass
            return AgentResult(status="provider_error", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("provider_unavailable",))

        if self._expired(context):
            try:
                self.audit.record_risk(decision_id=request.decision_id, candidate_id=candidate.candidate_id, approved=False, reasons=("candidate_expired",))
                if reservation:
                    self.audit.update_reservation(reservation.reservation_id, state="EXPIRED", error="candidate_expired")
            except Exception:
                pass
            return AgentResult(status="expired", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("candidate_expired",), latency_ms=latency_ms)

        if self.mode is AgentMode.SHADOW:
            try:
                self.audit.record_risk(decision_id=request.decision_id, candidate_id=candidate.candidate_id, approved=False, reasons=("shadow_only",))
            except Exception:
                return AgentResult(status="shadow_audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, reasons=("shadow_observability_failed",))
            return AgentResult(status="shadow", action=response.action.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=response.reason_codes, latency_ms=latency_ms)

        if response.action is not DecisionAction.ENTER:
            try:
                self.audit.record_risk(decision_id=request.decision_id, candidate_id=candidate.candidate_id, approved=False, reasons=(f"model_{response.action.value.lower()}",))
                if reservation:
                    self.audit.update_reservation(reservation.reservation_id, state="VETOED", error="model_no_entry")
            except Exception:
                return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("critical_state_persist_failed",))
            return AgentResult(status="no_action", action=response.action.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=response.reason_codes, latency_ms=latency_ms)

        effective_context = context
        if self.mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS}:
            try:
                effective_context = self._refresh_live_context_for_candidate(candidate, context)
            except Exception as exc:
                try:
                    self.audit.record_risk(decision_id=request.decision_id, candidate_id=candidate.candidate_id, approved=False, reasons=(str(exc),))
                    self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error=str(exc))
                except Exception:
                    pass
                return AgentResult(status="reconciliation_veto", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=(str(exc),), latency_ms=latency_ms)

        minimum_confidence = getattr(self.config, "minimum_confidence", None)
        if minimum_confidence is not None and response.confidence < float(minimum_confidence):
            risk = RiskDecision(False, ("model_confidence_below_minimum",), action=DecisionAction.REJECT.value)
        else:
            risk = self.risk_gate.evaluate_entry(
                candidate,
                account_equity=effective_context.account_equity,
                cash_available=effective_context.cash_available,
                current_positions=effective_context.current_positions,
                open_risk_amount=effective_context.open_risk_amount,
                spread_pct=effective_context.spread_pct,
                broker_connected=effective_context.broker_connected,
                account_synced=effective_context.account_synced,
                market_open=effective_context.market_open,
                first_unstable_minutes=effective_context.first_unstable_minutes,
                allow_first_unstable_minutes=effective_context.allow_first_unstable_minutes,
                symbol_news_risk=effective_context.symbol_news_risk if bool(getattr(self.config, "use_news", False)) else False,
                macro_risk=effective_context.macro_risk if bool(getattr(self.config, "use_news", False)) else False,
                atr_has_room=effective_context.atr_has_room,
                daily_realized_pnl=effective_context.daily_realized_pnl,
                data_age_seconds=effective_context.data_age_seconds,
                current_price=effective_context.current_price,
                isolated_paper=self.mode is AgentMode.PAPER_AUTONOMOUS or effective_context.isolated_paper,
                max_open_positions=(int(getattr(self.config, "ibkr_paper_max_open_positions", 1)) if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else None),
                risk_per_trade_pct=(float(getattr(self.config, "ibkr_paper_risk_per_trade_pct", 0.10)) if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else None),
            )
        try:
            self.audit.record_risk(
                decision_id=request.decision_id,
                candidate_id=candidate.candidate_id,
                approved=risk.approved,
                reasons=risk.reasons,
                quantity=risk.quantity,
                risk_amount=risk.risk_amount,
            )
        except Exception:
            if reservation:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="risk_persist_failed")
                except Exception:
                    pass
            return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("critical_state_persist_failed",), latency_ms=latency_ms)
        if not risk.approved:
            if reservation:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="VETOED", error=";".join(risk.reasons))
                except Exception:
                    pass
            return AgentResult(status="risk_veto", action=DecisionAction.REJECT.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=risk.reasons, risk=risk, latency_ms=latency_ms)

        fingerprint = order_fingerprint({
            "account": effective_context.account_id,
            "symbol": candidate.symbol,
            "side": "BUY" if candidate.direction == "long" else "SELL",
            "quantity": risk.quantity,
            "entry": candidate.entry,
            "stop": candidate.stop,
            "target": candidate.target,
            "candidate_id": candidate.candidate_id,
            "agent_id": request.agent_id,
        })
        if self.mode is AgentMode.PAPER_AUTONOMOUS:
            try:
                self.audit.update_reservation(reservation.reservation_id, state="PAPER_MUTATING", quantity=risk.quantity, order_fingerprint=fingerprint)
                if self.portfolio is None:
                    raise RuntimeError("paper portfolio unavailable")
                execution = self.portfolio.enter(
                    candidate,
                    quantity=risk.quantity,
                    quote=effective_context.quote,
                    slippage_pct=float(getattr(self.config, "slippage_pct", 0.0)),
                    commission_per_share=float(getattr(self.config, "commission_per_share", 0.0)),
                )
                self.audit.record_execution(
                    decision_id=request.decision_id,
                    candidate_id=candidate.candidate_id,
                    status="paper_simulated",
                    order_ids={},
                    position_id=str(execution["position_id"]),
                    mode=self.mode.value,
                    agent_id=request.agent_id,
                    reservation_id=reservation.reservation_id,
                )
                return AgentResult(status="paper_simulated", action=DecisionAction.ENTER.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=response.reason_codes, risk=risk, execution=execution, latency_ms=latency_ms)
            except Exception:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="paper_execution_failed")
                    self.audit.record_execution(decision_id=request.decision_id, candidate_id=candidate.candidate_id, status="paper_rejected", order_ids={}, mode=self.mode.value, agent_id=request.agent_id, reservation_id=reservation.reservation_id)
                except Exception:
                    pass
                return AgentResult(status="paper_rejected", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("paper_execution_failed",), risk=risk, latency_ms=latency_ms)

        if signal is None or self.order_manager is None:
            try:
                self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="live_execution_adapter_missing")
            except Exception:
                pass
            return AgentResult(status="ibkr_paper_rejected" if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else "live_rejected", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("live_execution_adapter_missing",), risk=risk, latency_ms=latency_ms)

        ref = broker_order_ref(agent_id=request.agent_id, candidate_id=candidate.candidate_id, decision_id=request.decision_id)
        try:
            self.audit.update_reservation(
                reservation.reservation_id,
                state="READY_TO_SUBMIT",
                decision_id=request.decision_id,
                quantity=risk.quantity,
                order_fingerprint=fingerprint,
                order_ref=ref,
            )
            self.audit.update_reservation(reservation.reservation_id, state="SUBMITTING")
        except Exception:
            try:
                self.audit.update_reservation(reservation.reservation_id, state="FAILED_PRE_SUBMIT", error="intent_persist_failed")
            except Exception:
                pass
            return AgentResult(status="audit_error", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("critical_state_persist_failed",), risk=risk, latency_ms=latency_ms)

        signal.metadata = {
            **signal.metadata,
            "owner": "LLM_AGENT",
            "agent_id": request.agent_id,
            "decision_id": request.decision_id,
            "candidate_id": candidate.candidate_id,
            "order_ref": ref,
        }
        guard = {
            "agent_id": request.agent_id,
            "decision_id": request.decision_id,
            "candidate_id": candidate.candidate_id,
            "order_ref": ref,
            "entry": candidate.entry,
            "max_data_age_seconds": float(getattr(self.config, "max_data_age_seconds", 30.0)),
            "max_entry_chase_pct": float(getattr(self.config, "max_entry_chase_pct", 0.01)),
            "max_spread_pct": float(getattr(SETTINGS.risk, "max_spread_pct", 0.003)),
            "account_synced": True,
            "quantity": risk.quantity,
        }
        try:
            success, payload = self.order_manager.execute_trade(
                signal,
                account_equity=effective_context.account_equity,
                cash_available=effective_context.cash_available,
                current_positions=[dict(item) for item in effective_context.current_positions],
                open_risk_amount=effective_context.open_risk_amount,
                autonomous_guard=guard,
            )
        except Exception:
            try:
                self.audit.update_reservation(reservation.reservation_id, state="RECONCILIATION_REQUIRED", error="broker_submission_uncertain")
            except Exception:
                pass
            return AgentResult(status="reconciliation_required", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("broker_submission_uncertain",), risk=risk, latency_ms=latency_ms)

        order_ids = {key: payload.get(key) for key in ("market_order_id", "stop_order_id", "limit_order_id") if payload.get(key) is not None}
        if success and payload.get("market_order_id"):
            execution_status = "ibkr_paper_executed" if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else "live_executed"
            try:
                self.audit.record_execution(decision_id=request.decision_id, candidate_id=candidate.candidate_id, status=execution_status, order_ids=order_ids, mode=self.mode.value, agent_id=request.agent_id, reservation_id=reservation.reservation_id)
            except Exception:
                try:
                    self.audit.update_reservation(reservation.reservation_id, state="RECONCILIATION_REQUIRED", error="post_submit_audit_failed", order_ids=order_ids)
                except Exception:
                    pass
                return AgentResult(status="reconciliation_required", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=("post_submit_audit_failed",), risk=risk, latency_ms=latency_ms)
            return AgentResult(status=execution_status, action=DecisionAction.ENTER.value, candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=tuple(str(item) for item in payload.get("reasons", ())), risk=risk, execution=payload, latency_ms=latency_ms)

        state = "RECONCILIATION_REQUIRED" if order_ids else "FAILED_PRE_SUBMIT"
        try:
            self.audit.update_reservation(reservation.reservation_id, state=state, error="broker_rejected", order_ids=order_ids)
            self.audit.record_execution(decision_id=request.decision_id, candidate_id=candidate.candidate_id, status="ibkr_paper_rejected" if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else "live_rejected", order_ids=order_ids, mode=self.mode.value, agent_id=request.agent_id, reservation_id=reservation.reservation_id)
        except Exception:
            pass
        return AgentResult(status="ibkr_paper_rejected" if self.mode is AgentMode.IBKR_PAPER_AUTONOMOUS else "live_rejected", action="NO_ACTION", candidate_id=candidate.candidate_id, decision_id=request.decision_id, reasons=tuple(str(item) for item in payload.get("reasons", ())) or ("live_execution_rejected",), risk=risk, execution=payload, latency_ms=latency_ms)

    def run_paper_safety_cycle(self, market_data: Any, *, now: float | None = None) -> list[dict[str, object]]:
        """Run deterministic Paper protection independently of the LLM."""
        if self.mode is not AgentMode.PAPER_AUTONOMOUS or self.portfolio is None:
            return []
        current = time.monotonic() if now is None else float(now)
        interval = float(getattr(self.config, "paper_protection_interval_seconds", 5.0))
        if current - self._last_paper_protection < interval:
            return []
        self._last_paper_protection = current
        quotes: dict[str, Mapping[str, object]] = {}
        for position in self.portfolio.positions():
            symbol = str(position.get("symbol", "")).upper()
            try:
                quote = market_data.get_quote(symbol)
                if quote_age_seconds(quote) is not None and quote_last(quote) > 0:
                    quotes[symbol] = quote
            except Exception:
                continue
        closed = self.portfolio.enforce_protection(quotes)
        for item in closed:
            if self.audit is not None:
                try:
                    self.audit.record_position_event(
                        position_id=str(item.get("position_id", "")),
                        candidate_id=str(item.get("candidate_id", "")) or None,
                        event_type=str(item.get("exit_reason", "deterministic_protection")),
                        payload=item,
                    )
                    self.audit.record_outcome(
                        candidate_id=str(item.get("candidate_id", "")),
                        position_id=str(item.get("position_id", "")),
                        final_status=str(item.get("final_status", "CLOSED")),
                        final_r=float(item.get("final_r", 0.0) or 0.0),
                        final_pnl=float(item.get("pnl", 0.0) or 0.0),
                        payload=item,
                    )
                except Exception:
                    pass
        if bool(getattr(self.config, "position_review_enabled", False)):
            for position in self.portfolio.positions():
                position_id = str(position.get("position_id", ""))
                last_review = self._last_position_review.get(position_id, 0.0)
                review_interval = float(getattr(self.config, "position_review_interval_seconds", 300.0))
                if not position_id or current - last_review < review_interval:
                    continue
                symbol = str(position.get("symbol", "")).upper()
                quote = quotes.get(symbol, {})
                if not quote:
                    continue
                if not self._queue_slots.acquire(blocking=False):
                    continue
                self._last_position_review[position_id] = current
                review_context = AgentRuntimeContext(
                    current_positions=tuple(self.portfolio.positions()),
                    current_price=quote_last(quote),
                    data_age_seconds=quote_age_seconds(quote),
                    quote=quote,
                    market_open=True,
                    isolated_paper=True,
                    market_context={"event": "configured_position_review", "symbol": symbol},
                )
                try:
                    future = self._decision_executor.submit(
                        self.review_position,
                        position,
                        context=review_context,
                    )
                    future.add_done_callback(lambda _future: self._queue_slots.release())
                    self._pending.add(future)
                    future.add_done_callback(self._pending.discard)
                except Exception:
                    self._queue_slots.release()
        return closed


def default_agent(order_manager: Any | None = None) -> AutonomousGerchikAgent:
    """Return the process-local agent for the current config.

    The cache keeps shadow-provider workers and SQLite ownership stable across
    scanner ticks.  ``off`` intentionally returns an uninitialized agent so a
    normal legacy scan creates no AI database or provider.
    """
    config = SETTINGS.decision_agent
    mode = AgentMode.from_value(config.mode)
    if mode is AgentMode.OFF:
        return AutonomousGerchikAgent(config=config)
    key = (
        mode.value,
        str(config.database_path),
        id(order_manager) if mode in {AgentMode.LIVE_AUTONOMOUS, AgentMode.IBKR_PAPER_AUTONOMOUS} else 0,
    )
    if key not in _DEFAULT_AGENTS:
        _DEFAULT_AGENTS[key] = AutonomousGerchikAgent(config=config, order_manager=order_manager)
    return _DEFAULT_AGENTS[key]
