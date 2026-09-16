import copy
import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.core.hashing import canonical_sha256
from relic_agent.inspector import InspectorError, create_inspector_server
from relic_agent.runtime import OrganizationRuntime


ROOT = Path(__file__).resolve().parents[1]


def _generated_trace(tmp_path: Path) -> tuple[dict, Path]:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml")).run(
        output_root=tmp_path,
        run_id="inspector-test",
    )
    return json.loads(result.trace_path.read_text(encoding="utf-8")), result.trace_path


def _resign(payload: dict) -> dict:
    payload.pop("trace_sha256", None)
    payload["trace_sha256"] = canonical_sha256(payload)
    return payload


@contextmanager
def _running_server(trace_path: Path, *, mode: str = "replay"):
    server = create_inspector_server(trace_path=trace_path, port=0, mode=mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    server,
    path: str,
    *,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    connection.request(method, path)
    response = connection.getresponse()
    body = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, body


@pytest.mark.release
def test_inspector_serves_only_allowlisted_routes_with_security_headers(tmp_path: Path) -> None:
    trace, trace_path = _generated_trace(tmp_path)
    with _running_server(trace_path) as server:
        status, headers, body = _request(server, "/api/health")
        assert status == 200
        health = json.loads(body)
        assert health["status"] == "ready"
        assert health["run_id"] == trace["run_id"]

        status, headers, body = _request(server, "/api/trace")
        assert status == 200
        assert json.loads(body)["trace_sha256"] == trace["trace_sha256"]
        assert "base-uri 'none'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["cache-control"] == "no-store"
        assert headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in headers
        assert headers["server"].strip() == "RelicInspector"

        for route, content_type in (
            ("/", "text/html"),
            ("/app.css", "text/css"),
            ("/app.js", "text/javascript"),
        ):
            status, route_headers, route_body = _request(server, route)
            assert status == 200
            assert route_headers["content-type"].startswith(content_type)
            assert route_body

        assert _request(server, "/../pyproject.toml")[0] == 404
        assert _request(server, "/api/unknown")[0] == 404
        status, method_headers, _ = _request(server, "/api/trace", method="POST")
        assert status == 405
        assert method_headers["allow"] == "GET, HEAD"
        status, _, head_body = _request(server, "/app.js", method="HEAD")
        assert status == 200
        assert head_body == b""


@pytest.mark.replay
def test_live_inspector_keeps_last_verified_append_only_trace(tmp_path: Path) -> None:
    full, trace_path = _generated_trace(tmp_path)
    prefix = copy.deepcopy(full)
    prefix["frames"] = prefix["frames"][:3]
    trace_path.write_text(json.dumps(_resign(prefix)), encoding="utf-8")

    with _running_server(trace_path, mode="live") as server:
        initial = json.loads(_request(server, "/api/trace")[2])
        assert len(initial["frames"]) == 3

        trace_path.write_text("{", encoding="utf-8")
        stale = json.loads(_request(server, "/api/trace")[2])
        health = json.loads(_request(server, "/api/health")[2])
        assert stale["trace_sha256"] == initial["trace_sha256"]
        assert health["status"] == "degraded"

        replacement = trace_path.with_suffix(".replacement")
        replacement.write_text(json.dumps(full), encoding="utf-8")
        replacement.replace(trace_path)
        refreshed = json.loads(_request(server, "/api/trace")[2])
        assert len(refreshed["frames"]) == len(full["frames"])
        assert json.loads(_request(server, "/api/health")[2])["status"] == "ready"

        rewritten = copy.deepcopy(full)
        rewritten["frames"][1]["organization"]["name"] = "rewritten history"
        trace_path.write_text(json.dumps(_resign(rewritten)), encoding="utf-8")
        retained = json.loads(_request(server, "/api/trace")[2])
        assert retained["trace_sha256"] == full["trace_sha256"]
        assert json.loads(_request(server, "/api/health")[2])["status"] == "degraded"


@pytest.mark.integration
def test_live_inspector_observes_an_active_runtime(tmp_path: Path) -> None:
    runtime = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml"))
    paused = threading.Event()
    resume = threading.Event()
    original_step = runtime._step_agent

    def gated_step(agent) -> None:
        if runtime.state.tick == 2 and not paused.is_set():
            paused.set()
            assert resume.wait(timeout=5)
        original_step(agent)

    runtime._step_agent = gated_step
    run_thread = threading.Thread(
        target=lambda: runtime.run(output_root=tmp_path, ticks=3, run_id="active-live"),
        daemon=True,
    )
    run_thread.start()
    assert paused.wait(timeout=5)
    trace_path = tmp_path / "active-live" / "trace.json"

    with _running_server(trace_path, mode="live") as server:
        during = json.loads(_request(server, "/api/trace")[2])
        assert during["frames"][-1]["tick"] == 1

        resume.set()
        run_thread.join(timeout=5)
        assert not run_thread.is_alive()
        completed = json.loads(_request(server, "/api/trace")[2])
        assert completed["frames"][-1]["tick"] == 3


@pytest.mark.release
def test_inspector_validates_before_binding_and_requires_remote_opt_in(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        create_inspector_server(trace_path=invalid, port=0)

    _, trace_path = _generated_trace(tmp_path)
    with pytest.raises(InspectorError, match="allow-remote"):
        create_inspector_server(trace_path=trace_path, host="0.0.0.0", port=0)

    server = create_inspector_server(
        trace_path=trace_path,
        host="0.0.0.0",
        port=0,
        allow_remote=True,
    )
    server.server_close()
