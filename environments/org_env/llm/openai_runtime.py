"""Small self-contained OpenAI runtime helpers used by the OrgEnv client.

The standalone Relic-Agent distribution does not include the source project's
``society_core`` package.  Keep the provider boundary usable from an installed
wheel by carrying only the shared timeout and request-configuration helpers it
needs here.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import json
import os
import re
import signal
import threading
from time import monotonic
from typing import Any, Iterator


DEFAULT_HEADERS_ENV = "SOCIETY_CORE_OPENAI_DEFAULT_HEADERS_JSON"
DISABLE_RESPONSE_STORAGE_ENV = "SOCIETY_CORE_OPENAI_DISABLE_RESPONSE_STORAGE"
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_FORBIDDEN_CONFIGURED_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "host",
        "proxy-authorization",
    }
)


class OpenAICallDeadlineExceeded(TimeoutError):
    """Raised when one OpenAI call exceeds its total wall-clock budget."""


@contextmanager
def openai_call_watchdog(
    seconds: float,
    *,
    label: str = "OpenAI call",
) -> Iterator[None]:
    """Enforce a wall-clock deadline when the platform supports SIGALRM.

    A signal can interrupt a call on the main thread.  Worker-thread calls use
    a diagnostic timer instead; the SDK's per-phase transport timeout remains
    responsible for interrupting a blocked socket there.
    """

    if seconds <= 0:
        yield
        return

    on_main_thread = threading.current_thread() is threading.main_thread()
    if (
        not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
        or not on_main_thread
    ):
        timer = threading.Timer(
            seconds,
            lambda: print(
                f"[watchdog] {label} exceeded {seconds:.0f}s on a worker thread; "
                "SIGALRM cannot fire off-main, so this call is not being "
                "interrupted - the process may be stalled on a socket read",
                flush=True,
            ),
        )
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
        return

    def _raise_timeout(signum: int, frame: Any) -> None:
        del signum, frame
        raise OpenAICallDeadlineExceeded(
            f"{label} exceeded {seconds:.3f}s wall-clock deadline"
        )

    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started_at = monotonic()
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            elapsed = max(0.0, monotonic() - started_at)
            remaining = old_timer[0] - elapsed
            if remaining <= 0:
                if old_timer[1] > 0:
                    missed_intervals = int((-remaining) // old_timer[1]) + 1
                    remaining += missed_intervals * old_timer[1]
                else:
                    remaining = 1e-6
            signal.setitimer(signal.ITIMER_REAL, remaining, old_timer[1])


def validate_openai_default_headers(payload: Mapping[str, Any]) -> dict[str, str]:
    """Validate compatibility headers without persisting credentials."""

    if not isinstance(payload, Mapping):
        raise ValueError("openai_default_headers_must_be_object")
    headers: dict[str, str] = {}
    for raw_name, raw_value in payload.items():
        name = str(raw_name).strip()
        if not name or not _HEADER_NAME.fullmatch(name):
            raise ValueError("invalid_openai_default_header_name")
        if name.lower() in _FORBIDDEN_CONFIGURED_HEADERS:
            raise ValueError(f"forbidden_openai_default_header:{name.lower()}")
        if not isinstance(raw_value, str):
            raise ValueError(
                f"openai_default_header_value_must_be_string:{name.lower()}"
            )
        value = raw_value.strip()
        if (
            not value
            or len(value) > 4_096
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F for character in value
            )
        ):
            raise ValueError(f"invalid_openai_default_header_value:{name.lower()}")
        headers[name] = value
    return dict(sorted(headers.items(), key=lambda item: item[0].lower()))


def configured_openai_default_headers() -> dict[str, str]:
    """Return validated compatibility headers from a JSON environment value."""

    raw = os.environ.get(DEFAULT_HEADERS_ENV, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_openai_default_headers_json") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("openai_default_headers_must_be_object")
    return validate_openai_default_headers(payload)


def openai_response_storage_disabled() -> bool:
    """Default to private, non-persistent Responses API calls."""

    raw = os.environ.get(DISABLE_RESPONSE_STORAGE_ENV, "true")
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("invalid_openai_disable_response_storage")


__all__ = [
    "OpenAICallDeadlineExceeded",
    "configured_openai_default_headers",
    "openai_call_watchdog",
    "openai_response_storage_disabled",
    "validate_openai_default_headers",
]
