"""OrgEnv Live Inspector server (frontend spec §9).

A small FastAPI app that serves the two-tab live debugger at ``/org/inspector``
and feeds it JSON from ``/api/org/lived/*`` + sim controls at ``/api/org/sim/*``
+ object endpoints at ``/api/org/*``. All route LOGIC lives in plain functions
(``api_*``) operating on a module-global :class:`OrgInspectorSession`, so they're
unit-testable without FastAPI installed; ``build_app()`` imports FastAPI lazily
and wires thin route wrappers.

Run:  PYTHONPATH="." python -m environments.org_env.backend.main
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from environments.org_env.runtime_adapter.live import OrgInspectorSession

BASE_DIR = Path(__file__).resolve().parents[3]
TEMPLATE = BASE_DIR / "environments" / "org_env" / "frontend" / "templates" / "org_inspector.html"
# React + Vite + React Flow build output (served at /org/inspector; assets under /org/app)
APP_DIST = BASE_DIR / "environments" / "org_env" / "frontend" / "app" / "dist"
REPLAY_DIR = BASE_DIR / "docs" / "replays"
CHECKPOINT_DIR = BASE_DIR / "log" / "checkpoints"   # gitignored; faithful world snapshots

# module-global running session (the inspector drives ONE world)
SESSION = OrgInspectorSession(seed=42)


# --------------------------------------------------------------------------- #
# Route logic (plain, testable) — every function returns a JSON-able dict/list.
# --------------------------------------------------------------------------- #
def api_full() -> Dict[str, Any]:
    return SESSION.full()


def api_frames(since: int = -1) -> Dict[str, Any]:
    return SESSION.frames_since(since)


def api_state() -> Dict[str, Any]:
    return SESSION.state()


def api_sim_step(n: int = 1) -> Dict[str, Any]:
    return SESSION.step(n)


def api_sim_run_ticks(n: int = 24) -> Dict[str, Any]:
    return SESSION.run_ticks(n)


def api_sim_pause() -> Dict[str, Any]:
    return SESSION.pause()


def api_sim_reset(seed: Optional[int] = None) -> Dict[str, Any]:
    return SESSION.reset(seed=seed)


def api_agents() -> Dict[str, Any]:
    f = SESSION.full()
    return {"agents": f.get("agents", {})}


def api_agent(agent_id: str) -> Dict[str, Any]:
    f = SESSION.full()
    a = (f.get("agents") or {}).get(agent_id)
    if a is None:
        return {"error": f"agent '{agent_id}' not found", "available": list(f.get("agents", {}).keys())}
    return {"agent": a, "persona_graph": (f["graphs"]["persona"] or {}).get(agent_id, {}),
            "tick": f["tick"]}


def _internal_collection(f: Dict[str, Any], type_: str) -> List[Any]:
    internal, external = f.get("internal", {}), f.get("external", {})
    repo = internal.get("repo", {})
    table = {
        "task": internal.get("tasks", []), "doc": internal.get("docs", []),
        "file": internal.get("files", []), "message": internal.get("messages", []),
        "channel": internal.get("channels", []), "meeting": internal.get("meetings", []),
        "branch": repo.get("branches", []), "commit": repo.get("commits", []),
        "pr": repo.get("pull_requests", []), "experiment": internal.get("experiments", []),
        "result": internal.get("results", []), "sandbox_job": internal.get("sandbox", {}).get("jobs", []),
        "protocol": internal.get("protocols", []), "commitment": internal.get("commitments", []),
        "dispute": internal.get("disputes", []), "request": internal.get("requests", []),
        "cost_event": internal.get("cost_events", []), "ticket": internal.get("tickets", []),
        "artifact": internal.get("artifacts", []), "search": internal.get("searches", []),
        "external_profile": external.get("profiles", []), "external_post": external.get("posts", []),
        "signal": external.get("signals", []), "offer": external.get("offers", []),
    }
    return table.get(type_, [])


def api_objects(type_: str) -> Dict[str, Any]:
    return {"type": type_, "objects": _internal_collection(SESSION.full(), type_)}


def api_object(object_id: str) -> Dict[str, Any]:
    f = SESSION.full()
    for type_ in ("task", "doc", "file", "message", "meeting", "branch", "commit", "pr",
                  "experiment", "result", "protocol", "commitment", "dispute", "request",
                  "cost_event", "ticket", "artifact", "external_profile", "external_post"):
        for obj in _internal_collection(f, type_):
            oid = (obj.get("post_id") or obj.get("external_agent_id") or obj.get("message_id")
                   or obj.get("task_id") or obj.get("doc_id") or obj.get("object_id")
                   or obj.get("pr_id") or obj.get("commit_id") or obj.get("branch_id")
                   or obj.get("result_id") or obj.get("protocol_id") or obj.get("meeting_id")
                   or obj.get("commitment_id") or obj.get("dispute_id") or obj.get("request_id")
                   or obj.get("experiment_id") or obj.get("cost_event_id") or obj.get("ticket_id")
                   or obj.get("artifact_id"))
            if oid == object_id:
                edges = [e for e in f["graphs"]["event"]["edges"]
                         if e["src"] == object_id or e["dst"] == object_id]
                return {"type": type_, "object": obj, "linked_edges": edges}
    return {"error": f"object '{object_id}' not found"}


def api_section(section: str) -> Dict[str, Any]:
    f = SESSION.full()
    if section in ("messages", "channels", "meetings", "experiments", "protocols"):
        return {section: f["internal"].get(section, [])}
    if section == "repo":
        return {"repo": f["internal"].get("repo", {})}
    if section == "sandbox":
        return {"sandbox": f["internal"].get("sandbox", {}), "results": f["internal"].get("results", [])}
    if section == "external":
        return f["external"]
    if section == "event_graph":
        return f["graphs"]["event"]
    return {"error": f"unknown section '{section}'"}


def api_list_replays() -> Dict[str, Any]:
    out = []
    if REPLAY_DIR.is_dir():
        for p in sorted(REPLAY_DIR.glob("org_*.json")):
            meta = {}
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    meta = (json.load(fh) or {}).get("meta", {})
            except Exception:
                meta = {}
            out.append({"name": p.stem, "file": p.name, "meta": meta})
    return {"replays": out}


def api_get_replay(name: str) -> Dict[str, Any]:
    safe = Path(name).name
    p = REPLAY_DIR / f"{safe}.json"
    if not p.is_file():
        return {"error": f"replay '{safe}' not found"}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def api_export_replay(name: str = "org_run") -> Dict[str, Any]:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path = REPLAY_DIR / f"{name}.json"
    SESSION.save_replay(str(path), name=name)
    return {"saved": str(path), "name": name, "ticks": len(SESSION.buffer.frames)}


def api_checkpoint_save(name: str = "") -> Dict[str, Any]:
    """Save the full world at the CURRENT tick (resumable). Default name embeds the tick."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    safe = Path(name or f"ckpt_t{int(SESSION.world.world_tick)}").name
    if not safe.endswith(".pkl"):
        safe += ".pkl"
    info = SESSION.save_checkpoint(str(CHECKPOINT_DIR / safe), meta={"source": "inspector"})
    return {"saved": info["path"], "name": safe, "tick": info["tick"], "size_mb": info["size_mb"]}


def api_checkpoint_list() -> Dict[str, Any]:
    """Cheap listing (no unpickle): name + tick (from filename) + size + mtime."""
    import datetime as _dt
    import re
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(CHECKPOINT_DIR.glob("*.pkl")):
        m = re.search(r"t(\d+)", p.stem)
        out.append({"name": p.name, "tick": int(m.group(1)) if m else None,
                    "size_mb": round(p.stat().st_size / 1e6, 2),
                    "modified": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return {"checkpoints": out}


def api_checkpoint_load(name: str) -> Dict[str, Any]:
    """Resume the live session from a saved checkpoint (by name in CHECKPOINT_DIR or a path)."""
    base = Path(name).name
    candidates = [CHECKPOINT_DIR / base]
    if not base.endswith(".pkl"):
        candidates.append(CHECKPOINT_DIR / (base + ".pkl"))
    candidates.append(Path(name))
    for p in candidates:
        if p.is_file():
            return SESSION.load_checkpoint(str(p))
    return {"error": f"checkpoint '{name}' not found"}


def _product_profile(world) -> Dict[str, Any]:
    """The product's CURRENT capabilities + active limitations, derived from the live
    artifacts + known gaps — so a human trial reflects the actual emergent product."""
    arts = getattr(world, "product_artifacts", {}) or {}
    caps = sorted({c for a in arts.values() for c in (getattr(a, "capabilities", []) or [])})
    gaps = [getattr(g, "description", "") for g in (getattr(world, "known_gaps", {}) or {}).values()
            if getattr(g, "status", "active") in ("active", "regressed")]
    prod = getattr(world, "product", None)
    return {"name": getattr(prod, "name", "the product") if prod else "the product",
            "stage": getattr(prod, "stage", "prototype") if prod else "prototype",
            "capabilities": caps, "known_limitations": [g for g in gaps if g][:12],
            "tick": int(getattr(world, "world_tick", 0))}


def api_product_try(query: str) -> Dict[str, Any]:
    """Human product terminal: run the company's product on a human query. The output is
    generated to reflect EXACTLY the product's current capabilities + limitations, so a
    human can experience/evaluate the emergent product honestly (frontend spec — product
    trial)."""
    w = SESSION.world
    profile = _product_profile(w)
    if not (query or "").strip():
        return {"error": "empty query", "profile": profile}
    client = getattr(w, "llm_client", None)
    if client is None:
        rep = (f"[{profile['name']} · {profile['stage']} · t{profile['tick']}] (no LLM client)\n\n"
               f"Query: {query}\n\nCapabilities: {', '.join(profile['capabilities']) or 'minimal'}\n"
               f"Known limitations: {'; '.join(profile['known_limitations']) or 'none recorded'}")
        return {"report": rep, "profile": profile, "caveats": profile["known_limitations"][:6],
                "confidence": 0.3, "llm": False}
    system = (f"You ARE the company's product — a research agent named {profile['name']} at stage "
              f"'{profile['stage']}'. Produce ONLY what a product with EXACTLY the listed capabilities "
              f"and limitations could produce, and honestly reflect the limitations (e.g. if claim-"
              f"evidence is not enforced, some claims may be unsupported; if eval metrics are stubs, say "
              f"results are not validated). Do not invent capabilities you don't have. Return JSON only.")
    user = ("CAPABILITIES:\n- " + ("\n- ".join(profile["capabilities"]) or "(barely functional)")
            + "\n\nKNOWN LIMITATIONS:\n- " + ("\n- ".join(profile["known_limitations"]) or "(none recorded)")
            + f"\n\nUSER REQUEST:\n{query}\n\n"
            + "Return {\"report\": str, \"caveats\": [str], \"confidence\": number}.")
    try:
        data = client.generate_json(system, user,
                                    {"report": "str", "caveats": "list", "confidence": "number"})
    except Exception as e:  # pragma: no cover
        data = {"report": f"(product run failed: {e})", "caveats": [], "confidence": 0.0}
    data = data if isinstance(data, dict) else {"report": str(data), "caveats": [], "confidence": 0.0}
    data["profile"] = profile
    data["llm"] = True
    return data


def api_product_feedback(rating: float = 0.0, comment: str = "", query: str = "") -> Dict[str, Any]:
    """Record a human's product evaluation as an external customer signal/ticket on the live
    world — so human trials feed the market loop (customer tickets advance the customers
    milestone / funding)."""
    from environments.org_env.backend.entities.economy import CustomerTicket
    w = SESSION.world
    tick = int(getattr(w, "world_tick", 0))
    tickets = w.__dict__.setdefault("tickets", {})
    tid = f"ticket_human_{len(tickets) + 1}"
    sev = "major" if rating and float(rating) <= 2 else ("minor" if rating and float(rating) >= 4 else "moderate")
    t = CustomerTicket(ticket_id=tid, customer_type="human_evaluator",
                       complaint_or_request=(comment or query or "human product evaluation"),
                       severity=sev, topic="product_evaluation", status="open")
    t.__dict__.update({"rating": float(rating or 0), "query": query, "comment": comment, "created_tick": tick})
    tickets[tid] = t
    if hasattr(w, "events"):
        w.events.append({"type": "external_signal_event", "subtype": "human_product_feedback",
                         "ticket_id": tid, "rating": float(rating or 0), "tick": tick})
    return {"ok": True, "ticket_id": tid, "rating": float(rating or 0), "tick": tick,
            "total_human_tickets": sum(1 for x in tickets.values()
                                       if getattr(x, "customer_type", "") == "human_evaluator")}


def api_product_export() -> Dict[str, Any]:
    """Materialize the in-world product repo to a real directory + run its smoke test
    (Product Materialization Layer). Returns the export manifest + smoke metrics."""
    from environments.org_env.product.materialize import export_and_smoke
    w = SESSION.world
    dest = str(BASE_DIR / "docs" / "product_exports" / "lanternscout")
    res = export_and_smoke(w, dest)
    res["tick"] = int(getattr(w, "world_tick", 0))
    return res


def inspector_html() -> str:
    """Serve the React/Vite/React Flow build if present; else the legacy vanilla template."""
    idx = APP_DIST / "index.html"
    if idx.is_file():
        return idx.read_text(encoding="utf-8")
    if TEMPLATE.is_file():
        return TEMPLATE.read_text(encoding="utf-8")
    return "<h1>OrgEnv inspector not built — run `npm install && npm run build` in environments/org_env/frontend/app</h1>"


def legacy_inspector_html() -> str:
    if TEMPLATE.is_file():
        return TEMPLATE.read_text(encoding="utf-8")
    return "<h1>org_inspector.html not found</h1>"


# --------------------------------------------------------------------------- #
# FastAPI app (lazy import so this module loads without fastapi for tests)
# --------------------------------------------------------------------------- #
def build_app():
    from fastapi import FastAPI, Body
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="SocioGenesis OrgEnv Live Inspector")

    # serve the built React app's hashed assets (index.html uses base "/org/app/")
    if APP_DIST.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/org/app", StaticFiles(directory=str(APP_DIST), html=True), name="org_app")

    @app.get("/org/inspector", response_class=HTMLResponse)
    async def inspector():
        return inspector_html()

    @app.get("/org/legacy", response_class=HTMLResponse)
    async def inspector_legacy():
        return legacy_inspector_html()

    @app.get("/api/org/lived/full")
    async def lived_full():
        return api_full()

    @app.get("/api/org/lived/frames")
    async def lived_frames(since: int = -1):
        return api_frames(since)

    @app.get("/api/org/lived/state")
    async def lived_state():
        return api_state()

    @app.get("/api/org/lived/replays")
    async def lived_replays():
        return api_list_replays()

    @app.get("/api/org/lived/replay/{name}")
    async def lived_replay(name: str):
        return api_get_replay(name)

    @app.post("/api/org/sim/step")
    async def sim_step(payload: dict = Body(default={})):
        return api_sim_step(int(payload.get("n", 1)))

    @app.post("/api/org/sim/run_ticks")
    async def sim_run(payload: dict = Body(default={})):
        return api_sim_run_ticks(int(payload.get("n", 24)))

    @app.post("/api/org/sim/pause")
    async def sim_pause():
        return api_sim_pause()

    @app.post("/api/org/sim/reset")
    async def sim_reset(payload: dict = Body(default={})):
        return api_sim_reset(payload.get("seed"))

    @app.post("/api/org/sim/export")
    async def sim_export(payload: dict = Body(default={})):
        return api_export_replay(str(payload.get("name", "org_run")))

    @app.post("/api/org/sim/checkpoint/save")
    async def ckpt_save(payload: dict = Body(default={})):
        return api_checkpoint_save(str(payload.get("name", "")))

    @app.get("/api/org/sim/checkpoints")
    async def ckpt_list():
        return api_checkpoint_list()

    @app.post("/api/org/sim/checkpoint/load")
    async def ckpt_load(payload: dict = Body(default={})):
        return api_checkpoint_load(str(payload.get("name", "")))

    @app.post("/api/org/product/try")
    async def product_try(payload: dict = Body(default={})):
        return api_product_try(str(payload.get("query", "")))

    @app.post("/api/org/product/feedback")
    async def product_feedback(payload: dict = Body(default={})):
        return api_product_feedback(float(payload.get("rating", 0) or 0),
                                    str(payload.get("comment", "")), str(payload.get("query", "")))

    @app.post("/api/org/product/export")
    async def product_export():
        return api_product_export()

    @app.get("/api/org/agents")
    async def agents():
        return api_agents()

    @app.get("/api/org/agents/{agent_id}")
    async def agent(agent_id: str):
        return api_agent(agent_id)

    @app.get("/api/org/objects")
    async def objects(type: str = "task"):
        return api_objects(type)

    @app.get("/api/org/objects/{object_id}")
    async def obj(object_id: str):
        return api_object(object_id)

    @app.get("/api/org/{section}")
    async def section(section: str):
        return api_section(section)

    return app


def main() -> None:
    import socket
    import threading
    import webbrowser

    import uvicorn

    def free_port(start=8100):
        for p in range(start, start + 200):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", p)) != 0:
                    return p
        return start

    import os
    # enable the LLM cognitive layer from config/llm(.local).yaml for the live server
    # (import-time SESSION stays LLM-off so tests never hit a real API). Set ORG_LLM=0 for a
    # VIEW-ONLY server (replays + scrubbing need no LLM; Step/Run + Product Terminal do).
    SESSION.load_llm = os.environ.get("ORG_LLM", "1").lower() in ("1", "true", "yes")
    # realistic internal sim: institutions form only when designated approvers
    # explicitly approve (deadlock-safe). Use "auto" for a quick debug run.
    SESSION.approval_mode = "semi_auto"
    SESSION.reset(seed=SESSION.seed)
    lc = getattr(SESSION.world, "llm_client", None)
    print(f"LLM cognitive layer: {'ON ('+lc.provider+')' if lc else 'off (rule/template)'}")

    import os
    port = free_port()
    # don't auto-open the default browser — open the URL in your editor's browser instead
    # (set ORG_OPEN_BROWSER=1 to restore the old behaviour).
    if os.environ.get("ORG_OPEN_BROWSER", "0").lower() in ("1", "true", "yes"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}/org/inspector")).start()
    print(f"OrgEnv Live Inspector → http://127.0.0.1:{port}/org/inspector")
    uvicorn.run(build_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
