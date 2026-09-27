"""Read-only multi-provider shadow comparison for identical snapshots."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Mapping

from .audit import DecisionAudit
from .models import DecisionRequest, DecisionResponse
from .provider import DecisionProvider


@dataclass(frozen=True)
class ShadowProviderResult:
    decision_id: str
    provider: str
    model: str
    status: str
    response: DecisionResponse | None = None
    error: str | None = None
    latency_ms: float | None = None


class MultiProviderShadowRunner:
    """Fan out one immutable request; it cannot execute or approve a trade."""

    def __init__(self, *, audit: DecisionAudit, providers: Mapping[str, DecisionProvider]) -> None:
        self.audit = audit
        self.providers = dict(providers)

    def run(self, request: DecisionRequest) -> list[ShadowProviderResult]:
        results: list[ShadowProviderResult] = []
        for name, provider in self.providers.items():
            provider_name = str(getattr(provider, "provider_name", name))
            model = str(getattr(provider, "model", ""))
            provider_request = replace(
                request,
                decision_id=f"shadow-{provider_name}-{uuid.uuid4().hex}",
                provider=provider_name,
                model=model,
            )
            started = time.perf_counter()
            try:
                self.audit.record_snapshot(provider_request)
                response = provider.decide(provider_request)
                latency_ms = (time.perf_counter() - started) * 1000.0
                self.audit.record_decision(provider_request, response, latency_ms=latency_ms)
                self.audit.record_provider_health(provider=provider_name, model=model, status="ok", latency_ms=latency_ms)
                results.append(ShadowProviderResult(provider_request.decision_id, provider_name, model, "ok", response, latency_ms=latency_ms))
            except Exception:
                latency_ms = (time.perf_counter() - started) * 1000.0
                self.audit.record_decision(provider_request, error="provider_error", status="provider_error")
                self.audit.record_provider_health(
                    provider=provider_name, model=model, status="error",
                    error="provider_error", latency_ms=latency_ms,
                )
                results.append(ShadowProviderResult(
                    provider_request.decision_id, provider_name, model, "provider_error",
                    error="provider_unavailable", latency_ms=latency_ms,
                ))
        return results
