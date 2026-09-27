"""Model-independent decision-provider boundary.

Only this module knows how to speak to an LLM endpoint.  Providers return a
validated decision and never receive broker clients or credentials in the
request payload.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import requests

from .models import DecisionAction, DecisionRequest, DecisionResponse


class DecisionProviderError(RuntimeError):
    """A provider could not produce a valid decision."""


class DecisionProvider(Protocol):
    provider_name: str
    model: str

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Return a validated decision or raise ``DecisionProviderError``."""


def build_system_prompt() -> str:
    return (
        "You are a decision component inside a Gerchik trading system.\n"
        "All numeric calculations, entries, stops, targets and position sizing "
        "have already been calculated by deterministic code.\n"
        "You may choose ONLY from the provided action menu.\n"
        "Do not invent prices. Do not invent strategies. Do not invent quantity.\n"
        "Do not call a broker, exchange, or account API.\n"
        "WAIT is valid. REJECT is valid. No trade is preferable to violating setup quality.\n"
        "Use the supplied Gerchik level, ATR, confirmation, data-quality and risk context.\n"
        "Return only JSON with action, confidence, ranked_actions, reason_codes, and short summary."
    )


def _extract_json_content(payload: Mapping[str, Any], *, provider_name: str) -> Mapping[str, Any]:
    content: Any = None
    if provider_name == "anthropic":
        blocks = payload.get("content")
        if isinstance(blocks, list):
            content = "".join(str(item.get("text", "")) for item in blocks if isinstance(item, Mapping))
    else:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                if content is None:
                    content = first.get("text")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
    if not isinstance(content, str) or not content.strip():
        raise DecisionProviderError("provider returned empty decision content")
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecisionProviderError("provider returned invalid decision JSON") from exc
    if not isinstance(parsed, Mapping):
        raise DecisionProviderError("provider decision JSON must be an object")
    return parsed


@dataclass
class HttpDecisionProvider:
    """OpenAI-compatible or Anthropic HTTP provider with injected transport."""

    provider_name: str
    model: str
    base_url: str
    api_key: str = ""
    session: Any = None
    timeout_seconds: float = 15.0
    max_tokens: int = 300
    anthropic_version: str = "2023-06-01"

    def __post_init__(self) -> None:
        self.provider_name = str(self.provider_name).strip().lower()
        self.base_url = str(self.base_url).rstrip("/")
        if self.provider_name not in {"local_openai", "openai", "deepseek", "anthropic"}:
            raise ValueError(f"unsupported decision provider: {self.provider_name}")
        if not self.model:
            raise ValueError("decision provider model is required")
        if not self.base_url:
            raise ValueError("decision provider base URL is required")
        self.session = self.session or requests.Session()

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        started = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.provider_name == "anthropic":
                headers["x-api-key"] = self.api_key
                headers["anthropic-version"] = self.anthropic_version
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"
        prompt_payload = request.to_prompt_payload()
        try:
            if self.provider_name == "anthropic":
                url = f"{self.base_url}/messages"
                body = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "temperature": 0,
                    "system": build_system_prompt(),
                    "messages": [{"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"))}],
                }
            else:
                url = f"{self.base_url}/chat/completions"
                body = {
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": build_system_prompt()},
                        {"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"))},
                    ],
                }
            response = self.session.post(url, headers=headers, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise DecisionProviderError("provider response must be an object")
            raw = _extract_json_content(payload, provider_name=self.provider_name)
            try:
                return DecisionResponse.from_mapping(raw, allowed_actions=request.allowed_actions)
            except (TypeError, ValueError) as exc:
                raise DecisionProviderError("provider returned invalid decision") from exc
        except DecisionProviderError:
            raise
        except Exception as exc:
            raise DecisionProviderError(
                f"provider request failed after {time.perf_counter() - started:.3f}s"
            ) from exc


def build_provider(config: Any = None) -> DecisionProvider:
    """Build one configured provider; never silently fail over to another model."""
    if config is None:
        from src.config import SETTINGS

        config = SETTINGS.decision_agent
    provider_name = str(config.provider).lower()
    if provider_name == "local_openai":
        base_url = config.local_base_url
        model = config.local_model or config.model
        api_key = ""
    elif provider_name == "openai":
        base_url = config.openai_base_url
        model = config.model
        api_key = config.openai_api_key
    elif provider_name == "deepseek":
        base_url = config.deepseek_base_url
        model = config.model
        api_key = config.deepseek_api_key
    elif provider_name == "anthropic":
        base_url = config.anthropic_base_url
        model = config.model
        api_key = config.anthropic_api_key
    else:
        raise ValueError(f"unsupported LLM_DECISION_PROVIDER: {provider_name}")
    return HttpDecisionProvider(
        provider_name=provider_name,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=config.timeout_seconds,
    )
