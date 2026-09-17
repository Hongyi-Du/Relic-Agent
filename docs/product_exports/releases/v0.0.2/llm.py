"""LanternScout LLM core — the agent calls an LLM to synthesize findings from the
retrieved sources. Uses OpenAI when an API key is present (LANTERN_LLM_KEY or OPENAI_API_KEY);
otherwise returns "" so callers fall back to a deterministic offline synthesis (so the tool, and
the smoke test, always run). Self-contained (stdlib urllib only, no extra deps)."""
import json
import os
import urllib.request


def _key():
    return os.environ.get("LANTERN_LLM_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def available():
    return bool(_key())


def call_llm(prompt, system="You are LanternScout, a careful research assistant.", max_tokens=500):
    """Return the model's text, or "" when offline / on any error (never raises)."""
    key = _key()
    if not key:
        return ""
    base = os.environ.get("LANTERN_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LANTERN_LLM_MODEL", "gpt-4o-mini")
    body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""
