"""Model-independent decision-provider boundary.

Only this module knows how to speak to an LLM endpoint.  Providers return a
validated decision and never receive broker clients or credentials in the
request payload.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import requests

from .models import DecisionAction, DecisionRequest, DecisionResponse, sanitize_mapping, sanitize_text
from .prompt_compiler import LOCKED_SYSTEM_POLICY


PROMPT_VERSION = "decision-v1"


class DecisionProviderError(RuntimeError):
    """A provider could not produce a valid decision."""

    def __init__(self, message: str, *, category: str = "UNKNOWN_PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.category = category


class DecisionProvider(Protocol):
    provider_name: str
    model: str

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Return a validated decision or raise ``DecisionProviderError``."""


def build_system_prompt() -> str:
    return (
        f"{LOCKED_SYSTEM_POLICY}\n"
        "Use only concepts present in the selected Strategy Definition and candidate data.\n"
        "Return JSON with action, confidence, ranked_actions, reason_codes, and short summary. "
        "Use ranked_actions as an array of [action, score] pairs, for example [[\"WAIT\", 1.0]]. "
        "Use reason_codes as an array of strings."
    )


def _extract_raw_content(payload: Mapping[str, Any], *, provider_name: str) -> str:
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
        raise DecisionProviderError("provider returned empty decision content", category="INVALID_RESPONSE")
    return content.strip()


def _extract_json_content(payload: Mapping[str, Any], *, provider_name: str) -> Mapping[str, Any]:
    text = _extract_raw_content(payload, provider_name=provider_name)
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecisionProviderError("provider returned invalid decision JSON", category="INVALID_RESPONSE") from exc
    if not isinstance(parsed, Mapping):
        raise DecisionProviderError("provider decision JSON must be an object", category="INVALID_RESPONSE")
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
    max_tokens: int = 900
    anthropic_version: str = "2023-06-01"
    last_observation: dict[str, object] = field(default_factory=dict, init=False)

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
        prompt_version = str(request.prompt_version or PROMPT_VERSION)
        system_prompt = build_system_prompt()
        user_prompt = json.dumps(prompt_payload, separators=(",", ":"))
        self.last_observation = {
            "prompt_version": prompt_version,
            "provider_status": "REQUESTED",
            "provider_error_category": None,
            "request_payload": sanitize_mapping({
                "model": self.model,
                "temperature": 0,
                "max_tokens": self.max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": prompt_payload,
                "allowed_actions": list(request.allowed_actions),
            }),
            "raw_model_text_sanitized": None,
            "parsed_response_json": None,
            "finish_reason": None,
            "token_usage": None,
        }
        try:
            if self.provider_name == "anthropic":
                url = f"{self.base_url}/messages"
                body = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "temperature": 0,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
            else:
                url = f"{self.base_url}/chat/completions"
                body = {
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
            response = self.session.post(url, headers=headers, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
            response_text = sanitize_text(getattr(response, "text", ""))
            if response_text:
                self.last_observation["raw_model_text_sanitized"] = response_text
            try:
                payload = response.json()
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.last_observation.update({
                    "provider_status": "INVALID_RESPONSE",
                    "provider_error_category": "INVALID_RESPONSE",
                })
                raise DecisionProviderError("provider returned invalid response JSON", category="INVALID_RESPONSE") from exc
            if not isinstance(payload, Mapping):
                self.last_observation.update({
                    "provider_status": "INVALID_RESPONSE",
                    "provider_error_category": "INVALID_RESPONSE",
                })
                raise DecisionProviderError("provider response must be an object", category="INVALID_RESPONSE")
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                self.last_observation["token_usage"] = sanitize_mapping(usage)
            finish_reason = None
            choices = payload.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                finish_reason = choices[0].get("finish_reason")
            elif self.provider_name == "anthropic":
                finish_reason = payload.get("stop_reason")
            self.last_observation["finish_reason"] = sanitize_text(finish_reason) if finish_reason is not None else None
            truncated = str(finish_reason or "").lower() in {"length", "max_tokens", "max_completion_tokens"}
            try:
                raw_text = _extract_raw_content(payload, provider_name=self.provider_name)
            except DecisionProviderError as exc:
                if truncated:
                    self.last_observation.update({
                        "provider_status": "FAILED",
                        "provider_error_category": "OUTPUT_TRUNCATED",
                    })
                    raise DecisionProviderError("provider output was truncated before valid decision JSON", category="OUTPUT_TRUNCATED") from exc
                raise
            self.last_observation["raw_model_text_sanitized"] = sanitize_text(raw_text)
            parse_text = raw_text.strip()
            if parse_text.startswith("```"):
                parse_text = parse_text.removeprefix("```").strip()
                if parse_text.lower().startswith("json"):
                    parse_text = parse_text[4:].lstrip()
                if parse_text.endswith("```"):
                    parse_text = parse_text[:-3].rstrip()
            try:
                raw = json.loads(parse_text)
            except json.JSONDecodeError as exc:
                self.last_observation.update({
                    "provider_status": "FAILED" if truncated else "INVALID_RESPONSE",
                    "provider_error_category": "OUTPUT_TRUNCATED" if truncated else "INVALID_RESPONSE",
                })
                if truncated:
                    raise DecisionProviderError("provider output was truncated before valid decision JSON", category="OUTPUT_TRUNCATED") from exc
                raise DecisionProviderError("provider returned invalid decision JSON", category="INVALID_RESPONSE") from exc
            if not isinstance(raw, Mapping):
                self.last_observation.update({
                    "provider_status": "INVALID_RESPONSE",
                    "provider_error_category": "INVALID_RESPONSE",
                })
                raise DecisionProviderError("provider decision JSON must be an object", category="INVALID_RESPONSE")
            self.last_observation["parsed_response_json"] = sanitize_mapping(raw)
            if truncated:
                self.last_observation.update({
                    "provider_status": "FAILED",
                    "provider_error_category": "OUTPUT_TRUNCATED",
                })
                raise DecisionProviderError("provider output was truncated before a complete decision", category="OUTPUT_TRUNCATED")
            try:
                response = DecisionResponse.from_mapping(
                    raw,
                    allowed_actions=request.allowed_actions,
                    enforce_allowed_actions=False,
                )
            except (TypeError, ValueError) as exc:
                self.last_observation.update({
                    "provider_status": "INVALID_RESPONSE",
                    "provider_error_category": "SCHEMA_VALIDATION_ERROR",
                })
                raise DecisionProviderError("provider returned invalid decision", category="SCHEMA_VALIDATION_ERROR") from exc
            self.last_observation.update({
                "provider_status": "SUCCESS",
                "provider_error_category": None,
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            })
            return response
        except DecisionProviderError as exc:
            status = self.last_observation.get("provider_status")
            if status == "REQUESTED":
                status = "INVALID_RESPONSE" if exc.category in {"INVALID_RESPONSE", "SCHEMA_VALIDATION_ERROR"} else "FAILED"
            self.last_observation.update({
                "provider_status": status or "FAILED",
                "provider_error_category": exc.category,
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            })
            raise
        except requests.Timeout as exc:
            self.last_observation.update({
                "provider_status": "FAILED",
                "provider_error_category": "TIMEOUT",
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            })
            raise DecisionProviderError(f"provider request failed after {time.perf_counter() - started:.3f}s", category="TIMEOUT") from exc
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            category = "AUTH_ERROR" if status_code in {401, 403} else "MODEL_NOT_FOUND" if status_code == 404 else "RATE_LIMITED" if status_code == 429 else "NETWORK_ERROR"
            self.last_observation.update({
                "provider_status": "FAILED",
                "provider_error_category": category,
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            })
            raise DecisionProviderError(f"provider request failed after {time.perf_counter() - started:.3f}s", category=category) from exc
        except Exception as exc:
            self.last_observation.update({
                "provider_status": "FAILED",
                "provider_error_category": "NETWORK_ERROR",
                "latency_ms": (time.perf_counter() - started) * 1000.0,
            })
            raise DecisionProviderError(
                f"provider request failed after {time.perf_counter() - started:.3f}s",
                category="NETWORK_ERROR",
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
        max_tokens=int(getattr(config, "max_completion_tokens", 900)),
    )
