"""Lazy, per-agent model provider routing for generic Relic-Agent worlds.

The generic configuration keeps provider credentials as environment-variable
*names*.  This module turns that normalized configuration into a small
``OrgLLMClient`` compatible router.  Provider clients are constructed only at
the first call for a route, which means loading or validating a configuration
never reads credentials and never imports an optional SDK.

The existing OrgEnv clients remain the implementation for OpenAI-compatible
and generic HTTP calls.  The small HTTP adapter below fills the protocol gap
needed by a configurable generic organization (Anthropic's messages wire
format).  OpenAI-compatible execution uses the optional OpenAI SDK supplied by
the shared client.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from environments.org_env.llm.client import (
    ACCOUNTING_OUTPUT_CEILING,
    GenericHTTPOrgLLMClient,
    LLMError,
    MockOrgLLMClient,
    OpenAIOrgLLMClient,
    OrgLLMClient,
    UNCAPPED_OUTPUT,
)
from relic_agent.config_schema import AgentSpec, GenericConfig, ProviderSpec


USAGE_COUNTERS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")
_DEFAULT_TEXT_TEMPERATURE = 0.3
_DEFAULT_TEXT_MAX_TOKENS = 800
_DEFAULT_JSON_TEMPERATURE = 0.2
_DEFAULT_JSON_MAX_TOKENS = 1200
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_INLINE_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "authorization",
        "auth_token",
        "access_token",
        "token",
        "password",
        "secret",
        "x_api_key",
        "x-api-key",
        "proxy-authorization",
        "proxy_authorization",
        "x-auth-token",
        "x_auth_token",
        "x-access-token",
        "x_access_token",
        "cookie",
    }
)


class ProviderError(LLMError):
    """A provider failure with a deliberately safe, structured message.

    Provider SDK exception strings often contain request URLs, response
    bodies, or authorization material.  Callers can inspect ``provider_id``
    and ``code`` without having to serialize the original exception.
    """

    def __init__(self, provider_id: str, code: str, detail: str | None = None) -> None:
        self.provider_id = str(provider_id)
        self.code = str(code)
        message = f"provider {self.provider_id!r} {self.code}"
        if detail:
            # ``detail`` is only used for configuration names and fixed,
            # redacted guidance.  Raw provider exception text never reaches
            # this constructor.
            message += f": {detail}"
        super().__init__(message)


class ProviderConfigurationError(ProviderError):
    """A route cannot be called with its current local configuration."""


class ProviderCallError(ProviderError):
    """A provider transport or response failure with no raw response text."""


class SecretRedactor:
    """Redact known secret values from local diagnostics.

    The router does not publish provider traffic at all.  This helper is
    exposed for integrations that need to include a safe error in their own
    logs.  Secrets are write-only: there is intentionally no accessor for the
    internal value set.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        for secret in secrets:
            self.add(secret)

    def add(self, secret: Any) -> None:
        if isinstance(secret, str) and secret:
            self._secrets.add(secret)

    def add_config_secrets(self, value: Any) -> None:
        """Recognize explicit credential fields, including user metadata."""
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).strip().lower().replace(" ", "_") in _INLINE_SECRET_KEYS:
                    self.add(item)
                self.add_config_secrets(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.add_config_secrets(item)

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            result = value
            for secret in sorted(self._secrets, key=len, reverse=True):
                result = result.replace(secret, "<redacted>")
            return result
        if isinstance(value, Mapping):
            return {self.redact(str(key)): self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        return value

    def __call__(self, value: Any) -> Any:
        return self.redact(value)


# A short alias is convenient for callers that use ``Redactor`` in their
# observability wiring.
Redactor = SecretRedactor


@dataclass(frozen=True)
class AgentRoute:
    """The effective provider/model and per-agent generation settings."""

    agent_id: str
    provider: str
    model: str | None = None
    reasoning: Mapping[str, Any] = field(default_factory=dict)
    generation: Mapping[str, Any] = field(default_factory=dict)
    explicit_model: bool = False

    @property
    def provider_id(self) -> str:
        return self.provider

    @property
    def model_name(self) -> str | None:
        return self.model


@dataclass
class _MetricBucket:
    """Private mutable usage bucket; ``stats`` converts it to plain JSON."""

    calls: int = 0
    logical_calls: int = 0
    failures: int = 0
    retries: int = 0
    provider_attempts: int = 0
    fallbacks: int = 0
    fallback_successes: int = 0
    usage_totals: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in USAGE_COUNTERS}
    )
    cost: float = 0.0
    models: dict[str, int] = field(default_factory=dict)

    def add_usage(self, usage: Mapping[str, Any]) -> None:
        for name in USAGE_COUNTERS:
            try:
                value = max(0, int(usage.get(name, 0) or 0))
            except (TypeError, ValueError):
                value = 0
            self.usage_totals[name] += value


class _HTTPFailure(Exception):
    """Internal HTTP error that carries status category only, never a body."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _CodedLLMError(LLMError):
    """An adapter error whose already-safe code survives the registry boundary."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _canonical_provider_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "mock": "mock",
        "deterministic": "mock",
        "openai": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "open_ai": "openai_compatible",
        "anthropic": "anthropic_compatible",
        "anthropic_compatible": "anthropic_compatible",
        "generic": "generic_http",
        "http": "generic_http",
        "generic_http": "generic_http",
    }
    return aliases.get(normalized, normalized)


normalize_provider_type = _canonical_provider_type


def _copy_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _coerce_provider(value: Any) -> ProviderSpec:
    if isinstance(value, ProviderSpec):
        return value
    raw = _copy_mapping(value)
    timeout = raw.get("timeout_seconds", raw.get("timeout", 60.0))
    retries = raw.get("retry_count", raw.get("retries", 0))
    fallback = raw.get("fallback_provider", raw.get("fallback"))
    params = _copy_mapping(raw.get("parameters", {}))
    # Data passed directly to the router is usually already normalized, but
    # accepting provider-level generation/reasoning aliases keeps this class
    # useful to small integrations that do not use config_schema.py.
    for name in ("generation", "reasoning"):
        if name in raw and name not in params:
            params[name] = raw[name]
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        timeout_value = 60.0
    try:
        retry_value = max(0, int(retries))
    except (TypeError, ValueError):
        retry_value = 0
    return ProviderSpec(
        type=str(raw.get("type", "mock") or "mock"),
        base_url_env=(str(raw["base_url_env"]) if raw.get("base_url_env") is not None else None),
        api_key_env=(str(raw["api_key_env"]) if raw.get("api_key_env") is not None else None),
        default_model=(
            str(raw["default_model"]).strip()
            if raw.get("default_model") is not None and str(raw["default_model"]).strip()
            else None
        ),
        timeout_seconds=max(0.001, timeout_value),
        retry_count=retry_value,
        fallback_provider=(
            str(fallback).strip() if fallback is not None and str(fallback).strip() else None
        ),
        parameters=params,
    )


def _coerce_agent(value: Any, index: int = 0) -> AgentRoute:
    if isinstance(value, AgentRoute):
        return value
    if isinstance(value, AgentSpec):
        return AgentRoute(
            agent_id=value.id,
            provider=value.provider or "",
            model=value.model,
            reasoning=dict(value.reasoning),
            generation=dict(value.generation),
            explicit_model=value.model is not None,
        )
    raw = _copy_mapping(value)
    agent_id = str(raw.get("id", raw.get("agent_id", f"agent-{index}"))).strip()
    provider = str(raw.get("provider", "") or "").strip()
    model_value = raw.get("model")
    model = str(model_value).strip() if model_value is not None and str(model_value).strip() else None
    reasoning = raw.get("reasoning", raw.get("reasoning_parameters", {}))
    generation = raw.get("generation", raw.get("generation_parameters", {}))
    return AgentRoute(
        agent_id=agent_id,
        provider=provider,
        model=model,
        reasoning=_copy_mapping(reasoning),
        generation=_copy_mapping(generation),
        explicit_model=model is not None,
    )


def _merge_options(*values: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if isinstance(value, Mapping):
            result.update({str(key): item for key, item in value.items()})
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _safe_error_code(value: Any) -> str:
    """Keep provider error codes structural and free of raw exception text."""

    candidate = str(value or "").strip()
    if candidate and len(candidate) <= 80 and re.fullmatch(r"[A-Za-z0-9_.-]+", candidate):
        return candidate
    return "provider_call_failed"


def _usage_from_stats(stats: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(stats, Mapping):
        return {name: 0 for name in USAGE_COUNTERS}
    usage = stats.get("usage_totals")
    if not isinstance(usage, Mapping):
        usage = stats
    return {name: _safe_int(usage.get(name), 0) for name in USAGE_COUNTERS}


def _usage_delta(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, int]:
    old = _usage_from_stats(before)
    new = _usage_from_stats(after)
    return {name: max(0, new[name] - old[name]) for name in USAGE_COUNTERS}


def _extract_json_text(value: str) -> Any:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        # A few gateways prepend a short explanation despite the JSON-only
        # instruction.  Decode the first balanced object/array without
        # retaining the explanation in any error.
        starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
        if not starts:
            raise ValueError("invalid_json")
        start = min(starts)
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except (TypeError, ValueError):
                continue
        raise ValueError("invalid_json")


def _body_text(body: Any) -> str:
    if isinstance(body, str):
        return body.strip()
    if not isinstance(body, Mapping):
        return ""
    for key in ("text", "output_text"):
        value = body.get(key)
        if isinstance(value, str):
            return value.strip()
    content = body.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks).strip()
    choices = body.get("choices")
    if isinstance(choices, Sequence) and choices:
        choice = choices[0]
        if isinstance(choice, Mapping):
            message = choice.get("message")
            if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(choice.get("text"), str):
                return choice["text"].strip()
    output = body.get("output")
    if isinstance(output, str):
        return output.strip()
    if isinstance(output, Mapping):
        return _body_text(output)
    if isinstance(output, Sequence) and not isinstance(output, (str, bytes, bytearray)):
        chunks = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            for block in item.get("content", ()) or ():
                if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
        return "".join(chunks).strip()
    for key in ("result", "data"):
        value = body.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Mapping):
            return _body_text(value)
    # Keep the shared GenericHTTPOrgLLMClient contract for endpoints that
    # return the result object itself (for example ``{"answer": "yes"}``).
    # The serialized object also lets ``generate_json`` validate that shape.
    if body and not any(
        key in body for key in ("text", "output_text", "content", "choices", "output", "result", "data")
    ):
        try:
            return json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    return ""


def _body_json(body: Any) -> Any:
    if isinstance(body, Mapping):
        for key in ("json", "result", "output", "data"):
            candidate = body.get(key)
            if isinstance(candidate, Mapping):
                return candidate
            if isinstance(candidate, (str, bytes)):
                return _extract_json_text(candidate.decode() if isinstance(candidate, bytes) else candidate)
        content = body.get("content")
        if isinstance(content, Mapping):
            return content
        return _extract_json_text(_body_text(body))
    if isinstance(body, str):
        return _extract_json_text(body)
    return body


def _response_usage(body: Any) -> Any:
    if isinstance(body, Mapping):
        usage = body.get("usage")
        if isinstance(usage, Mapping):
            normalized = dict(usage)
            if "prompt_tokens" not in normalized and "input_tokens" in normalized:
                normalized["prompt_tokens"] = normalized["input_tokens"]
            if "completion_tokens" not in normalized and "output_tokens" in normalized:
                normalized["completion_tokens"] = normalized["output_tokens"]
            if "total_tokens" not in normalized:
                normalized["total_tokens"] = _safe_int(normalized.get("prompt_tokens")) + _safe_int(
                    normalized.get("completion_tokens")
                )
            return normalized
        return usage
    return None


class _HTTPAdapter:
    """Small stdlib JSON transport shared by the two wire-format adapters."""

    def __init__(self, *, endpoint: str, timeout_seconds: float, headers: Mapping[str, str]) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = float(timeout_seconds)
        self.headers = {str(key): str(value) for key, value in headers.items()}

    def request(self, payload: Mapping[str, Any]) -> Any:
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json", **self.headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                status_value = getattr(response, "status", None)
                status = int(status_value if status_value is not None else response.getcode())
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = f"http_status_{int(exc.code)}" if exc.code else "http_error"
            raise _HTTPFailure(code) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
                raise _HTTPFailure("timeout") from None
            raise _HTTPFailure("transport_error") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise _HTTPFailure("response_too_large")
        if status < 200 or status >= 300:
            raise _HTTPFailure(f"http_status_{status}")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _HTTPFailure("invalid_json_response") from None


def _append_endpoint(base_url: str, suffix: str) -> str:
    """Append a protocol path while preserving a configured gateway prefix."""

    base = base_url.rstrip("/")
    if base.endswith(suffix):
        return base
    return base + suffix


def _headers_with_auth(
    *,
    api_key: str | None,
    parameters: Mapping[str, Any],
    default_header: str,
    default_prefix: str,
) -> dict[str, str]:
    headers = _copy_mapping(parameters.get("headers", parameters.get("default_headers", {})))
    result = {str(key): str(value) for key, value in headers.items()}
    if api_key:
        header = str(parameters.get("api_key_header", default_header) or default_header)
        prefix = str(parameters.get("api_key_prefix", default_prefix) or "")
        result[header] = f"{prefix}{api_key}"
    return result


def _has_inline_secret(parameters: Mapping[str, Any]) -> bool:
    """Reject credentials that would otherwise enter config/artifact output."""

    seen: set[int] = set()

    def visit(value: Any) -> bool:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                return False
            seen.add(identity)
            try:
                for raw_key, item in value.items():
                    key = str(raw_key).strip().lower().replace(" ", "_")
                    if key in _INLINE_SECRET_KEYS and item:
                        return True
                    if visit(item):
                        return True
            finally:
                seen.remove(identity)
        elif isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return False
            seen.add(identity)
            try:
                return any(visit(item) for item in value)
            finally:
                seen.remove(identity)
        return False

    return visit(parameters)


class GenericHTTPProviderClient(GenericHTTPOrgLLMClient):
    """Generic JSON HTTP provider using the existing OrgEnv client contract."""

    provider = "generic_http"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str = "local",
        timeout_seconds: float = 60.0,
        api_key: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(endpoint=endpoint, model=model)
        self.timeout_seconds = float(timeout_seconds)
        params = dict(parameters or {})
        self._transport = _HTTPAdapter(
            endpoint=endpoint,
            timeout_seconds=self.timeout_seconds,
            headers=_headers_with_auth(
                api_key=api_key,
                parameters=params,
                default_header="Authorization",
                default_prefix="Bearer ",
            ),
        )
        self._parameters = params

    def _payload(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        generation = _copy_mapping(self._parameters.get("generation", {}))
        for key in ("top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "stop", "stop_sequences"):
            if key in generation:
                payload[key] = generation[key]
        reasoning = _copy_mapping(self._parameters.get("reasoning", {}))
        if reasoning:
            payload["reasoning"] = reasoning
        return payload

    def _call(self, payload: Mapping[str, Any]) -> Any:
        return self._transport.request(payload)

    def generate_text(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        self.calls += 1
        try:
            body = self._call(self._payload(system_prompt, user_prompt, temperature, max_tokens))
            self._record_usage(_response_usage(body))
            text = _body_text(body)
            if not text:
                raise LLMError("empty provider response")
            return text
        except LLMError:
            self.failures += 1
            raise
        except _HTTPFailure as exc:
            self.failures += 1
            raise _CodedLLMError(exc.code) from None

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], *, temperature: float = 0.2, max_tokens: int = 1200) -> dict[str, Any]:
        self.calls += 1
        try:
            payload = self._payload(
                system_prompt + "\nReturn ONLY valid JSON.",
                user_prompt + OrgLLMClient._schema_hint(schema),
                temperature,
                max_tokens,
            )
            body = self._call(payload)
            self._record_usage(_response_usage(body))
            data = _body_json(body)
            return self._validate(data, schema)
        except LLMError:
            self.failures += 1
            raise
        except (ValueError, TypeError):
            self.failures += 1
            raise LLMError("invalid_json_response") from None
        except _HTTPFailure as exc:
            self.failures += 1
            raise _CodedLLMError(exc.code) from None


class AnthropicCompatibleOrgLLMClient(OrgLLMClient):
    """Thin stdlib adapter for Anthropic's ``/v1/messages`` protocol."""

    provider = "anthropic_compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_seconds: float = 60.0,
        api_key: str | None = None,
        reasoning: Mapping[str, Any] | None = None,
        generation: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.reasoning = dict(reasoning or {})
        self.generation = dict(generation or {})
        params = dict(parameters or {})
        headers = _headers_with_auth(
            api_key=api_key,
            parameters=params,
            default_header="x-api-key",
            default_prefix="",
        )
        headers.setdefault("anthropic-version", str(params.get("anthropic_version", "2023-06-01")))
        self._transport = _HTTPAdapter(
            endpoint=endpoint,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
        )

    def _payload(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> dict[str, Any]:
        generation = _merge_options(self.generation)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": user_prompt}],
            "system": system_prompt,
            "max_tokens": max_tokens,
        }
        if "temperature" in generation:
            temperature = generation["temperature"]
        reasoning = _merge_options(self.reasoning)
        budget = reasoning.get("budget_tokens", reasoning.get("thinking_budget"))
        if budget is None and reasoning.get("enabled") is True:
            budget = max(1, int(max_tokens or 1))
        if budget is not None and _safe_int(budget) > 0:
            payload["thinking"] = {"type": "enabled", "budget_tokens": _safe_int(budget)}
        else:
            payload["temperature"] = temperature
        for key in ("top_p", "top_k", "stop_sequences"):
            if key in generation:
                payload[key] = generation[key]
        return payload

    def _call(self, payload: Mapping[str, Any]) -> Any:
        return self._transport.request(payload)

    def generate_text(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        self.calls += 1
        try:
            body = self._call(self._payload(system_prompt, user_prompt, temperature, max_tokens))
            self._record_usage(_response_usage(body))
            text = _body_text(body)
            if not text:
                raise LLMError("empty provider response")
            return text
        except LLMError:
            self.failures += 1
            raise
        except _HTTPFailure as exc:
            self.failures += 1
            raise _CodedLLMError(exc.code) from None

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], *, temperature: float = 0.2, max_tokens: int = 1200) -> dict[str, Any]:
        self.calls += 1
        try:
            body = self._call(
                self._payload(
                    system_prompt + "\nReturn ONLY valid JSON.",
                    user_prompt + OrgLLMClient._schema_hint(schema),
                    temperature,
                    max_tokens,
                )
            )
            self._record_usage(_response_usage(body))
            return self._validate(_body_json(body), schema)
        except LLMError:
            self.failures += 1
            raise
        except (ValueError, TypeError):
            self.failures += 1
            raise LLMError("invalid_json_response") from None
        except _HTTPFailure as exc:
            self.failures += 1
            raise _CodedLLMError(exc.code) from None


class OpenAICompatibleOrgLLMClient(OrgLLMClient):
    """Thin adapter around the shared ``OpenAIOrgLLMClient`` implementation."""

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        reasoning: Mapping[str, Any] | None = None,
        generation: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.base_url = base_url
        self.reasoning = dict(reasoning or {})
        self.generation = dict(generation or {})
        self.parameters = dict(parameters or {})
        effort = self.reasoning.get("effort", self.reasoning.get("reasoning_effort", "low"))
        self._delegate = OpenAIOrgLLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            reasoning_effort=str(effort),
            # Retries are owned by ProviderRegistry.  This prevents a
            # provider-level retry count from being multiplied by SDK retries.
            max_retries=0,
            retry_backoff_seconds=0.0,
            wire_api=str(self.parameters.get("wire_api", "chat_completions")),
            json_transport=str(self.parameters.get("json_transport", "native")),
            request_timeout_seconds=timeout_seconds,
            store_responses=bool(self.parameters.get("store_responses", False)),
            default_headers=_copy_mapping(
                self.parameters.get("default_headers", self.parameters.get("headers", {}))
            ),
        )

    def _sync_stats(self) -> None:
        stats = self._delegate.stats()
        self.failures = _safe_int(stats.get("failures"))
        self.retries = _safe_int(stats.get("retries"))
        self.provider_attempts = _safe_int(stats.get("provider_attempts"))
        self.usage_totals = _usage_from_stats(stats)
        models = stats.get("response_model_counts")
        if isinstance(models, Mapping):
            self.response_model_counts = {
                str(key): _safe_int(value) for key, value in models.items()
            }
        self.response_id_digest = str(stats.get("response_id_digest") or "")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 800,
    ) -> str:
        self.calls += 1
        try:
            result = self._delegate.generate_text(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._sync_stats()
            return result
        except Exception as exc:
            self._sync_stats()
            if isinstance(exc, LLMError):
                raise
            raise LLMError("openai provider call failed") from None

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        self.calls += 1
        try:
            result = self._delegate.generate_json(
                system_prompt,
                user_prompt,
                schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._sync_stats()
            return result
        except Exception as exc:
            self._sync_stats()
            if isinstance(exc, LLMError):
                raise
            raise LLMError("openai provider call failed") from None


# Friendly aliases matching the names used by the surrounding OrgEnv code.
AnthropicOrgLLMClient = AnthropicCompatibleOrgLLMClient
GenericHTTPConfiguredClient = GenericHTTPProviderClient


class AgentProviderClient(OrgLLMClient):
    """Lazy route proxy bound to one agent's provider/model settings."""

    def __init__(self, registry: "ProviderRegistry", route: AgentRoute) -> None:
        super().__init__()
        self.registry = registry
        self.route = route
        self.agent_id = route.agent_id
        self.provider = route.provider
        self.provider_id = route.provider
        self.provider_type = _canonical_provider_type(registry._provider_spec(route.provider).type)
        self.model = route.model
        self.reasoning = dict(route.reasoning)
        self.generation = dict(route.generation)

    def generate_text(self, system_prompt: str, user_prompt: str, *, temperature: float | None = None, max_tokens: int | None = None) -> str:
        try:
            return self.registry._invoke_route(
                self.route,
                "generate_text",
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            self._sync_route_stats()

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], *, temperature: float | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        try:
            return self.registry._invoke_route(
                self.route,
                "generate_json",
                system_prompt,
                user_prompt,
                schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            self._sync_route_stats()

    def _sync_route_stats(self) -> None:
        stats = self.registry._route_stats(self.route)
        self.calls = _safe_int(stats.get("calls"))
        self.failures = _safe_int(stats.get("failures"))
        self.retries = _safe_int(stats.get("retries"))
        self.provider_attempts = _safe_int(stats.get("provider_attempts"))
        self.usage_totals = _usage_from_stats(stats)

    def stats(self) -> dict[str, Any]:
        return self.registry._route_stats(self.route)

    def request_resource_envelope(self, max_tokens: int) -> dict[str, int]:
        spec = self.registry._provider_spec(self.route.provider)
        return {
            "provider_attempts": 1 + max(0, int(spec.retry_count)),
            "output_tokens_per_attempt": (
                ACCOUNTING_OUTPUT_CEILING
                if max(0, int(max_tokens)) == UNCAPPED_OUTPUT
                else max(0, int(max_tokens))
            ),
        }


class ProviderRegistry(OrgLLMClient):
    """Registry and per-agent router for generic organization model calls.

    ``ProviderRegistry`` can be attached directly to ``world.llm_client``.
    Existing OrgWorld modules call the inherited two-prompt API; use
    ``use_agent`` around an agent turn to select the route.  Integrations that
    already know the agent can use ``client_for_agent`` or
    ``generate_json_for_agent`` instead.
    """

    provider = "router"

    def __init__(
        self,
        providers: Mapping[str, Any] | GenericConfig | Any = None,
        agents: Sequence[Any] | None = None,
        *,
        config: Any = None,
        default_provider: str | None = None,
        default_agent_id: str | None = None,
        environment: Mapping[str, str] | None = None,
        redactor: SecretRedactor | None = None,
        client_factories: Mapping[str, Callable[..., OrgLLMClient]] | None = None,
        token_logging: bool = True,
        cost_logging: bool = True,
    ) -> None:
        super().__init__()
        if providers is None:
            providers = config
        if providers is None:
            raise ProviderConfigurationError("router", "missing_configuration") from None
        self._lock = threading.RLock()
        self._environment = environment if environment is not None else os.environ
        self._redactor = redactor or SecretRedactor()
        self.token_logging = bool(token_logging)
        self.cost_logging = bool(cost_logging)
        self._client_factories = {
            _canonical_provider_type(key): factory
            for key, factory in (client_factories or {}).items()
        }
        config_runtime_provider: str | None = None
        config_observability: Any = None
        if isinstance(providers, GenericConfig):
            config = providers
            providers = config.providers
            if agents is None:
                agents = config.agents
            config_runtime_provider = config.runtime.provider
            config_observability = config.observability
        elif hasattr(providers, "generic") and getattr(providers, "generic", None) is not None:
            config = providers.generic
            providers = config.providers
            if agents is None:
                agents = config.agents
            config_runtime_provider = config.runtime.provider
            config_observability = config.observability
        elif isinstance(providers, Mapping) and "providers" in providers:
            raw_root = providers
            if agents is None:
                agents = raw_root.get("agents")
            providers = raw_root["providers"]
            runtime = raw_root.get("runtime")
            if isinstance(runtime, Mapping):
                config_runtime_provider = str(runtime.get("provider") or "") or None
            config_observability = raw_root.get("observability")
        if isinstance(config_observability, Mapping):
            if "token_logging" in config_observability:
                self.token_logging = bool(config_observability["token_logging"])
            if "cost_logging" in config_observability:
                self.cost_logging = bool(config_observability["cost_logging"])
        elif config_observability is not None:
            token_logging_value = getattr(config_observability, "token_logging", None)
            cost_logging_value = getattr(config_observability, "cost_logging", None)
            if token_logging_value is not None:
                self.token_logging = bool(token_logging_value)
            if cost_logging_value is not None:
                self.cost_logging = bool(cost_logging_value)
        self._providers: dict[str, ProviderSpec] = {
            str(provider_id): _coerce_provider(spec)
            for provider_id, spec in _copy_mapping(providers).items()
        }
        for provider_id, spec in self._providers.items():
            if spec.fallback_provider and spec.fallback_provider not in self._providers:
                raise ProviderConfigurationError(
                    provider_id, "unknown_fallback_provider"
                ) from None
        self._routes: dict[str, AgentRoute] = {}
        if isinstance(agents, Mapping):
            agent_values: Sequence[Any] = tuple(
                {**_copy_mapping(value), "id": key} for key, value in agents.items()
            )
        else:
            agent_values = agents or ()
        for index, agent in enumerate(agent_values):
            route = _coerce_agent(agent, index)
            if route.agent_id and route.agent_id not in self._routes:
                provider_id = route.provider
                if not provider_id and len(self._providers) == 1:
                    provider_id = next(iter(self._providers))
                if not provider_id and config_runtime_provider and config_runtime_provider in self._providers:
                    provider_id = config_runtime_provider
                spec = self._providers.get(provider_id)
                if provider_id and spec is None:
                    raise ProviderConfigurationError(
                        provider_id, "unknown_provider"
                    ) from None
                model = route.model or (spec.default_model if spec is not None else None)
                self._routes[route.agent_id] = AgentRoute(
                    agent_id=route.agent_id,
                    provider=provider_id,
                    model=model,
                    reasoning=route.reasoning,
                    generation=route.generation,
                    explicit_model=route.explicit_model,
                )
        if default_provider is None:
            default_provider = config_runtime_provider if config_runtime_provider in self._providers else None
        if default_provider is None and len(self._providers) == 1:
            default_provider = next(iter(self._providers))
        self.default_provider = default_provider
        self.default_agent_id = default_agent_id or (next(iter(self._routes)) if self._routes else None)
        self._current_agent: ContextVar[str | None] = ContextVar(
            "relic_agent_provider_context", default=default_agent_id
        )
        self._raw_clients: dict[tuple[Any, ...], OrgLLMClient] = {}
        self._metrics = _MetricBucket()
        self._provider_metrics: dict[str, _MetricBucket] = {}
        self._agent_metrics: dict[str, _MetricBucket] = {}
        self._last_errors: list[dict[str, str]] = []

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "ProviderRegistry":
        return cls(config, **kwargs)

    @property
    def redactor(self) -> SecretRedactor:
        return self._redactor

    def redact(self, value: Any) -> Any:
        """Return a recursively redacted value suitable for public output."""

        return self._redactor.redact(value)

    redact_for_output = redact

    @property
    def providers(self) -> Mapping[str, ProviderSpec]:
        return dict(self._providers)

    @property
    def agents(self) -> Mapping[str, AgentRoute]:
        return dict(self._routes)

    @property
    def current_agent_id(self) -> str | None:
        return self._current_agent.get()

    @current_agent_id.setter
    def current_agent_id(self, value: str | None) -> None:
        if value is not None:
            self.resolve(value)
        self._current_agent.set(value)

    def register_factory(self, provider_type: str, factory: Callable[..., OrgLLMClient]) -> None:
        self._client_factories[_canonical_provider_type(provider_type)] = factory

    def _provider_spec(self, provider_id: str) -> ProviderSpec:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderConfigurationError(provider_id, "unknown_provider") from None

    @staticmethod
    def _agent_key(agent_id: Any) -> str:
        if isinstance(agent_id, AgentRoute):
            return agent_id.agent_id
        if isinstance(agent_id, AgentSpec):
            return agent_id.id
        if isinstance(agent_id, Mapping):
            return str(agent_id.get("id", agent_id.get("agent_id", ""))).strip()
        attr_id = getattr(agent_id, "agent_id", getattr(agent_id, "id", None))
        return str(attr_id if attr_id is not None else agent_id).strip()

    def resolve(self, agent_id: Any) -> AgentRoute:
        key = self._agent_key(agent_id)
        try:
            return self._routes[key]
        except KeyError:
            raise ProviderConfigurationError(key, "unknown_agent") from None

    resolve_agent = resolve
    route_for_agent = resolve
    resolve_route = resolve

    def provider_for_agent(self, agent_id: Any) -> str:
        return self.resolve(agent_id).provider

    provider_for = provider_for_agent

    def _direct_route(
        self,
        provider_id: str,
        *,
        model: str | None = None,
        agent_id: str | None = None,
        reasoning: Mapping[str, Any] | None = None,
        generation: Mapping[str, Any] | None = None,
    ) -> AgentRoute:
        spec = self._provider_spec(provider_id)
        explicit = model is not None
        return AgentRoute(
            agent_id=agent_id or "__direct__",
            provider=provider_id,
            model=model or spec.default_model,
            reasoning=dict(reasoning or {}),
            generation=dict(generation or {}),
            explicit_model=explicit,
        )

    def client_for_agent(self, agent_id: str) -> AgentProviderClient:
        return AgentProviderClient(self, self.resolve(agent_id))

    for_agent = client_for_agent
    agent_client = client_for_agent
    route = client_for_agent
    get_client_for_agent = client_for_agent
    client_for = client_for_agent

    def get_client(
        self,
        provider_id: str,
        model: str | None = None,
        *,
        agent_id: str | None = None,
        reasoning: Mapping[str, Any] | None = None,
        generation: Mapping[str, Any] | None = None,
    ) -> AgentProviderClient:
        return AgentProviderClient(
            self,
            self._direct_route(
                provider_id,
                model=model,
                agent_id=agent_id,
                reasoning=reasoning,
                generation=generation,
            ),
        )

    client = get_client

    @contextmanager
    def use_agent(self, agent_id: str) -> Iterator[AgentProviderClient]:
        route = self.resolve(agent_id)
        token = self._current_agent.set(route.agent_id)
        try:
            yield AgentProviderClient(self, route)
        finally:
            self._current_agent.reset(token)

    agent_context = use_agent

    def _selected_route(self, agent_id: str | None = None) -> AgentRoute:
        selected = agent_id or self._current_agent.get()
        if selected:
            if selected in self._routes:
                return self._routes[selected]
            # A direct provider can use ``default_agent_id`` only when it is a
            # known route; unknown context IDs should fail clearly.
            raise ProviderConfigurationError(selected, "unknown_agent") from None
        if self.default_provider:
            return self._direct_route(self.default_provider)
        if self._routes:
            return next(iter(self._routes.values()))
        raise ProviderConfigurationError("router", "no_agent_route") from None

    def generate_text(self, system_prompt: str, user_prompt: str, *, temperature: float | None = None, max_tokens: int | None = None, agent_id: str | None = None) -> str:
        return self._invoke_route(
            self._selected_route(agent_id),
            "generate_text",
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], *, temperature: float | None = None, max_tokens: int | None = None, agent_id: str | None = None) -> dict[str, Any]:
        return self._invoke_route(
            self._selected_route(agent_id),
            "generate_json",
            system_prompt,
            user_prompt,
            schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate_text_for_agent(self, agent_id: str, system_prompt: str, user_prompt: str, *, temperature: float | None = None, max_tokens: int | None = None) -> str:
        return self._invoke_route(self.resolve(agent_id), "generate_text", system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)

    def generate_json_for_agent(self, agent_id: str, system_prompt: str, user_prompt: str, schema: dict[str, Any], *, temperature: float | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        return self._invoke_route(self.resolve(agent_id), "generate_json", system_prompt, user_prompt, schema, temperature=temperature, max_tokens=max_tokens)

    # Common explicit aliases used by runtime adapters.
    text_for_agent = generate_text_for_agent
    json_for_agent = generate_json_for_agent

    def _effective_generation(self, route: AgentRoute, method: str, temperature: float | None, max_tokens: int | None) -> tuple[float, int]:
        spec = self._provider_spec(route.provider)
        provider_params = spec.parameters
        provider_generation = _copy_mapping(provider_params.get("generation", {}))
        generation = _merge_options(provider_generation, route.generation)
        default_temperature = _DEFAULT_JSON_TEMPERATURE if method == "generate_json" else _DEFAULT_TEXT_TEMPERATURE
        default_max = _DEFAULT_JSON_MAX_TOKENS if method == "generate_json" else _DEFAULT_TEXT_MAX_TOKENS
        selected_temperature = temperature
        if selected_temperature is None:
            selected_temperature = generation.get("temperature", default_temperature)
        selected_max = max_tokens
        if selected_max is None:
            selected_max = generation.get("max_tokens", generation.get("max_output_tokens", default_max))
        try:
            selected_temperature = float(selected_temperature)
        except (TypeError, ValueError):
            selected_temperature = default_temperature
        try:
            selected_max = int(selected_max)
        except (TypeError, ValueError):
            selected_max = default_max
        return selected_temperature, max(0, selected_max)

    def _env_get(self, name: str | None, *, provider_id: str, kind: str, required: bool) -> str | None:
        if not name:
            return None
        try:
            value = self._environment.get(name)
        except Exception:
            value = None
        if value is not None:
            value = str(value).strip()
        if not value and required:
            raise ProviderConfigurationError(
                provider_id,
                f"missing_{kind}_environment",
                f"set environment variable {name!r}",
            ) from None
        if value:
            self._redactor.add(value)
        return value or None

    def _resolve_runtime_values(self, provider_id: str, spec: ProviderSpec, canonical_type: str) -> tuple[str | None, str | None, dict[str, Any]]:
        params = dict(spec.parameters)
        if _has_inline_secret(params):
            raise ProviderConfigurationError(provider_id, "inline_credentials_forbidden") from None
        base_url = params.get("base_url", params.get("endpoint"))
        if canonical_type != "mock" and spec.base_url_env:
            base_url = self._env_get(spec.base_url_env, provider_id=provider_id, kind="base_url", required=True)
        elif base_url is not None:
            base_url = str(base_url)
        api_key = None
        if canonical_type != "mock" and spec.api_key_env:
            api_key = self._env_get(spec.api_key_env, provider_id=provider_id, kind="api_key", required=True)
        elif canonical_type != "mock":
            # Standard variables are opt-in convenience fallbacks.  They are
            # read only once this provider is actually being called.
            standard_name = "ANTHROPIC_API_KEY" if canonical_type == "anthropic_compatible" else "OPENAI_API_KEY" if canonical_type == "openai_compatible" else None
            if standard_name:
                api_key = self._env_get(standard_name, provider_id=provider_id, kind="api_key", required=False)
        if base_url is not None:
            base_url = str(base_url)
        return base_url, api_key, params

    def _raw_client(self, route: AgentRoute) -> OrgLLMClient:
        provider_id = route.provider
        spec = self._provider_spec(provider_id)
        model = route.model or spec.default_model
        if not model or not str(model).strip():
            raise ProviderConfigurationError(
                provider_id,
                "missing_model",
                "请设置该 provider 对应的 runtime model name。",
            ) from None
        model = str(model).strip()
        canonical_type = _canonical_provider_type(spec.type)
        base_url, api_key, params = self._resolve_runtime_values(provider_id, spec, canonical_type)
        reasoning = _merge_options(_copy_mapping(params.get("reasoning", {})), route.reasoning)
        generation = _merge_options(_copy_mapping(params.get("generation", {})), route.generation)
        cache_key = (
            provider_id,
            canonical_type,
            model,
            json.dumps(reasoning, sort_keys=True, separators=(",", ":"), default=str),
            json.dumps(generation, sort_keys=True, separators=(",", ":"), default=str),
            json.dumps({key: value for key, value in params.items() if key not in {"api_key", "headers", "default_headers"}}, sort_keys=True, separators=(",", ":"), default=str),
            str(base_url or ""),
            # The credential value itself never enters the cache key.  A
            # digest still prevents a changed environment value from reusing
            # a client carrying the previous key.
            hashlib.sha256((api_key or "").encode("utf-8")).hexdigest(),
        )
        with self._lock:
            existing = self._raw_clients.get(cache_key)
            if existing is not None:
                return existing
        factory = self._client_factories.get(canonical_type)
        if factory is not None:
            try:
                client = factory(
                    provider_id=provider_id,
                    provider=spec,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    timeout_seconds=spec.timeout_seconds,
                    reasoning=reasoning,
                    generation=generation,
                    parameters=params,
                )
            except ProviderError:
                raise
            except Exception:
                raise ProviderConfigurationError(provider_id, "client_factory_failed") from None
        elif canonical_type == "mock":
            mock_kwargs: dict[str, Any] = {}
            if isinstance(params.get("fail"), bool):
                mock_kwargs["fail"] = params["fail"]
            responder = params.get("responder")
            if callable(responder):
                mock_kwargs["responder"] = responder
            script = params.get("script")
            if isinstance(script, list) and all(isinstance(item, Mapping) for item in script):
                mock_kwargs["script"] = [dict(item) for item in script]
            client = MockOrgLLMClient(**mock_kwargs)
        elif canonical_type == "openai_compatible":
            try:
                client = OpenAICompatibleOrgLLMClient(
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    timeout_seconds=spec.timeout_seconds,
                    reasoning=reasoning,
                    generation=generation,
                    parameters=params,
                )
            except Exception:
                raise ProviderConfigurationError(provider_id, "client_initialization_failed") from None
        elif canonical_type == "anthropic_compatible":
            endpoint = base_url or "https://api.anthropic.com/v1/messages"
            endpoint_path = urlsplit(endpoint).path.rstrip("/")
            endpoint = (
                _append_endpoint(endpoint, "/v1/messages")
                if endpoint_path in {"", "/v1"}
                else endpoint
            )
            # ProviderRegistry has already resolved generation defaults and
            # explicit per-call overrides in ``_effective_generation``.  The
            # Anthropic adapter also accepts generation defaults for direct
            # use, so omit the fields it would otherwise re-apply and thereby
            # override the resolved call values.
            adapter_generation = {
                key: value
                for key, value in generation.items()
                if key not in {"temperature", "max_tokens", "max_output_tokens"}
            }
            client = AnthropicCompatibleOrgLLMClient(
                endpoint=endpoint,
                model=model,
                timeout_seconds=spec.timeout_seconds,
                api_key=api_key,
                reasoning=reasoning,
                generation=adapter_generation,
                parameters=params,
            )
        elif canonical_type == "generic_http":
            endpoint = base_url
            if not endpoint:
                raise ProviderConfigurationError(provider_id, "missing_endpoint", "set base_url_env or parameters.endpoint") from None
            client = GenericHTTPProviderClient(
                endpoint=endpoint,
                model=model,
                timeout_seconds=spec.timeout_seconds,
                api_key=api_key,
                parameters={**params, "reasoning": reasoning, "generation": generation},
            )
        else:
            raise ProviderConfigurationError(provider_id, "unsupported_type") from None
        with self._lock:
            self._raw_clients[cache_key] = client
        return client

    def _bucket(self, collection: dict[str, _MetricBucket], key: str) -> _MetricBucket:
        with self._lock:
            bucket = collection.get(key)
            if bucket is None:
                bucket = _MetricBucket()
                collection[key] = bucket
            return bucket

    @staticmethod
    def _cost_rates(parameters: Mapping[str, Any]) -> tuple[float, float, float | None]:
        pricing = parameters.get("pricing", parameters.get("cost", {}))
        pricing = pricing if isinstance(pricing, Mapping) else {}
        def _rate(names: Sequence[str]) -> float:
            for name in names:
                value = parameters.get(name, pricing.get(name))
                if value is not None:
                    try:
                        return max(0.0, float(value))
                    except (TypeError, ValueError):
                        pass
            return 0.0
        input_rate = _rate(("input_cost_per_1k", "prompt_cost_per_1k", "input_price_per_1k", "prompt_price_per_1k", "input_per_1k"))
        output_rate = _rate(("output_cost_per_1k", "completion_cost_per_1k", "output_price_per_1k", "completion_price_per_1k", "output_per_1k"))
        total_rate_value = parameters.get("cost_per_1k_tokens", pricing.get("cost_per_1k_tokens"))
        total_rate: float | None = None
        if total_rate_value is not None:
            try:
                total_rate = max(0.0, float(total_rate_value))
            except (TypeError, ValueError):
                total_rate = None
        return input_rate, output_rate, total_rate

    def _record_usage(self, provider_id: str, agent_id: str, model: str, usage: Mapping[str, Any]) -> None:
        if not self.token_logging:
            return
        provider_bucket = self._bucket(self._provider_metrics, provider_id)
        agent_bucket = self._bucket(self._agent_metrics, agent_id)
        provider_bucket.add_usage(usage)
        agent_bucket.add_usage(usage)
        self._metrics.add_usage(usage)
        spec = self._provider_spec(provider_id)
        input_rate, output_rate, total_rate = self._cost_rates(spec.parameters)
        prompt = _safe_int(usage.get("prompt_tokens"))
        completion = _safe_int(usage.get("completion_tokens"))
        total = _safe_int(usage.get("total_tokens")) or prompt + completion
        cost = ((prompt / 1000.0) * input_rate) + ((completion / 1000.0) * output_rate)
        if total_rate is not None and not input_rate and not output_rate:
            cost = (total / 1000.0) * total_rate
        if self.cost_logging:
            provider_bucket.cost += cost
            agent_bucket.cost += cost
            self._metrics.cost += cost
        # ``ProviderRegistry`` is an ``OrgLLMClient`` drop-in.  Keep the
        # inherited public counters in sync for integrations that read the
        # attributes directly instead of calling ``stats()``.
        self.usage_totals = dict(self._metrics.usage_totals)

    def _record_attempt(
        self,
        route: AgentRoute,
        *,
        failed: bool,
        retry: bool,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
    ) -> None:
        provider_bucket = self._bucket(self._provider_metrics, route.provider)
        agent_bucket = self._bucket(self._agent_metrics, route.agent_id)
        for bucket in (self._metrics, provider_bucket, agent_bucket):
            bucket.calls += 1
            bucket.provider_attempts += 1
            if failed:
                bucket.failures += 1
            if retry:
                bucket.retries += 1
            bucket.models[route.model or "unreported"] = bucket.models.get(route.model or "unreported", 0) + 1
        usage = _usage_delta(before, after)
        self._record_usage(route.provider, route.agent_id, route.model or "unreported", usage)
        self.calls = self._metrics.calls
        self.failures = self._metrics.failures
        self.retries = self._metrics.retries
        self.provider_attempts = self._metrics.provider_attempts
        self.response_model_counts = dict(self._metrics.models)

    def _safe_call_error(self, provider_id: str, exc: Exception) -> ProviderCallError:
        if isinstance(exc, ProviderCallError):
            return exc
        if isinstance(exc, ProviderConfigurationError):
            return exc  # type: ignore[return-value]
        code = _safe_error_code(getattr(exc, "code", None))
        return ProviderCallError(provider_id, code)

    def _invoke_route(
        self,
        route: AgentRoute,
        method: str,
        *args: Any,
        temperature: float | None,
        max_tokens: int | None,
        _stack: tuple[str, ...] = (),
        _count_logical: bool = True,
    ) -> Any:
        spec = self._provider_spec(route.provider)
        if _count_logical:
            self._metrics.logical_calls += 1
            self._bucket(self._provider_metrics, route.provider).logical_calls += 1
            self._bucket(self._agent_metrics, route.agent_id).logical_calls += 1
        effective_temperature, effective_max_tokens = self._effective_generation(route, method, temperature, max_tokens)
        last_error: ProviderError | None = None
        attempts = max(0, int(spec.retry_count)) + 1
        for attempt in range(attempts):
            raw: OrgLLMClient | None = None
            before: Mapping[str, Any] | None = None
            try:
                raw = self._raw_client(route)
                before = raw.stats()
                result = getattr(raw, method)(
                    *args,
                    temperature=effective_temperature,
                    max_tokens=effective_max_tokens,
                )
                after = raw.stats()
                if isinstance(result, str) and not result.strip():
                    raise LLMError("empty provider response")
                self._record_attempt(route, failed=False, retry=attempt > 0, before=before, after=after)
                if _count_logical:
                    self._bucket(self._provider_metrics, route.provider).models[route.model or "unreported"] = self._bucket(self._provider_metrics, route.provider).models.get(route.model or "unreported", 0)
                return result
            except ProviderConfigurationError as exc:
                after = raw.stats() if raw is not None else None
                self._record_attempt(route, failed=True, retry=attempt > 0, before=before, after=after)
                last_error = exc
                break
            except Exception as exc:
                after = raw.stats() if raw is not None else None
                safe_error = self._safe_call_error(route.provider, exc)
                self._record_attempt(route, failed=True, retry=attempt > 0, before=before, after=after)
                last_error = safe_error
                if attempt + 1 < attempts:
                    continue
                break
        fallback = spec.fallback_provider
        if fallback and fallback not in _stack and fallback != route.provider:
            self._metrics.fallbacks += 1
            self._bucket(self._provider_metrics, route.provider).fallbacks += 1
            self._bucket(self._agent_metrics, route.agent_id).fallbacks += 1
            fallback_spec = self._provider_spec(fallback)
            fallback_model = route.model if route.explicit_model else fallback_spec.default_model
            fallback_route = AgentRoute(
                agent_id=route.agent_id,
                provider=fallback,
                model=fallback_model,
                reasoning=route.reasoning,
                generation=route.generation,
                explicit_model=route.explicit_model,
            )
            try:
                result = self._invoke_route(
                    fallback_route,
                    method,
                    *args,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    _stack=(*_stack, route.provider),
                    _count_logical=False,
                )
                self._metrics.fallback_successes += 1
                self._bucket(self._provider_metrics, route.provider).fallback_successes += 1
                self._bucket(self._agent_metrics, route.agent_id).fallback_successes += 1
                return result
            except ProviderError as exc:
                last_error = exc
        if last_error is None:
            last_error = ProviderCallError(route.provider, "provider_call_failed")
        # Fallback calls recurse through this method.  Record only the final
        # error once at the logical-call boundary instead of duplicating the
        # same failure for every fallback frame.
        if _count_logical:
            self._last_errors.append({"provider": last_error.provider_id, "code": last_error.code})
            del self._last_errors[:-16]
        # Suppress the original SDK exception chain at the public boundary.
        raise last_error from None

    @staticmethod
    def _bucket_dict(bucket: _MetricBucket) -> dict[str, Any]:
        return {
            "calls": bucket.calls,
            "logical_calls": bucket.logical_calls,
            "failures": bucket.failures,
            "retries": bucket.retries,
            "provider_attempts": bucket.provider_attempts,
            "fallbacks": bucket.fallbacks,
            "fallback_successes": bucket.fallback_successes,
            "usage_totals": dict(bucket.usage_totals),
            "cost": round(bucket.cost, 12),
            "cost_usd": round(bucket.cost, 12),
            "estimated_cost": round(bucket.cost, 12),
            "response_model_counts": dict(bucket.models),
        }

    def _route_stats(self, route: AgentRoute) -> dict[str, Any]:
        bucket = self._bucket(self._agent_metrics, route.agent_id)
        result = self._bucket_dict(bucket)
        result.update({"provider": route.provider, "model": route.model, "agent_id": route.agent_id})
        return result

    def stats(self) -> dict[str, Any]:
        with self._lock:
            result = self._bucket_dict(self._metrics)
            result["provider_calls_made"] = result["calls"]
            result.update(
                {
                    "provider": "router",
                    "providers": {key: self._bucket_dict(value) for key, value in self._provider_metrics.items()},
                    "agents": {key: self._bucket_dict(value) for key, value in self._agent_metrics.items()},
                    "default_agent_id": self.default_agent_id,
                    "default_provider": self.default_provider,
                    "last_errors": list(self._last_errors[-16:]),
                    "secrets_redacted": True,
                }
            )
            return result

    usage_metrics = stats
    get_usage = stats
    get_metrics = stats

    @property
    def metrics(self) -> dict[str, Any]:
        return self.stats()

    def request_resource_envelope(self, max_tokens: int) -> dict[str, int]:
        route = self._selected_route()
        spec = self._provider_spec(route.provider)
        return {
            "provider_attempts": 1 + max(0, int(spec.retry_count)),
            "output_tokens_per_attempt": (
                ACCOUNTING_OUTPUT_CEILING
                if max(0, int(max_tokens)) == UNCAPPED_OUTPUT
                else max(0, int(max_tokens))
            ),
        }


AgentProviderRouter = ProviderRegistry
PerAgentProviderRouter = ProviderRegistry
ProviderRouter = ProviderRegistry
ProviderRoutingError = ProviderError
ProviderClient = AgentProviderClient


def build_provider_registry(config: Any, **kwargs: Any) -> ProviderRegistry:
    """Build a lazy registry from ``GenericConfig`` or ``OrganizationConfig``."""

    return ProviderRegistry(config, **kwargs)


create_provider_registry = build_provider_registry


__all__ = [
    "AgentProviderClient",
    "AgentProviderRouter",
    "AgentRoute",
    "AnthropicCompatibleOrgLLMClient",
    "AnthropicOrgLLMClient",
    "GenericHTTPConfiguredClient",
    "GenericHTTPProviderClient",
    "GenericHTTPOrgLLMClient",
    "LLMError",
    "MockOrgLLMClient",
    "OpenAICompatibleOrgLLMClient",
    "OpenAIOrgLLMClient",
    "OrgLLMClient",
    "PerAgentProviderRouter",
    "ProviderCallError",
    "ProviderClient",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRegistry",
    "ProviderRouter",
    "ProviderRoutingError",
    "Redactor",
    "SecretRedactor",
    "build_provider_registry",
    "create_provider_registry",
    "normalize_provider_type",
]
