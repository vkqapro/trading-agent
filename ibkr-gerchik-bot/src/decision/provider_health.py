"""Safe, non-trading provider health diagnostics.

This module is the single provider-connectivity contract shared by the
operator-facing Decision Lab diagnostic and the persistent stock worker.  It
does not write decision, risk, reservation, portfolio, or execution records.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import AgentMode, DecisionAction, DecisionCandidate, DecisionRequest, DecisionSnapshot
from .provider import DecisionProviderError, build_provider


@dataclass(frozen=True)
class ProviderHealthResult:
    """Sanitized result suitable for a worker heartbeat or dashboard response."""

    status: str
    provider: str
    model: str
    checked_at: str
    latency_ms: float | None = None
    error: str | None = None

    @property
    def connected(self) -> bool:
        return self.status == "CONNECTED"


def provider_health_request(*, provider_name: str, model: str) -> DecisionRequest:
    """Build the synthetic request accepted only by the provider health probe."""
    now = datetime.now(timezone.utc)
    candidate = DecisionCandidate(
        candidate_id="provider-connectivity-diagnostic",
        created_at=now,
        asset_class="diagnostic",
        symbol="TEST",
        strategy="connectivity_check",
        direction="none",
        entry=0.0,
        stop=0.0,
        target=0.0,
        metadata={"diagnostic": True, "non_trading": True, "health_probe": True},
    )
    return DecisionRequest(
        decision_id="provider-connectivity-diagnostic",
        agent_id="PROVIDER_HEALTH_DIAGNOSTIC",
        mode=AgentMode.SHADOW,
        provider=provider_name,
        model=model,
        snapshot=DecisionSnapshot.from_candidate(
            candidate,
            session="provider_connectivity",
            allowed_actions=(DecisionAction.WAIT.value,),
            context={"diagnostic": True, "non_trading": True, "health_probe": True},
        ),
        allowed_actions=(DecisionAction.WAIT.value,),
    )


def _safe_provider_error(error: BaseException) -> str:
    """Map provider failures to bounded categories without transport details."""
    cause = error.__cause__ or error
    response = getattr(cause, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return "AUTH_ERROR"
    if status_code == 404:
        return "MODEL_NOT_FOUND"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in {408, 504}:
        return "TIMEOUT"
    error_name = type(cause).__name__.lower()
    message = str(error).lower()
    if "timeout" in error_name or "timeout" in message:
        return "TIMEOUT"
    if "rate" in message or "too many requests" in message:
        return "RATE_LIMITED"
    if "unauthorized" in message or "forbidden" in message or "api key" in message:
        return "AUTH_ERROR"
    if "model" in message and ("not found" in message or "unavailable" in message):
        return "MODEL_NOT_FOUND"
    if "invalid decision json" in message or "empty decision" in message or "response must be" in message:
        return "INVALID_RESPONSE"
    if "schema" in message or "invalid decision" in message or "non-wait" in message:
        return "SCHEMA_VALIDATION_ERROR"
    if "connection" in error_name or "connection" in message or "dns" in message or "network" in message:
        return "NETWORK_ERROR"
    if isinstance(error, DecisionProviderError):
        return "UNKNOWN_PROVIDER_ERROR"
    return "UNKNOWN_PROVIDER_ERROR"


def _perform_provider_health_check(config: Any) -> ProviderHealthResult:
    checked_at = datetime.now(timezone.utc).isoformat()
    provider_name = str(getattr(config, "provider", "") or "").strip().lower()
    model = str(getattr(config, "model", "") or getattr(config, "local_model", "") or "").strip()
    started = time.perf_counter()
    try:
        provider = build_provider(config)
        provider_name = str(getattr(provider, "provider_name", provider_name)).strip().lower()
        model = str(getattr(provider, "model", model)).strip()
        response = provider.decide(provider_health_request(provider_name=provider_name, model=model))
        action = getattr(getattr(response, "action", None), "value", getattr(response, "action", None))
        if str(action).upper() != DecisionAction.WAIT.value:
            raise ValueError("provider health diagnostic returned a non-WAIT action")
        return ProviderHealthResult(
            status="CONNECTED",
            provider=provider_name,
            model=model,
            checked_at=checked_at,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
    except Exception as exc:
        return ProviderHealthResult(
            status="UNAVAILABLE",
            provider=provider_name,
            model=model,
            checked_at=checked_at,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            error=_safe_provider_error(exc),
        )


def run_provider_health_check(config: Any, *, timeout_seconds: float | None = None) -> ProviderHealthResult:
    """Run the strict WAIT diagnostic with a bounded, daemonized worker thread."""
    configured_timeout = timeout_seconds
    if configured_timeout is None:
        configured_timeout = float(getattr(config, "timeout_seconds", 15.0) or 15.0)
    timeout = max(float(configured_timeout), 0.1)
    result: list[ProviderHealthResult] = []

    def _run() -> None:
        result.append(_perform_provider_health_check(config))

    thread = threading.Thread(target=_run, name="provider-health-diagnostic", daemon=True)
    started = time.perf_counter()
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        return ProviderHealthResult(
            status="UNAVAILABLE",
            provider=str(getattr(config, "provider", "") or "").strip().lower(),
            model=str(getattr(config, "model", "") or getattr(config, "local_model", "") or "").strip(),
            checked_at=datetime.now(timezone.utc).isoformat(),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            error="TIMEOUT",
        )
    return result[0] if result else ProviderHealthResult(
        status="UNAVAILABLE",
        provider=str(getattr(config, "provider", "") or "").strip().lower(),
        model=str(getattr(config, "model", "") or getattr(config, "local_model", "") or "").strip(),
        checked_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        error="UNKNOWN_PROVIDER_ERROR",
    )
