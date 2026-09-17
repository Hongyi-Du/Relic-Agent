"""Offline coverage for the generic per-agent provider router."""

from __future__ import annotations

import json
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from typing import Any

import pytest

import relic_agent.runtime.providers as provider_module
import environments.org_env.llm.client as shared_client
from relic_agent.runtime.providers import (
    OrgLLMClient,
    ProviderCallError,
    ProviderConfigurationError,
    ProviderRegistry,
)


def test_same_model_routes_keep_agent_specific_generation_settings(monkeypatch):
    requests = []

    def respond(_url, payload, **_kwargs):
        requests.append(payload)
        return {"text": "done"}

    monkeypatch.setattr(provider_module._HTTPAdapter, "request", respond)
    registry = ProviderRegistry(
        {"local": {"type": "generic_http", "default_model": "same-model",
                   "parameters": {"endpoint": "https://local.invalid"}}},
        [{"id": "first", "provider": "local", "generation": {"top_p": 0.2}},
         {"id": "second", "provider": "local", "generation": {"top_p": 0.9}}],
    )
    registry.client_for_agent("first").generate_text("system", "task")
    registry.client_for_agent("second").generate_text("system", "task")
    assert [request["top_p"] for request in requests] == [0.2, 0.9]


@pytest.mark.unit
def test_mock_routes_use_each_agent_model_and_do_not_read_environment() -> None:
    class NoEnvironmentReads:
        def get(self, name: str, default: Any = None) -> Any:
            raise AssertionError(f"environment read for {name}")

    registry = ProviderRegistry(
        {
            "writer": {"type": "deterministic", "default_model": "writer-default"},
            "reviewer": {
                "type": "mock",
                "default_model": "review-default",
                "parameters": {"script": [{"answer": "approved"}]},
            },
        },
        [
            {"id": "author", "provider": "writer", "model": "writer-v2"},
            {"id": "critic", "provider": "reviewer"},
        ],
        environment=NoEnvironmentReads(),
    )

    assert registry.resolve("author").model == "writer-v2"
    assert registry.resolve("critic").model == "review-default"
    assert registry.client_for_agent("author").generate_text("system", "hello") == (
        "(mock surface text)"
    )
    assert registry.client_for_agent("critic").generate_json(
        "system", "review", {"type": "object", "required": ["answer"]}
    ) == {"answer": "approved"}
    assert registry.stats()["agents"]["author"]["response_model_counts"] == {"writer-v2": 1}


@pytest.mark.unit
def test_mock_provider_does_not_require_declared_live_credentials() -> None:
    registry = ProviderRegistry(
        {
            "mock": {
                "type": "mock",
                "default_model": "offline",
                "base_url_env": "UNUSED_ENDPOINT",
                "api_key_env": "UNUSED_TOKEN",
            }
        },
        [{"id": "agent", "provider": "mock"}],
        environment={},
    )
    assert registry.client_for_agent("agent").generate_text("s", "u") == "(mock surface text)"


@pytest.mark.unit
def test_credentials_and_model_are_validated_only_when_route_is_called() -> None:
    registry = ProviderRegistry(
        {
            "live": {
                "type": "generic_http",
                "base_url_env": "LIVE_ENDPOINT",
                "api_key_env": "LIVE_TOKEN",
                # Deliberately no model: selecting this route must remain lazy.
            }
        },
        [{"id": "agent", "provider": "live"}],
        environment={},
    )

    client = registry.client_for_agent("agent")
    assert client.model is None
    with pytest.raises(ProviderConfigurationError, match="missing_model"):
        client.generate_text("system", "hello")

    configured = ProviderRegistry(
        {
            "live": {
                "type": "generic_http",
                "base_url_env": "LIVE_ENDPOINT",
                "api_key_env": "LIVE_TOKEN",
                "default_model": "model-a",
            }
        },
        [{"id": "agent", "provider": "live"}],
        environment={},
    )
    configured_client = configured.client_for_agent("agent")
    with pytest.raises(ProviderConfigurationError, match="missing_base_url_environment"):
        configured_client.generate_text("system", "hello")


@pytest.mark.unit
def test_inline_api_keys_and_authorization_headers_are_rejected_at_call_boundary() -> None:
    registry = ProviderRegistry(
        {
            "live": {
                "type": "generic_http",
                "default_model": "model-a",
                "parameters": {
                    "endpoint": "https://gateway.invalid/generate",
                    "headers": {"Authorization": "Bearer should-not-be-inline"},
                },
            }
        },
        [{"id": "agent", "provider": "live"}],
    )
    with pytest.raises(ProviderConfigurationError, match="inline_credentials_forbidden"):
        registry.client_for_agent("agent").generate_text("system", "hello")


@pytest.mark.unit
def test_nested_inline_credentials_are_rejected_at_call_boundary() -> None:
    registry = ProviderRegistry(
        {
            "live": {
                "type": "generic_http",
                "default_model": "model-a",
                "parameters": {
                    "gateway": {"auth": {"authorization": "Bearer should-not-be-inline"}},
                },
            }
        },
        [{"id": "agent", "provider": "live"}],
    )
    with pytest.raises(ProviderConfigurationError, match="inline_credentials_forbidden"):
        registry.client_for_agent("agent").generate_text("system", "hello")


@pytest.mark.unit
def test_generic_http_records_usage_cost_and_redacts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    class Response:
        status = 200

        def getcode(self) -> int:
            return self.status

        def read(self, limit: int = -1) -> bytes:
            return json.dumps(
                {
                    "content": '{"answer":"yes"}',
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                }
            ).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float) -> Response:
        observed["url"] = request.full_url
        observed["headers"] = dict(request.header_items())
        observed["payload"] = json.loads(request.data.decode())
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)
    registry = ProviderRegistry(
        {
            "gateway": {
                "type": "generic_http",
                "parameters": {
                    "endpoint": "https://gateway.invalid/generate",
                    "pricing": {"input_cost_per_1k": 2, "output_cost_per_1k": 4},
                },
                "api_key_env": "GATEWAY_TOKEN",
                "default_model": "local-model",
                "timeout_seconds": 7,
            }
        },
        [{"id": "agent", "provider": "gateway"}],
        environment={"GATEWAY_TOKEN": "secret-value"},
    )

    result = registry.client_for_agent("agent").generate_json(
        "system", "review", {"type": "object", "required": ["answer"]}
    )
    assert result == {"answer": "yes"}
    assert observed["payload"]["model"] == "local-model"
    assert observed["headers"]["Authorization"] == "Bearer secret-value"
    assert observed["timeout"] == 7
    stats = registry.stats()
    assert stats["usage_totals"] == {
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
        "cached_prompt_tokens": 0,
    }
    assert stats["cost"] == pytest.approx(0.022)
    assert "secret-value" not in json.dumps(stats)
    assert registry.redact("token=secret-value") == "token=<redacted>"


@pytest.mark.unit
def test_generic_http_accepts_a_direct_json_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 200

        def getcode(self) -> int:
            return self.status

        def read(self, limit: int = -1) -> bytes:
            return json.dumps({"answer": "yes"}).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(provider_module, "urlopen", lambda *_args, **_kwargs: Response())
    registry = ProviderRegistry(
        {
            "gateway": {
                "type": "generic_http",
                "parameters": {"endpoint": "https://gateway.invalid/generate"},
                "default_model": "local-model",
            }
        },
        [{"id": "agent", "provider": "gateway"}],
    )
    assert registry.client_for_agent("agent").generate_json(
        "system", "review", {"type": "object", "required": ["answer"]}
    ) == {"answer": "yes"}


@pytest.mark.unit
def test_http_status_code_survives_the_safe_error_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 503

        def getcode(self) -> int:
            return self.status

        def read(self, limit: int = -1) -> bytes:
            return b'{"error":"upstream secret-value"}'

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(provider_module, "urlopen", lambda *_args, **_kwargs: Response())
    registry = ProviderRegistry(
        {
            "gateway": {
                "type": "generic_http",
                "parameters": {"endpoint": "https://gateway.invalid/generate"},
                "default_model": "local-model",
            }
        },
        [{"id": "agent", "provider": "gateway"}],
    )
    with pytest.raises(ProviderCallError, match="http_status_503") as raised:
        registry.client_for_agent("agent").generate_text("system", "hello")
    assert "secret-value" not in str(raised.value)
    assert registry.stats()["last_errors"][-1] == {
        "provider": "gateway",
        "code": "http_status_503",
    }


@pytest.mark.unit
def test_provider_errors_do_not_include_raw_transport_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("upstream response leaked secret-value")

    monkeypatch.setattr(provider_module, "urlopen", fail_urlopen)
    registry = ProviderRegistry(
        {
            "gateway": {
                "type": "generic_http",
                "parameters": {"endpoint": "https://gateway.invalid/generate"},
                "default_model": "model-a",
            }
        },
        [{"id": "agent", "provider": "gateway"}],
    )
    with pytest.raises(ProviderCallError) as raised:
        registry.client_for_agent("agent").generate_text("s", "u")
    assert "secret-value" not in str(raised.value)


@pytest.mark.unit
def test_anthropic_compatible_adapter_uses_messages_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    class Response:
        status = 200

        def getcode(self) -> int:
            return self.status

        def read(self, limit: int = -1) -> bytes:
            return json.dumps(
                {"content": [{"type": "text", "text": "hello"}], "usage": {"input_tokens": 2, "output_tokens": 4}}
            ).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float) -> Response:
        observed["path"] = request.full_url
        observed["headers"] = dict(request.header_items())
        observed["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)
    registry = ProviderRegistry(
        {
            "claude": {
                "type": "anthropic_compatible",
                "base_url_env": "ANTHROPIC_ENDPOINT",
                "api_key_env": "ANTHROPIC_TOKEN",
                "default_model": "claude-test",
                "parameters": {"reasoning": {"budget_tokens": 256}},
            }
        },
        [{"id": "reviewer", "provider": "claude", "generation": {"temperature": 0.1}}],
        environment={"ANTHROPIC_ENDPOINT": "https://anthropic.invalid", "ANTHROPIC_TOKEN": "secret"},
    )

    assert registry.client_for_agent("reviewer").generate_text("system", "hello") == "hello"
    assert observed["path"] == "https://anthropic.invalid/v1/messages"
    assert observed["payload"]["model"] == "claude-test"
    assert observed["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert observed["payload"]["thinking"] == {"type": "enabled", "budget_tokens": 256}
    assert observed["headers"]["X-api-key"] == "secret"
    assert registry.stats()["usage_totals"]["prompt_tokens"] == 2


@pytest.mark.unit
def test_anthropic_explicit_temperature_overrides_route_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class Response:
        status = 200

        def getcode(self) -> int:
            return self.status

        def read(self, limit: int = -1) -> bytes:
            return json.dumps({"content": [{"type": "text", "text": "hello"}]}).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float) -> Response:
        observed["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)
    registry = ProviderRegistry(
        {
            "claude": {
                "type": "anthropic_compatible",
                "parameters": {"endpoint": "https://anthropic.invalid", "generation": {"temperature": 0.1}},
                "default_model": "claude-test",
            }
        },
        [{"id": "reviewer", "provider": "claude"}],
    )
    assert registry.client_for_agent("reviewer").generate_text(
        "system", "hello", temperature=0.8
    ) == "hello"
    assert observed["payload"]["temperature"] == 0.8


@pytest.mark.unit
def test_failed_provider_retries_then_uses_configured_fallback() -> None:
    registry = ProviderRegistry(
        {
            "primary": {
                "type": "mock",
                "default_model": "primary-model",
                "retry_count": 1,
                "fallback_provider": "backup",
                "parameters": {"fail": True},
            },
            "backup": {
                "type": "mock",
                "default_model": "backup-model",
                "parameters": {"script": [{"answer": "backup"}]},
            },
        },
        [{"id": "agent", "provider": "primary"}],
    )

    result = registry.client_for_agent("agent").generate_json(
        "system", "hello", {"type": "object", "required": ["answer"]}
    )
    assert result == {"answer": "backup"}
    stats = registry.stats()
    assert stats["retries"] == 1
    assert stats["fallbacks"] == 1
    assert stats["fallback_successes"] == 1
    assert stats["providers"]["primary"]["calls"] == 2
    assert stats["providers"]["backup"]["calls"] == 1


@pytest.mark.unit
def test_openai_adapter_forwards_model_reasoning_and_generation_to_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeOpenAI(OrgLLMClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            seen["init"] = kwargs

        def generate_text(self, *_args: Any, **kwargs: Any) -> str:
            seen["text"] = kwargs
            self.calls += 1
            return "openai result"

        def generate_json(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
            seen["json"] = kwargs
            self.calls += 1
            return {"answer": "ok"}

    monkeypatch.setattr(provider_module, "OpenAIOrgLLMClient", FakeOpenAI)
    registry = ProviderRegistry(
        {
            "openai": {
                "type": "openai_compatible",
                "base_url_env": "OPENAI_ENDPOINT",
                "api_key_env": "OPENAI_TOKEN",
                "default_model": "gpt-test",
                "parameters": {"wire_api": "chat_completions"},
            }
        },
        [{"id": "author", "provider": "openai", "reasoning": {"effort": "high"}, "generation": {"temperature": 0.4}}],
        environment={"OPENAI_ENDPOINT": "https://openai.invalid/v1", "OPENAI_TOKEN": "secret"},
    )

    assert registry.client_for_agent("author").generate_text("s", "u") == "openai result"
    assert seen["init"]["model"] == "gpt-test"
    assert seen["init"]["base_url"] == "https://openai.invalid/v1"
    assert seen["init"]["reasoning_effort"] == "high"
    assert seen["text"] == {"temperature": 0.4, "max_tokens": 800}
    assert "openai.invalid" not in json.dumps(registry.stats())


@pytest.mark.integration
def test_local_http_server_exercises_all_live_provider_wire_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise only loopback transports; this test never contacts a paid API."""

    requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            requests.append((self.path, payload, dict(self.headers.items())))
            if self.path.endswith("/messages"):
                body = {
                    "content": [{"type": "text", "text": "anthropic-local"}],
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                }
            elif self.path.endswith("/chat/completions"):
                body = {
                    "id": "local-response",
                    "model": payload.get("model"),
                    "choices": [{"message": {"content": "openai-local"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            else:
                body = {
                    "text": "generic-local",
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args: Any) -> None:
            return None

    try:
        server = HTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("sandbox disallows loopback sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # The vendored shared client normally uses the optional source
        # ``society_core`` watchdog.  Bypass that process watchdog here while
        # still exercising the installed OpenAI SDK against loopback.
        monkeypatch.setattr(shared_client, "_openai_call_watchdog", lambda *_args, **_kwargs: nullcontext())
        port = server.server_port
        registry = ProviderRegistry(
            {
                "openai": {
                    "type": "openai_compatible",
                    "base_url_env": "OPENAI_LOCAL_ENDPOINT",
                    "api_key_env": "OPENAI_LOCAL_TOKEN",
                    "default_model": "gpt-local",
                },
                "anthropic": {
                    "type": "anthropic_compatible",
                    "base_url_env": "ANTHROPIC_LOCAL_ENDPOINT",
                    "api_key_env": "ANTHROPIC_LOCAL_TOKEN",
                    "default_model": "claude-local",
                },
                "generic": {
                    "type": "generic_http",
                    "base_url_env": "GENERIC_LOCAL_ENDPOINT",
                    "api_key_env": "GENERIC_LOCAL_TOKEN",
                    "default_model": "http-local",
                },
            },
            [
                {"id": "openai-agent", "provider": "openai"},
                {"id": "anthropic-agent", "provider": "anthropic"},
                {"id": "generic-agent", "provider": "generic"},
            ],
            environment={
                "OPENAI_LOCAL_ENDPOINT": f"http://127.0.0.1:{port}/v1",
                "OPENAI_LOCAL_TOKEN": "local-openai-token",
                "ANTHROPIC_LOCAL_ENDPOINT": f"http://127.0.0.1:{port}",
                "ANTHROPIC_LOCAL_TOKEN": "local-anthropic-token",
                "GENERIC_LOCAL_ENDPOINT": f"http://127.0.0.1:{port}/generic",
                "GENERIC_LOCAL_TOKEN": "local-generic-token",
            },
        )
        assert registry.client_for_agent("openai-agent").generate_text("s", "u") == "openai-local"
        assert registry.client_for_agent("anthropic-agent").generate_text("s", "u") == "anthropic-local"
        assert registry.client_for_agent("generic-agent").generate_text("s", "u") == "generic-local"
        assert {path for path, _payload, _headers in requests} == {
            "/v1/chat/completions",
            "/v1/messages",
            "/generic",
        }
        assert registry.stats()["usage_totals"]["total_tokens"] == 9
        assert all("local-" not in json.dumps(registry.stats()) for _path, _payload, _headers in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
