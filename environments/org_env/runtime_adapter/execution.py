"""OrgActionMapper (candidate generation §7) + OrgExecutionAdapter (real world
mutations §10) — DESIGN env_org O1.

The mapper turns a perception into 5-20 ActionCandidates from concrete triggers
(unread mention / unowned task / local untracked result / PR awaiting review /
external complaint / deadline pressure / payroll issue / skill fit). The
execution adapter applies each chosen action to the real OrgWorld subsystems and
returns an ExecutionResult (created/modified objects + events + cost) the
appraisal + event graph consume.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, is_dataclass
from typing import Any, Dict, Iterable, List, Optional

from agent_sdk.lived.core.contracts import ActionCandidate, CandidateSource
from agent_sdk.lived.domain.interfaces import DomainAction, DomainState
from environments.org_env.backend.actions import make_action, registered_action_types

MAX_CANDIDATES = 20

# v8-run fix: break the proposal request_changes loop (a reviewer re-requesting changes on
# the same proposal every tick). Shared by the mapper (candidate gating) + the handler.
PROPOSAL_CHANGES_COOLDOWN = 8     # a reviewer can't re-request changes on the same proposal sooner
PROPOSAL_MAX_CHANGES = 2          # after this many total, force an approve/reject (no more loops)

# A real product verdict is reusable for an unchanged PR tree.  ``not_run`` is
# different: it records that infrastructure prevented the integration check
# from answering.  Retry it, but not on every tick (which recreates the old CI
# attractor loop for an outage lasting more than one tick).
CI_NOT_RUN_RETRY_COOLDOWN = 3

# v4 §3: action/object cooldowns + the attractor guard now live in policy/attractor_guard.
# Re-exported here for back-compat with callers/tests that import them from this module.
from environments.org_env.policy.attractor_guard import (  # noqa: E402
    ACTION_OBJECT_COOLDOWN,
    GLOBAL_OBJECT_REVISION_COOLDOWN,
    AttractorGuard,
    candidate_target_artifact as _candidate_target_artifact,
)

_ATTRACTOR_GUARD = AttractorGuard()


def _restore_snapshot_in_place(live: Any, snapshot: Any) -> Any:
    """Restore a deepcopy snapshot without detaching shared registry references."""
    if isinstance(live, dict) and isinstance(snapshot, dict):
        for key in tuple(live):
            if key not in snapshot:
                del live[key]
        for key, snapshot_value in snapshot.items():
            if key in live:
                live[key] = _restore_snapshot_in_place(live[key], snapshot_value)
            else:
                live[key] = copy.deepcopy(snapshot_value)
        return live
    if isinstance(live, list) and isinstance(snapshot, list):
        live[:] = copy.deepcopy(snapshot)
        return live
    if isinstance(live, set) and isinstance(snapshot, set):
        live.clear()
        live.update(copy.deepcopy(snapshot))
        return live
    if (
        is_dataclass(live)
        and is_dataclass(snapshot)
        and type(live) is type(snapshot)
    ):
        live_values = vars(live)
        snapshot_values = vars(snapshot)
        for name in tuple(live_values):
            if name not in snapshot_values:
                delattr(live, name)
        for name, snapshot_value in snapshot_values.items():
            if hasattr(live, name):
                setattr(
                    live,
                    name,
                    _restore_snapshot_in_place(getattr(live, name), snapshot_value),
                )
            else:
                setattr(live, name, copy.deepcopy(snapshot_value))
        return live
    return copy.deepcopy(snapshot)


def _cap_pool(kept: List[ActionCandidate]) -> List[ActionCandidate]:
    """Bound the menu without letting the bound decide the delivery question.

    ``kept[:MAX_CANDIDATES]`` is order-dependent, and the delivery chain is
    generated after the task, product, tool and governance drivers. On a full
    pool it is the chain that falls off the end - so whether an organization
    could commit or merge this tick came down to how many documents it had to
    read. The chain contributes at most one candidate per stage, so admitting
    all of them keeps the pool bounded while making the cap unable to answer
    the question the experiment is asking.
    """
    if len(kept) <= MAX_CANDIDATES:
        return kept
    from environments.org_env.runtime_adapter.delivery_funnel import DELIVERY_ACTIONS

    budget = MAX_CANDIDATES - sum(1 for c in kept if c.action_type in DELIVERY_ACTIONS)
    out: List[ActionCandidate] = []
    for candidate in kept:
        if candidate.action_type in DELIVERY_ACTIONS:
            out.append(candidate)
        elif budget > 0:
            out.append(candidate)
            budget -= 1
    return out


def _apply_repetition_guard(pool, agent_id, world, tick):
    """Thin wrapper around the policy attractor guard's hard masks (kept for callers/
    tests that import this symbol). Returns only the kept candidates."""
    return _ATTRACTOR_GUARD.filter(pool, agent_id, world, tick)[0]


# category -> §11 appraisal event type (for the generic wrapper event).
_CAT_EVENT = {
    "work": "task_progress_event", "comm": "communication_event", "meeting": "meeting_event",
    "repo": "repo_event", "sandbox": "experiment_event", "search": "search_event",
    "doc": "file_share_event", "artifact": "file_share_event", "protocol": "protocol_use_event",
    "time": "overtime_event", "payroll": "payroll_event", "bridge": "external_signal_event",
    "governance": "governance_event", "release": "release_event",
}


@dataclass
class ExecutionResult:
    action_id: str
    agent_id: str
    action_type: str
    success: bool = True
    failure_reason: str = ""
    created_objects: List[str] = field(default_factory=list)
    modified_objects: List[str] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    cost_events: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    state_delta: Dict[str, Any] = field(default_factory=dict)
    memory_delta: List[dict] = field(default_factory=list)
    graph_edges: List[tuple] = field(default_factory=list)


def _c(action_type: str, source=CandidateSource.ENVIRONMENT, target=None, **params) -> ActionCandidate:
    return ActionCandidate(action_type=action_type, parameters=dict(params),
                           source=source, target_uid=target)


# How much delivery an organization may have in flight at once.
#
# Unbounded in either direction is a failure. With every later commit joining the
# author's one stuck request, a request opened at t9 was still absorbing commits
# at t288 and nothing could ever be offered on its own. With independent work
# freely starting its own request, the opposite arrives: neighbouring files split
# across requests that pass alone and break combined, review and CI cost
# multiplied, and a merged-request count that rewards splitting over delivering.
#
# A request is one coherent change — a step, an issue, a behaviour — which is one
# or two closely related modules, not five. An organization carries a few of them
# at a time and has to land, repair or drop before starting more.
_MAX_MODULES_PER_REQUEST = 2
_MAX_OPEN_REQUESTS_PER_AUTHOR = 2
_MAX_OPEN_REQUESTS = 6
_MAX_OPEN_REQUESTS_PER_MODULE = 2


def _rotating(items: Iterable, tick: int) -> list:
    """`items` starting from a tick-dependent offset, so a capped reader is not
    always handed the same prefix.

    Every affordance below caps how much it deals per agent per tick, which
    keeps the shortlist short. Taking that cap off a stable ordering is what
    made the tail unreachable: on the pilot nine modules carried open issues
    and the same three were dealt at every checkpoint of a 336-tick run, so an
    organization that stalled on the front of the queue could never be offered
    the rest of it. Rotating the start keeps the cap and removes the dead zone.
    """
    seq = list(items)
    if not seq:
        return []
    offset = int(tick) % len(seq)
    return seq[offset:] + seq[:offset]


# market signal -> issue (agent-driven): broaden which external posts are worth filing beyond
# customer_pain/api_cost, so live market pressure can become actionable work (self-iteration §).
_MARKET_FEEDBACK_TOPICS = frozenset({
    "customer_pain", "api_cost", "bug", "bug_report", "feature_request", "performance",
    "reliability", "quality", "usability", "docs", "pricing"})


def _is_market_feedback(post: dict) -> bool:
    """A visible external post worth filing as an issue: a complaint (negative stance) OR a
    product-feedback topic. Positive/neutral chatter is left as reply-only."""
    try:
        if float(post.get("stance", 0) or 0) < -0.05:
            return True
    except (TypeError, ValueError):
        pass
    return str(post.get("topic", "") or "").lower() in _MARKET_FEEDBACK_TOPICS


def _pr_needing_ci(w):
    """A PR whose CI verdict is still unknown FOR ITS CURRENT CONTENT.

    ``not pr.ci_passed`` alone made a red PR need CI forever: the option was
    dealt every tick, running it changed nothing, and the same verdict came back.
    Observed on a greenfield pack where the founder spent 161 of its 168 actions
    re-running CI on an unchanged tree and edited code once. It hurts a small
    roster worst — one action per tick means the loop consumes the whole
    organization, so a ladder reads as "more people, more code" when part of the
    gap is only who could afford the loop.

    A tree that has not changed since its last CI run has nothing new to learn;
    the code has to move first.
    """
    # include changes_requested so a bounced PR can get CI re-run (PR-revival: not a dead end).
    for pid, pr in w.repo_system.repo.pull_requests.items():
        if getattr(pr.status, "value", str(pr.status)) not in (
                "open", "review_requested", "approved", "changes_requested"):
            continue
        if bool(getattr(pr, "merge_conflict", False)):
            continue
        if pr.ci_passed:
            continue
        repo = w.repo_system.repo
        branch = repo.branches.get(str(getattr(pr, "source_branch", "") or ""))
        head = branch.commit_ids[-1] if branch and branch.commit_ids else None
        latest_ci = next(
            (
                repo.ci_runs.get(ci_id)
                for ci_id in reversed(getattr(pr, "ci_run_ids", []) or [])
                if repo.ci_runs.get(ci_id) is not None
            ),
            None,
        )
        # A verdict is reusable only for this request's exact branch head and
        # the mainline base it was evaluated against. A global working-tree hash
        # conflates unrelated branches and can hide a stale-base re-CI.
        if (
            latest_ci is not None
            and latest_ci.commit_id == head
            and getattr(pr, "ci_base_main_commit_ids", None) is not None
            and tuple(pr.ci_base_main_commit_ids) == tuple(repo.main_commit_ids)
        ):
            status = str(getattr(latest_ci, "status", "") or "").casefold()
            if status == "not_run":
                attempted = int(getattr(latest_ci, "created_at_tick", 0) or 0)
                now = int(getattr(w, "world_tick", 0) or 0)
                if now - attempted >= CI_NOT_RUN_RETRY_COOLDOWN:
                    return pid
            continue
        return pid
    return None


def _mergeable_pr(w):
    """An approved + CI-passed PR, preferring ones that carry patches (so the merge
    actually advances the mainline product artifact)."""
    fallback = ""
    for pid, pr in w.repo_system.repo.pull_requests.items():
        if getattr(pr.status, "value", str(pr.status)) == "approved" and pr.ci_passed:
            if pr.patch_ids:
                return pid
            fallback = fallback or pid
    return fallback


def _open_rc(w, cid=None):
    """The release candidate currently in flight (draft/under_review/approved/blocked)."""
    rcs = w.repo_system.repo.release_candidates
    if cid and cid in rcs:
        return rcs[cid]
    for rc in rcs.values():
        if rc.status in ("draft", "under_review", "approved", "blocked"):
            return rc
    return None


def _has_merged_patch_pr(w):
    return any(pr.patch_ids for pr in w.repo_system.repo.pull_requests.values()
               if getattr(pr.status, "value", str(pr.status)) == "merged")


def _role_or_roster_fallback(world, agent, roles) -> bool:
    """Whether ``agent`` may act on a step reserved for ``roles``.

    A role gate is a division of labour, and a division of labour needs someone
    to divide it with. When the roster staffs none of the named roles the step
    is not delegated to anybody, it simply cannot happen - so the founder, who
    holds every authority a one-person organization has, carries it. Where the
    roles do exist the gate is unchanged. Same rule as
    ``release.release_approval_met``: cap the requirement at the roster.
    """
    role = str(getattr(agent, "role", ""))
    if role in roles:
        return True
    staffed = {str(getattr(other, "role", ""))
               for other in (getattr(world, "agents", {}) or {}).values()}
    return not (staffed & set(roles)) and bool(getattr(agent, "is_founder", False))


_DOC_FILE_SUFFIXES = (".md", ".markdown", ".rst", ".txt")

# Which files the code editor owns. This has to agree with
# patch_validator._is_code_path, which already accepted the compiled and
# JS-family languages while the routing here still listed only the Python-era
# suffixes. A TypeScript source therefore fell through to the document editor,
# which looks for prose sections, finds none, and emits a no-op that validation
# rejects: on vite that path rejected 46 of B0's 47 patches and left the whole
# ladder unable to change a single line of the repository.
_CODE_FILE_SUFFIXES = (
    ".py", ".pyx", ".pyi", ".json", ".yaml", ".yml", ".toml",
    ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue",
    ".go", ".rs", ".java", ".c", ".h", ".cpp",
    # Build and container definitions carry no prose sections either, so the
    # document editor can only ever return a no-op on them. gitingest ships an
    # issue whose sole target is a Dockerfile. Matched by name because these
    # have no suffix of their own.
    "dockerfile", "makefile", ".sh", ".mk",
)

_RECONSTRUCTION_NON_IMPLEMENTATION_ROOTS = frozenset(
    {"eval", "test", "tests", "docs", "knowledge", ".github"}
)
_REPO_TARGET_FAILURE_DETAIL_CODES = frozenset({
    "choice_out_of_range",
    "create_not_unbound",
    "edit_has_no_existing_file",
    "invalid_path",
    "inventory_failed",
    "llm_required",
    "no_open_coding_issue",
    "nonimplementation_surface",
    "path_conflict",
    "provider_failed",
    "response_not_object",
    "unknown_operation",
})

_PROGRAMBENCH_PROBE_FALLBACK = '''#!/usr/bin/env python3
import json

print(json.dumps({
    "schema_version": "programbench_public_probe_cases_v1",
    "cases": [{
        "argv": [],
        "stdin": "",
        "input_files": [],
        "env": {},
    }],
}, separators=(",", ":"), sort_keys=True))
'''

_PUBLIC_RECONSTRUCTION_KNOWLEDGE_MAX_FILES = 4
_PUBLIC_RECONSTRUCTION_KNOWLEDGE_MAX_CHARS = 4096
_ORGANIZATION_BUILD_CONTRACT_MAX_CHARS = 3072
_PRIVATE_KNOWLEDGE_PATH_WORDS = frozenset(
    {"hidden", "reference", "evaluator", "oracle"}
)


def _bounded_public_reconstruction_knowledge(world: Any) -> str:
    """Return a small, public, mainline-only slice of ``knowledge/``.

    ProgramBench reconstruction starts from an empty source tree, so the code
    and probe editors otherwise see an empty file plus a one-line root issue and
    have to guess the documented interface.  Only product artifacts rooted at
    the public ``knowledge/`` directory are eligible.  Working-tree text is
    deliberately ignored: an unmerged organization edit is not the frozen task
    contract.  Evaluator bindings and every other world registry are never
    traversed here.
    """
    rows: List[tuple[str, str]] = []
    for artifact in (getattr(world, "product_artifacts", {}) or {}).values():
        path = str(getattr(artifact, "linked_file_path", "") or "").replace(
            "\\", "/"
        )
        normalized = path.casefold()
        if not normalized.startswith("knowledge/"):
            continue
        # Keep this a text-document channel.  In particular, a logo or other
        # binary starter artifact under knowledge/ must not become prompt text.
        if str(getattr(artifact, "artifact_type", "") or "").casefold() != "doc":
            continue
        path_words = {
            token
            for token in re.split(r"[^a-z0-9]+", normalized)
            if token
        }
        if path_words.intersection(_PRIVATE_KNOWLEDGE_PATH_WORDS):
            continue
        content = str(getattr(artifact, "mainline_content", "") or "").strip()
        if not content:
            continue
        rows.append((path, content))

    # README-like public contracts carry behavior before ancillary prose such
    # as changelogs.  The order is deterministic for replay/checkpoint parity.
    rows.sort(
        key=lambda row: (
            0 if row[0].rsplit("/", 1)[-1].casefold().startswith("readme") else 1,
            row[0].casefold(),
        )
    )
    blocks = [
        f"[{path}]\n{content}"
        for path, content in rows[:_PUBLIC_RECONSTRUCTION_KNOWLEDGE_MAX_FILES]
    ]
    rendered = "\n\n".join(blocks)
    return rendered[:_PUBLIC_RECONSTRUCTION_KNOWLEDGE_MAX_CHARS]


def _bounded_organization_build_contract(world: Any) -> str:
    """The organization's current build entrypoint, never the seed placeholder."""
    product = getattr(world, "product", None)
    meta = getattr(product, "substrate_meta", {}) or {}
    compile_path = str(meta.get("reconstruction_compile_path") or "").replace("\\", "/")
    if not compile_path:
        return ""
    words = {token for token in re.split(r"[^a-z0-9]+", compile_path.casefold()) if token}
    if words.intersection(_PRIVATE_KNOWLEDGE_PATH_WORDS):
        return ""

    patches = getattr(world, "patches", {}) or {}
    for artifact in (getattr(world, "product_artifacts", {}) or {}).values():
        path = str(getattr(artifact, "linked_file_path", "") or "").replace("\\", "/")
        if path.casefold() != compile_path.casefold():
            continue
        authored_here = int(getattr(artifact, "created_at_tick", 0) or 0) > 0
        if not authored_here:
            authored_here = any(
                getattr(patches.get(patch_id), "validation_status", "")
                in ("accepted", "applied", "merged")
                and int(getattr(patches.get(patch_id), "applied_tick", 0) or 0) > 0
                for patch_id in (getattr(artifact, "patch_history_ids", []) or [])
            )
        if not authored_here:
            return ""
        content = str(getattr(artifact, "content", "") or "").strip()
        if not content:
            return ""
        return f"[{compile_path}]\n{content}"[:_ORGANIZATION_BUILD_CONTRACT_MAX_CHARS]
    return ""


def _reconstruction_implementation_paths(world: Any) -> List[str]:
    """Organization-selected implementation paths in the public component map."""
    product = getattr(world, "product", None)
    meta = getattr(product, "substrate_meta", {}) or {}
    compile_path = str(meta.get("reconstruction_compile_path") or "").replace(
        "\\", "/").casefold()
    paths: List[str] = []
    for raw in ((world.__dict__.get("_oss_component_map", {}) or {}).get("reconstruction") or ()):
        path = str(raw or "").replace("\\", "/").strip("/")
        if not path or path.casefold() == compile_path:
            continue
        if path.split("/", 1)[0].casefold() in _RECONSTRUCTION_NON_IMPLEMENTATION_ROOTS:
            continue
        if path not in paths:
            paths.append(path)
    return paths


def _build_contract_mentions_source(contract: str, path: str) -> bool:
    """Return true only when a live build command consumes ``path``.

    A filename in prose, a comment, or an unreachable shell branch is not a
    connection between the build contract and the implementation.  Keep this
    deliberately conservative: a false mismatch asks the organization to
    inspect the build again, while a false connection can strand a completed
    implementation behind an unrelated entrypoint.
    """
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return False
    escaped_source = re.escape(normalized)
    source_pattern = re.compile(
        rf"(?:"
        rf"(?<![A-Za-z0-9_./-])(?:\./)?{escaped_source}|"
        rf"(?<![A-Za-z0-9_.-])\$\(\s*dirname\b[^)]*\)/{escaped_source}"
        r")(?!(?:[A-Za-z0-9_./-]))",
        re.IGNORECASE,
    )

    live_lines = list(_live_build_contract_lines(contract))
    if any(_shell_line_mutates_source(line, source_pattern) for line in live_lines):
        return False

    for line in live_lines:
        for match in source_pattern.finditer(line):
            prefix = line[:match.start()]
            if _shell_prefix_is_statically_unreachable(prefix):
                continue
            command_segment = re.split(r"&&|\|\||[;|]", prefix)[-1]
            if _shell_segment_is_source_consumer(command_segment):
                return True
    return False


def _live_build_contract_lines(contract: str):
    """Yield non-comment shell lines outside simple statically-dead branches."""
    dead_if_depth = 0
    for raw_line in str(contract or "").splitlines():
        line = _shell_without_comment(raw_line).strip()
        if not line:
            continue
        lowered = line.casefold()
        if_count = len(re.findall(r"\bif\b", lowered))
        fi_count = len(re.findall(r"\bfi\b", lowered))
        starts_dead_if = bool(re.search(
            r"(?:^|[;|&]\s*)if\s+(?:false|!\s*true)\b", lowered))
        if dead_if_depth:
            dead_if_depth = max(0, dead_if_depth + if_count - fi_count)
            continue
        if starts_dead_if:
            dead_if_depth = max(0, if_count - fi_count)
            continue
        yield line


def _shell_prefix_is_statically_unreachable(prefix: str) -> bool:
    """Recognize constant shell guards whose right side cannot execute."""
    return bool(
        re.search(r"(?:^|[;|&]\s*)false\s*&&\s*[^;|&]*$",
                  prefix, re.IGNORECASE)
        or re.search(r"(?:^|[;|&]\s*)true\s*\|\|\s*[^;|&]*$",
                     prefix, re.IGNORECASE)
    )


_SOURCE_DESTRUCTIVE_COMMANDS = {
    "mv", "rm", "shred", "tee", "touch", "truncate", "unlink",
}
_SOURCE_DESTINATION_COMMANDS = {"cp", "install", "ln", "rsync"}


def _shell_line_mutates_source(line: str,
                               source_pattern: re.Pattern[str]) -> bool:
    """Whether a live build command can replace, truncate, or delete the source."""
    for match in source_pattern.finditer(line):
        prefix = line[:match.start()]
        if _shell_prefix_is_statically_unreachable(prefix):
            continue
        suffix = line[match.end():]
        command_segment = re.split(r"&&|\|\||[;|]", prefix)[-1]
        command = _shell_segment_command_name(command_segment)

        # Shell redirection targets, including quoted and fd-prefixed targets.
        if re.search(r"(?:^|\s)(?:\d*>>?|&>)\s*['\"]?$", prefix):
            return True
        if command in _SOURCE_DESTRUCTIVE_COMMANDS:
            return True
        if command == "dd" and re.search(r"(?:^|\s)of=\s*['\"]?$", prefix):
            return True
        if command in {"sed", "perl"} and re.search(
                r"(?:^|\s)-(?:[^\s]*i[^\s]*)\b", command_segment,
                re.IGNORECASE):
            return True
        if re.search(r"(?:^|\s)(?:-o|--output(?:=)?)\s*['\"]?$", prefix):
            return True

        # Copy-like commands may read the implementation as their first
        # operand, but the same path in destination position overwrites it.
        if command in _SOURCE_DESTINATION_COMMANDS and re.match(
                r"^['\"]?\s*(?:$|&&|\|\||[;|])", suffix):
            return True
    return False


def _shell_segment_command_name(segment: str) -> str:
    """Return the normalized command name from a shell command prefix."""
    value = str(segment or "").strip()
    value = re.sub(r"^(?:(?:then|do|else)\b\s*)+", "", value,
                   flags=re.IGNORECASE)
    value = re.sub(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S+)\s+)*",
        "", value)
    wrappers = {"command", "env", "exec", "nice", "nohup", "sudo", "time"}
    while value:
        token_match = re.match(
            r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[A-Za-z0-9_./+:-]+)", value)
        if token_match is None or token_match.group(1).casefold() not in wrappers:
            break
        token = token_match.group(1).casefold()
        value = value[token_match.end():].lstrip()
        if token == "env":
            value = re.sub(r"^(?:-[^\s]+\s+)*", "", value)
            value = re.sub(
                r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S+)\s+)*",
                "", value)
    command_match = re.match(
        r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[A-Za-z0-9_./+:-]+)", value)
    if command_match is None:
        return ""
    return command_match.group(1).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _shell_without_comment(line: str) -> str:
    """Strip an unquoted shell comment from one build-contract line."""
    quote = ""
    escaped = False
    for index, character in enumerate(str(line or "")):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in ("'", '"'):
            quote = character
            continue
        if character == "#":
            return line[:index]
    return line


_SOURCE_CONSUMER_COMMAND = re.compile(
    r"^(?:"
    r"cp|install|mv|ln|rsync|cat|dd|"
    r"python(?:\d+(?:\.\d+)*)?|pypy\d*|sh|bash|dash|zsh|node|ruby|perl|php|"
    r"cc|c\+\+|gcc(?:-\d+)?|g\+\+(?:-\d+)?|clang(?:-\d+)?|"
    r"clang\+\+(?:-\d+)?|rustc|go|java|javac|kotlinc|swiftc|"
    r"make|gmake|cmake|ninja|meson|cargo|dotnet|"
    r"\$\{?(?:python|cc|cxx)\}?"
    r")$",
    re.IGNORECASE,
)


def _shell_segment_is_source_consumer(segment: str) -> bool:
    """Whether the command before a source token can build, copy, or launch it."""
    value = str(segment or "").strip()
    if not value:
        # The matched source token is itself the command at this shell-command
        # boundary (for example ``./yj.py "$@"``).
        return True
    value = re.sub(r"^(?:(?:then|do|else)\b\s*)+", "", value,
                   flags=re.IGNORECASE)
    value = re.sub(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S+)\s+)*",
        "",
        value,
    )
    wrappers = {"command", "env", "exec", "nice", "nohup", "sudo", "time"}
    direct_wrapper = False
    while value:
        token_match = re.match(
            r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[A-Za-z0-9_./+:-]+)", value)
        if token_match is None:
            break
        token = token_match.group(1)
        if token.casefold() not in wrappers:
            break
        direct_wrapper = direct_wrapper or token.casefold() in {"exec", "command"}
        value = value[token_match.end():].lstrip()
        if token.casefold() == "env":
            value = re.sub(r"^(?:-[^\s]+\s+)*", "", value)
            value = re.sub(
                r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S+)\s+)*",
                "",
                value,
            )
    if not value:
        # ``exec ./path`` and ``command ./path`` have the source itself as the
        # command token, so the prefix contains wrappers only.
        return direct_wrapper
    command_match = re.match(
        r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[A-Za-z0-9_./+:-]+)", value)
    if command_match is None:
        return False
    command = command_match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_SOURCE_CONSUMER_COMMAND.fullmatch(command))


def _append_public_reconstruction_knowledge(goal: str, world: Any) -> str:
    context = _bounded_public_reconstruction_knowledge(world)
    build_contract = _bounded_organization_build_contract(world)
    rendered = goal
    if context:
        rendered += (
            "\n\nPUBLIC DOCUMENTATION FROM THE MAINLINE knowledge/ DIRECTORY "
            "(bounded; treat it as the behavioral contract):\n"
            f"{context}"
        )
    if build_contract:
        rendered += (
            "\n\nCURRENT ORGANIZATION-AUTHORED BUILD CONTRACT (bounded working version; "
            "choose and implement a source path that this contract can actually package, or "
            "explicitly repair the contract):\n"
            f"{build_contract}"
        )
    return rendered


def _commit_risk_level(w, artifact_ids, changed_files=()):
    """Structural risk of a commit, derived from WHAT it touches — never tuned per
    substrate: doc-only -> low, one code artifact -> medium, several -> high. Feeds
    the CI quality gate (run_ci blocks a high-risk head commit without tests)."""
    arts = getattr(w, "product_artifacts", {}) or {}
    names = list(dict.fromkeys(artifact_ids)) or list(changed_files)
    code = set()
    for name in names:
        art = arts.get(name)
        title = str(getattr(art, "title", "") or name).lower()
        if getattr(art, "artifact_type", "") == "doc" or title.endswith(_DOC_FILE_SUFFIXES):
            continue
        code.add(name)
    if not code:
        return "low"
    return "medium" if len(code) == 1 else "high"


def urgency_to_importance(urgency: str) -> str:
    return {"incident": "blocker", "urgent": "decision_relevant", "high": "decision_relevant",
            "normal": "useful", "low": "trivial"}.get(urgency, "useful")


class OrgActionMapper:
    """Generates the per-tick candidate pool (§7) + the DomainAdapter-protocol
    ``available_actions`` shim (skeleton test). Holds per-agent emission state so
    broadcast/creation actions don't spam (idempotency + cooldown, O1.6/1.7)."""

    def __init__(self) -> None:
        self._emitted: Dict[tuple, int] = {}   # (agent_id, action, key) -> last emit tick

    def _cooldown_ok(self, agent_id: str, action: str, key: str, tick: int, cooldown: int) -> bool:
        """True if this (agent, action, target) wasn't offered within `cooldown` ticks."""
        k = (agent_id, action, str(key))
        last = self._emitted.get(k)
        if last is not None and tick - last < cooldown:
            return False
        self._emitted[k] = tick
        return True

    def to_core_candidates(self, perception: Any, org_world: Any) -> List[ActionCandidate]:
        p = perception
        cands: List[ActionCandidate] = []
        clk = p.clock_state

        # unread mentions -> acknowledge / reply / clarify
        for m in p.unread_mentions[:2]:
            cands.append(_c("acknowledge_message", CandidateSource.SOCIAL, message_id=m["message_id"]))
            cands.append(_c("reply_thread", CandidateSource.SOCIAL,
                            message_id=m["message_id"], channel_id=m["channel"]))
            cands.append(_c("ask_for_clarification", CandidateSource.SOCIAL,
                            message_id=m["message_id"], channel_id=m["channel"]))

        # a protocol may only be PROPOSED off a repeated pattern (§11.3/§20#6), and
        # only by institution-minded roles — else the LLM spams propose_protocol.
        # (the execution handler enforces the same gate + cooldown.)
        _agent = org_world.agents.get(p.agent_id)
        proto_ok = (self._mapper_protocol_trigger(org_world)
                    and getattr(_agent, "role", "") in ("founder", "cofounder", "reliability", "editorial")
                    # per-agent cooldown: don't re-offer propose_protocol every tick (§20#6)
                    and self._cooldown_ok(p.agent_id, "propose_protocol", "",
                                          int(getattr(org_world, "world_tick", 0)), 12))
        if proto_ok:
            cands.extend(self._protocol_wish_driven(p.agent_id, org_world))

        # unowned critical task -> ownership cluster
        for t in p.unowned_critical_tasks[:2]:
            cands.append(_c("pick_task", CandidateSource.NEED, task_id=t["task_id"]))
            cands.append(_c("assign_task_owner", CandidateSource.PERSONA,
                            task_id=t["task_id"], owner_id=p.agent_id))
            cands.append(_c("send_message", CandidateSource.SOCIAL, channel_id="team_general",
                            text=f"who owns {t['title']}?", importance="decision_relevant"))

        # assigned tasks -> work
        for t in p.assigned_tasks[:3]:
            cands.append(_c("work_on_task", CandidateSource.NEED, task_id=t["task_id"]))
            cands.append(_c("update_task_status", CandidateSource.ENVIRONMENT,
                            task_id=t["task_id"], status="in_progress"))

        # local untracked result -> share / export / tracker
        tick0 = int(clk.get("current_tick", org_world.world_tick))
        tracker_exists = "doc_experiment_tracker" in org_world.documents
        for r in p.local_unshared_results[:2]:
            cands.append(_c("export_result_to_tracker", CandidateSource.NEED, result_id=r["result_id"]))
            cands.append(_c("share_sandbox_output", CandidateSource.SOCIAL,
                            result_id=r["result_id"], channel_id="experiments"))
            if not tracker_exists:        # idempotent: only propose a tracker once
                cands.append(_c("create_experiment_tracker", CandidateSource.INSTITUTION))

        # PR awaiting my review
        for pr in p.prs_awaiting_my_review[:2]:
            cands.append(_c("review_pr", CandidateSource.SOCIAL, pr_id=pr["pr_id"]))
            cands.append(_c("request_changes", CandidateSource.SOCIAL, pr_id=pr["pr_id"], comment="needs tests"))
            cands.append(_c("approve_pr", CandidateSource.SOCIAL, pr_id=pr["pr_id"]))

        # visible external customer complaint -> bridge / triage (don't re-share the
        # same post for the same agent within a day; triage sheet is idempotent)
        triage_exists = "doc_customer_triage" in org_world.documents
        for post in p.visible_external_posts[:2]:
            # always allow REPLYING publicly on the external feed (回帖) — the company can engage
            # the external community, not only mirror it inward.
            if self._cooldown_ok(p.agent_id, "respond_to_public_comment", post["post_id"], tick0, 24):
                cands.append(_c("respond_to_public_comment", CandidateSource.SOCIAL,
                                post_id=post["post_id"], channel_id="external_signals"))
            # market signal -> issue (agent-driven): any COMPLAINT (negative stance) or product-feedback
            # topic is fileable, not just customer_pain/api_cost — so live market pressure can become
            # actionable work. The topic is carried onto the issue so the backlog can map it to a code
            # module when relevant.
            if _is_market_feedback(post):
                if self._cooldown_ok(p.agent_id, "share_external_post", post["post_id"], tick0, 24):
                    cands.append(_c("share_external_post", CandidateSource.SOCIAL,
                                    post_id=post["post_id"], channel_id="customer_feedback"))
                # v8d P2b: only OFFER to file the issue for a post once — once an issue exists
                # the handler just dedups to support, which otherwise spams "I opened an issue
                # ..." every tick. Repeat sightings raise support via the existing issue.
                pid = post["post_id"]
                filed = any(pid in (getattr(iss, "source_signal_ids", []) or [])
                            for iss in (getattr(org_world, "issues", {}) or {}).values()
                            if getattr(iss, "status", "open") in ("open", "triaged", "in_progress"))
                if not filed:
                    cands.append(_c("create_issue", CandidateSource.NEED,
                                    title=f"customer: {post['summary'][:40]}",
                                    post_id=pid, topic=post.get("topic", ""),
                                    channel_id="customer_feedback"))
                if not triage_exists:
                    cands.append(_c("create_customer_triage_sheet", CandidateSource.INSTITUTION))

        # payroll pressure
        pay = p.visible_payroll_summary
        if pay and (pay.get("unpaid_salary", 0) > 0 or pay.get("payroll_status") in ("delayed", "partial", "missed")):
            cands.append(_c("ask_about_payroll", CandidateSource.NEED))
            cands.append(_c("create_runway_update", CandidateSource.INSTITUTION))
            if pay.get("retention_risk", 0) > 0.6:
                cands.append(_c("consider_external_offer", CandidateSource.PERSONA))

        # deadline / after-hours pressure. Skipped entirely when work_rhythm is
        # ablated: the shortlist the agent chooses from is short, so offering
        # defer / rest / overtime at night crowds real work off it, and an
        # ablation that claims continuous availability must not spend an agent's
        # options on the mechanism it removed.
        rhythm_on = bool(getattr(getattr(org_world, "time", None),
                                 "rhythm_enabled", True))
        if rhythm_on and (clk.get("is_after_hours") or clk.get("is_late_night")):
            # v8h P1c: only offer an EOD update tied to a CONCRETE object the agent actually
            # moved today, as a STRING id (never a stringified task dict), with a structured
            # changed/next body. No object -> no EOD (just defer / rest).
            eod_obj = next((a.get("target") for a in reversed(org_world.action_log or [])
                            if a.get("agent_id") == p.agent_id and a.get("target")
                            and int(a.get("tick", 0) or 0) >= tick0 - 12), None)
            if eod_obj is None and p.assigned_tasks:
                t0 = p.assigned_tasks[0]
                eod_obj = (t0.get("task_id") or t0.get("id")) if isinstance(t0, dict) else t0
            if isinstance(eod_obj, dict):
                eod_obj = eod_obj.get("task_id") or eod_obj.get("id")
            if isinstance(eod_obj, str) and eod_obj:
                cands.append(_c("send_async_update", CandidateSource.SOCIAL, channel_id="team_general",
                                object_id=eod_obj,
                                text=f"EOD — changed: {eod_obj}; blocker: none flagged; next: continue {eod_obj}"))
            cands.append(_c("defer_until_work_hours", CandidateSource.ENVIRONMENT))
            cands.append(_c("rest_offline", CandidateSource.NEED))
            if p.assigned_tasks:
                cands.append(_c("work_overtime", CandidateSource.PERSONA,
                                task_id=p.assigned_tasks[0]["task_id"]))

        # O1.7 policy-grounded text / feedback candidates — GATED by appraisal +
        # the §17 feedback gate (policy, not raw text), with idempotency so the same
        # object isn't challenged/flagged every tick. Appraisals + decisions logged.
        from environments.org_env.runtime_adapter.text_layer import decide_feedback
        appraiser = (org_world._loop or {}).get("object_appraiser")
        agent = org_world.agents[p.agent_id]

        def _appraise(oid):
            if not appraiser or not oid:
                return None
            ap = appraiser.appraise(agent_id=p.agent_id, target_object_id=oid, world=org_world, tick=tick0)
            org_world.object_appraisal_log.append(
                {"tick": tick0, "appraiser": p.agent_id, "object_id": oid,
                 "object_type": ap.target_object_type, "risk": ap.risk_level,
                 "issue_tags": list(ap.issue_tags), "method": ap.method})
            return ap

        def _gated(oid, *, reviewer=False):
            ap = _appraise(oid)
            if ap is None or not (ap.issue_tags or ap.evidence_gaps):
                return None
            from environments.org_env.experiments.ablations import (
                PROFILE_POLICY,
                mechanism_disabled,
            )

            use_profile_conditioning = bool(
                getattr(org_world, "profile_conditioning_enabled", True)
                and not mechanism_disabled(org_world, PROFILE_POLICY)
            )
            d = decide_feedback(agent, ap, is_assigned_reviewer=reviewer,
                                after_hours=bool(clk.get("is_after_hours") or clk.get("is_late_night")),
                                use_profile_conditioning=use_profile_conditioning)
            org_world.feedback_decision_log.append(
                {"tick": tick0, "agent": p.agent_id, "object_id": oid, "act": d.feedback_act,
                 "should": d.should_feedback, "score": d.score, "reason": d.reason})
            return d if d.should_feedback else None

        # own assigned task -> promise to deliver (creates a commitment)
        for t in p.assigned_tasks[:1]:
            if self._cooldown_ok(p.agent_id, "promise_work", t["task_id"], tick0, 18):
                cands.append(_c("promise_work", CandidateSource.PERSONA, task_id=t["task_id"],
                                channel_id="team_general"))
        # own untracked result -> ask self/team to log + reproduce (request)
        for r in p.local_unshared_results[:1]:
            d = _gated(r.get("result_id"))
            if d and self._cooldown_ok(p.agent_id, "request_reproduction", r["result_id"], tick0, 12):
                cands.append(_c("request_reproduction", CandidateSource.NEED,
                                result_id=r["result_id"], channel_id="experiments"))
        # someone else's flagged result -> challenge it ONCE (creates a dispute). v8 #1:
        # an established/already-joined dispute does NOT take more challenges — it routes to
        # RESOLUTION (request_reproduction) so the critique reaches closure instead of looping.
        disputes = org_world.commitment_registry.disputes
        open_disp = {d.target_object_id: d for d in disputes.values()
                     if d.status in ("open", "escalated")}
        for r in (p.visible_results or [])[:1]:
            rid = r.get("result_id")
            if not (rid and _gated(rid)):
                continue
            d = open_disp.get(rid)
            # v8d P1c: a result whose same-problem dispute already resolved is settled —
            # don't re-offer a challenge (it would only no-op in the handler).
            prior = org_world.commitment_registry.find_dispute_any_status(rid)
            settled = d is None and prior is not None and prior.status == "resolved"
            joined = d is not None and (p.agent_id == d.challenger_id or p.agent_id in d.supporter_ids)
            established = d is not None and int(getattr(d, "support_count", 1)) >= 3
            if settled:
                continue
            if d is None:
                cands.append(_c("challenge_result", CandidateSource.PERSONA,
                                result_id=rid, channel_id="experiments"))
            elif not joined and not established:
                cands.append(_c("challenge_result", CandidateSource.PERSONA,
                                result_id=rid, channel_id="experiments"))
            elif self._cooldown_ok(p.agent_id, "request_reproduction", rid, tick0, 12):
                cands.append(_c("request_reproduction", CandidateSource.NEED,
                                result_id=rid, channel_id="experiments"))
        # a PR awaiting my review -> formal review (blocking) if it has issues
        for pr in p.prs_awaiting_my_review[:1]:
            if _gated(pr["pr_id"], reviewer=True):
                cands.append(_c("formal_pr_review", CandidateSource.SOCIAL, pr_id=pr["pr_id"]))
        # my own un-reviewed PR -> ask a reviewer (creates a RequestedAction)
        for pr in (p.visible_prs or [])[:2]:
            if pr.get("author") == p.agent_id and not pr.get("reviewed") and pr.get("reviewers") \
                    and self._cooldown_ok(p.agent_id, "ask_for_review", pr["pr_id"], tick0, 12):
                cands.append(_c("ask_for_review", CandidateSource.SOCIAL, pr_id=pr["pr_id"],
                                target_agent=pr["reviewers"][0], channel_id="engineering"))

        # skill / role driven defaults (so engineers run pilots, writers write docs...)
        cands.extend(self._skill_driven(agent, p, tracker_exists))
        # product-substrate work (act on the messy research-agent prototype)
        cands.extend(self._product_driven(agent, org_world))
        # use an adopted composed tool (closes wish->proposal->tool->use loop)
        cands.extend(self._tool_driven(agent, org_world))
        # governance: if this agent is a designated approver of a pending proposal,
        # surface explicit approve / reject / request-changes (persona decides)
        cands.extend(self._governance_driven(agent, org_world))
        # self-correction: offer to relax/deprecate an adopted protocol that's over-constraining
        # the org (high violation rate) — so a self-binding rule can be undone, not only tightened.
        cands.extend(self._policy_repair_driven(agent, org_world))
        # v5 repo workflow: promote the next step of the chain (commit_patch -> open_pr
        # -> run_ci -> merge_pr) so accepted patches actually reach the mainline.
        cands.extend(self._repo_workflow_driven(agent, org_world))
        # v11 coding layer: an OPEN release smoke blocker forces the fix/debug loop
        cands.extend(self._blocker_coding_driven(agent, org_world))
        # option A: a prior edit dropped a cross-file symbol -> force the importers to update (references)
        cands.extend(self._interface_repair_driven(agent, org_world))
        # OSS time-machine: an OPEN historical issue with a linked code module + no patch forces an
        # edit on that module (the affordance the gpt-5 run was missing — review fix §1)
        cands.extend(self._oss_issue_coding_driven(agent, org_world))
        # Keep the public greenfield build entrypoint independently editable.
        cands.extend(self._reconstruction_compile_contract_driven(agent, org_world))
        # ...and the test that would PROVE that edit worked. Without this the org
        # can only assert a fix, never demonstrate one (10 merged, 1 oracle passed).
        cands.extend(self._regression_test_driven(agent, org_world))
        # ...and the mirror of _interface_repair_driven: an edit that ADDED a
        # parameter and never passed it on compiles, passes, and does nothing.
        cands.extend(self._parameter_propagation_driven(agent, org_world))
        # ...and the other half of the same failure: one end of a two-file path
        # rewritten, the other left byte-identical (two oracles died on one such file).
        cands.extend(self._untouched_callee_driven(agent, org_world))

        # baseline: search + a generic message so the pool is never empty
        cands.append(_c("internal_search", CandidateSource.ENVIRONMENT, query="reproducibility"))
        cands.append(_c("read_feed", CandidateSource.ENVIRONMENT))
        cands.extend(self._unread_knowledge(agent, org_world))

        # dedupe by (type, target signature) and cap.
        # The target artifact MUST be part of the key. Without it every
        # edit_repo_file candidate - one per open issue, each naming a different
        # component file - collapsed into a single entry, so the organization
        # could only ever advance one file at a time no matter how many issues
        # were open (observed: 14 of ~20 patches landed on one file while nine
        # other issues went untouched).
        seen = set()
        pool = []
        for c in cands:
            params = c.parameters or {}
            key = (c.action_type, params.get("task_id"), params.get("pr_id"),
                   params.get("result_id"), params.get("post_id"),
                   _candidate_target_artifact(c),
                   params.get("issue_id"))
            if key in seen:
                continue
            seen.add(key)
            pool.append(c)
        # v4 §3: the attractor guard removes safe-action loops + churn from the choice set
        # (so neither the LLM nor the rule policy can pick them) and records what it masked.
        kept, masked = _ATTRACTOR_GUARD.filter(pool, p.agent_id, org_world, tick0)
        if hasattr(org_world, "_attractor_masked"):
            org_world._attractor_masked[p.agent_id] = [
                {"action": c.action_type,
                 "target": _candidate_target_artifact(c) or (c.parameters or {}).get("task_id"),
                 "reason": r} for c, r in masked]
        return _cap_pool(kept)

    _MAKER_ROLES = ("fast_engineer", "reliability", "cofounder", "artifact_design", "editorial", "founder")

    @staticmethod
    def _artifact_for_build_error(arts, err: str):
        """Localize an agent-visible repository file named by a build error."""
        rendered = str(err or "").replace("\\", "/").lstrip(" \t'\"")
        head = rendered.split("|", 1)[0].strip()
        candidates = []
        for artifact in arts.values():
            path = str(getattr(artifact, "linked_file_path", "") or "").replace(
                "\\", "/"
            )
            if path:
                candidates.append((path, artifact))
        # Prefer the exact repository path before a basename alias.  This works
        # for Go/Ruby/shell/Makefile diagnostics without inventing a language
        # list, while still selecting only an already-visible artifact.
        for path, artifact in sorted(candidates, key=lambda row: len(row[0]), reverse=True):
            names = (path, path.rsplit("/", 1)[-1])
            if any(
                head == name
                or head.startswith(name + ":")
                or head.startswith(name + " ")
                for name in names
            ):
                return artifact
        return None

    def _blocker_coding_driven(self, agent, world) -> List[ActionCandidate]:
        """v11 coding layer (Priority 1): when a release SMOKE blocker is OPEN, force the
        debugging loop — localize the failing file from world._build_error and offer a
        mask-immune fix on it — so agents FIX the blocker instead of searching/meeting.
        The repo chain (commit->PR->CI->merge) + rerun-gate are already surfaced elsewhere."""
        if agent.role not in self._MAKER_ROLES:
            return []
        arts = getattr(world, "product_artifacts", {}) or {}
        be = getattr(world, "_build_error", "") or ""
        blocker = next((a for a in arts.values()
                        if getattr(a, "artifact_type", "") == "issue" and getattr(a, "status", "") == "open"
                        and str(getattr(a, "artifact_id", "")).startswith("rel_blocker_gate_smoke")), None)
        # fire on a release-gate smoke blocker OR a live WORKING-tree build error (e.g. a cross-file
        # ImportError from an unmerged edit): localize the broken file and force a mask-immune fix, so
        # agents repair the actual break instead of editing around it.
        if blocker is None and not be:
            return []
        art = self._artifact_for_build_error(arts, be)
        if art is None:
            return []
        goal = f"Fix the build/import break so the product runs end-to-end again: {be[:200]}" if be else None
        params = dict(file_path=art.linked_file_path, artifact_id=art.artifact_id, _blocker_fix=True)
        if goal:
            params["edit_goal"] = goal
        return [_c("edit_repo_file", CandidateSource.NEED, **params)]

    def _interface_repair_driven(self, agent, world) -> List[ActionCandidate]:
        """IDE-like "update references": when a prior edit DROPPED a public symbol that other modules
        import, force a mask-immune edit on each such importer to update its import/usage — so a
        refactor cascades to callers instead of leaving the tree ImportError-red. Entries self-clear
        once the importer no longer imports the removed symbol."""
        if agent.role not in self._MAKER_ROLES:
            return []
        reg = getattr(world, "__dict__", {}).get("_interface_repairs") or {}
        if not reg:
            return []
        from environments.org_env.product.interface_guard import imports_from_module
        arts = getattr(world, "product_artifacts", {}) or {}
        out: List[ActionCandidate] = []
        for imp_id, info in _rotating(reg.items(),
                                      getattr(world, "world_tick", 0) or 0):
            a = arts.get(imp_id)
            if a is None or not getattr(a, "linked_file_path", None):
                reg.pop(imp_id, None)
                continue
            base = info.get("module", "")
            still = set(info.get("symbols", []) or []) & imports_from_module(getattr(a, "content", "") or "", base)
            if not still:
                reg.pop(imp_id, None)                 # importer no longer needs the dropped symbol -> done
                continue
            goal = (f"Module '{base}' no longer exports {sorted(still)}; update THIS file's import + usage "
                    f"(switch to the new API or drop the dependency) so it imports and runs cleanly.")
            out.append(_c("edit_repo_file", CandidateSource.NEED, file_path=a.linked_file_path,
                          artifact_id=imp_id, _blocker_fix=True, edit_goal=goal))
            if len(out) >= 3:
                break
        return out

    @staticmethod
    def _source_from_parameter_signature(signature) -> str:
        """Rebuild a stub source from the stored baseline signatures.

        The baseline holds names, not text, so the diff is done by synthesising
        the smallest source that reproduces the original parameter lists. This
        keeps one implementation of "which parameters were added" instead of a
        second one that works on dicts and can drift from the AST version.
        """
        return "\n".join(
            f"def {name}({', '.join(params)}):\n    pass"
            for name, params in sorted((signature or {}).items())
        )

    def _parameter_propagation_driven(self, agent, world) -> List[ActionCandidate]:
        """Finish a fix that added a parameter and never passed it anywhere.

        ``_interface_repair_driven`` handles a symbol being REMOVED, which breaks
        callers loudly. Adding one fails the opposite way and in silence: the
        file compiles, the public suite still passes, and the value simply never
        reaches the code that was supposed to act on it.

        Measured: the organization added ``include_submodules`` to ``ingest``
        and ``ingest_async`` - the right name, the right file, derived correctly
        from a prose issue that never states it - and never passed it to
        ``clone_repo``, which is where the hidden oracle reads it off the clone
        config. It got the hard half right and the mechanical half unfinished,
        nothing told it so, and the issue-driven editor deals one module per
        issue, so the second half was not merely unchosen but unreachable.

        The callee list is every cross-module symbol the function calls that did
        not receive the value. Naming all of them is honest: this knows the
        value goes nowhere, not which callee should have had it.
        """
        if agent.role not in self._MAKER_ROLES:
            return []
        baseline = world.__dict__.get("_starter_function_parameters") or {}
        if not baseline:
            return []
        try:
            from environments.org_env.product.interface_guard import (
                imported_symbol_sources,
                newly_added_parameters,
                unforwarded_parameters,
            )
        except Exception:
            return []
        arts = getattr(world, "product_artifacts", {}) or {}
        out: List[ActionCandidate] = []
        for art_id, starter_params in _rotating(sorted(baseline.items()),
                                                getattr(world, "world_tick", 0) or 0):
            art = arts.get(art_id)
            if art is None:
                continue
            content = str(getattr(art, "content", "") or "")
            if not content:
                continue
            added = newly_added_parameters(
                self._source_from_parameter_signature(starter_params), content)
            if not added:
                continue
            sources = imported_symbol_sources(world, art)
            unforwarded = unforwarded_parameters(
                content, added=added, cross_module_symbols=set(sources))
            for parameter, callees in sorted(unforwarded.items()):
                target = next((sources[c] for c in callees if sources.get(c)), "")
                target_art = arts.get(target)
                if target_art is None:
                    continue
                out.append(_c(
                    "edit_repo_file", CandidateSource.NEED,
                    file_path=getattr(target_art, "linked_file_path", ""),
                    artifact_id=target, _blocker_fix=True,
                    edit_goal=(
                        f"{getattr(art, 'linked_file_path', art_id)} now accepts a "
                        f"'{parameter}' parameter but never passes it on, so it has no "
                        f"effect. Accept and act on '{parameter}' here, and make sure the "
                        f"caller forwards it through: {', '.join(sorted(callees))}."),
                    rationale=f"thread '{parameter}' to its callee"[:200]))
                if len(out) >= 2:
                    return out
        return out

    def _untouched_callee_driven(self, agent, world) -> List[ActionCandidate]:
        """Offer the module a half-finished fix calls into but never edited.

        The parameter check catches a value that goes nowhere. This catches the
        other half of the same failure: one end of a two-file path rewritten and
        the other left exactly as it was.

        Measured, twice over the same callee: the organization rewrote
        ``_parse_patterns`` in query_parsing.py across four revisions and left
        ``_is_valid_pattern`` in query_parser_utils.py byte-identical, so its own
        character allow-list still rejected ``?`` and ``[`` and every pattern was
        refused before a single file was selected. Two separate oracles failed on
        that one untouched file.

        Only fires for issues already attempted, so it is a follow-up rather
        than another way to start; and only names callees the edited file
        actually imports, so it points at the path the fix runs along instead of
        at the repository at large.
        """
        if agent.role not in self._MAKER_ROLES:
            return []
        try:
            from environments.org_env.product.interface_guard import imported_symbol_sources
            from environments.org_env.product.substrates.issue_stream import (
                unpatched_coding_issues,
            )
            # Two attempts, not one: a single incomplete attempt is often just
            # work in progress, and offering a neighbour then fires on callees
            # that plainly need nothing (an imported exception class). Twice
            # stuck with an untouched neighbour is the signal worth acting on.
            items = [it for it in unpatched_coding_issues(world)
                     if int(it.get("attempts") or 0) >= 2]
        except Exception:
            return []
        if not items:
            return []
        arts = getattr(world, "product_artifacts", {}) or {}
        edited = {
            str((p if isinstance(p, dict) else getattr(p, "__dict__", {})).get("target_object_id") or "")
            for p in (getattr(world, "patches", {}) or {}).values()
        }
        out: List[ActionCandidate] = []
        for item in _rotating(items, getattr(world, "world_tick", 0) or 0):
            for art_id in item.get("artifact_ids") or ():
                art = arts.get(art_id)
                if art is None or art_id not in edited:
                    continue      # only reason about a file the fix actually touched
                if not str(getattr(art, "linked_file_path", "") or "").endswith(".py"):
                    continue
                for symbol, callee_id in sorted(imported_symbol_sources(world, art).items()):
                    if not callee_id or callee_id in edited:
                        continue
                    callee = arts.get(callee_id)
                    if callee is None:
                        continue
                    acceptance = (item.get("acceptance") or item.get("title") or "").strip()
                    out.append(_c(
                        "edit_repo_file", CandidateSource.NEED,
                        file_path=getattr(callee, "linked_file_path", ""),
                        artifact_id=callee_id, _blocker_fix=True,
                        _oss_issue=item["issue_id"],
                        edit_goal=(
                            f"The fix for this has changed "
                            f"{getattr(art, 'linked_file_path', art_id)}, which calls "
                            f"'{symbol}' from this file - and this file is still exactly as "
                            f"it started. Check whether '{symbol}' also has to change for "
                            f"this to hold: {acceptance}"),
                        rationale=f"unedited callee of {item['issue_id']}"))
                    if len(out) >= 2:
                        return out
        return out

    def _regression_test_driven(self, agent, world) -> List[ActionCandidate]:
        """Let the organization write the test that would prove its own fix.

        The public suite ships with the starter repo and therefore only covers
        behaviour that already existed; nothing in it can confirm a fix for a
        newly reported issue. Measured consequence: ten issue-linked tasks
        reached MERGED with progress 1.0 while one of eleven reachable oracles
        actually passed — the organization had no way to tell a real fix from a
        plausible-looking one, so every attempt looked equally finished.

        Authoring a failing test first is the ordinary engineering answer, and
        it was simply unreachable: no candidate ever named a test file. The edit
        goal states the red-then-green contract explicitly; the acceptance text
        is the issue's own agent-visible wording, never the hidden oracle.

        Only files the declared command actually runs are offered — a test
        written anywhere else never executes, so it would teach nothing.
        """
        if agent.role not in self._MAKER_ROLES:
            return []
        try:
            from environments.org_env.product.materialize import public_test_target_files
            from environments.org_env.product.substrates.issue_stream import unpatched_coding_issues
            # Files, not the directory the command names: see
            # public_test_target_files for why twelve of thirteen packs offered
            # this candidate zero times.
            test_files = public_test_target_files(world)
            items = unpatched_coding_issues(world)
        except Exception:
            return []
        if not test_files or not items:
            return []
        arts = getattr(world, "product_artifacts", {}) or {}
        # Keyed by real path only. Issues carry no linked_file_path, so keeping
        # the empty key collapsed every one of them onto "" and handed that
        # artifact back for an issue no test file matched — see below.
        by_path = {path: a for a in arts.values()
                   if (path := str(getattr(a, "linked_file_path", "") or ""))}
        tick = int(getattr(world, "world_tick", 0) or 0)
        # Offer the test for issues a fix has ALREADY been attempted on, before
        # issues with no attempt yet. Writing the failing test first is the
        # better discipline, but it is not what the organization does: given
        # both, it fixes first, and across 336 ticks it took this candidate once
        # out of ten opportunities. Right after an attempt is the moment the
        # verification is most obviously the next step and most obviously
        # needed, so that is where the candidate should land.
        ordered = sorted(items, key=lambda it: 0 if int(it.get("attempts") or 0) > 0 else 1)
        out: List[ActionCandidate] = []
        for item in ordered[:2]:
            target = self._closest_test_file(test_files, item, tick)
            if not target:                     # no test file matched this issue
                continue
            art = by_path.get(target)
            if art is None:
                continue
            acceptance = (item.get("acceptance") or item.get("title") or "").strip()
            if not acceptance:
                continue
            out.append(_c("edit_repo_file", CandidateSource.NEED, file_path=target,
                          artifact_id=getattr(art, "artifact_id", ""),
                          _oss_issue=item["issue_id"], _regression_test=True,
                          edit_goal=(
                              "Add a test that FAILS on the current code and will pass only "
                              f"once this holds: {acceptance}. Keep every existing test in "
                              "this file."),
                          rationale=f"Prove the fix for {item['issue_id']}"))
        return out

    @staticmethod
    def _closest_test_file(test_files: List[str], item: Dict[str, Any], tick: int) -> str:
        """The runnable test file whose name matches the issue's own words, or "".

        An unmatched issue gets no candidate rather than an arbitrary file.
        Observed when this rotated instead: a Docker-startup issue was paired
        with the gitignore test file, and the model anchored on that file's
        existing content and wrote gitignore scaffolding for a goal about the
        container starting. A goal that contradicts its target produces exactly
        the incoherent patch this affordance exists to prevent, so no candidate
        is better than a mismatched one.
        """
        text = " ".join(str(item.get(k, "") or "") for k in ("issue_id", "title", "acceptance")).lower()
        best, best_score = "", 0
        for path in test_files:
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            tokens = [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if len(t) > 3 and t != "test"]
            score = sum(1 for t in tokens if t in text)
            if score > best_score:
                best, best_score = path, score
        return best

    def _oss_issue_coding_driven(self, agent, world) -> List[ActionCandidate]:
        """OSS time-machine affordance (review fix §1): when an OPEN historical issue has a linked
        CODE module (via component_map) and no post-issue patch yet, deal a mask-immune
        ``edit_repo_file`` candidate on that module to a maker role — mirroring _blocker_coding_driven.

        This is what closes the gap where the org built process but never edited gitingest: OSS
        friction lives in *issue* artifacts (not code `known_gaps`), so `_product_driven` never
        offered an edit. Anti-scripting preserved: we only use VISIBLE info (the open issue + the file
        it is linked to) — never the hidden test or the future fix; the LLM still decides the patch."""
        if agent.role not in self._MAKER_ROLES:
            return []
        try:
            from environments.org_env.product.substrates.issue_stream import unpatched_coding_issues
            items = list(unpatched_coding_issues(world))
        except Exception:
            return []
        arts = getattr(world, "product_artifacts", {}) or {}
        tick = int(getattr(world, "world_tick", 0) or 0)
        # Group issues by the ONE module they'd edit (each issue's first mapped file), in severity
        # order. When several open issues share a single module (git_utils.py is mapped by BOTH
        # ``windows`` and ``http``), a plain per-file dedup lets the higher-severity issue monopolize
        # that module forever and permanently STARVES the lower one — observed: issue_httpx_migration
        # never once got an edit candidate across a whole 14-day run because a stuck
        # issue_windows_longpaths held git_utils.py. So we rotate the shared-module winner by tick:
        # each sharer gets a fair turn, while we still deal at most ONE edit per module per tick (no
        # same-file clobber). The EDIT GOAL is the issue's agent-visible behavioral acceptance (never
        # "improve <file>", never the hidden test/future fix) so the code editor gets a concrete
        # target — without it the editor produced no-op/vague patches (the gpt-5 run's 38/38 no-ops).
        from collections import OrderedDict
        by_file: "OrderedDict[str, list]" = OrderedDict()
        for it in items:
            for art_id in it["artifact_ids"]:
                art = arts.get(art_id)
                fp = getattr(art, "linked_file_path", None) if art is not None else None
                if not fp:
                    continue
                by_file.setdefault(fp, []).append((it, art_id))
                break                              # one module per issue
        if not by_file:
            # A reconstruction pack may intentionally have no source artifact to
            # map its root issue to. That is different from a broken component
            # map: the issue stream marks the one deliberately unbound item, and
            # only that item can mint this cold-start affordance. A seeded OPEN
            # task is merely the initial board row and must not freeze the empty
            # repository; actual in-progress implementation work does suppress a
            # second start.
            unbound = [it for it in items if it.get("unbound_reconstruction") is True]
            if len(unbound) != 1 or self._has_active_reconstruction_work(world, unbound[0]):
                return []
            item = unbound[0]
            issue_id = str(item.get("issue_id") or "")
            return [_c(
                "edit_repo_file", CandidateSource.NEED,
                _blocker_fix=True,
                _oss_backlog=1,
                _oss_issue=issue_id,
                _unbound_reconstruction=True,
                rationale="Start or extend the candidate implementation",
            )]

        # One candidate, and it carries no module. Whether to work on the
        # backlog at all is the question this affordance answers; which module
        # to open is answered later, by the model, from the full list of open
        # issues. Dealing a handful of pre-targeted candidates instead made the
        # environment pick the work: the cap took the same three modules off a
        # stable ordering and the other six were unreachable for an entire run,
        # and it left the two selection modes with different reach, since an
        # agent that names its target could ask for a module the shortlist had
        # not offered while an agent scored from that shortlist could not.
        heading = ", ".join(list(by_file)[:4])
        if len(by_file) > 4:
            heading += f" and {len(by_file) - 4} more"
        return [_c("edit_repo_file", CandidateSource.NEED, _blocker_fix=True,
                   _oss_backlog=len(by_file),
                   rationale=f"Open reported problems in {heading}")]

    def _reconstruction_compile_contract_driven(
        self, agent, world
    ) -> List[ActionCandidate]:
        """Expose only the agent-visible build entrypoint for reconstruction."""
        if agent.role not in self._MAKER_ROLES:
            return []
        product = getattr(world, "product", None)
        meta = getattr(product, "substrate_meta", {}) or {}
        path = str(meta.get("reconstruction_compile_path") or "")
        command = meta.get("reconstruction_compile_command") or []
        output = str(meta.get("reconstruction_output_path") or "")
        if not path or not isinstance(command, list) or not command or not output:
            return []
        if bool((world.__dict__.get("_public_tests_last") or {}).get("passed")):
            return []
        artifact = next(
            (
                item
                for item in (getattr(world, "product_artifacts", {}) or {}).values()
                if str(getattr(item, "linked_file_path", "") or "").replace("\\", "/")
                == path.replace("\\", "/")
            ),
            None,
        )
        if artifact is None:
            return []
        root_entry = next(
            (
                entry
                for entry in (world.__dict__.get("_oss_issue_stream", []) or [])
                if str(entry.get("component") or "") == "reconstruction"
            ),
            None,
        )
        issue_id = str((root_entry or {}).get("issue_id") or "")
        display_output = output.replace("\\", "/")
        if not display_output.startswith("./"):
            display_output = f"./{display_output}"
        goal = (
            f"Implement the public build contract in {path}: "
            f"`{' '.join(str(item) for item in command)}` must succeed and produce exactly "
            f"{display_output}. The output must be a regular executable file, so "
            f"`test -x {display_output}` must pass. Do not write the executable to an "
            "alternate path, and do not substitute a placeholder, stub, dummy, or no-op "
            "executable: the declared path must launch the real reconstructed program."
        )
        source_paths = _reconstruction_implementation_paths(world)
        build_text = str(getattr(artifact, "content", "") or "")
        source_mismatch = bool(source_paths and not any(
            _build_contract_mentions_source(build_text, source_path)
            for source_path in source_paths
        ))
        if source_paths:
            goal += (
                " The organization has selected these implementation source paths: "
                f"{', '.join(source_paths[:4])}. Keep the build entrypoint connected to the "
                "selected implementation; do not assume a different future filename."
            )
        return [_c(
            "edit_repo_file",
            CandidateSource.NEED,
            artifact_id=artifact.artifact_id,
            file_path=path,
            _oss_issue=issue_id,
            _programbench_compile_contract=True,
            _build_contract_source_mismatch=source_mismatch,
            _blocker_fix=source_mismatch,
            edit_goal=goal,
            rationale=("Connect the selected implementation to the public build contract"
                       if source_mismatch else
                       "Make the public reconstruction build contract executable"),
        )]

    @staticmethod
    def _has_active_reconstruction_work(world, item: Dict[str, Any]) -> bool:
        """Whether an unbound root issue already has real implementation work.

        ``TaskStatus.OPEN`` is intentionally not active here. OSS substrate
        seeding creates that row before anybody acts (and may assign an owner),
        so treating it as implementation freezes every empty-tree pack at t0.
        A non-empty implementation file created by the organization is concrete
        work and prevents duplicate cold starts.  Task/branch status alone is
        deliberately insufficient: editing the public ``compile.sh`` contract
        also opens a delivery branch, but must not make the first source file
        unreachable.
        """
        issue_id = str(item.get("issue_id") or "")
        issue_tasks = set()
        for task in (getattr(world, "tasks", {}) or {}).values():
            if issue_id not in (getattr(task, "linked_issues", []) or []):
                continue
            issue_tasks.add(str(getattr(task, "task_id", "") or ""))
            # ``work_on_task`` can move the seeded row to IN_PROGRESS without
            # touching the repository.  Status alone is therefore not
            # implementation evidence; the artifact/branch checks below are.
        component = str(item.get("component") or "")
        component_paths = {
            str(path).replace("\\", "/").casefold()
            for path in (
                (world.__dict__.get("_oss_component_map", {}) or {}).get(component)
                or ()
            )
        }
        for artifact in (getattr(world, "product_artifacts", {}) or {}).values():
            if not getattr(artifact, "created_as_new_file", False):
                continue
            path = str(getattr(artifact, "linked_file_path", "") or "")
            normalized_path = path.replace("\\", "/").casefold()
            first_component = normalized_path.split("/", 1)[0]
            # Probe/test/docs artifacts are useful organizational work, but
            # they are not the candidate implementation. Counting one here
            # freezes a clean-room repository before its first source file.
            if first_component in {"eval", "test", "tests", "docs", ".github"}:
                continue
            linked_tasks = {
                str(task_id)
                for task_id in (getattr(artifact, "linked_task_ids", []) or [])
            }
            explicitly_bound = bool(
                linked_tasks.intersection(issue_tasks)
                or normalized_path in component_paths
            )
            if (
                explicitly_bound
                and not normalized_path.endswith(_DOC_FILE_SUFFIXES)
                and str(getattr(artifact, "content", "") or "").strip()
            ):
                return True
        # Do not infer implementation from a branch linked to the root task.
        # Public contract edits, probe definitions, and prose can all be routed
        # through that task.  The artifact test above is the authoritative
        # evidence that an actual reconstruction surface now exists.
        return False

    @staticmethod
    def _public_tests_worth_running(world) -> bool:
        """True when a public-test run would tell the org something new.

        Gated on (a) the substrate declaring a suite, (b) unresolved coding work
        existing, and (c) the working tree having changed since the last run, so
        the action is available when it is informative and does not burn ticks
        re-running an unchanged tree.
        """
        try:
            from environments.org_env.product.materialize import (
                _repo_hash,
                declared_public_test_command,
            )
            from environments.org_env.product.substrates.issue_stream import (
                unpatched_coding_issues,
            )
            if not declared_public_test_command(world):
                return False
            if not unpatched_coding_issues(world):
                return False
            last = world.__dict__.get("_public_tests_last_hash")
            return last != _repo_hash(world, prefer_mainline=False)
        except Exception:
            return False

    def _repo_workflow_driven(self, agent, world) -> List[ActionCandidate]:
        """Surface the agent's next repo-workflow step based on repo state, so the
        patch->commit->PR->CI->merge chain actually advances (v5 §8).

        Every stage whose precondition holds is offered. A head slice used to cap
        this at three, which is invisible on a staffed roster - the upstream
        stages belong to whoever holds the patches and the downstream ones to
        somebody else, so the organization sees the whole chain each tick across
        its members. On a one-person roster all stages queue behind the same
        agent, and the slice cut merge_pr and the entire release lifecycle off
        the end of every decision it made while it also had a patch to commit.
        A one-person condition that never merges must be a condition that chose
        not to, not one that was never shown the option; the count is bounded by
        construction at one candidate per stage.
        """
        aid = agent.id
        if agent.role not in self._MAKER_ROLES:
            return []
        rs = getattr(world, "repo_system", None)
        if rs is None:
            return []
        from environments.org_env.backend.repo.workflow import branches_with_pending

        out: List[ActionCandidate] = []
        # 1. a branch with something pending -> commit it. Named, because an
        # agent carrying two branches is carrying two changes and the commit
        # says which; the readiest (a branch whose request is red, so the commit
        # is a repair) is offered first.
        for bid in branches_with_pending(world, aid)[:2]:
            out.append(_c("commit_patch", CandidateSource.NEED, branch_id=bid))
        # 2. my ready branch with commits -> open a PR
        for bid, b in rs.repo.branches.items():
            if b.owner_id == aid and getattr(b.status, "value", str(b.status)) == "ready_for_pr" \
                    and b.commit_ids:
                out.append(_c("open_pr", CandidateSource.NEED, source_branch=bid))
                break
        # 3. an open PR without a passing CI -> run CI
        ci_pr = _pr_needing_ci(world)
        if ci_pr:
            out.append(_c("run_ci", CandidateSource.NEED, pr_id=ci_pr))
        # 3b. unverified coding work on the board -> run the substrate's own public
        # tests. This is the organization's only legitimate fix-verification signal
        # (hidden oracles are evaluator-only), so it must be reachable whenever
        # there is still unresolved coding work.
        if self._public_tests_worth_running(world):
            out.append(_c("run_public_tests", CandidateSource.NEED))
        # 4. an approved + CI-passed PR -> merge (author or a lead)
        merge_pr_id = _mergeable_pr(world)
        if merge_pr_id:
            pr = rs.repo.pull_requests.get(merge_pr_id)
            if pr and (pr.author_id == aid or agent.role in ("cofounder", "founder", "reliability")):
                out.append(_c("merge_pr", CandidateSource.NEED, pr_id=merge_pr_id))
        out.extend(self._release_driven(agent, world))
        return out

    def _release_driven(self, agent, world) -> List[ActionCandidate]:
        """Promote the next release-lifecycle step (v5 §4.2/§8): merged work -> RC ->
        readiness check -> approve (lead + reliability) -> publish -> post-launch feedback."""
        aid = agent.id
        rs = world.repo_system
        out: List[ActionCandidate] = []
        rc = _open_rc(world)
        if rc is None:
            if agent.role in ("founder", "cofounder") and _has_merged_patch_pr(world):
                out.append(_c("create_release_candidate", CandidateSource.INSTITUTION))
        elif rc.status == "draft":
            out.append(_c("run_launch_readiness_check", CandidateSource.INSTITUTION,
                          candidate_id=rc.candidate_id))
        elif rc.status == "under_review":
            if agent.role in ("founder", "cofounder", "reliability") and aid not in rc.approvals:
                out.append(_c("approve_release_candidate", CandidateSource.INSTITUTION,
                              candidate_id=rc.candidate_id))
        elif rc.status == "approved":
            if agent.role in ("founder", "cofounder"):
                out.append(_c("publish_product_release", CandidateSource.INSTITUTION,
                              candidate_id=rc.candidate_id))
        elif rc.status == "blocked":
            out.append(_c("run_launch_readiness_check", CandidateSource.INSTITUTION,
                          candidate_id=rc.candidate_id))
        if rs.repo.releases and _role_or_roster_fallback(
                world, agent, ("community", "external_voice", "cofounder")):
            rel = next(reversed(list(rs.repo.releases.values())), None)
            if rel is not None and not rel.post_launch_feedback_ids:
                out.append(_c("collect_post_launch_feedback", CandidateSource.NEED))
        return out

    def _policy_repair_driven(self, agent, world) -> List[ActionCandidate]:
        """Deal an ``amend_protocol`` (relax) candidate for an adopted protocol that is doing more
        harm than good, to an institution-minded role, on cooldown. This is the agent-initiated
        counterpart to the automatic policy-repair wish; both let the org UNDO a self-binding rule
        instead of only ever tightening it. What counts as harm is in protocol/harm.py."""
        if agent.role not in ("founder", "cofounder", "reliability", "editorial"):
            return []
        pm = getattr(world, "proposal_manager", None)
        if pm is None:
            return []
        from environments.org_env.backend.protocol.harm import rule_is_doing_harm

        from environments.org_env.backend.protocol.harm import blocked_without_delivery

        tick = int(getattr(world, "world_tick", 0))
        out: List[ActionCandidate] = []
        # Worst first. When the pipeline is stalled several rules can each show
        # as harmful, and taking whichever the dict happened to yield first
        # offered a repair for a rule that had turned away three requests while
        # the one that had turned away two hundred went unmentioned.
        specs = sorted(getattr(pm, "protocol_specs", {}).items(),
                       key=lambda kv: -blocked_without_delivery(world, kv[1])[0])
        for sid, s in specs:
            if getattr(s, "status", "") != "adopted":
                continue
            harmful, why = rule_is_doing_harm(world, s)
            if not harmful:
                continue
            if not self._cooldown_ok(agent.id, "amend_protocol", sid, tick, 24):
                continue
            # Carry the evidence with the candidate. Nothing else can: no per-rule
            # count reaches any prompt, so an approver deciding whether to loosen
            # a rule that has refused three hundred merges would otherwise read
            # only "it is over-constraining the org" and have to take it on faith.
            out.append(_c("amend_protocol", CandidateSource.INSTITUTION,
                          protocol_id=sid, repair_kind="relax", evidence=why))
            break                                   # one repair at a time
        return out

    def _governance_driven(self, agent, world) -> List[ActionCandidate]:
        """In agent/semi_auto modes, a designated approver of an under-review proposal
        gets explicit approve / reject / request-changes candidates (the policy/LLM —
        i.e. the persona — decides). In 'auto' mode the system approves, so none."""
        if getattr(world, "approval_mode", "auto") == "auto":
            return []
        pm = getattr(world, "proposal_manager", None)
        if pm is None:
            return []
        out: List[ActionCandidate] = []
        tick = int(getattr(world, "world_tick", 0))
        chlog = getattr(world, "_proposal_change_log", {}) or {}
        for p in pm.proposals.values():
            if p.status != "under_review" or agent.id not in (p.approval_required_from or []):
                continue
            if agent.id in p.approved_by or agent.id in p.rejected_by:
                continue
            out.append(_c("approve_proposal", CandidateSource.INSTITUTION, proposal_id=p.proposal_id))
            out.append(_c("reject_proposal", CandidateSource.INSTITUTION, proposal_id=p.proposal_id))
            # only offer request_changes if this reviewer isn't on cooldown AND the proposal
            # hasn't hit the max-revisions cap — otherwise force an approve/reject decision (no loop).
            log = chlog.get(p.proposal_id, [])
            on_cd = any(r == agent.id and tick - t < PROPOSAL_CHANGES_COOLDOWN for r, t in log)
            if len(log) < PROPOSAL_MAX_CHANGES and not on_cd:
                out.append(_c("request_proposal_changes", CandidateSource.INSTITUTION, proposal_id=p.proposal_id))
            if len(out) >= 6:    # cap: at most 2 pending proposals surfaced per tick
                break
        return out

    def _tool_driven(self, agent, world) -> List[ActionCandidate]:
        """Offer an adopted, role-callable tool to maker roles (1 candidate)."""
        pm = getattr(world, "proposal_manager", None)
        if pm is None or agent.role not in ("fast_engineer", "reliability", "artifact_design", "editorial"):
            return []
        active = [t for t in pm.tools.values() if t.status == "active"
                  and (not t.callable_by_roles or agent.role in t.callable_by_roles)]
        if not active:
            return []
        # v5 §P0-2: use_tool is capped by cooldown + masked while patches are uncommitted,
        # so it can't become the new safe-action attractor (was 55/100t).
        return [_c("use_tool", CandidateSource.INSTITUTION, tool_id=active[-1].tool_id)]

    @staticmethod
    def _protocol_wish_driven(agent_id: str, world) -> List[ActionCandidate]:
        """Offer to propose the rule this agent has already asked for, in its words.

        The two places that used to offer this action carried the rule with them —
        "every critical task needs an explicit owner", "publish a runway update
        every payroll cycle" — so an agent taking it was picking a written rule off
        a shelf rather than saying what its organization should require. What the
        rule says, and which recurring failure it answers, comes from the agent's
        own protocol_need wish; this only carries it to the handler.
        """
        rm = getattr(world, "reflection_manager", None)
        if rm is None:
            return []
        mine = [w for w in getattr(rm, "wishes", {}).values()
                if getattr(w, "wish_type", "") == "protocol_need"
                and str(getattr(w, "status", "")) in ("open", "interpreted")
                and (getattr(w, "agent_id", "") == agent_id
                     or agent_id in (getattr(w, "supporting_agent_ids", None) or []))]
        if not mine:
            return []
        wish = max(mine, key=lambda w: float(getattr(w, "urgency", 0.0) or 0.0))
        rule = str(getattr(wish, "suggested_improvement", "")
                   or getattr(wish, "interpreted_need", "")).strip()
        if not rule:
            return []
        return [_c("propose_protocol", CandidateSource.INSTITUTION,
                   rule_summary=rule[:200],
                   source_problem=str(getattr(wish, "target_problem", ""))[:200],
                   scope="org",
                   related_objects=list(getattr(wish, "related_object_ids", []) or []),
                   related_episodes=list(getattr(wish, "related_episode_ids", []) or []))]

    @staticmethod
    def _mapper_protocol_trigger(world) -> bool:
        """A repeated pattern / protocol_need wish exists -> propose_protocol warranted
        (else the LLM spams it; mirrors the execution handler gate, §11.3/§20#6).

        There is no ceiling on how many rules an organization may carry. There was
        one, at three, and the environment seeds two of its own before any agent
        acts, so the third arrival closed the door: across three runs the action
        was offered to nobody and attempted zero times, and every rule that existed
        came from a fixed catalogue in the codebase. What stops a duplicate is the
        fingerprint at proposal time, which attaches support to the existing rule
        instead of minting a second one — a check on sameness, not on how much an
        organization is allowed to have learned.
        """
        rm = getattr(world, "reflection_manager", None)
        if rm is not None and any(
                x.wish_type == "protocol_need" and x.status in ("open", "interpreted", "converted_to_proposal")
                for x in rm.wishes.values()):
            return True
        epm = getattr(world, "episode_manager", None)
        if epm is not None:
            eps = list(epm.episodes.values())
            if sum(1 for e in eps if e.episode_type == "claim_dispute_episode") >= 2:
                return True
            if sum(1 for e in eps if e.episode_type == "protocol_formation_episode") >= 2:
                return True
        return False

    def _product_driven(self, agent, world) -> List[ActionCandidate]:
        """Role-appropriate work on the messy product substrate (real artifacts)."""
        arts = getattr(world, "product_artifacts", {}) or {}
        if not arts:
            return []
        role = agent.role
        tick = int(getattr(world, "world_tick", 0) or 0)
        # (block, candidate). The block is the role the work belongs to, and it
        # is what the tick rotation below turns on - never the position in this
        # list, which shifts whenever an unrelated block happens to fire.
        out: List[tuple[int, ActionCandidate]] = []

        def by_path(sub):
            return next((a for a in arts.values() if a.linked_file_path and sub in a.linked_file_path), None)

        def messy(types):
            return [a for a in arts.values() if a.artifact_type in types and a.known_gaps and a.status != "closed"]

        # v11 dogfooding: periodically actually USE the product we built (experience the
        # real report / credibility / breakage first-hand). Offered to product-facing roles
        # on a per-agent cooldown so it informs (not floods) the loop.
        if role in ("fast_engineer", "reliability", "founder", "cofounder", "editorial") \
                and self._cooldown_ok(agent.id, "dogfood_product", "", tick, 24):
            out.append((0, _c(
                "dogfood_product", CandidateSource.PERSONA,
                query="What are the leading open-source vector databases and their tradeoffs?")))

        # hands-on product work goes to maker roles; founders/cofounders lead via
        # direction/review/reflection (their candidate pool is left unchanged so
        # coordination actions like meetings are not displaced). Leading rather
        # than building presupposes builders: on a roster that staffs neither
        # engineer role the same gate leaves nobody able to touch the product at
        # all, which is a different condition from the one it was written for.
        # Whoever holds a coding task may edit what it is about, whatever their
        # role. The role gate hands unclaimed product work to the maker roles,
        # which is the point of having roles; handing somebody a coding task and
        # then refusing them the means is not specialisation, it is a dead turn.
        # Measured at t120: two of eight agents had no code option at all, and
        # one of them held three coding tasks whose whole menu was work_on_task
        # and update_task_status against them -- effort that could never become
        # code, chosen 48 times because nothing else was on offer.
        mine = self._file_of_my_coding_task(world, agent)
        if mine is not None:
            out.append((1, _c("edit_repo_file", CandidateSource.NEED,
                              file_path=mine.linked_file_path,
                              artifact_id=mine.artifact_id)))
        if _role_or_roster_fallback(world, agent, ("fast_engineer", "reliability")):
            mf = messy(("repo_file", "tool_stub"))
            if mf:
                out.append((1, _c("edit_repo_file", CandidateSource.NEED,
                                  file_path=mf[0].linked_file_path,
                                  artifact_id=mf[0].artifact_id)))
            out.append((1, _c("run_eval_stub", CandidateSource.PERSONA)))
        if _role_or_roster_fallback(world, agent, ("reliability",)):
            ct = by_path("claim_tracker")
            if ct:
                out.append((2, _c("update_claim_tracker", CandidateSource.NEED,
                                  artifact_id=ct.artifact_id)))
            out.append((2, _c("create_eval_stub", CandidateSource.INSTITUTION)))
        if _role_or_roster_fallback(world, agent, ("editorial",)):
            rm = by_path("README")
            if rm:
                out.append((3, _c("audit_readme_claims", CandidateSource.PERSONA,
                                  artifact_id=rm.artifact_id)))
        if _role_or_roster_fallback(world, agent, ("artifact_design",)):
            out.append((4, _c("create_report_quality_checklist", CandidateSource.INSTITUTION)))
            st = by_path("source_tracker")
            if st:
                out.append((4, _c("update_source_tracker", CandidateSource.NEED,
                                  artifact_id=st.artifact_id)))
            open_issues = [a for a in arts.values() if a.artifact_type == "issue" and a.status == "open"]
            if open_issues:
                out.append((4, _c("write_design_note", CandidateSource.NEED,
                                  artifact_id=open_issues[0].artifact_id)))
        if _role_or_roster_fallback(world, agent, ("external_voice", "external_docs", "community")):
            out.append((5, _c("create_onboarding_doc", CandidateSource.PERSONA)))
        return self._rotate_product_blocks(out, tick)

    @staticmethod
    def _file_of_my_coding_task(world, agent):
        """The module a coding task this agent holds is about, or None.

        A task carries the issue it came from, and an issue on this substrate
        names the file it is about. Ownership is the link: it is the agent's own
        work, not the backlog, so this widens nothing for anyone who has not
        been given the task.
        """
        aid = agent.id
        arts = getattr(world, "product_artifacts", {}) or {}
        for task in (getattr(world, "tasks", {}) or {}).values():
            if getattr(task, "owner_id", None) != aid:
                continue
            if "COMPLETE" in str(getattr(task, "status", "")).upper():
                continue
            linked = list(getattr(task, "linked_artifacts", []) or [])
            for iid in list(getattr(task, "linked_issue_ids", []) or []):
                linked.append(iid)
            for oid in linked:
                art = arts.get(oid)
                path = str(getattr(art, "linked_file_path", "") or "") if art else ""
                if path.endswith(".py") and getattr(art, "artifact_type", "") != "issue":
                    return art
                # An issue names its module rather than being one.
                issue = arts.get(oid)
                for name in (getattr(issue, "linked_artifact_ids", []) or []):
                    got = arts.get(name)
                    p2 = str(getattr(got, "linked_file_path", "") or "") if got else ""
                    if p2.endswith(".py"):
                        return got
        return None

    _PRODUCT_BLOCK_COUNT = 6
    _PRODUCT_CONTENT_SLOTS = 2

    @classmethod
    def _rotate_product_blocks(cls, blocks, tick: int) -> List[ActionCandidate]:
        """Keep the repo edit; spend the two content slots on a rotating block.

        The cap is there so product work does not crowd out core actions, and
        the thing it is capping is content work - trackers, checklists, design
        notes. ``edit_repo_file`` is not that: it is the first step of the
        delivery chain and the only action that changes the product, so it is
        held out of the cap the same way ``_cap_pool`` holds the rest of the
        chain out of the pool cap.

        What remains rotates. A staffed member matches one or two blocks and
        barely notices; an agent that inherited every unstaffed role matches all
        of them, and a head slice would hand it the same two forever - leaving
        the README audit and the quality checklist, which two release gates
        require, permanently out of reach. Priority depends on the block id and
        the tick alone, so which blocks fire this tick cannot reorder the rest.
        """
        from environments.org_env.runtime_adapter.delivery_funnel import DELIVERY_ACTIONS

        chain = [c for _, c in blocks if c.action_type in DELIVERY_ACTIONS]
        content = [(i, b, c) for i, (b, c) in enumerate(blocks)
                   if c.action_type not in DELIVERY_ACTIONS]
        if len(content) > cls._PRODUCT_CONTENT_SLOTS:
            content = sorted(
                content,
                key=lambda item: ((item[1] - tick) % cls._PRODUCT_BLOCK_COUNT, item[0]),
            )[: cls._PRODUCT_CONTENT_SLOTS]
        return chain + [c for _, _, c in content]

    def _unread_knowledge(self, agent, w) -> List[ActionCandidate]:
        """The product's own knowledge base, for anyone who has not read it yet.

        review_doc reaches w.documents -- the notes and updates the organization
        writes for itself -- and was offered to one role, for the first document
        it could see. The knowledge the pack ships with the product lives among
        the product artifacts and no action reached it at all, so the contract
        naming every symbol the tests import sat unread for a whole run while the
        names were guessed.

        Offered to everyone, rotated so a capped shortlist eventually deals every
        file, and dropped once read: it is a thing to go and find, not a thing
        handed over at t0.
        """
        aid = agent.id
        pw = (getattr(w, "personal", {}) or {}).get(aid)
        already = set(getattr(pw, "downloaded_doc_ids", []) or []) if pw else set()
        out = []
        arts = (getattr(w, "product_artifacts", {}) or {}).values()
        tick = int(getattr(w, "world_tick", 0) or 0)
        for art in _rotating(sorted(arts, key=lambda a: getattr(a, "artifact_id", "")),
                             tick):
            path = str(getattr(art, "linked_file_path", "") or "")
            if not path.endswith(".md") or "knowledge/" not in path:
                continue
            if art.artifact_id in already:
                continue
            out.append(_c("read_knowledge", CandidateSource.ENVIRONMENT,
                          artifact_id=art.artifact_id))
            if len(out) >= 2:
                break
        return out

    def _skill_driven(self, agent, p, tracker_exists: bool = False) -> List[ActionCandidate]:
        out: List[ActionCandidate] = []
        role = agent.role
        # engineers: run pilots; the repo chain (commit_patch -> open_pr -> run_ci ->
        # merge) is now driven by _repo_workflow_driven off accepted patches, so we no
        # longer emit patch-less commit_changes/open_pr here (they never reached mainline).
        if role in ("reliability", "fast_engineer", "cofounder"):
            out.append(_c("run_cheap_pilot", CandidateSource.PERSONA,
                          experiment_id="reliability_v0"))
        if role == "reliability" and not tracker_exists:
            out.append(_c("create_experiment_tracker", CandidateSource.INSTITUTION))
        if role in ("editorial",):
            for d in p.visible_docs[:1]:
                out.append(_c("review_doc", CandidateSource.PERSONA, doc_id=d["doc_id"]))
        if role in ("external_voice", "community", "artifact_design"):
            out.append(_c("create_doc", CandidateSource.PERSONA, title="launch update", doc_type="external_update"))
        if role == "community":
            out.append(_c("monitor_customer_feedback", CandidateSource.PERSONA))
        if role in ("founder", "cofounder"):
            out.append(_c("schedule_meeting", CandidateSource.PERSONA,
                          meeting_type="daily_sync", title="daily sync"))
        return out

    # -- DomainAdapter-protocol shim (skeleton test) -----------------------
    def available_actions(self, *, agent_id: str, state: DomainState) -> List[DomainAction]:
        return [make_action(a, actor_id=agent_id) for a in registered_action_types()]

    def to_core_candidates_from_actions(self, actions: List[DomainAction]) -> List[ActionCandidate]:
        return [_c(a.action_type) for a in actions]


class OrgExecutionAdapter:
    """Executes a chosen ActionCandidate against the real OrgWorld (§10.1)."""

    def execute(self, agent_id: str, action: Any, org_world: Any) -> ExecutionResult:
        w = org_world
        at = action.action_type
        params = dict(action.parameters or {})
        tick = w.world_tick
        from environments.org_env.backend.actions import (
            CAT_RELEASE,
            CAT_REPO,
            CAT_SANDBOX,
            action_category,
        )
        cat = action_category(at)
        res = ExecutionResult(action_id=f"act_{agent_id}_{tick}_{at}", agent_id=agent_id,
                              action_type=at)
        # no_executable_product_workflow ablation: the guarded repo/sandbox/release
        # workflow is severed at execution too (belt-and-braces with the candidate
        # filter — an LLM free-choice action must not land a patch/CI/release either).
        if cat in (CAT_REPO, CAT_SANDBOX, CAT_RELEASE):
            from environments.org_env.experiments.ablations import (
                PRODUCT_WORKFLOW,
                mechanism_disabled,
            )
            if mechanism_disabled(w, PRODUCT_WORKFLOW):
                res.success = False
                res.failure_reason = "mechanism_ablation:product_workflow"
                res.events.append({"type": "mechanism_ablation_event",
                                   "subtype": "product_workflow_blocked",
                                   "agent_id": agent_id, "action_type": at, "tick": tick})
                return res
        # snapshot adopted-protocol counters so structured use-crediting (below) can
        # tell whether a keyword / governed-claim path already judged this action.
        pre_proto = {s.protocol_id: (int(getattr(s, "use_count", 0) or 0),
                                     int(getattr(s, "violation_count", 0) or 0),
                                     int(getattr(s, "enforcement_count", 0) or 0))
                     for s in self._adopted_specs(w)}
        handler = getattr(self, f"_h_{at}", None)
        try:
            if handler is not None:
                handler(w, agent_id, params, res, tick)
            else:
                self._h_generic(w, agent_id, at, params, res, tick)
        except Exception as e:  # never crash the loop on a single bad action
            res.success = False
            res.failure_reason = f"{type(e).__name__}: {e}"
        self._apply_cost(w, agent_id, action, cat, res, tick)
        # thread/channel context for the episode layer (a real linkage signal):
        # stamp the action's channel so same-thread events can attach.
        ch = params.get("channel_id")
        if ch:
            res.state_delta.setdefault("channel_id", ch)
        self._maybe_surface(w, agent_id, at, params, res, tick)
        # spec #4: an adopted protocol actively governs the action (use / violation /
        # enforcement), not just exists as text.
        self._enforce_protocols(w, agent_id, at, params, res, tick)
        # spec #4 generality: a protocol's own declared required_actions are the
        # structured crediting channel — keyword tuples only reach the CI / release /
        # evidence families, so triage / review / meeting-family protocols could never
        # accrue a use at any run length without this.
        self._credit_declared_protocol_uses(w, agent_id, at, res, tick, pre_proto)
        # spec #7: high-value messages must reference the objects they're about.
        self._ground_messages(w, params, res)
        # only emit a generic wrapper event if the handler emitted none, mapping
        # the category to the §11 taxonomy (avoids a flood of "action_event").
        if not res.events:
            res.events.append({"type": _CAT_EVENT.get(cat, "action_event"), "action_type": at,
                               "agent_id": agent_id, "tick": tick, "success": res.success})
        res.graph_edges.append((agent_id, "performed", res.action_id))
        return res

    # spec #4: external-facing claim actions are governed by an adopted evidence protocol.
    _EXTERNAL_CLAIM_ACTIONS = {"share_external_post", "publish_product_release", "create_report",
                              "export_result_to_tracker", "share_sandbox_output"}

    @staticmethod
    def _active_evidence_protocol(w):
        pm = getattr(w, "proposal_manager", None)
        for s in (pm.protocol_specs.values() if pm else []):
            if s.status != "adopted":
                continue
            blob = f"{s.name} {s.trigger_condition} {s.enforcement_rule}".lower()
            if any(k in blob for k in ("evidence", "claim", "credib", "source", "traceab")):
                return s
        return None

    @staticmethod
    def _adopted_specs(w):
        pm = getattr(w, "proposal_manager", None)
        return [s for s in (pm.protocol_specs.values() if pm else [])
                if getattr(s, "status", "") == "adopted"]

    @staticmethod
    def _declared_actions(spec):
        """Normalize a spec's affected_actions into action-id tokens for exact matching
        against the executed action_type (shared normalizer lives on ProtocolSpec so use
        and enforcement crediting agree on what a spec declares; the inline fallback
        covers stub specs in tests)."""
        fn = getattr(spec, "declared_action_ids", None)
        if callable(fn):
            return fn()
        out = set()
        for a in (getattr(spec, "affected_actions", None) or []):
            if isinstance(a, dict):
                a = a.get("action") or a.get("action_type") or a.get("name") or ""
            a = str(a).strip().lower().replace(" ", "_").replace("-", "_")
            if a:
                out.add(a)
        return out

    def _credit_declared_protocol_uses(self, w, aid, at, res, tick, pre_counts) -> None:
        """spec #4 generality: credit every adopted protocol that DECLARES the executed
        action in its affected_actions (the proposal's validated required_actions) with
        a USE when an agent successfully performs that action. This is the structured
        counterpart of the note_protocol_use keyword tuples, which only reach the
        CI / release-gate / readiness / external-claim families — a Customer Triage or
        PR-review protocol governs real actions yet had NO reachable crediting path, so
        its use_count (and weak emergence via USE_MIN) was structurally frozen at 0.
        A spec whose use/violation/enforcement counters already moved during this
        execution was judged by a keyword or governed-claim path — skip it so one
        action never counts twice (and a violating action is never also a use)."""
        if not res.success:
            return
        obj = (res.created_objects[0] if res.created_objects else None) or res.action_id
        for spec in self._adopted_specs(w):
            before = pre_counts.get(spec.protocol_id)
            if before is None:                 # adopted mid-action: this action predates it
                continue
            now = (int(getattr(spec, "use_count", 0) or 0),
                   int(getattr(spec, "violation_count", 0) or 0),
                   int(getattr(spec, "enforcement_count", 0) or 0))
            if now != before:                  # already credited/judged for this action
                continue
            if at not in self._declared_actions(spec):
                continue
            spec.use_count += 1
            spec.last_used_tick = tick
            if res.action_id not in spec.affected_action_ids:
                spec.affected_action_ids.append(res.action_id)
            spec.use_event_ids.append(f"protocol_use_event@t{tick}")   # v8h P1 parity
            res.events.append({"type": "protocol_use_event", "protocol_id": spec.protocol_id,
                               "agent_id": aid, "tick": tick, "action_id": res.action_id,
                               "object_id": obj, "governed_action": at})
            res.graph_edges.append((res.action_id, "governed_by", spec.protocol_id))
            # v8f P1a parity: the emergence detector reads the registry mirror
            _mirror = getattr(w, "_mirror_protocol_event", None)
            if _mirror is not None:
                _mirror(spec, "use", tick, obj, aid)

    def _enforce_protocols(self, w, aid, at, params, res, tick) -> None:
        if at not in self._EXTERNAL_CLAIM_ACTIONS:
            return
        spec = self._active_evidence_protocol(w)
        if spec is None:
            return
        from environments.org_env.product.objects import artifact_purpose
        arts = getattr(w, "product_artifacts", {}) or {}

        def _cap(purpose, *kw):
            for a in arts.values():
                if artifact_purpose(getattr(a, "linked_file_path", "") or a.artifact_id) == purpose:
                    caps = [str(c).lower() for c in (getattr(a, "capabilities", []) or [])]
                    if any(k in c for k in kw for c in caps):
                        return True
            return False

        # #7: scope the "unsupported external claim" violation so it stops mis-firing.
        #  (a) publish_product_release is a FACTUAL announcement backed by the release gate it just
        #      passed (smoke/CI/hidden) — not an unsupported marketing claim -> compliant USE.
        #  (b) the LanternScout claim_tracker/report_writer evidence check does NOT exist for an OSS
        #      product (gitingest); there, "evidence" = the product actually RUNS (mainline smoke ok).
        #      Without this, EVERY OSS external action was a violation (protospec_1's 25/27 inflation).
        if at == "publish_product_release":
            supported = True
        else:
            supported = _cap("claim_tracker", "evidence", "source") or _cap("report_writer", "support")
            if not supported:
                try:
                    from environments.org_env.product.substrates.eval_assets import is_oss_substrate
                    if is_oss_substrate(w) and not (getattr(w, "_mainline_smoke_error", "") or ""):
                        supported = True
                except Exception:
                    pass
        obj = params.get("post_id") or params.get("result_id") or params.get("artifact_id") or res.action_id
        spec.last_used_tick = tick
        if supported:                                  # compliant external claim -> protocol USE
            spec.use_count += 1
            if res.action_id not in spec.affected_action_ids:
                spec.affected_action_ids.append(res.action_id)
            res.events.append({"type": "protocol_use_event", "protocol_id": spec.protocol_id,
                               "agent_id": aid, "tick": tick, "action_id": res.action_id,
                               "object_id": obj, "governed_action": at})
            res.graph_edges.append((res.action_id, "governed_by", spec.protocol_id))
            # v8f P1a parity: the emergence detector reads the registry mirror, so a
            # governed use must advance it too (else weak emergence can never fire
            # when all protocol activity flows through this path).
            _mirror = getattr(w, "_mirror_protocol_event", None)
            if _mirror is not None:
                _mirror(spec, "use", tick, obj, aid)
        else:                                          # unsupported external claim -> VIOLATION + enforcement
            spec.violation_count += 1
            spec.enforcement_count += 1
            spec.violation_event_ids.append(f"protocol_violation_event@t{tick}")      # v8h P1 parity
            spec.enforcement_event_ids.append(f"protocol_enforcement_event@t{tick}")  # v8h P1 parity
            res.events.append({"type": "protocol_violation_event", "protocol_id": spec.protocol_id,
                               "agent_id": aid, "tick": tick, "action_id": res.action_id,
                               "object_id": obj, "governed_action": at,
                               "reason": "external claim without evidence support"})
            iid = self._enforcement_issue(w, spec, tick)
            res.events.append({"type": "protocol_enforcement_event", "protocol_id": spec.protocol_id,
                               "agent_id": aid, "tick": tick, "follow_up_issue": iid})
            res.graph_edges.append((spec.protocol_id, "enforced_via", iid))
            _mirror = getattr(w, "_mirror_protocol_event", None)
            if _mirror is not None:
                _mirror(spec, "violate", tick, obj, aid)
                _mirror(
                    spec,
                    "enforce",
                    tick,
                    obj,
                    aid,
                    blocked=False,
                    state_impact_ref=iid,
                )

    @staticmethod
    def _enforcement_issue(w, spec, tick) -> str:
        from environments.org_env.product.objects import ProductArtifact
        arts = w.product_artifacts
        iid = f"proto_violation_{spec.protocol_id}"
        if iid not in arts:
            arts[iid] = ProductArtifact(
                artifact_id=iid, artifact_type="issue", status="open", priority="high",
                title="protocol violation: unsupported external claim",
                problem=f"{spec.name} requires evidence before external claims; add claim-evidence support",
                summary="external claim made without evidence support",
                created_at_tick=tick, updated_at_tick=tick)
            if getattr(w, "product", None) is not None:
                w.product.artifact_ids.append(iid)
                if iid not in w.product.open_issue_ids:
                    w.product.open_issue_ids.append(iid)
        return iid

    # high-salience actions surface a short team message so the org isn't silent
    # (preflight review #7); per-agent+action cooldown keeps it from spamming.
    # preflight v3 §8: readable, first-person surface text for high-salience actions
    # (no extra LLM call — concrete enough to read as organizational communication).
    _SURFACE_ACTIONS = {
        "propose_protocol": ("team_general",
            "I'm proposing a protocol ({p}) so we stop relitigating the same problem ad hoc."),
        "audit_readme_claims": ("engineering",
            "I'm flagging the README: it still implies evidence guarantees the claim tracker "
            "does not enforce yet."),
        "create_report_quality_checklist": ("engineering",
            "I drafted a report-quality checklist so we don't ship unsupported claims."),
        "schedule_meeting": ("team_general", "I'm scheduling a {p} meeting to get us aligned."),
        "create_issue": ("team_general", "I opened an issue: {p}."),
        "challenge_result": ("experiments",
            "I'm challenging this result — it needs source ids and a reproducible run before we trust it."),
        "request_changes": ("engineering",
            "I'm requesting changes on this PR before it merges — the evidence path isn't covered."),
        "ask_for_evidence": ("experiments",
            "Can we get evidence / a reproduction for this? I don't want to build on an unverified claim."),
        "export_result_to_tracker": ("experiments",
            "I logged this result to the tracker with its config so others can reproduce it."),
        "post_company_update": ("team_general", "Company update posted — here's where we actually are."),
        "summarize_decision": ("team_general", "Summarizing what we decided so it doesn't get lost: {p}."),
        "approve_proposal": ("team_general", "I'm approving this proposal — it addresses a real gap."),
        "object_to_proposal": ("team_general", "I object to this proposal as written — {p}."),
        "create_doc": ("team_general", "I wrote up a doc to make our workflow/trust boundaries explicit."),
    }

    def _maybe_surface(self, w, agent_id, at, params, res, tick):
        spec = self._SURFACE_ACTIONS.get(at)
        if spec is None or not res.success:
            return
        if res.messages:        # the handler already surfaced a (richer) message
            return
        cd = self.__dict__.setdefault("_surface_cd", {})
        k = (agent_id, at)
        if cd.get(k) is not None and tick - cd[k] < 6:   # per-agent+action cooldown
            return
        channel, tmpl = spec
        # prefer the handler-derived content (real decision/summary) over raw params
        detail = (res.state_delta.get("surface_detail") if hasattr(res, "state_delta") else None) \
            or params.get("protocol_name") or params.get("protocol_type") \
            or params.get("meeting_type") or params.get("title") \
            or params.get("reason") or params.get("summary") or ""
        detail = str(detail).strip()
        # v8-run fix: don't broadcast a contentless summary ("...we decided: .")
        if at == "summarize_decision" and not detail:
            return
        cd[k] = tick
        text = tmpl.format(p=detail[:80]) if "{p}" in tmpl else tmpl
        m = self._send(w, agent_id, channel, text, tick, importance="decision_relevant")
        res.messages.append(m.message_id)
        res.events.append({"type": "communication_event", "subtype": "surface", "agent_id": agent_id,
                           "tick": tick, "object_id": m.message_id})

    _CORE_MODULE_PURPOSES = {"research_loop", "report_writer", "claim_tracker",
                             "source_tracker", "eval"}

    @staticmethod
    def _artifact_complexity(w, oid) -> float:
        if not oid:
            return 0.0
        art = (getattr(w, "product_artifacts", {}) or {}).get(oid)
        if art is None:
            return 0.0
        from environments.org_env.product.objects import artifact_purpose
        path = (getattr(art, "linked_file_path", "") or "").lower()
        purpose = artifact_purpose(path or art.artifact_id)
        pts = 0.0
        if purpose in OrgExecutionAdapter._CORE_MODULE_PURPOSES or "agent.py" in path:
            pts += 3.0                                   # core module rewrite is heavy
        pts += 0.5 * len(getattr(art, "known_gaps", []) or [])
        if path.endswith(_CODE_FILE_SUFFIXES):
            pts += 0.5
        return pts

    def _scale_duration_by_workload(self, w, at, cat, action, res, spec, aid, tick):
        """spec #6: blocking work actions cost ticks proportional to workload (patch size
        / generated content), capped per action — the overflow splits into pending work
        units the agent works through over later ticks."""
        if not (spec.is_blocking and cat in ("work", "repo", "doc", "artifact")):
            return spec
        from dataclasses import replace
        from environments.org_env.backend.actions.workload import (
            estimate_workload, workload_tick_cost, MAX_TICK_PER_ACTION)
        import math
        patch = w.patches.get(res.state_delta.get("patch_id")) if res.state_delta.get("patch_id") else None
        params = dict(getattr(action, "parameters", {}) or {})
        workload = estimate_workload(at, params, patch)
        # #5: a change to a CORE module (research loop / trackers / report writer / eval) or
        # to an artifact with many open gaps is heavier than a one-line edit.
        oid = (getattr(patch, "target_object_id", None) if patch else None) or params.get("artifact_id")
        workload += self._artifact_complexity(w, oid)
        cost = workload_tick_cost(workload)
        res.state_delta["workload"] = round(workload, 2)
        if cost > MAX_TICK_PER_ACTION:                       # oversized -> split the remainder
            extra_units = math.ceil(cost / MAX_TICK_PER_ACTION) - 1
            cost = MAX_TICK_PER_ACTION
            w.enqueue_work_split(aid, at, extra_units, res, tick, workload, task_id=params.get("task_id"))
        w.__dict__.setdefault("_action_tick_cost", {}).setdefault(at, []).append(int(cost))
        if cost > spec.blocking_duration_ticks:
            return replace(spec, blocking_duration_ticks=cost)
        return spec

    # -- cost / vitals / duration (O1.6 §4-§9) ----------------------------
    def _apply_cost(self, w, agent_id, action, cat, res, tick):
        from environments.org_env.backend.actions.duration import DURATION_REGISTRY
        agent = w.agents[agent_id]
        at = getattr(action, "action_type", action)
        ws = getattr(agent, "work_state", None)
        rhythm = bool(getattr(w.time, "rhythm_enabled", True))
        spec = DURATION_REGISTRY.estimate(action, agent)
        if rhythm:
            spec = DURATION_REGISTRY.apply_time_modifiers(spec, agent, w.time.clock)
        spec = self._scale_duration_by_workload(w, at, cat, action, res, spec, agent_id, tick)
        res.state_delta["duration"] = spec.total_busy_ticks
        res.state_delta["is_auxiliary_speech"] = spec.is_auxiliary_speech
        if spec.error_risk_delta:
            res.state_delta["error_risk"] = round(spec.error_risk_delta, 3)

        # recovery (sleep / rest) — strong/partial restoration, occupies time
        if spec.is_recovery:
            dur = max(1, spec.blocking_duration_ticks)
            if at == "sleep":
                w.time.sleep(agent, duration=dur)
            else:
                w.time.rest(agent, duration=dur)
            if ws:
                ws.start_activity(action_id=res.action_id, action_type=at, tick=tick, duration=dur)
            return

        # auxiliary speech — no time block; consumes a slot + a little attention
        if spec.is_auxiliary_speech:
            if ws:
                ws.aux_speech_slots_remaining = max(0, ws.aux_speech_slots_remaining - 1)
                if rhythm:
                    ws.attention_remaining_today = max(
                        0.0, ws.attention_remaining_today - spec.attention_cost
                    )
                    ws.stress = min(1.0, ws.stress + spec.stress_delta)
                if at in ("send_message", "reply_thread", "reply_thread_short", "send_async_update"):
                    ws.daily_message_count += 1
            return

        # background — submit now (busy for submit ticks), completes later
        if spec.can_run_in_background:
            if ws:
                ws.next_available_tick = max(ws.next_available_tick, tick + spec.submit_duration_ticks)
                if rhythm:
                    ws.attention_remaining_today = max(
                        0.0, ws.attention_remaining_today - spec.attention_cost
                    )
            job = w.time.submit_background_job(
                agent_id=agent_id, action_type=at, submit_tick=tick,
                background_duration=spec.background_duration_ticks,
                result_id=(res.created_objects[-1] if res.created_objects else None))
            if ws:
                ws.background_jobs.append(job.job_id)
            res.state_delta["background_job"] = job.job_id
            return

        # blocking — occupies the agent's main time
        dur = max(1, spec.blocking_duration_ticks)
        is_work = cat in ("work", "repo", "sandbox", "meeting", "doc", "artifact") \
            or at in ("work_overtime", "weekend_work")
        if is_work:
            log = w.time.log_work(agent, action_type=at, is_deep_work=cat in ("repo", "sandbox", "work"),
                                  duration=dur)
            res.state_delta["work_session"] = log.work_session_id
            if ws and cat in ("repo", "sandbox", "work"):
                ws.deep_work_blocks_used_today += 1
            # real after-hours / weekend work surfaces as its own appraised event
            if log.is_overtime:
                res.events.append({"type": "overtime_event", "agent_id": agent_id, "tick": tick,
                                   "action_type": at, "duration": dur})
            if log.is_weekend:
                res.events.append({"type": "weekend_work_event", "agent_id": agent_id, "tick": tick,
                                   "action_type": at, "duration": dur})
        elif rhythm:
            v = agent.vitals.variables
            v["attention"] = max(0.0, v.get("attention", 1.0) - spec.attention_cost)
            v["fatigue"] = min(1.0, v.get("fatigue", 0.0) + spec.fatigue_delta)
            v["stress"] = min(1.0, v.get("stress", 0.0) + spec.stress_delta)
            v["context_switch_cost"] = min(1.0, v.get("context_switch_cost", 0.0) + spec.context_switch_delta)
            if spec.burnout_risk_delta:
                v["burnout_risk"] = min(1.0, v.get("burnout_risk", 0.0) + spec.burnout_risk_delta)
        if ws:
            ws.start_activity(action_id=res.action_id, action_type=at, tick=tick, duration=dur)
            res.state_delta["next_available_tick"] = ws.next_available_tick

    _HIGH_IMPORTANCE = {"decision_relevant", "blocker", "policy_relevant"}
    _LINK_PARAM_KEYS = ("task_id", "issue_id", "pr_id", "result_id", "protocol_id",
                        "post_id", "artifact_id", "candidate_id", "doc_id", "object_id")

    def _ground_messages(self, w, params, res) -> None:
        """spec #7 + v8f P1c: attach the concrete objects an action touched to its messages.
        High-value messages link everything they touched; ANY message that EXPLICITLY
        references an object via params (e.g. an EOD update with object_id) is also grounded
        — so EOD broadcasts stop being free-floating text. Trivial chit-chat that merely
        coincided with a created artifact is NOT force-grounded."""
        if not res.messages:
            return
        param_objs = [v for k, v in (params or {}).items()
                      if k in self._LINK_PARAM_KEYS and isinstance(v, str)]
        all_objs = list(dict.fromkeys(
            list(res.created_objects) + list(res.modified_objects) + param_objs
            + ([res.state_delta.get("patch_id")] if res.state_delta.get("patch_id") else [])))
        if not all_objs:
            return
        msgs = getattr(getattr(w, "comm", None), "messages", {}) or {}
        for mid in res.messages:
            m = msgs.get(mid)
            if m is None or m.linked_objects:
                continue
            if getattr(m, "importance", "") in self._HIGH_IMPORTANCE or param_objs:
                m.linked_objects = all_objs[:6]

    # -- communication -----------------------------------------------------
    def _send(self, w, agent_id, channel, text, tick, importance="useful", attachments=None):
        ch = channel or "team_general"
        if ch not in w.comm.channels:
            ch = "team_general"
        return w.comm.send_message(sender_id=agent_id, channel_id=ch, text=text, tick=tick,
                                   importance=importance, attachments=attachments)

    def _h_send_message(self, w, aid, p, res, tick):
        m = self._send(w, aid, p.get("channel_id"), p.get("text", "(update)"), tick,
                       p.get("importance", "useful"))
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        for r in m.recipients:
            res.graph_edges.append((m.message_id, "received", r))
    _h_send_async_update = _h_send_message
    _h_announce_payroll_delay = _h_send_message

    def _h_reply_thread(self, w, aid, p, res, tick):
        m = self._send(w, aid, p.get("channel_id"), "re: " + p.get("text", "ack"), tick)
        res.created_objects.append(m.message_id)

    def _h_ask_for_clarification(self, w, aid, p, res, tick):
        m = self._send(w, aid, p.get("channel_id"), "could you clarify?", tick, "decision_relevant")
        res.created_objects.append(m.message_id)
    _h_ask_for_help = _h_ask_for_clarification

    def _h_acknowledge_message(self, w, aid, p, res, tick):
        ok = w.comm.acknowledge(aid, p.get("message_id", ""))
        res.success = ok
        if ok:
            res.modified_objects.append(p["message_id"])
            res.graph_edges.append((aid, "read", p["message_id"]))

    # -- O1.7 policy-grounded text + write-back (§24-§27) -------------------
    def _text(self, w, aid, *, speech_act, channel, target_ids, tick, urgency="normal",
              summary="", due_tick=None):
        """Appraise -> intent -> semantic plan -> style -> realize (template/LLM)
        -> validate -> send as a real message. Returns (output, message, appraisal)."""
        from types import SimpleNamespace
        from environments.org_env.runtime_adapter.text_layer import (
            build_communication_style, build_semantic_plan, build_text_intent, realize_text)
        agent = w.agents[aid]
        loop = w._loop or {}
        appraisal = None
        tids = [t for t in (target_ids or []) if t]
        if tids and loop.get("object_appraiser") is not None:
            appraisal = loop["object_appraiser"].appraise(agent_id=aid, target_object_id=tids[0],
                                                          world=w, tick=tick)
        intent = build_text_intent(agent_id=aid, speech_act=speech_act, channel_id=channel,
                                   target_object_ids=tids, urgency=urgency, summary=summary)
        plan = build_semantic_plan(intent, appraisal, due_tick=due_tick)
        ctx = SimpleNamespace(self_state=dict(agent.vitals.variables), visible_object_ids=set(tids))
        from environments.org_env.experiments.ablations import (
            PROFILE_POLICY,
            mechanism_disabled,
        )
        use_profile_conditioning = bool(
            getattr(w, "profile_conditioning_enabled", True)
            and not mechanism_disabled(w, PROFILE_POLICY)
        )
        style = build_communication_style(
            agent,
            speech_act,
            ctx,
            use_profile_conditioning=use_profile_conditioning,
        )
        client = getattr(w, "llm_client", None)
        if client is not None:        # unified surface realization via OrgLLMClient
            from environments.org_env.llm.surface_realizer import SurfaceRealizer
            sr = SurfaceRealizer().realize(
                actor=aid, speech_act=speech_act, target=(tids[0] if tids else None),
                reason=summary or plan.required_points and "; ".join(plan.required_points) or speech_act,
                tone={"directness": getattr(style, "directness", 0.5)},
                channel_id=channel, client=client)
            out = SimpleNamespace(surface_text=sr.get("surface_text", ""),
                                  validation_status="llm" if not sr.get("contains_new_facts") else "llm_flagged")
            if not out.surface_text:   # empty LLM -> template fallback
                out = realize_text(intent, plan, style, kernel=None,
                                   validator=loop.get("text_validator"), context=ctx)
        else:
            out = realize_text(intent, plan, style, kernel=loop.get("text_kernel"),
                               validator=loop.get("text_validator"), context=ctx)
        m = self._send(w, aid, channel, out.surface_text, tick, urgency_to_importance(urgency))
        # O1.7 analysis logs: every grounded utterance + any appraisal it used
        w.text_generation_log.append(
            {"tick": tick, "agent": aid, "speech_act": speech_act,
             "text_action_type": intent.text_action_type, "validation_status": out.validation_status,
             "chars": len(out.surface_text), "target": tids[:1]})
        if appraisal is not None:
            w.object_appraisal_log.append(
                {"tick": tick, "appraiser": aid, "object_id": appraisal.target_object_id,
                 "object_type": appraisal.target_object_type, "risk": appraisal.risk_level,
                 "issue_tags": list(appraisal.issue_tags), "method": appraisal.method})
        return out, m, appraisal

    def _h_challenge_result(self, w, aid, p, res, tick):
        rid = p.get("result_id") or p.get("target_id") or p.get("object_id")
        out, m, appr = self._text(w, aid, speech_act="challenge_result",
                                  channel=p.get("channel_id", "experiments"),
                                  target_ids=[rid], tick=tick, urgency="high")
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        tags = appr.issue_tags if appr else []
        # v8d P1c: do not re-litigate a result whose same-problem dispute already RESOLVED
        # with evidence (this is what spawned 13 disputes on result_3). Only re-open on a
        # genuine regression (handled elsewhere); otherwise the challenge is a no-op message.
        prior = w.commitment_registry.find_dispute_any_status(rid or "", tags)
        if prior is not None and prior.status == "resolved":
            res.events.append({"type": "claim_dispute_event", "subtype": "already_resolved",
                               "agent_id": aid, "tick": tick, "dispute_id": prior.dispute_id,
                               "target": rid, "resolution": prior.resolution})
            return
        # #4: support the existing dispute on this result instead of forking a new one.
        existing = w.commitment_registry.find_open_dispute(rid or "", tags)
        if existing is not None and aid != existing.challenger_id:
            if aid not in existing.supporter_ids:
                existing.supporter_ids.append(aid)
                existing.support_count = 1 + len(existing.supporter_ids)
            existing.evidence_request_count += 1
            existing.last_activity_tick = tick
            res.events.append({"type": "claim_dispute_event", "subtype": "supported", "agent_id": aid,
                               "tick": tick, "dispute_id": existing.dispute_id, "target": rid,
                               "support_count": existing.support_count})
            res.events.append({"type": "speech_act_event", "subtype": "challenge_result",
                               "agent_id": aid, "tick": tick})
            res.graph_edges.append((aid, "supported", existing.dispute_id))
            return
        d = w.commitment_registry.add_dispute(
            challenger_id=aid, target_object_id=rid or "", target_object_type="result",
            owner_id=w._result_owner(rid), reason=", ".join(tags) or "evidence/reproduction missing",
            claim_summary=out.surface_text[:120], issue_tags=tags,
            created_tick=tick, last_activity_tick=tick, source_message_id=m.message_id)
        res.created_objects.append(d.dispute_id)
        res.events.append({"type": "claim_dispute_event", "agent_id": aid, "tick": tick,
                           "dispute_id": d.dispute_id, "target": rid})
        res.events.append({"type": "speech_act_event", "subtype": "challenge_result",
                           "agent_id": aid, "tick": tick})
        if rid:
            res.graph_edges.append((aid, "challenged", rid))
        res.graph_edges.append((aid, "disputed", d.dispute_id))

    def _h_promise_work(self, w, aid, p, res, tick):
        tid = p.get("task_id") or p.get("object_id")
        due = p.get("due_tick", tick + int(p.get("due_in", 6)))
        out, m, _ = self._text(w, aid, speech_act="promise_work", channel=p.get("channel_id", "team_general"),
                               target_ids=[tid], tick=tick, summary=p.get("summary", "deliver the task"),
                               due_tick=due)
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        c = w.commitment_registry.add_commitment(
            agent_id=aid, description=out.surface_text[:120], target_object_id=tid,
            linked_task_id=tid, created_tick=tick, due_tick=due, source_message_id=m.message_id)
        res.created_objects.append(c.commitment_id)
        res.events.append({"type": "commitment_event", "agent_id": aid, "tick": tick,
                           "commitment_id": c.commitment_id})
        res.events.append({"type": "speech_act_event", "subtype": "promise_work",
                           "agent_id": aid, "tick": tick})
        if tid:
            res.graph_edges.append((aid, "promised", tid))
        res.graph_edges.append((aid, "committed_to", c.commitment_id))

    def _h_ask_for_review(self, w, aid, p, res, tick):
        oid = p.get("pr_id") or p.get("object_id") or p.get("doc_id")
        target_agent = p.get("target_agent") or p.get("reviewer")
        out, m, _ = self._text(w, aid, speech_act="ask_for_review", channel=p.get("channel_id", "engineering"),
                               target_ids=[oid], tick=tick)
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        r = w.commitment_registry.add_request(
            requester_id=aid, target_agent_id=target_agent, action_requested="review",
            target_object_id=oid, created_tick=tick, source_message_id=m.message_id)
        res.created_objects.append(r.request_id)
        res.events.append({"type": "requested_action_event", "agent_id": aid, "tick": tick,
                           "request_id": r.request_id, "object_id": oid})
        res.events.append({"type": "speech_act_event", "subtype": "ask_for_review",
                           "agent_id": aid, "tick": tick})
        if target_agent:
            res.graph_edges.append((aid, "requested_review_from", target_agent))
        if oid:
            res.graph_edges.append((r.request_id, "requested", oid))

    def _h_request_reproduction(self, w, aid, p, res, tick):
        oid = p.get("result_id") or p.get("object_id")
        out, m, appr = self._text(w, aid, speech_act="request_reproduction",
                                  channel=p.get("channel_id", "experiments"), target_ids=[oid], tick=tick)
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        r = w.commitment_registry.add_request(requester_id=aid, action_requested="reproduction",
                                              target_object_id=oid, created_tick=tick,
                                              source_message_id=m.message_id)
        res.created_objects.append(r.request_id)
        res.events.append({"type": "requested_action_event", "agent_id": aid, "tick": tick,
                           "request_id": r.request_id, "object_id": oid})
        if oid:
            res.graph_edges.append((r.request_id, "requested", oid))
    _h_ask_for_evidence = _h_request_reproduction

    def _h_warn_about_risk(self, w, aid, p, res, tick):
        oid = p.get("object_id") or p.get("pr_id") or p.get("result_id")
        out, m, _ = self._text(w, aid, speech_act="warn_about_risk",
                               channel=p.get("channel_id", "team_general"), target_ids=[oid], tick=tick,
                               urgency="high", summary=p.get("summary", "a risk worth flagging"))
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        res.events.append({"type": "speech_act_event", "subtype": "warn_about_risk",
                           "agent_id": aid, "tick": tick})
        if oid:
            res.graph_edges.append((aid, "warned_about", oid))

    def _h_speak(self, w, aid, p, res, tick, speech_act=None):
        """Generic grounded speech act (apologize / deescalate / explain_delay /
        push_team / coordinate_followup / suggest_rewrite / share_result / ...)."""
        sa = speech_act or p.get("speech_act", "send_async_update")
        oid = p.get("object_id") or p.get("doc_id") or p.get("result_id") or p.get("task_id")
        out, m, _ = self._text(w, aid, speech_act=sa, channel=p.get("channel_id", "team_general"),
                               target_ids=[oid] if oid else [], tick=tick,
                               summary=p.get("summary", sa.replace("_", " ")))
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        res.events.append({"type": "speech_act_event", "subtype": sa, "agent_id": aid, "tick": tick})

    def _h_suggest_rewrite(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="suggest_rewrite")
    def _h_explain_delay(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="explain_delay")
    def _h_apologize(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="apologize")
    def _h_deescalate(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="deescalate")
    def _h_push_team(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="push_team")
    def _h_coordinate_followup(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="coordinate_followup")
    def _h_commit_to_direction(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="commit_to_direction")
    def _h_defend_demo_progress(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="defend_demo_progress")
    def _h_share_result(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="share_result")
    def _h_share_external_signal(self, w, aid, p, res, tick):
        self._h_speak(w, aid, p, res, tick, speech_act="share_external_signal")

    def _h_post_company_update(self, w, aid, p, res, tick):
        out, m, _ = self._text(w, aid, speech_act="post_company_update",
                               channel=p.get("channel_id", "team_general"), target_ids=[], tick=tick,
                               summary=p.get("summary", "company progress update"))
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        res.events.append({"type": "external_signal_event", "subtype": "company_update",
                           "agent_id": aid, "tick": tick})

    def _h_respond_to_public_comment(self, w, aid, p, res, tick):
        """Reply publicly on the external community feed (回帖): the company engages the external
        conversation, creating a public company-authored Post in reply to an external post."""
        from environments.org_env.backend.community.objects import Post
        target_pid = p.get("post_id", "")
        target = w.community.posts.get(target_pid)
        topic = getattr(target, "topic", "") if target else "company_update"
        w._extseq = getattr(w, "_extseq", 0) + 1
        pid = f"post_company_reply_{w._extseq}"
        text = p.get("text") or (
            f"Company reply re: {topic} — we hear the concern and are addressing it; "
            "transparency on what we can and can't do yet.")
        w.community.posts[pid] = Post(
            post_id=pid, author_id=aid, topic=topic or "company_update", content_summary=text,
            stance=0.2, credibility=0.6, reach=120, created_tick=tick, visibility="public")
        if target is not None:
            target.comments = int(getattr(target, "comments", 0) or 0) + 1
        res.created_objects.append(pid)
        res.events.append({"type": "external_signal_event", "subtype": "public_reply",
                           "agent_id": aid, "post_id": pid, "in_reply_to": target_pid, "tick": tick})

    def _h_formal_pr_review(self, w, aid, p, res, tick):
        """Blocking, thorough review: appraise -> generate a grounded review COMMENT
        via the text layer -> request_changes (if issues) or approve (if clean)."""
        pr_id = p.get("pr_id", "")
        appr = None
        if pr_id and w._loop.get("object_appraiser"):
            appr = w._loop["object_appraiser"].appraise(agent_id=aid, target_object_id=pr_id,
                                                        world=w, tick=tick)
        needs_changes = appr is not None and (bool(appr.issue_tags) or appr.risk_level in ("medium", "high"))
        sa = "request_changes" if needs_changes else "approve_with_note"
        out, m, _ = self._text(w, aid, speech_act=sa, channel=p.get("channel_id", "engineering"),
                               target_ids=[pr_id], tick=tick)
        res.created_objects.append(m.message_id); res.messages.append(m.message_id)
        comment = out.surface_text[:200]
        if needs_changes:
            ok = w.repo_system.request_changes(reviewer_id=aid, pr_id=pr_id, comment=comment)
            if ok:
                res.modified_objects.append(pr_id)
                res.events.append({"type": "repo_event", "subtype": "changes_requested",
                                   "agent_id": aid, "tick": tick})
                res.graph_edges.append((aid, "requested_changes", pr_id))
                self._maybe_review_protocol(w, aid, tick, res)
        else:
            ok = w.repo_system.approve_pr(reviewer_id=aid, pr_id=pr_id)
            if ok:
                res.modified_objects.append(pr_id)
                res.events.append({"type": "repo_event", "subtype": "pr_reviewed", "agent_id": aid,
                                   "tick": tick, "pr_id": pr_id})
                res.graph_edges.append((aid, "reviewed", pr_id))
                self._maybe_review_protocol(w, aid, tick, res)
        res.events.append({"type": "speech_act_event", "subtype": "formal_pr_review",
                           "agent_id": aid, "tick": tick})

    def _share_object(self, w, aid, channel, object_id, atype, tick, title=""):
        ch = channel if channel in w.comm.channels else "team_general"
        return w.comm.share_object(sender_id=aid, channel_id=ch, object_id=object_id,
                                   attachment_type=atype, title=title, tick=tick)

    def _h_share_external_post(self, w, aid, p, res, tick):
        m = self._share_object(w, aid, p.get("channel_id", "external_signals"),
                               p.get("post_id", ""), "external_post", tick)
        res.created_objects.append(m.message_id)
        res.events.append({"type": "external_signal_event", "post_id": p.get("post_id"),
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "shared", p.get("post_id")))

    def _h_share_sandbox_output(self, w, aid, p, res, tick):
        m = self._share_object(w, aid, p.get("channel_id", "experiments"),
                               p.get("result_id", ""), "json_result", tick)
        res.created_objects.append(m.message_id)
        res.graph_edges.append((aid, "shared", p.get("result_id")))
    _h_share_json_result = _h_share_sandbox_output
    _h_share_experiment_result = _h_share_sandbox_output

    def _h_share_doc(self, w, aid, p, res, tick):
        m = self._share_object(w, aid, p.get("channel_id"), p.get("doc_id", ""), "doc", tick)
        res.created_objects.append(m.message_id)
    _h_share_file = _h_share_doc
    _h_share_search_result = _h_share_doc

    # -- tasks -------------------------------------------------------------
    def _h_work_on_task(self, w, aid, p, res, tick, *, effort_kind: str = "work_on_task"):
        """Spend effort on a task and record it as auditable evidence.

        This is the organization's highest-volume action and used to be inert with
        respect to the work itself: it appended an event and a graph edge but never
        touched the task, so effort was invisible to every downstream measure and a
        task could be worked on indefinitely while staying OPEN with
        progress_score 0.

        It still must NOT hand out completion credit directly - progress_score is
        derived from auditable checks (artifact revised, gaps cleared, a second
        party involved, multiple kinds of evidence) precisely so that repeating one
        action cannot fake a finished task. So this records effort and lets the
        existing scorer judge it.
        """
        tid = p.get("task_id")
        t = w.tasks.get(tid)
        if t is None:
            res.success = False; res.failure_reason = "no_task"; return
        if aid not in w.agents[aid].active_tasks:
            w.agents[aid].active_tasks.append(tid)

        from environments.org_env.backend.entities.work import TaskStatus

        # An untouched task becomes IN_PROGRESS on first real effort; later stages
        # (review/merge/release) are owned by their own transitions and never
        # regressed here.
        if getattr(t, "status", None) == TaskStatus.OPEN:
            t.status = TaskStatus.IN_PROGRESS
        t.actual_effort = float(getattr(t, "actual_effort", 0.0) or 0.0) + 1.0
        if not getattr(t, "owner_id", None):
            t.owner_id = aid
            owners = getattr(getattr(w, "board", None), "owners", None)
            if owners is not None:
                owners[tid] = aid
        evidence = getattr(t, "progress_evidence", None)
        if evidence is not None:
            evidence.append({
                "tick": tick, "actor": aid, "action": effort_kind,
                "evidence_type": "effort_applied",
            })
        res.modified_objects.append(tid)
        res.state_delta["task_status"] = str(getattr(t, "status", ""))
        res.state_delta["actual_effort"] = t.actual_effort
        res.events.append({"type": "task_progress_event", "task_id": tid, "agent_id": aid,
                           "tick": tick, "subtype": effort_kind,
                           "status": str(getattr(t, "status", "")),
                           "actual_effort": t.actual_effort})
        res.graph_edges.append((aid, "worked_on", tid))

    def _h_work_overtime(self, w, aid, p, res, tick):
        self._h_work_on_task(w, aid, p, res, tick, effort_kind="work_overtime")

    def _h_weekend_work(self, w, aid, p, res, tick):
        self._h_work_on_task(w, aid, p, res, tick, effort_kind="weekend_work")

    def _h_debug_code(self, w, aid, p, res, tick):
        self._h_work_on_task(w, aid, p, res, tick, effort_kind="debug_code")

    def _h_pick_task(self, w, aid, p, res, tick):
        tid = p.get("task_id")
        t = w.tasks.get(tid)
        if t is None or t.owner_id:
            res.success = False; res.failure_reason = "unavailable"; return
        t.owner_id = aid; w.board.owners[tid] = aid
        res.modified_objects.append(tid)
        res.events.append({"type": "task_progress_event", "subtype": "owned", "task_id": tid,
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "owned", tid))

    def _h_assign_task_owner(self, w, aid, p, res, tick):
        tid, owner = p.get("task_id"), p.get("owner_id", aid)
        t = w.tasks.get(tid)
        if t is None:
            res.success = False; return
        t.owner_id = owner; w.board.owners[tid] = owner
        res.modified_objects.append(tid)
        res.events.append({"type": "task_progress_event", "subtype": "assigned", "task_id": tid,
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "assigned", tid))

    def _h_update_task_status(self, w, aid, p, res, tick):
        tid = p.get("task_id"); t = w.tasks.get(tid)
        if t is None:
            res.success = False; return
        res.modified_objects.append(tid)
        res.events.append({"type": "task_progress_event", "task_id": tid, "agent_id": aid, "tick": tick})
    _h_update_tracker = _h_update_task_status
    _h_inspect_task_board = _h_update_task_status

    def _h_create_issue(self, w, aid, p, res, tick):
        from environments.org_env.backend.entities import Issue
        title = p.get("title", "issue")
        source = p.get("source", "customer")
        post_id = p.get("post_id") or p.get("signal_id") or p.get("source_signal_id")
        # v6 P0.3: cluster repeat customer pain onto the existing issue instead of re-filing.
        dup = self._find_duplicate_issue(w, title, source, post_id)
        if dup is not None:
            if aid not in dup.supporting_agent_ids:
                dup.supporting_agent_ids.append(aid)
                dup.support_count = len(dup.supporting_agent_ids)
            if post_id and post_id not in dup.source_signal_ids:
                dup.source_signal_ids.append(post_id)
            dup.last_seen_tick = tick
            res.modified_objects.append(dup.issue_id)
            res.events.append({"type": "task_progress_event", "subtype": "issue_support",
                               "issue_id": dup.issue_id, "agent_id": aid, "tick": tick,
                               "support_count": dup.support_count})
            res.graph_edges.append((aid, "supported", dup.issue_id))
            return
        iid = f"issue_{len(w.issues)}"
        issue = Issue(issue_id=iid, title=title, owner_id=aid, source=source, created_tick=tick,
                      topic=p.get("topic", ""), last_seen_tick=tick, supporting_agent_ids=[aid])
        if post_id:
            issue.source_signal_ids = [post_id]
        w.issues[iid] = issue
        res.created_objects.append(iid)
        res.events.append({"type": "task_progress_event", "subtype": "issue", "issue_id": iid,
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "created", iid))

    @staticmethod
    def _find_duplicate_issue(w, title: str, source: str, post_id):
        """An existing OPEN issue that captures the same customer pain — matched by the
        originating signal id, else same source + semantically-equal title (v6 P0.3)."""
        from environments.org_env.llm.semantic_dedup import equivalent
        client = getattr(w, "llm_client", None)
        for iss in (getattr(w, "issues", {}) or {}).values():
            if getattr(iss, "status", "open") not in ("open", "triaged", "in_progress"):
                continue
            if post_id and post_id in (getattr(iss, "source_signal_ids", []) or []):
                return iss
            if (getattr(iss, "source", "") or "") != (source or ""):
                continue
            # identical/near-identical titles settle deterministically (the v5 case);
            # the gray zone defers to the LLM when one is wired.
            if equivalent(w, [title], [getattr(iss, "title", "") or ""],
                          kind="customer issue", client=client, lo=0.30):
                return iss
        return None

    # -- sandbox / experiment ---------------------------------------------
    def _h_run_cheap_pilot(self, w, aid, p, res, tick):
        agent = w.agents[aid]
        # max of both channels, not fallback-on-absence: growth writing the first
        # eval_design delta must never DROP the read below the coding-skill proxy.
        skill = max(agent.skill("experimental_design", 0.0), agent.skill("core_coding", 0.4))
        job = w.sandbox_system.run_job(agent_id=aid, job_type="run_cheap_pilot",
                                       experiment_id=p.get("experiment_id", "reliability_v0"),
                                       skill=skill, difficulty=0.4,
                                       reproducible_config=agent.skill("reproducibility_tracking", 0.3) > 0.5,
                                       seed=w.scenario.seed + tick, cost=8.0, tick=tick)
        # v8g P1: a "cheap" pilot's token cost (the per-action charge in _log_events adds the
        # rest); the old 8.0 here dominated daily burn once everything became token-metered.
        ce = w.budget_system.charge(agent_id=aid, action_type="run_cheap_pilot", base_cost=1.5,
                                    experiment_id=p.get("experiment_id"), tick=tick)
        res.cost_events.append(ce.cost_event_id)
        res.created_objects.append(job.job_id)
        if job.result_id:
            res.created_objects.append(job.result_id)
            res.graph_edges.append((aid, "produced", job.result_id))
        res.events.append({"type": "experiment_event", "job_id": job.job_id, "status": job.status,
                           "agent_id": aid, "tick": tick, "result_id": job.result_id})
        res.success = job.status == "completed"

    def _h_run_experiment(self, w, aid, p, res, tick):
        self._h_run_cheap_pilot(w, aid, p, res, tick)
    _h_run_paper_baseline = _h_run_experiment
    _h_run_script = _h_run_cheap_pilot

    def _h_export_result_to_tracker(self, w, aid, p, res, tick):
        rid = p.get("result_id")
        r = w.sandbox_system.export_result_to_tracker(rid) if rid else None
        if r is None:
            res.success = False; res.failure_reason = "no_result"; return
        self._ensure_tracker(w, aid, tick)
        res.modified_objects.append(rid)
        res.modified_objects.append("doc_experiment_tracker")   # links result→tracker for the episode chain
        res.events.append({"type": "experiment_event", "subtype": "logged_to_tracker",
                           "result_id": rid, "doc_id": "doc_experiment_tracker",
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((rid, "exported_to_tracker", "doc_experiment_tracker"))
        from environments.org_env.experiments.ablations import (
            INSTITUTIONALIZATION,
            mechanism_disabled,
        )
        if (
            not mechanism_disabled(w, INSTITUTIONALIZATION)
            and "proto_experiment_logging" in w.protocol_registry.protocols
        ):
            w.protocol_registry.use(aid, "proto_experiment_logging", tick=tick)
            res.events.append({"type": "protocol_use_event", "protocol_id": "proto_experiment_logging",
                               "agent_id": aid, "tick": tick})

    def _ensure_tracker(self, w, aid, tick):
        from environments.org_env.backend.entities import Document
        if "doc_experiment_tracker" not in w.documents:
            w.documents["doc_experiment_tracker"] = Document(
                doc_id="doc_experiment_tracker", title="Experiment Tracker",
                doc_type="experiment_tracker", author_id=aid, owner_id=aid, visibility="team")

    def _h_create_experiment_tracker(self, w, aid, p, res, tick):
        from environments.org_env.experiments.ablations import (
            INSTITUTIONALIZATION,
            mechanism_disabled,
        )
        existed = "doc_experiment_tracker" in w.documents
        self._ensure_tracker(w, aid, tick)
        if not existed:
            res.created_objects.append("doc_experiment_tracker")
            if (
                getattr(w, "institutionalization_enabled", True)
                and not mechanism_disabled(w, INSTITUTIONALIZATION)
                and "proto_experiment_logging" not in w.protocol_registry.protocols
            ):
                # propose only — adoption emerges from repeated use/support (§34.14/15),
                # NOT from creating the tracker artifact.
                w.protocol_registry.propose(proposer_id=aid, protocol_type="experiment_logging",
                                            rule_summary="log every experiment result to the tracker",
                                            scope="experiment", tick=tick,
                                            protocol_id="proto_experiment_logging")
                res.events.append({"type": "protocol_proposal_event",
                                   "protocol_id": "proto_experiment_logging", "agent_id": aid, "tick": tick})
        res.events.append({"type": "file_share_event", "subtype": "tracker", "agent_id": aid, "tick": tick})

    # -- repo --------------------------------------------------------------
    def _h_create_branch(self, w, aid, p, res, tick):
        b = w.repo_system.create_branch(aid, linked_task=p.get("linked_task"), tick=tick)
        w.personal[aid].local_branch_ids.append(b.branch_id)
        res.created_objects.append(b.branch_id)
        res.events.append({"type": "repo_event", "subtype": "branch", "agent_id": aid, "tick": tick})

    def _h_commit_changes(self, w, aid, p, res, tick):
        bid = p.get("branch_id") or self._own_branch(w, aid)
        if not bid:
            b = w.repo_system.create_branch(aid, linked_task=p.get("task_id"), tick=tick)
            bid = b.branch_id; res.created_objects.append(bid)
        w.repo_system.edit_file(aid, bid)
        agent = w.agents[aid]
        has_tests = agent.skill("test_writing", 0.3) >= 0.4
        qflags = [] if has_tests else ["missing_tests"]
        c = w.repo_system.commit_changes(agent_id=aid, branch_id=bid, message=p.get("message", "wip"),
                                         changed_files=["mod.py"], tick=tick, quality_flags=qflags,
                                         test_status="pass" if has_tests else "missing",
                                         risk_level=_commit_risk_level(w, [], ["mod.py"]),
                                         linked_task_id=p.get("task_id"))
        if c:
            res.created_objects.append(c.commit_id)
            res.events.append({"type": "repo_event", "subtype": "commit", "agent_id": aid, "tick": tick})
            res.graph_edges.append((aid, "created", c.commit_id))
    _h_edit_file = _h_commit_changes

    def _own_branch(self, w, aid):
        for bid, b in w.repo_system.repo.branches.items():
            if b.owner_id == aid and b.status.value in ("clean", "dirty", "ready_for_pr"):
                return bid
        return None

    @staticmethod
    def _modules_on(w, artifact_ids) -> set:
        """The product code among these artifacts — what a request is really about."""
        arts = getattr(w, "product_artifacts", {}) or {}
        out = set()
        for aid in artifact_ids or ():
            art = arts.get(aid)
            path = str(getattr(art, "linked_file_path", "") or "") if art else ""
            if path.endswith(".py") and getattr(art, "artifact_type", "") != "issue":
                out.add(aid)
        return out

    @staticmethod
    def _branch_modules(w, bid) -> set:
        repo = w.repo_system.repo
        b = repo.branches.get(bid)
        carried = set()
        for cid in (getattr(b, "commit_ids", []) or []) if b else ():
            commit = repo.commits.get(cid)
            carried.update(getattr(commit, "artifact_ids", []) or [])
        return OrgExecutionAdapter._modules_on(w, carried)

    @staticmethod
    def _open_requests(w):
        repo = w.repo_system.repo
        return [pr for pr in repo.pull_requests.values()
                if str(getattr(getattr(pr, "status", None), "value",
                               getattr(pr, "status", ""))) not in ("merged", "closed")]

    def _carrying_nothing(self, w, aid) -> bool:
        """This agent holds no live branch and no open request."""
        repo = w.repo_system.repo
        if any(getattr(b, "owner_id", None) == aid
               and "MERGED" not in str(getattr(b, "status", "")).upper()
               for b in (getattr(repo, "branches", {}) or {}).values()):
            return False
        return not any(getattr(pr, "author_id", None) == aid
                       for pr in self._open_requests(w))

    def _work_in_progress_refusal(self, w, aid, modules) -> str:
        """Why this work cannot become a new request yet, or "" if it can.

        Letting independent work leave a stuck request is right; letting it leave
        without limit is how a "one giant request" problem becomes a dozen tiny
        ones — neighbouring files in separate requests, each green alone and red
        once combined, review and CI cost multiplied, and a merged-request count
        that rewards splitting rather than delivering. So the pipe is opened, and
        then bounded: a request is one coherent change, and an organization
        carries only so many at once. At the limit the answer is to land, repair
        or drop something already in flight — which is the decision a queue is
        supposed to force.
        """
        # That decision is only available to someone who is carrying something.
        # Two agents finished 24 patches between them over 96 ticks and committed
        # none: every request in flight belonged to somebody else, the module
        # ceiling refused them a branch of their own, and the fixes those very
        # requests were failing for sat on the desk the whole time. A limit that
        # leaves you with nothing to land, repair or drop is not a queue, it is a
        # door.
        if self._carrying_nothing(w, aid):
            return ""
        mine = [pr for pr in self._open_requests(w) if getattr(pr, "author_id", None) == aid]
        if len(mine) >= _MAX_OPEN_REQUESTS_PER_AUTHOR:
            return (f"already carrying {len(mine)} open requests; land, repair or "
                    f"drop one before starting another")
        everyone = self._open_requests(w)
        if len(everyone) >= _MAX_OPEN_REQUESTS:
            return (f"the organization is carrying {len(everyone)} open requests; "
                    f"the queue has to move before it grows")
        for module in modules:
            touching = [pr for pr in everyone
                        if module in self._modules_on(
                            w, [a for _p, a in w.repo_system.merged_commit_patches(pr)])]
            if len(touching) >= _MAX_OPEN_REQUESTS_PER_MODULE:
                path = str(getattr((getattr(w, "product_artifacts", {}) or {}).get(module),
                                   "linked_file_path", module))
                return (f"{len(touching)} open requests already change {path}; a third "
                        f"would collide with them on merge")
        return ""

    def _h_open_pr(self, w, aid, p, res, tick):
        from environments.org_env.backend.repo.workflow import active_branches
        bid = p.get("source_branch")
        if not bid:
            ready = [b.branch_id for b in active_branches(w, aid) if b.commit_ids]
            bid = ready[0] if ready else None
        if not bid:
            res.success = False; res.failure_reason = "no_branch"; return
        branch = w.repo_system.repo.branches.get(bid)
        if branch is None or not getattr(branch, "commit_ids", None):
            res.success = False; res.failure_reason = "nothing_to_request"; return
        already = next((pr for pr in w.repo_system.repo.pull_requests.values()
                        if getattr(pr, "source_branch", "") == bid
                        and getattr(pr.status, "value", str(pr.status))
                        not in ("merged", "closed")), None)
        if already is not None:
            # A request is the branch. Pushing to it updates the request, which
            # is what a second request on the same branch was trying to be.
            res.success = False
            res.failure_reason = "already_requested"
            res.state_delta["pr_id"] = already.pr_id
            return
        # §11.1: route the review to whoever has earned authority in the PR's domain
        # (authority x availability softmax), not a fixed founder/cofounder list.
        reviewers = p.get("reviewers")
        if not reviewers:
            from environments.org_env.growth.authority import select_reviewers
            dmn = "engineering_execution"          # PRs are code -> engineering domain
            reviewers = select_reviewers(w, domain=dmn, exclude=[aid], k=1)
        if not reviewers and len(getattr(w, "agents", {}) or {}) <= 1:
            # Nobody else exists to review this. The world sweep already
            # self-reviews such a PR after the review latency, so the outcome is
            # unchanged; what was missing is that the agent could never CHOOSE
            # to review its own work, because the review candidates key off
            # ``prs_awaiting_my_review``, which keys off the reviewer list. A
            # one-person organization's review is its own - that is the honest
            # difference from a staffed one - but it has to be an act, not
            # something that only ever happens to it while it looks away.
            reviewers = [aid]
        pr = w.repo_system.open_pr(agent_id=aid, source_branch=bid, reviewers=reviewers)
        pr.opened_tick = tick
        # v6 P0.4: carry the task/issue linkage from the branch's commits onto the PR.
        tset, iset = [], []
        for cid in pr.commit_ids:
            c = w.repo_system.repo.commits.get(cid)
            if c is None:
                continue
            for tid in (getattr(c, "linked_task_ids", []) or ([c.linked_task_id] if c.linked_task_id else [])):
                if tid and tid not in tset:
                    tset.append(tid)
            for iid in getattr(c, "linked_issue_ids", []) or []:
                if iid not in iset:
                    iset.append(iid)
        pr.linked_task_ids, pr.linked_issue_ids = tset, iset
        pr.linked_task = pr.linked_task or (tset[0] if tset else None)
        pr.linked_issue = pr.linked_issue or (iset[0] if iset else None)
        # spec #2: opening a PR moves its implemented tasks into review_pending.
        from environments.org_env.backend.entities import TaskStatus
        for tid in tset:
            t = w.tasks.get(tid)
            if t is not None and getattr(t.status, "value", str(t.status)) in (
                    "in_progress", "implementation_done"):
                t.status = TaskStatus.REVIEW_PENDING
        res.created_objects.append(pr.pr_id)
        res.events.append({"type": "repo_event", "subtype": "pr_opened", "agent_id": aid, "tick": tick,
                           "pr_id": pr.pr_id, "linked_task": pr.linked_task, "linked_issue": pr.linked_issue})
        for r in reviewers:
            res.graph_edges.append((pr.pr_id, "review_requested", r))

    @staticmethod
    def _repo_linkage(w, artifact_ids):
        """v6 P0.4: tasks + issues a commit/PR touching these artifacts should carry, so
        the repo chain is traceable (commit/PR -> task -> issue) and merge can credit them."""
        arts = getattr(w, "product_artifacts", {}) or {}
        task_ids, issue_ids = [], []
        for a in artifact_ids:
            art = arts.get(a)
            if art is None:
                continue
            for tid in getattr(art, "linked_task_ids", []) or []:
                if tid not in task_ids:
                    task_ids.append(tid)
            if getattr(art, "artifact_type", "") == "issue" and a not in issue_ids:
                issue_ids.append(a)
        for tid in task_ids:
            t = (getattr(w, "tasks", {}) or {}).get(tid)
            for iid in (getattr(t, "linked_issues", []) or []) if t else []:
                if iid not in issue_ids:
                    issue_ids.append(iid)
        return task_ids, issue_ids

    def _h_review_pr(self, w, aid, p, res, tick):
        ok = w.repo_system.approve_pr(reviewer_id=aid, pr_id=p.get("pr_id", ""), tick=tick)
        res.success = ok
        if ok:
            res.modified_objects.append(p["pr_id"])
            res.events.append({"type": "repo_event", "subtype": "pr_reviewed", "agent_id": aid,
                               "tick": tick, "pr_id": p["pr_id"]})
            res.graph_edges.append((aid, "reviewed", p["pr_id"]))
            self._maybe_review_protocol(w, aid, tick, res)
    _h_approve_pr = _h_review_pr

    def _h_request_changes(self, w, aid, p, res, tick):
        ok = w.repo_system.request_changes(reviewer_id=aid, pr_id=p.get("pr_id", ""),
                                           comment=p.get("comment", "changes"), tick=tick)
        res.success = ok
        if ok:
            res.modified_objects.append(p["pr_id"])
            res.events.append({"type": "repo_event", "subtype": "changes_requested", "agent_id": aid,
                               "tick": tick})
            res.graph_edges.append((aid, "requested_changes", p["pr_id"]))
            self._maybe_review_protocol(w, aid, tick, res)

    def _maybe_review_protocol(self, w, aid, tick, res):
        from environments.org_env.experiments.ablations import (
            INSTITUTIONALIZATION,
            mechanism_disabled,
        )
        if (
            not getattr(w, "institutionalization_enabled", True)
            or mechanism_disabled(w, INSTITUTIONALIZATION)
        ):
            return
        reg = w.protocol_registry
        if "proto_review_before_merge" not in reg.protocols:
            # propose only; repeated reviews (use) then establish + adopt it (§34.15)
            reg.propose(proposer_id=aid, protocol_type="review_before_merge",
                        rule_summary="no merge without a review", scope="repo", tick=tick,
                        protocol_id="proto_review_before_merge")
            res.events.append({"type": "protocol_proposal_event",
                               "protocol_id": "proto_review_before_merge", "agent_id": aid, "tick": tick})
        else:
            reg.use(aid, "proto_review_before_merge", tick=tick)
            res.events.append({"type": "protocol_use_event", "protocol_id": "proto_review_before_merge",
                               "agent_id": aid, "tick": tick})

    def _h_commit_patch(self, w, aid, p, res, tick):
        """Commit what is pending on a branch.

        A change already knows its branch: it was put there when it was made,
        by the work item it is for. So there is nothing to route here and no
        cap to apply -- a branch holds one work item, which is what the cap was
        trying to approximate by counting modules after the fact.
        """
        from environments.org_env.backend.repo.workflow import (
            branches_with_pending, clear_pending, pending_on,
        )
        bid = p.get("branch_id")
        if not bid or not pending_on(w, bid):
            ready = branches_with_pending(w, aid)
            bid = ready[0] if ready else None
        if not bid:
            res.success = False
            res.failure_reason = "no_uncommitted_patches"
            res.events.append({"type": "repo_event", "subtype": "commit_noop", "agent_id": aid, "tick": tick})
            return
        taking = pending_on(w, bid)
        patch_ids = [pid for pid, _ in taking]
        artifact_ids = [a for _, a in taking if a]
        repo = getattr(getattr(w, "repo_system", None), "repo", None)
        branch = (getattr(repo, "branches", {}) or {}).get(bid)
        if branch is None or str(getattr(branch, "owner_id", "") or "") != aid:
            res.success = False
            res.failure_reason = "commit_failed"
            res.state_delta["commit_error"] = "branch_missing_or_not_owned"
            res.events.append({
                "type": "repo_event", "subtype": "commit_failed",
                "branch_id": bid, "agent_id": aid, "tick": tick,
                "reason": "branch_missing_or_not_owned",
            })
            return
        agent = w.agents[aid]
        has_tests = agent.skill("test_writing", 0.3) >= 0.4
        qflags = [] if has_tests else ["missing_tests"]
        # v6 P0.4: carry the task/issue this work belongs to onto the commit (traceability).
        task_ids, issue_ids = self._repo_linkage(w, artifact_ids)
        repo_before = copy.deepcopy(repo)
        repo_seq_before = getattr(w.repo_system, "_seq", None)
        pending_before = copy.deepcopy(w.__dict__.get("_pending_by_branch", {}) or {})
        try:
            if not w.repo_system.edit_file(aid, bid):
                raise RuntimeError("branch_edit_failed")
            c = w.repo_system.commit_changes(
                agent_id=aid, branch_id=bid, message=f"apply {len(patch_ids)} patch(es)",
                changed_files=list(dict.fromkeys(artifact_ids)) or ["mod"], tick=tick,
                quality_flags=qflags, patch_ids=patch_ids, artifact_ids=artifact_ids,
                test_status="pass" if has_tests else "missing",
                risk_level=_commit_risk_level(w, artifact_ids, ["mod"]),
                linked_task_id=(task_ids[0] if task_ids else None))
            if c is None:
                raise RuntimeError("commit_returned_none")
            clear_pending(w, bid)
        except Exception as error:  # noqa: BLE001 - delivery transaction rollback
            w.repo_system.repo = _restore_snapshot_in_place(
                w.repo_system.repo, repo_before
            )
            if repo_seq_before is not None:
                w.repo_system._seq = repo_seq_before
            pending = w.__dict__.get("_pending_by_branch")
            if isinstance(pending, dict):
                _restore_snapshot_in_place(pending, pending_before)
            else:
                w.__dict__["_pending_by_branch"] = pending_before
            res.success = False
            res.failure_reason = "commit_failed"
            res.state_delta["commit_error"] = type(error).__name__
            res.events.append({
                "type": "repo_event", "subtype": "commit_failed",
                "branch_id": bid, "agent_id": aid, "tick": tick,
                "reason": type(error).__name__,
            })
            return
        res.state_delta["branch_id"] = bid
        c.linked_task_ids = task_ids
        c.linked_issue_ids = issue_ids
        for a in set(artifact_ids):
            art = w.product_artifacts.get(a)
            if art:
                if bid not in art.linked_branch_ids:
                    art.linked_branch_ids.append(bid)
                if c.commit_id not in art.linked_commit_ids:
                    art.linked_commit_ids.append(c.commit_id)
        res.created_objects.append(c.commit_id)
        res.events.append({"type": "repo_event", "subtype": "commit", "commit_id": c.commit_id,
                           "agent_id": aid, "tick": tick, "patch_ids": patch_ids})
        res.graph_edges.append((aid, "created", c.commit_id))

    def _h_push_commit(self, w, aid, p, res, tick):
        bid = p.get("branch_id") or self._own_branch(w, aid)
        b = w.repo_system.repo.branches.get(bid) if bid else None
        pushed = 0
        for cid in (b.commit_ids if b else []):
            c = w.repo_system.repo.commits.get(cid)
            if c and c.status == "local":
                c.status = "pushed"
                pushed += 1
        res.events.append({"type": "repo_event", "subtype": "push", "agent_id": aid, "tick": tick, "pushed": pushed})
        if pushed == 0:
            res.success = False
            res.failure_reason = "nothing_to_push"

    @staticmethod
    def _record_programbench_probe_evidence(outcome, res, aid: str, tick: int) -> None:
        """Promote the materializer's bounded/redacted report into action events."""
        report = outcome.get("programbench_public_probes")
        if not isinstance(report, dict) or report.get("schema_version") != (
            "programbench_public_probe_materialization_v1"
        ):
            return
        res.state_delta["programbench_public_probes"] = report
        for case in report.get("cases") or []:
            if not isinstance(case, dict):
                continue
            common = {
                "type": "repo_event",
                "subtype": "probe_execution",
                "probe_id": str(case.get("probe_id") or "")[:300],
                "definition_source": str(case.get("definition_source") or "")[:300],
                "definition": case.get("definition") or {},
                "case_index": case.get("case_index"),
                "input": case.get("input") or {},
                "matched": case.get("matched"),
                "infra_side": case.get("infra_side"),
                "agent_id": aid,
                "tick": tick,
            }
            for role in ("reference", "candidate"):
                side = case.get(role)
                if not isinstance(side, dict):
                    continue
                res.events.append({
                    **common,
                    "evaluation_role": role,
                    "status": str(side.get("status") or "invalid")[:80],
                    "stdout": side.get("stdout"),
                    "stderr": side.get("stderr"),
                    "exit": side.get("exit"),
                    "filesystem_effects": side.get("filesystem_effects"),
                    "reason": str(side.get("reason") or "")[:300],
                })

    def _h_run_public_tests(self, w, aid, p, res, tick):
        """Run the substrate's declared public test suite and report the result.

        This is the organization's only legitimate way to learn whether a patch
        actually fixed anything: hidden oracles are evaluator-only, so without
        this affordance every fix attempt is unverifiable and the backlog can
        never converge (observed: 336 ticks, 14 patches, 0 of 18 oracles fixed).
        The suite lives in the agent-visible starter repo, so no hidden-test
        information is disclosed.
        """
        from environments.org_env.product.materialize import (
            _repo_hash,
            run_public_tests,
        )

        # Record the tree we ran against before anything can return early. The
        # affordance is offered only while the tree differs from the last run,
        # and the two early exits below used to skip the bookkeeping, so a tree
        # whose suite could not start left the marker unset and the option was
        # re-offered every tick forever. Observed on a greenfield pack whose
        # stub modules raise on import, which makes collection fail: one agent
        # spent 144 ticks running the suite 72 times and wrote no code at all.
        # Re-running an unchanged tree tells the organization nothing, whether
        # the last attempt passed, failed, or never started.
        repo_hash = _repo_hash(w, prefer_mainline=False)
        w.__dict__["_public_tests_last_hash"] = repo_hash

        outcome = run_public_tests(w)
        # ProgramBench reports are already bounded and private-path-redacted by
        # the trusted materializer. Preserve per-role evidence even when one
        # side reports infrastructure failure and the aggregate action fails.
        self._record_programbench_probe_evidence(outcome, res, aid, tick)
        if not outcome.get("available"):
            res.success = False
            res.failure_reason = "no_public_tests_declared"
            res.events.append({"type": "repo_event", "subtype": "public_tests_unavailable",
                               "agent_id": aid, "tick": tick})
            return
        if outcome.get("error"):
            # Infrastructure, not product: never let it read as a failing suite.
            res.success = False
            res.failure_reason = "public_tests_infrastructure_error"
            res.events.append({"type": "repo_event", "subtype": "public_tests_infrastructure_error",
                               "agent_id": aid, "tick": tick,
                               "brief": str(outcome.get("error"))[:200]})
            return
        passed = bool(outcome.get("ok"))
        res.success = True
        res.state_delta["public_tests_passed"] = passed
        res.state_delta["public_tests_summary"] = outcome.get("summary", "")
        if not passed:
            res.state_delta["public_tests_failed"] = list(outcome.get("failed_tests") or [])
        from environments.org_env.product.test_history import record_public_test_run
        w.__dict__["_public_tests_last"] = {
            "tick": int(tick), "passed": passed,
            "failed_tests": list(outcome.get("failed_tests") or []),
            "summary": outcome.get("summary", ""),
        }
        # The latest snapshot answers "is it green now"; the history answers "did
        # this work change anything", which is the only evidence the org has that
        # a patch fixed something (hidden oracles are evaluator-only).
        record_public_test_run(w, outcome, tick, repo_hash=repo_hash)
        res.events.append({"type": "repo_event",
                           "subtype": "public_tests_passed" if passed else "public_tests_failed",
                           "agent_id": aid, "tick": tick,
                           "summary": str(outcome.get("summary", ""))[:200],
                           "failed_count": len(outcome.get("failed_tests") or [])})

    def _h_run_ci(self, w, aid, p, res, tick):
        from environments.org_env.product.materialize import _repo_hash

        pr_id = p.get("pr_id") or _pr_needing_ci(w)
        pr_for_ci = w.repo_system.repo.pull_requests.get(pr_id) if pr_id else None
        pr_status = getattr(pr_for_ci, "status", "") if pr_for_ci is not None else ""
        pr_status = str(getattr(pr_status, "value", pr_status) or "").lower()
        if pr_for_ci is not None and pr_status in {"merged", "closed", "stale"}:
            res.success = False
            res.failure_reason = "pr_not_open"
            res.events.append({
                "type": "repo_event", "subtype": "ci_noop",
                "pr_id": pr_id, "agent_id": aid, "tick": tick,
                "reason": "pr_not_open",
            })
            return
        conflicts = self._new_file_conflicts_for_pr(w, pr_for_ci)
        if conflicts:
            self._refuse_new_file_conflict(
                w, pr_for_ci, conflicts, res, aid, tick, boundary="ci")
            return
        ci = w.repo_system.run_ci(pr_id=pr_id, tick=tick) if pr_id else None
        if ci is None:
            res.success = False
            res.failure_reason = "no_pr_for_ci"
            res.events.append({"type": "repo_event", "subtype": "ci_noop", "agent_id": aid, "tick": tick})
            return
        # Record the content this verdict is about before anything can return
        # early, so a red PR stops being dealt CI until the code actually moves.
        pr_for_ci = w.repo_system.repo.pull_requests.get(pr_id)
        if pr_for_ci is not None:
            try:
                pr_for_ci.ci_tree_hash = _repo_hash(w, prefer_mainline=False)
            except Exception:  # noqa: BLE001  a PR that cannot carry the mark still runs CI
                pass
        res.created_objects.append(ci.ci_id)
        # v13 P2 / v14b Integration CI: CI must reflect REAL end-to-end behavior, not just "patch
        # exists". Run the product integration check — the materialized product must (1) still RUN
        # end-to-end (contract) AND (2) report self-consistent eval metrics (metric consistency).
        # Either failure fails CI HERE (at the PR), not only at the release gate, and routes the
        # broken file into the debugging loop. (Grounding *quality* stays a release concern.)
        try:
            from environments.org_env.product.contracts import run_integration_ci
            cc = run_integration_ci(w, pr_for_ci)
            infra = cc.get("kind") == "infrastructure_error"
            # The request answers for what it carries; the desk answers for itself.
            # The editor can only edit the desk, so that is the break it is shown.
            from environments.org_env.product.contracts import record_working_tree_break
            record_working_tree_break(
                w, run_integration_ci(w) if pr_for_ci is not None else cc)
            # One place decides what a verdict does to the record and the request,
            # so this path and the world's own sweep cannot disagree about it.
            from environments.org_env.product.contracts import record_integration_verdict
            record_integration_verdict(ci, pr_for_ci, cc, world=w)
            if cc["ok"] and pr_for_ci is not None:
                pr_for_ci.ci_brief = ""
            if not cc["ok"]:
                res.events.append({"type": "repo_event",
                                   "subtype": ("ci_infrastructure_error" if infra
                                               else "ci_metric_inconsistency"
                                               if cc["kind"] == "metric_inconsistency"
                                               else "ci_contract_break"),
                                   "pr_id": pr_id, "agent_id": aid, "tick": tick,
                                   "boundary": cc.get("boundary", ""),
                                   "brief": str(cc.get("brief", ""))[:200]})
            # v14b (#4): CI that runs the contract/metric check IS the enforcement arm of the
            # interface-contract protocol — credit its spec use (and enforcement when it blocks)
            # so the protocol becomes an EXECUTABLE company skill, not just adopted text.
            _ck = ("contract", "interface", "schema", "integration", "evidence chain")
            w.note_protocol_use(_ck, tick, obj=pr_id)
            # Only a genuine product failure is protocol enforcement. Crediting an
            # infrastructure outage as enforcement manufactures capability evidence.
            if not cc["ok"] and not infra:
                w.note_protocol_enforcement(_ck, tick, obj=pr_id, agent=aid,
                                            actions=("run_ci", "ci_test"),
                                            blocked=True)
        except Exception as exc:  # noqa: BLE001
            # This used to be `except: pass`, which made every possible outcome
            # of the integration check — passed, failed, and never ran — leave
            # the same trace: ci.status untouched at "passed". A crash inside
            # run_integration_ci therefore reported a green CI, and the PR
            # merged on it. CI whose failure mode is silent success is worse
            # than no CI, because the merge gate believes it.
            ci.status = "failed"
            pr = w.repo_system.repo.pull_requests.get(pr_id)
            if pr is not None:
                pr.ci_passed = False
                pr.test_status = "failed"
            res.events.append({"type": "repo_event",
                               "subtype": "ci_infrastructure_error",
                               "pr_id": pr_id, "agent_id": aid, "tick": tick,
                               "boundary": "integration_ci",
                               "brief": f"{type(exc).__name__}: {exc}"[:200]})
        res.events.append({"type": "repo_event", "subtype": "ci", "pr_id": pr_id, "agent_id": aid,
                           "tick": tick, "status": ci.status})
    _h_ci_test = _h_run_ci

    def _h_merge_pr(self, w, aid, p, res, tick):
        pr_id = p.get("pr_id", "") or _mergeable_pr(w)
        pr = w.repo_system.repo.pull_requests.get(pr_id)
        if pr is None:
            res.success = False
            res.failure_reason = "no_pr"
            return
        pr_status = getattr(pr, "status", "")
        pr_status = str(getattr(pr_status, "value", pr_status) or "").lower()
        if pr_status in {"merged", "closed", "stale"}:
            res.success = False
            res.failure_reason = "pr_not_open"
            res.events.append({
                "type": "repo_event", "subtype": "merge_noop",
                "pr_id": pr_id, "agent_id": aid, "tick": tick,
                "reason": "pr_not_open",
            })
            return
        conflicts = self._new_file_conflicts_for_pr(w, pr)
        if conflicts:
            self._refuse_new_file_conflict(
                w, pr, conflicts, res, aid, tick, boundary="merge")
            return
        force = (not pr.reviewed)   # bypassing review = violation
        # RepoLite's terminal transition and the product/mainline promotion are
        # one logical transaction.  If apply_merged_pr raises after RepoLite has
        # marked the PR, branch, and commits merged, the request otherwise
        # becomes terminal while the file remains absent from mainline forever.
        # Snapshot only mutable business state (never clients/locks/methods) and
        # restore it in place so observers holding registry/object references do
        # not split off onto detached deepcopies.
        transaction_names = (
            "product_artifacts",
            "product",
            "tasks",
            "known_gaps",
            "issues",
            "events",
            "product_readiness",
            "institution_context",
            "company_skills",
            "_reconcile_warnings",
            "_mainline_smoke_error",
            "_smoke_cache",
            "_public_test_cache",
            "_oss_hidden_gate_cache",
            "_oss_release_hidden",
        )
        source_branch = w.repo_system.repo.branches.get(
            str(getattr(pr, "source_branch", "") or "")
        )
        expected_branch_commit_ids = list(
            getattr(source_branch, "commit_ids", []) or []
        )
        try:
            world_before = {
                name: copy.deepcopy(w.__dict__[name])
                for name in transaction_names
                if name in w.__dict__
            }
            repo_before = copy.deepcopy(w.repo_system.repo)
            res_before = copy.deepcopy(res)
        except Exception as error:  # noqa: BLE001 - snapshot is fail-closed
            res.success = False
            res.failure_reason = "merge_snapshot_failed"
            res.state_delta["merge_snapshot_error"] = type(error).__name__
            res.events.append({
                "type": "repo_event",
                "subtype": "merge_snapshot_failed",
                "pr_id": pr_id,
                "agent_id": aid,
                "tick": tick,
                "reason": type(error).__name__,
            })
            return

        def rollback_merge(
            error: Exception, failure_reason: str = "merge_promotion_failed"
        ) -> bool:
            try:
                w.repo_system.repo = _restore_snapshot_in_place(
                    w.repo_system.repo, repo_before
                )
                for name in transaction_names:
                    if name in world_before:
                        if name in w.__dict__:
                            w.__dict__[name] = _restore_snapshot_in_place(
                                w.__dict__[name], world_before[name]
                            )
                        else:
                            w.__dict__[name] = copy.deepcopy(world_before[name])
                    else:
                        w.__dict__.pop(name, None)
                _restore_snapshot_in_place(res, res_before)
            except Exception as rollback_error:  # noqa: BLE001
                res.success = False
                res.failure_reason = "merge_rollback_failed"
                res.state_delta["merge_rollback_error"] = type(rollback_error).__name__
                res.events.append({
                    "type": "repo_event",
                    "subtype": "merge_rollback_failed",
                    "pr_id": pr_id,
                    "agent_id": aid,
                    "tick": tick,
                    "reason": type(rollback_error).__name__,
                })
                return False
            res.success = False
            res.failure_reason = failure_reason
            res.state_delta["merge_promotion_error"] = type(error).__name__
            res.events.append({
                "type": "repo_event",
                "subtype": "merge_rolled_back",
                "pr_id": pr_id,
                "agent_id": aid,
                "tick": tick,
                "reason": type(error).__name__,
            })
            return True

        try:
            ok = w.repo_system.merge_pr(pr_id=pr_id, tick=tick, force=force)
        except Exception as error:  # noqa: BLE001 - transaction owns rollback
            rollback_merge(error)
            return
        res.success = ok
        if not ok:
            res.failure_reason = "merge_blocked: needs approved review + passing CI"
            res.events.append({"type": "repo_event", "subtype": "merge_blocked", "pr_id": pr_id,
                               "agent_id": aid, "tick": tick})
            return
        res.modified_objects.append(pr_id)
        res.graph_edges.append((aid, "merged", pr_id))
        res.events.append({"type": "repo_event", "subtype": "pr_merged", "pr_id": pr_id,
                           "agent_id": aid, "tick": tick})
        # apply the merged commits' patches to the MAINLINE artifact(s) AND credit the
        # linked task(s) with merge evidence (v6 P0.4; shared with the world sweep).
        try:
            touched = w.apply_merged_pr(pr, aid, tick, res=res)
        except Exception as error:  # noqa: BLE001 - transaction owns rollback
            rollback_merge(error)
            return
        current_branch = w.repo_system.repo.branches.get(
            str(getattr(pr, "source_branch", "") or "")
        )
        current_pr_status = getattr(pr, "status", "")
        current_pr_status = str(
            getattr(current_pr_status, "value", current_pr_status) or ""
        ).lower()
        current_branch_status = getattr(current_branch, "status", "")
        current_branch_status = str(
            getattr(current_branch_status, "value", current_branch_status) or ""
        ).lower()
        repo_complete = (
            current_pr_status == "merged"
            and all(
                commit_id in w.repo_system.repo.main_commit_ids
                for commit_id in expected_branch_commit_ids
            )
            and (
                not expected_branch_commit_ids
                or current_branch_status == "merged"
            )
        )
        if not repo_complete:
            rollback_merge(
                RuntimeError("repository_merge_incomplete"),
                "merge_repository_incomplete",
            )
            return
        # A custom/no-op promotion hook must not silently turn a base-0 create
        # into a terminal repo merge.  Validate every created path and compare
        # the resulting mainline text with the last patch for that artifact.
        patches = getattr(w, "patches", {}) or {}
        creation_targets = {
            str(getattr(patches.get(patch_id), "target_object_id", "") or "")
            for patch_id in (getattr(pr, "patch_ids", []) or [])
            if getattr(patches.get(patch_id), "creates_file", False)
        }
        latest_contents = {}
        for patch_id in (getattr(pr, "patch_ids", []) or []):
            carried = patches.get(patch_id)
            if carried is not None:
                latest_contents[str(getattr(carried, "target_object_id", "") or "")] = (
                    getattr(carried, "new_content", "")
                )
        incomplete = []
        artifacts = getattr(w, "product_artifacts", {}) or {}
        for artifact_id in sorted(creation_targets):
            artifact = artifacts.get(artifact_id)
            if (
                not artifact_id
                or artifact_id not in (touched or [])
                or artifact is None
                or int(getattr(artifact, "mainline_revision", 0) or 0) <= 0
                or pr_id not in (getattr(artifact, "linked_pr_ids", []) or [])
                or getattr(artifact, "mainline_content", "")
                != latest_contents.get(artifact_id, "")
            ):
                incomplete.append(artifact_id)
        if incomplete:
            rollback_merge(
                RuntimeError("created_file_promotion_incomplete"),
                "merge_promotion_incomplete",
            )
            res.state_delta["incomplete_artifact_ids"] = incomplete
            return
        for art_id in touched:
            if art_id not in res.modified_objects:
                res.modified_objects.append(art_id)
            res.graph_edges.append((pr_id, "merged_into", art_id))
        from environments.org_env.experiments.ablations import (
            PROTOCOL_ENFORCEMENT,
            mechanism_disabled,
        )
        # A force merge is the review/merge family's caught non-compliance; a reviewed
        # merge is its compliance. Without the spec-side moment, force merges never
        # reached any adopted ProtocolSpec (the live-registry write below touches only
        # the hardcoded proto_review_before_merge), AND the declared-action use
        # crediting in execute() then counted the successful force merge as a
        # compliant USE — violations fed the protocol's compliance evidence. The
        # enforcement moment moves the spec's counters during this action, which is
        # exactly what that crediting's dedupe keys on to withhold the bogus use.
        _rk = ("review before merge", "code review", "pr review", "peer review",
               "unreviewed", "force merge", "force-merge", "merge approval")
        if force:
            if (not mechanism_disabled(w, PROTOCOL_ENFORCEMENT)
                    and "proto_review_before_merge" in w.protocol_registry.protocols):
                w.protocol_registry.violate(aid, "proto_review_before_merge", tick=tick)
                res.events.append({"type": "protocol_violation_event",
                                   "protocol_id": "proto_review_before_merge", "agent_id": aid, "tick": tick})
            w.note_protocol_enforcement(_rk, tick, obj=pr_id, agent=aid,
                                        actions=("merge_pr", "review_pr", "formal_pr_review"),
                                        blocked=False)
        else:
            w.note_protocol_use(_rk, tick, obj=pr_id)

    @staticmethod
    def _new_file_conflicts_for_pr(w, pr) -> List[Dict[str, str]]:
        """Return stale/aliased create operations carried by ``pr``.

        A path choice is race-checked when its provisional artifact is minted,
        but two requests can both be opened while that path is still absent
        from mainline. Once one lands, the other's base-0 create must not become
        an ordinary overwrite merely because its CI verdict was obtained before
        the first merge. Inspect both the request snapshot and the source
        branch's current commits so a follow-up commit cannot evade this gate by
        waiting for RepoLiteSystem.run_ci to synchronize the request.
        """
        if pr is None:
            return []
        repo_system = getattr(w, "repo_system", None)
        repo = getattr(repo_system, "repo", None)
        if repo is None:
            return [{"reason": "repository_state_missing"}]

        commit_ids = list(getattr(pr, "commit_ids", []) or [])
        declared_patch_ids = list(getattr(pr, "patch_ids", []) or [])
        branch = (getattr(repo, "branches", {}) or {}).get(
            str(getattr(pr, "source_branch", "") or ""))
        if branch is None:
            if not commit_ids and not declared_patch_ids:
                return []
            return [{
                "pr_id": str(getattr(pr, "pr_id", "") or ""),
                "reason": "source_branch_missing",
            }]
        if not (getattr(branch, "commit_ids", []) or []):
            if not commit_ids and not declared_patch_ids:
                return []
            return [{
                "pr_id": str(getattr(pr, "pr_id", "") or ""),
                "reason": "source_branch_empty",
            }]
        for commit_id in (getattr(branch, "commit_ids", []) or []) if branch else ():
            if commit_id not in commit_ids:
                commit_ids.append(commit_id)
        patch_ids = declared_patch_ids
        patch_targets: Dict[str, str | None] = {}
        for commit_id in commit_ids:
            commit = (getattr(repo, "commits", {}) or {}).get(commit_id)
            commit_patch_ids = list(getattr(commit, "patch_ids", []) or []) if commit else []
            commit_artifact_ids = list(getattr(commit, "artifact_ids", []) or []) if commit else []
            for index, patch_id in enumerate(commit_patch_ids):
                if patch_id not in patch_ids:
                    patch_ids.append(patch_id)
                target = commit_artifact_ids[index] if index < len(commit_artifact_ids) else ""
                previous = patch_targets.get(str(patch_id), target)
                patch_targets[str(patch_id)] = target if previous == target else None

        patches = getattr(w, "patches", {}) or {}
        artifacts = getattr(w, "product_artifacts", {}) or {}
        from environments.org_env.product.repo_paths import (
            InvalidRepoPath,
            normalize_repo_relative_path,
        )

        conflicts: List[Dict[str, str]] = []
        paths_in_request: Dict[str, str] = {}
        for patch_id in patch_ids:
            patch = patches.get(patch_id)
            committed_target = patch_targets.get(str(patch_id), "")
            if patch is None:
                conflicts.append({
                    "patch_id": str(patch_id),
                    "artifact_id": str(committed_target or ""),
                    "reason": "patch_ledger_missing",
                })
                continue
            artifact_id = str(getattr(patch, "target_object_id", "") or "")
            artifact = artifacts.get(artifact_id)
            row = {"patch_id": str(patch_id), "artifact_id": artifact_id}
            if committed_target is None or (
                committed_target and str(committed_target) != artifact_id
            ):
                conflicts.append({**row, "reason": "patch_artifact_mismatch"})
                continue
            if artifact is None:
                conflicts.append({**row, "reason": "patch_artifact_missing"})
                continue
            if not getattr(patch, "creates_file", False):
                continue
            try:
                path = normalize_repo_relative_path(
                    getattr(artifact, "linked_file_path", None))
            except InvalidRepoPath:
                conflicts.append({**row, "reason": "create_path_invalid"})
                continue
            row["file_path"] = path
            key = path.casefold()
            previous = paths_in_request.get(key)
            if previous is not None and previous != artifact_id:
                conflicts.append({**row, "reason": "aliased_create_in_request"})
                continue
            paths_in_request[key] = artifact_id

            base = int(getattr(patch, "base_mainline_revision", 0) or 0)
            if base != 0:
                conflicts.append({**row, "reason": "create_base_is_not_zero"})
                continue
            # ``created_as_new_file && mainline_revision == 0`` is the one
            # explicit non-existence representation. Every other artifact at an
            # aliased path is already a mainline file, including legacy seeded
            # artifacts whose revision field historically defaulted to zero.
            for existing in artifacts.values():
                existing_path = getattr(existing, "linked_file_path", None)
                if not existing_path:
                    continue
                try:
                    existing_key = normalize_repo_relative_path(existing_path).casefold()
                except InvalidRepoPath:
                    continue
                if existing_key != key:
                    continue
                absent = bool(
                    getattr(existing, "created_as_new_file", False)
                    and int(getattr(existing, "mainline_revision", 0) or 0) == 0
                )
                if not absent:
                    conflicts.append({**row, "reason": "path_exists_on_mainline"})
                    break
        return conflicts

    @staticmethod
    def _refuse_new_file_conflict(
        w, pr, conflicts, res, aid: str, tick: int, *, boundary: str,
    ) -> None:
        """Fail a CI/merge action closed and leave inspectable repo state."""
        from environments.org_env.backend.repo.repo import BranchStatus, PRStatus

        res.success = False
        res.failure_reason = "new_file_create_conflict"
        res.state_delta["new_file_conflicts"] = [dict(row) for row in conflicts]
        if pr is not None:
            pr.merge_conflict = True
            pr.ci_passed = False
            pr.test_status = "failed"
            pr.status = PRStatus.STALE
            if pr.pr_id not in res.modified_objects:
                res.modified_objects.append(pr.pr_id)
            branch = (getattr(getattr(w, "repo_system", None), "repo", None))
            branch = (getattr(branch, "branches", {}) or {}).get(
                str(getattr(pr, "source_branch", "") or ""))
            if branch is not None:
                branch.status = BranchStatus.CONFLICTED
                if branch.branch_id not in res.modified_objects:
                    res.modified_objects.append(branch.branch_id)
                res.graph_edges.append(
                    (pr.pr_id, "conflicted_with", branch.branch_id)
                )
        res.events.append({
            "type": "repo_event",
            "subtype": "new_file_create_conflict",
            "pr_id": getattr(pr, "pr_id", None),
            "agent_id": aid,
            "tick": tick,
            "boundary": boundary,
            "conflicts": [dict(row) for row in conflicts],
        })

    # -- v5 release lifecycle (RC -> gates -> approve -> publish -> feedback) --
    def _h_create_release_candidate(self, w, aid, p, res, tick):
        from environments.org_env.backend.repo.release import release_gates_for
        rs = w.repo_system
        if _open_rc(w) is not None:                       # one RC in flight at a time
            res.success = False
            res.failure_reason = "rc_in_flight"
            res.events.append({"type": "release_event", "subtype": "rc_noop", "agent_id": aid, "tick": tick})
            return
        if not any(pr.patch_ids for pr in rs.repo.pull_requests.values()
                   if getattr(pr.status, "value", str(pr.status)) == "merged"):
            res.success = False
            res.failure_reason = "nothing_merged_to_release"
            res.events.append({"type": "release_event", "subtype": "rc_noop", "agent_id": aid, "tick": tick})
            return
        rc = rs.create_release_candidate(created_by=aid, tick=tick,
                                         required_gates=list(release_gates_for(w)))
        res.created_objects.append(rc.candidate_id)
        res.events.append({"type": "release_event", "subtype": "rc_created", "candidate_id": rc.candidate_id,
                           "version": rc.version, "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "created", rc.candidate_id))

    def _h_run_launch_readiness_check(self, w, aid, p, res, tick):
        from environments.org_env.backend.repo.release import evaluate_release_gates
        rc = _open_rc(w, p.get("candidate_id"))
        if rc is None:
            res.success = False
            res.failure_reason = "no_rc"
            return
        rc.gate_results = evaluate_release_gates(w, rc)
        rc.blockers = [r["gate"] for r in rc.gate_results if not r["passed"] and r["gate"] not in rc.waived_gates]
        # #2: record this agent's check + whether blockers changed, and spin up blocker tasks
        w.__dict__.setdefault("_last_readiness_tick", {})[aid] = tick
        w._note_rc_blockers(rc.blockers, tick)
        if rc.blockers:
            w._blockers_to_issues(rc, tick)
        rc.status = "blocked" if rc.blockers else "under_review"
        # v5 §P0-5: a passed evidence gate USES the relevant protocol (enforcement, not just text)
        reg = w.protocol_registry
        proto = next((pid for pid, pp in reg.protocols.items()
                      if any(k in pp.protocol_type.lower() for k in ("evidence", "claim", "review"))), None)
        if proto and any(r["gate"] == "gate_claim_evidence_protocol_active_or_pending" and r["passed"]
                         for r in rc.gate_results):
            try:
                reg.use(aid, proto, tick=tick)
            except Exception:
                pass
            res.events.append({"type": "protocol_use_event", "protocol_id": proto, "agent_id": aid,
                               "tick": tick, "used_in": "release_gate"})
            # spec #4/#8: also credit the adopted ProtocolSpec (registry use != spec use)
            w.note_protocol_use(("evidence", "claim", "credib", "traceab"), tick, obj=rc.candidate_id)
        # v14 P6: a readiness check USES an adopted Launch/Release Readiness protocol — credit its
        # spec use_count (fixes use_count=0 despite 19 readiness runs that shipped v0.0.2).
        w.note_protocol_use(("readiness", "launch readiness", "release readiness", "launch checklist"),
                            tick, obj=rc.candidate_id)
        res.events.append({"type": "release_event", "subtype": "readiness_check", "candidate_id": rc.candidate_id,
                           "status": rc.status, "blockers": list(rc.blockers), "agent_id": aid, "tick": tick})

    def _h_approve_release_candidate(self, w, aid, p, res, tick):
        rc = _open_rc(w, p.get("candidate_id"))
        if rc is None or rc.status not in ("under_review", "draft"):
            res.success = False
            res.failure_reason = "rc_not_reviewable"
            return
        from environments.org_env.backend.repo.release import release_approval_met

        w.repo_system.approve_release_candidate(rc_id=rc.candidate_id, approver=aid)
        # §5: >=2 approvers incl. a product/lead AND an evidence/reliability
        # approver, each capped by the roster — see release_approval_met.
        if (
            release_approval_met(w, rc)
            and not rc.blockers
            and rc.status == "under_review"
        ):
            rc.status = "approved"
        res.events.append({"type": "release_event", "subtype": "rc_approved", "candidate_id": rc.candidate_id,
                           "approver": aid, "status": rc.status, "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "approved", rc.candidate_id))

    def _h_block_release_candidate(self, w, aid, p, res, tick):
        rc = _open_rc(w, p.get("candidate_id"))
        if rc is None:
            res.success = False
            res.failure_reason = "no_rc"
            return
        reason = p.get("reason") or "release blocked by reviewer"
        if reason not in rc.blockers:
            rc.blockers.append(reason)
        rc.status = "blocked"
        res.events.append({"type": "release_event", "subtype": "rc_blocked", "candidate_id": rc.candidate_id,
                           "reason": reason, "agent_id": aid, "tick": tick})

    def _h_publish_product_release(self, w, aid, p, res, tick):
        rc = _open_rc(w, p.get("candidate_id"))
        if rc is None or rc.status != "approved":
            res.success = False
            res.failure_reason = "rc_not_approved"
            res.events.append({"type": "release_event", "subtype": "publish_blocked", "agent_id": aid,
                               "tick": tick, "reason": "rc_not_approved"})
            return
        lims: List[str] = []
        for cid in rc.included_commit_ids:
            c = w.repo_system.repo.commits.get(cid)
            for pid in (c.patch_ids if c else []):
                patch = w.patches.get(pid)
                for l in (getattr(patch, "added_limitations", []) or []) + \
                        (getattr(patch, "known_limitations", []) or []):
                    if l not in lims:
                        lims.append(l)
        _pname = (getattr(w, "company_config", {}) or {}).get("product_name") or "product"
        rel = w.repo_system.publish_release(rc_id=rc.candidate_id, released_by=aid, tick=tick,
                                            public_summary=p.get("summary") or f"{_pname} {rc.version}",
                                            known_limitations=lims)
        if rel is None:
            res.success = False
            res.failure_reason = "publish_failed"
            return
        if getattr(w, "product", None) is not None:
            w.product.stage = "beta_released"
        res.created_objects.append(rel.release_id)
        snap = w._materialize_release(rel.version) if hasattr(w, "_materialize_release") else {}
        res.events.append({"type": "release_event", "subtype": "published", "release_id": rel.release_id,
                           "version": rel.version, "release_tag": rel.release_tag, "agent_id": aid, "tick": tick,
                           "snapshot_dir": snap.get("dir"), "smoke_ok": snap.get("smoke_ok")})
        res.graph_edges.append((aid, "released", rel.release_id))
        # v14 P5 / v16 §6: a published release goes to market (external society if attached,
        # else P5 synthetic personas). Shared with the internal auto-publish path (world.py) so
        # the market fires however a release ships, not only via this rarely-chosen action.
        try:
            from environments.org_env.external_society.bridge import drive_post_release_market
            for tid in drive_post_release_market(w, tick):
                res.created_objects.append(tid)
                res.graph_edges.append((rel.release_id, "trialed_by", tid))
        except Exception:
            pass

    @staticmethod
    def _post_launch_report(w, tick):
        """What users say after a release, in the words of this product.

        Two sentences were written into this handler -- "a user found the
        report's evidence links unclear" and "an expert questioned the eval
        metrics' validity". They belong to the synthetic research product. On a
        pack that brings its own work they are about nothing: urllib3 has no
        report and no eval metrics, so every release manufactured another task
        with no reachable code behind it. On urllib3 these two sentences were 7
        of B2's 25 open tasks at t312 and 5 of B3's 28 at t240; none of the
        twelve ever completed, and three of B3's five were in progress -- effort
        spent on work the pack does not contain and the evaluator cannot score.

        On such a pack the feedback is a user meeting a problem the product
        already has -- one of its own open, agent-visible issues, restated as
        somebody running into it. Nothing is revealed that the organization
        could not already read, and when there is no open issue left, users
        report nothing rather than something invented.
        """
        from environments.org_env.product.substrates.eval_assets import (
            is_oss_substrate,
        )

        if not is_oss_substrate(w):
            fb = ["a user found the report's evidence links unclear",
                  "an expert questioned the eval metrics' validity"]
            return fb[tick % len(fb)]

        open_issues = [a for a in (getattr(w, "product_artifacts", {}) or {}).values()
                       if getattr(a, "artifact_type", "") == "issue"
                       and str(getattr(a, "status", "")) == "open"
                       and not str(getattr(a, "artifact_id", "")).startswith("cust_issue")]
        if not open_issues:
            return None
        chosen = open_issues[tick % len(open_issues)]
        what = (str(getattr(chosen, "problem", "") or getattr(chosen, "title", ""))
                .strip())
        return f"a user ran into this in the released build: {what}"[:180]

    def _h_collect_post_launch_feedback(self, w, aid, p, res, tick):
        from environments.org_env.product.objects import ProductArtifact
        rel = next(reversed(list(w.repo_system.repo.releases.values())), None) \
            if w.repo_system.repo.releases else None
        if rel is None:
            res.success = False
            res.failure_reason = "no_release"
            return
        arts = w.product_artifacts
        text = self._post_launch_report(w, tick)
        if text is None:
            # Users of this product had nothing new to say. Saying so is the
            # honest outcome; inventing a complaint would be work the pack does
            # not contain and nothing scores.
            res.events.append({"type": "external_signal_event",
                               "subtype": "post_launch_feedback_none",
                               "release_id": rel.release_id, "agent_id": aid,
                               "tick": tick})
            return
        seq = w.__dict__.setdefault("_cust_issue_seq", 0) + 1
        w._cust_issue_seq = seq
        iid = f"cust_issue_{seq}"
        while iid in arts:
            seq += 1; w._cust_issue_seq = seq; iid = f"cust_issue_{seq}"
        arts[iid] = ProductArtifact(artifact_id=iid, artifact_type="issue",
                                    title=f"post-launch: {text[:40]}", status="open", owner_agent_id=aid,
                                    problem=text, summary=text, priority="normal",
                                    created_at_tick=tick, updated_at_tick=tick)
        if getattr(w, "product", None) is not None:
            w.product.artifact_ids.append(iid)
            w.product.open_issue_ids.append(iid)
        rel.post_launch_feedback_ids.append(iid)
        res.created_objects.append(iid)
        res.events.append({"type": "external_signal_event", "subtype": "post_launch_feedback",
                           "artifact_id": iid, "release_id": rel.release_id, "agent_id": aid, "tick": tick,
                           "object_ids": [iid]})
        res.graph_edges.append((rel.release_id, "produced", iid))
        # v14 P5: collecting feedback also samples the live market (fresh trials/WTP).
        try:
            from environments.org_env.backend.market import run_market_trials
            for t in run_market_trials(w, tick, n=2, trigger="post_launch"):
                res.graph_edges.append((rel.release_id, "trialed_by", t.trial_id))
        except Exception:
            pass

    # -- docs / artifacts --------------------------------------------------
    def _h_create_doc(self, w, aid, p, res, tick):
        from environments.org_env.backend.entities import Document
        did = f"doc_{aid}_{tick}"
        w.documents[did] = Document(doc_id=did, title=p.get("title", "doc"),
                                    doc_type=p.get("doc_type", "doc"), author_id=aid,
                                    owner_id=aid, visibility="team")
        res.created_objects.append(did)
        res.events.append({"type": "file_share_event", "subtype": "doc", "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "created", did))
    _h_create_runway_update = _h_create_doc
    _h_create_cost_ledger = _h_create_doc

    def _h_read_knowledge(self, w, aid, p, res, tick):
        """Read one of the product's own knowledge files.

        What it costs is the turn; what it buys is the file's text in the next
        decision, the way a read post is.
        """
        oid = p.get("artifact_id") or p.get("object_id")
        art = (getattr(w, "product_artifacts", {}) or {}).get(oid)
        if art is None:
            res.success = False
            res.failure_reason = "no_such_knowledge_file"
            return
        pw = (getattr(w, "personal", {}) or {}).get(aid)
        if pw is not None and oid not in pw.downloaded_doc_ids:
            pw.downloaded_doc_ids.append(oid)
        res.events.append({"type": "file_share_event", "subtype": "knowledge_read",
                           "agent_id": aid, "tick": tick, "artifact_id": oid,
                           "path": str(getattr(art, "linked_file_path", "") or "")})
        res.graph_edges.append((aid, "read", oid))

    def _h_review_doc(self, w, aid, p, res, tick):
        did = p.get("doc_id")
        if did not in w.documents:
            res.success = False; return
        res.modified_objects.append(did)
        res.events.append({"type": "file_share_event", "subtype": "doc_review", "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "reviewed", did))
    _h_request_doc_changes = _h_review_doc

    def _h_create_customer_triage_sheet(self, w, aid, p, res, tick):
        from environments.org_env.backend.entities import Document
        if "doc_customer_triage" not in w.documents:
            w.documents["doc_customer_triage"] = Document(doc_id="doc_customer_triage",
                title="Customer Triage", doc_type="customer_feedback_summary",
                author_id=aid, owner_id=aid, visibility="team")
            res.created_objects.append("doc_customer_triage")
        res.events.append({"type": "file_share_event", "subtype": "triage", "agent_id": aid, "tick": tick})
    _h_create_claim_evidence_table = _h_create_doc
    _h_create_review_checklist = _h_create_doc
    _h_create_workflow_artifact = _h_create_doc

    # -- meetings ----------------------------------------------------------
    @staticmethod
    def _meeting_agenda(w, tick):
        """v8 #4: a meeting needs a concrete trigger — release blockers, open disputes, a due
        funding checkpoint, a protocol under review, or high-priority issues. No trigger ->
        no agenda -> no meeting."""
        items = []
        rcs = getattr(getattr(w, "repo_system", None), "repo", None)
        for rc in (rcs.release_candidates.values() if rcs else []):
            if getattr(rc, "status", "") == "blocked" and rc.blockers:
                items.append(("release_gate_review", f"resolve release blockers: {', '.join(rc.blockers[:3])}"))
                break
        cr = getattr(w, "commitment_registry", None)
        if cr and any(d.status in ("open", "escalated") for d in cr.disputes.values()):
            items.append(("dispute_resolution", "resolve open / escalated claim disputes"))
        bs = getattr(w, "budget_system", None)
        if bs and any(tr.status in ("scheduled", "delayed") and tr.scheduled_tick <= tick
                      and tr.condition for tr in bs.funding.tranches):
            items.append(("funding_checkpoint_review", "funding checkpoint: milestone review"))
        pm = getattr(w, "proposal_manager", None)
        if pm and any(pp.status == "under_review" and pp.proposal_type == "protocol_proposal"
                      for pp in pm.proposals.values()):
            items.append(("major_protocol_review", "review protocol proposal under review"))
        highp = [a for a in (getattr(w, "product_artifacts", {}) or {}).values()
                 if a.artifact_type == "issue" and a.status == "open"
                 and str(getattr(a, "priority", "")).lower() in ("high", "critical")]
        if highp:
            items.append(("release_gate_review", f"triage {len(highp)} high-priority issues"))
        return items

    def _h_schedule_meeting(self, w, aid, p, res, tick):
        agenda_items = self._meeting_agenda(w, tick)
        if not agenda_items:                              # #4: no trigger -> no meeting
            res.success = False
            res.failure_reason = "no_agenda_no_trigger"
            res.events.append({"type": "meeting_event", "subtype": "schedule_skipped",
                               "agent_id": aid, "tick": tick, "reason": "no_agenda"})
            return
        mtype = agenda_items[0][0]
        agenda = [a for _, a in agenda_items]
        ms = w.meeting_system
        day = tick // 24
        if any((mm.scheduled_tick // 24) == day and mm.meeting_type == mtype
               for mm in ms.meetings.values()):           # #4: same type at most once/day
            res.success = False
            res.failure_reason = "same_type_meeting_today"
            return
        clk = getattr(getattr(w, "time", None), "clock", None)
        night = bool(clk and getattr(clk, "phase_of_day", "") in ("night_sleep", "late_night"))
        emergency = bool(w.commitment_registry and any(
            d.status == "escalated" for d in w.commitment_registry.disputes.values()))
        if night and not emergency:                       # #4: no night meetings unless emergency
            res.success = False
            res.failure_reason = "night_no_meeting"
            return
        participants = p.get("participants") or list(w.agents.keys())[:4]
        if aid not in participants:
            participants = [aid] + participants
        m = ms.schedule_meeting(created_by=aid, meeting_type=mtype, title=mtype.replace("_", " "),
                                participants=participants, scheduled_tick=tick + 1, agenda=agenda)
        w.time.calendar.add_event(event_type="meeting", title=m.title, start_tick=tick + 1,
                                  end_tick=tick + 2, participants=participants, created_by=aid)
        res.created_objects.append(m.meeting_id)
        res.events.append({"type": "meeting_event", "subtype": "scheduled", "meeting_id": m.meeting_id,
                           "agent_id": aid, "tick": tick, "participants": participants,
                           "meeting_type": mtype, "agenda": agenda})

    def _h_record_meeting_notes(self, w, aid, p, res, tick):
        ms = w.meeting_system
        mid = p.get("meeting_id") \
            or next((m for m, mm in ms.meetings.items()
                     if mm.status.value == "active" and aid in mm.participants), None) \
            or next((m for m, mm in ms.meetings.items() if mm.status.value == "active"), None)
        if not mid:
            res.success = False
            res.failure_reason = "no_active_meeting"
            return
        # v14 P4: a meeting with NO actual attendees produces no notes/decision (v13b had 2
        # zero-attendee meetings still emitting notes — coordination noise, not a real meeting).
        if not (getattr(ms.meetings[mid], "attendees", None) or []):
            res.success = False
            res.failure_reason = "no_attendees"
            return
        # v8-run fix: a meeting must PRODUCE a grounded decision + action item, not empty
        # "notes". Derive the decision from the most pressing open work and link the action
        # item to that task so the meeting actually feeds the product pipeline.
        decision, task_id, summary = self._meeting_decision(w)
        note = ms.record_meeting_notes(agent_id=aid, meeting_id=mid, summary=summary,
                                       decisions=([decision] if decision else []), tick=tick)
        m = ms.meetings[mid]
        if decision and m.action_item_ids:
            ai = ms.action_items[m.action_item_ids[-1]]
            if task_id:
                ai.linked_task_id = task_id
                res.modified_objects.append(task_id)
        res.created_objects.append(note.doc_id)
        res.state_delta["surface_detail"] = decision or summary
        res.events.append({"type": "meeting_event", "subtype": "notes", "agent_id": aid, "tick": tick,
                           "meeting_id": mid, "decision": decision,
                           "action_items": len(m.action_item_ids), "linked_task": task_id})
        res.graph_edges.append((aid, "created", note.doc_id))

    def _meeting_decision(self, w):
        """Derive a concrete decision + linked task + summary from the most pressing open
        work (high-priority task -> pending proposal -> open issue), so meeting notes are
        grounded rather than 'notes'. Returns (decision_text, task_id|None, summary)."""
        def _st(t):
            return getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
        tasks = list((getattr(w, "tasks", {}) or {}).values())
        pend = [t for t in tasks if _st(t) in ("open", "blocked", "in_progress")]
        pend.sort(key=lambda t: int(getattr(t, "priority", 3) or 3))
        # v14 P4: a release blocker is the meeting's REAL subject — decide on it first, so the
        # action item matches the agenda (v13b: blocker meetings produced "split design notes").
        blockers = [t for t in pend if str(getattr(t, "task_id", "")).startswith("task_rel_blocker")
                    or any(str(i).startswith("rel_blocker") for i in (getattr(t, "linked_issues", []) or []))]
        pick = blockers[0] if blockers else (pend[0] if pend else None)
        if pick is not None:
            t = pick
            return (f"Prioritize '{getattr(t, 'title', t.task_id)}' and confirm its owner/next step.",
                    t.task_id, f"Reviewed open work; decided to push '{getattr(t, 'title', t.task_id)}'.")
        pm = getattr(w, "proposal_manager", None)
        if pm is not None:
            ur = [p for p in pm.proposals.values() if p.status == "under_review"]
            if ur:
                return (f"Move proposal '{ur[0].title}' to a decision this cycle.", None,
                        f"Discussed pending proposal '{ur[0].title}'.")
        arts = getattr(w, "product_artifacts", {}) or {}
        issues = [a for a in arts.values() if getattr(a, "artifact_type", "") == "issue"
                  and getattr(a, "status", "") == "open"]
        if issues:
            return (f"Address issue '{getattr(issues[0], 'title', '')}'.", None,
                    f"Triaged open issue '{getattr(issues[0], 'title', '')}'.")
        return ("Align on the next product milestone.", None, "Alignment sync; no blocker raised.")

    def _h_assign_action_item(self, w, aid, p, res, tick):
        mid = p.get("meeting_id") or next((m for m, mm in w.meeting_system.meetings.items()
                                           if mm.status.value == "active" and aid in mm.participants), None)
        if not mid:
            res.success = False
            return
        # v8d P2a parity: an agent-created item gets the same governance defaults as the
        # auto path (system.py record_meeting_notes) — rotating owner + due window. Without
        # them (assignee_id=None, due_tick=None) the overdue-reassignment loop in
        # world.process_action_items never fires for agent items (its gate needs due_tick),
        # and the follow-up task starts unowned with no deadline. Explicit params win.
        m = w.meeting_system.meetings[mid]
        assignee = p.get("assignee_id")
        if not assignee:
            roster = list(m.attendees or m.participants) or [aid]
            assignee = roster[len(m.action_item_ids) % len(roster)]
        due = p.get("due_tick")
        ai = w.meeting_system.assign_action_item(meeting_id=mid, description=p.get("description", "follow up"),
                                                 assignee_id=assignee,
                                                 due_tick=int(due) if due is not None else tick + 24)
        res.created_objects.append(ai.action_item_id)
        res.events.append({"type": "meeting_event", "subtype": "action_item", "agent_id": aid, "tick": tick})

    def _h_summarize_decision(self, w, aid, p, res, tick):
        # v8-run fix: no more empty "Summarizing what we decided: ." — pull the latest
        # real decision (or derive one) so the surface message has content.
        detail = (p.get("summary") or p.get("decision") or "").strip() or self._latest_decision_text(w)
        res.state_delta["surface_detail"] = detail or ""
        res.events.append({"type": "meeting_event", "subtype": "summary", "agent_id": aid,
                           "tick": tick, "summary": detail})
    _h_present_report = _h_summarize_decision
    _h_ask_question = _h_summarize_decision
    _h_propose_decision = _h_summarize_decision
    _h_discuss_issue = _h_summarize_decision

    @staticmethod
    def _latest_decision_text(w) -> str:
        ms = getattr(w, "meeting_system", None)
        if ms is not None and getattr(ms, "decisions", None):
            d = list(ms.decisions.values())[-1]
            return getattr(d, "decision_summary", "") or ""
        em = getattr(w, "episode_manager", None)
        if em is not None:
            closed = [e for e in em.episodes.values()
                      if getattr(e, "status", "") != "open" and getattr(e, "outcome_summary", "")]
            if closed:
                return closed[-1].outcome_summary
        return ""

    # -- protocol ----------------------------------------------------------
    # protocol anti-spam (preflight §11)
    _PROTO_AGENT_COOLDOWN = 12
    _PROTO_GLOBAL_COOLDOWN = 6
    _PROTO_MAX_PER_DAY = 3

    def _h_amend_protocol(self, w, aid, p, res, tick):
        """Agent-initiated policy repair: file a policy_repair_proposal to RELAX or DEPRECATE an
        adopted protocol (routed to _amend_protocol on adoption). Fixes the previously-dead
        amend_protocol vocabulary so a self-binding rule can be undone."""
        pm = getattr(w, "proposal_manager", None)
        pid = p.get("protocol_id") or p.get("target_protocol_id")
        spec = getattr(pm, "protocol_specs", {}).get(pid or "") if pm is not None else None
        if spec is None:
            res.success = False
            res.failure_reason = "no such protocol to amend"
            return
        from environments.org_env.proposals.objects import Proposal
        from environments.org_env.backend.protocol.harm import rule_is_doing_harm
        kind = "deprecate" if p.get("repair_kind") == "deprecate" else "relax"
        name = getattr(spec, "name", pid)
        # Say what the rule has cost. The bare assertion that something is
        # "over-constraining the org" is the whole of what an approver used to
        # get, and no count for any rule reaches any prompt anywhere else, so
        # there was nothing behind the claim to weigh.
        evidence = p.get("evidence") or rule_is_doing_harm(w, spec)[1]
        because = f": {evidence}" if evidence else ""
        prop = Proposal(
            proposal_id=pm.next_id("proposal"), proposal_type="policy_repair_proposal",
            title=f"{'Deprecate' if kind == 'deprecate' else 'Relax'} {str(name)[:50]}",
            summary=p.get("comment") or f"{kind} {name}{because}",
            proposer_agent_id=aid,
            target_problem=f"{name} is costing more than it is worth{because}",
            proposed_solution=f"{kind} {name}", repair_kind=kind, repair_target_protocol_id=pid,
            created_at_tick=tick, updated_at_tick=tick)
        w._submit_proposal(prop)
        res.modified_objects.append(pid)
        res.events.append({"type": "governance_event", "subtype": "amend_protocol_proposed",
                           "protocol_id": pid, "repair_kind": kind, "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "proposed_amendment", pid))

    def _h_propose_protocol(self, w, aid, p, res, tick):
        reg = w.protocol_registry
        cd = w.__dict__.setdefault("_proto_cd", {"agent": {}, "global": -999, "day": {}})
        day = tick // 24
        # -- cooldown gates (§11.2) --
        if tick - cd["global"] < self._PROTO_GLOBAL_COOLDOWN:
            res.success = False; res.failure_reason = "protocol global cooldown"; return
        if tick - cd["agent"].get(aid, -999) < self._PROTO_AGENT_COOLDOWN:
            res.success = False; res.failure_reason = "protocol agent cooldown"; return
        if cd["day"].get(day, 0) >= self._PROTO_MAX_PER_DAY:
            res.success = False; res.failure_reason = "max protocol proposals per day"; return
        # -- trigger binding (§11.3): need a repeated pattern / protocol_need wish --
        ok, reason = self._protocol_trigger_ok(w, aid, p)
        if not ok:
            res.success = False
            res.failure_reason = "protocol proposal requires repeated pattern or protocol_need wish"
            return
        name = (p.get("protocol_name") or p.get("rule_summary")
                or f"{p.get('protocol_type', 'coordination')} protocol")
        ptype = str(p.get("protocol_type") or self._slugify(name))
        related = list(p.get("related_objects") or p.get("related_object_ids") or [])
        req_fields = list(p.get("required_fields") or [])
        trig = p.get("trigger_condition") or p.get("source_problem") or reason
        # -- dedup by fingerprint (§11.4): attach support instead of a duplicate --
        fp = self._proto_fingerprint(name, trig, related, req_fields)
        fps = w.__dict__.setdefault("_proto_fps", {})
        if fp in fps and fps[fp] in reg.protocols:
            pid = fps[fp]
            reg.support(aid, pid, tick=tick)
            res.events.append({"type": "protocol_support_event", "protocol_id": pid,
                               "agent_id": aid, "tick": tick})
        else:
            pid = self._unique_proto_id(reg, ptype)
            reg.propose(proposer_id=aid, protocol_type=ptype, rule_summary=name,
                        scope=p.get("scope", "org"), tick=tick, protocol_id=pid)
            proto = reg.protocols.get(pid)
            if proto is not None:
                proto.target_process = str(trig)[:160]
            fps[fp] = pid
            res.created_objects.append(pid)
            res.events.append({"type": "protocol_proposal_event", "protocol_id": pid,
                               "agent_id": aid, "tick": tick, "related_objects": related})
        cd["agent"][aid] = tick
        cd["global"] = tick
        cd["day"][day] = cd["day"].get(day, 0) + 1
        res.graph_edges.append((aid, "supported", pid))

    def _protocol_trigger_ok(self, w, aid, p):
        """A protocol may only be proposed off a repeated pattern / protocol_need
        wish / recurring coordination / high-priority issue (§11.3)."""
        rm = getattr(w, "reflection_manager", None)
        if rm is not None and any(
                x.wish_type == "protocol_need" and x.status in ("open", "interpreted", "converted_to_proposal")
                for x in rm.wishes.values()):
            return True, "protocol_need wish"
        epm = getattr(w, "episode_manager", None)
        if epm is not None:
            eps = list(epm.episodes.values())
            if sum(1 for e in eps if e.episode_type == "claim_dispute_episode") >= 2:
                return True, "repeated disputes"
            if sum(1 for e in eps if e.episode_type == "protocol_formation_episode") >= 2:
                return True, "recurring coordination problem"
        arts = getattr(w, "product_artifacts", {}) or {}
        for o in (p.get("related_objects") or p.get("related_object_ids") or []):
            a = arts.get(o)
            if a is not None and str(getattr(a, "priority", "")) == "high":
                return True, "high-priority product issue requires a standard"
        fails = [a for a in getattr(w, "action_log", []) if a.get("agent_id") == aid
                 and not a.get("success", True)]
        if len(fails) >= 2:
            return True, "repeated failures"
        return False, "no repeated pattern"

    @staticmethod
    def _slugify(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in str(name).lower())[:40] or "protocol"

    @staticmethod
    def _proto_fingerprint(name, trig, related, req_fields) -> str:
        norm = lambda s: " ".join(str(s or "").lower().split())
        return f"{norm(name)[:40]}|{norm(trig)[:40]}|{','.join(sorted(related))}|{','.join(sorted(req_fields))}"

    def _unique_proto_id(self, reg, ptype: str) -> str:
        base = f"proto_{ptype}"
        if base not in reg.protocols:
            return base
        i = 2
        while f"{base}_{i}" in reg.protocols:
            i += 1
        return f"{base}_{i}"

    def _h_support_protocol(self, w, aid, p, res, tick):
        pid = p.get("protocol_id")
        if pid in w.protocol_registry.protocols:
            w.protocol_registry.support(aid, pid, tick=tick)
            res.events.append({"type": "protocol_support_event", "protocol_id": pid, "agent_id": aid, "tick": tick})
    _h_follow_protocol = _h_support_protocol

    def _h_enforce_protocol(self, w, aid, p, res, tick):
        from environments.org_env.experiments.ablations import (
            PROTOCOL_ENFORCEMENT,
            mechanism_disabled,
        )
        if mechanism_disabled(w, PROTOCOL_ENFORCEMENT):
            res.success = False
            res.failure_reason = "mechanism_ablation:protocol_enforcement"
            return
        pid = p.get("protocol_id")
        if pid in w.protocol_registry.protocols:
            w.protocol_registry.enforce(aid, pid, tick=tick)
            res.events.append({"type": "protocol_enforcement_event", "protocol_id": pid, "agent_id": aid, "tick": tick})
            res.graph_edges.append((aid, "enforced_protocol", pid))

    # -- payroll / retention ----------------------------------------------
    def _h_ask_about_payroll(self, w, aid, p, res, tick):
        m = self._send(w, aid, "team_general", "any update on payroll / runway?", tick, "policy_relevant")
        res.created_objects.append(m.message_id)
        res.events.append({"type": "payroll_event", "subtype": "ask", "agent_id": aid, "tick": tick})

    def _h_consider_external_offer(self, w, aid, p, res, tick):
        comp = w.budget_system.comp.get(aid)
        if comp:
            comp.outside_options = min(1.0, comp.outside_options + 0.1)
            w.budget_system.update_retention(aid, tick=tick)
        res.events.append({"type": "retention_event", "subtype": "consider_offer", "agent_id": aid, "tick": tick})

    # -- search / bridge ---------------------------------------------------
    def _h_internal_search(self, w, aid, p, res, tick):
        log = w.search_system.search(agent_id=aid, query=p.get("query", "task"),
                                     domain="internal", tick=tick)
        res.events.append({"type": "search_event", "search_id": log.search_id, "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "searched", log.search_id))
    def _h_external_community_search(self, w, aid, p, res, tick):
        log = w.search_system.search(agent_id=aid, query=p.get("query", "eval cost"),
                                     domain="external_community", tick=tick)
        res.events.append({"type": "search_event", "search_id": log.search_id, "agent_id": aid, "tick": tick})
    def _h_repo_search(self, w, aid, p, res, tick):
        log = w.search_system.search(agent_id=aid, query=p.get("query", "module"), domain="repo", tick=tick)
        res.events.append({"type": "search_event", "search_id": log.search_id, "agent_id": aid, "tick": tick})
    def _h_frozen_web_search(self, w, aid, p, res, tick):
        log = w.search_system.search(agent_id=aid, query=p.get("query", "api cost"), domain="frozen_web", tick=tick)
        res.events.append({"type": "search_event", "search_id": log.search_id, "agent_id": aid, "tick": tick})

    def _h_read_feed(self, w, aid, p, res, tick):
        # record that this agent has now seen the current feed, so the attractor guard
        # only lets them read again once there is a NEW post (v4 §3 / v5 §P0-3).
        w.__dict__.setdefault("_last_monitor_tick", {})[aid] = tick
        pw = w.personal.get(aid)
        # surface the most RECENT unread posts (so controlled interventions — customer complaints,
        # competitor launches, price-shock chatter — actually reach the agent), not a static slice
        # of the seed corpus. This is how external pressure enters internal perception.
        recent = (w.community.recent_posts(limit=12) if hasattr(w.community, "recent_posts")
                  else list(w.community.posts.values()))
        picked = []
        for post in recent:
            if pw and post.post_id not in pw.saved_post_ids:
                pw.saved_post_ids.append(post.post_id)
                picked.append(post.post_id)
            if len(picked) >= 3:
                break
        res.events.append({"type": "external_signal_event", "subtype": "read_feed", "agent_id": aid,
                           "tick": tick, "post_ids": picked})

    _h_monitor_customer_feedback = _h_read_feed

    # -- time / recovery ---------------------------------------------------
    def _h_rest_offline(self, w, aid, p, res, tick):
        av = w.time.availability.get(aid)
        if av:
            av.current_availability_status = "resting"
        res.events.append({"type": "recovery_event", "subtype": "rest", "agent_id": aid, "tick": tick})

    def _h_sleep(self, w, aid, p, res, tick):
        av = w.time.availability.get(aid)
        if av:
            av.current_availability_status = "asleep"
        res.events.append({"type": "recovery_event", "subtype": "sleep", "agent_id": aid, "tick": tick})

    def _h_defer_until_work_hours(self, w, aid, p, res, tick):
        res.events.append({"type": "communication_event", "subtype": "deferred", "agent_id": aid, "tick": tick})

    # -- product substrate actions (work on the messy research-agent prototype) --
    _PRODUCT_CREATE = {
        "create_eval_stub": ("eval", "eval/eval_{n}.py"), "create_report_template": ("template", "report_template_{n}.md"),
        "create_onboarding_doc": ("doc", "docs/onboarding_{n}.md"), "create_product_demo": ("demo", "demo_{n}.md"),
        "create_report_quality_checklist": ("template", "report_quality_checklist.md"),
        "write_design_note": ("doc", "docs/design_note_{n}.md"),
    }

    def _find_artifact(self, w, p):
        arts = getattr(w, "product_artifacts", {}) or {}
        for k in ("artifact_id", "issue_id", "file_path", "target_object_id", "object_id", "doc_id"):
            v = p.get(k)
            if isinstance(v, str):
                if v in arts:
                    return arts[v]
                hit = next((a for a in arts.values() if a.linked_file_path == v), None)
                if hit:
                    return hit
        return None

    def _h_product_action(self, w, aid, at, p, res, tick):
        from environments.org_env.product.objects import ProductArtifact
        arts = getattr(w, "product_artifacts", {})
        ps = getattr(w, "product", None)
        if at == "open_issue":
            seq = w.__dict__.setdefault("_issue_seq",
                                        len([a for a in arts.values() if a.artifact_id.startswith("issue_")]))
            seq += 1
            w._issue_seq = seq
            iid = f"issue_{seq}"
            while iid in arts:
                seq += 1; w._issue_seq = seq; iid = f"issue_{seq}"
            a = ProductArtifact(artifact_id=iid, artifact_type="issue", title=p.get("title", "issue"),
                                status="open", owner_agent_id=aid, problem=p.get("problem", p.get("title", "")),
                                summary=p.get("problem", p.get("title", "")), priority=p.get("priority", "unclear"),
                                created_at_tick=tick, updated_at_tick=tick)
            arts[iid] = a
            if ps is not None:
                ps.artifact_ids.append(iid); ps.open_issue_ids.append(iid)
            res.created_objects.append(iid)
            res.events.append({"type": "product_event", "subtype": "open_issue", "artifact_id": iid,
                               "agent_id": aid, "tick": tick})
            res.graph_edges.append((aid, "created", iid))
            return
        if at == "create_eval_stub" and self._programbench_probe_contract(w) is not None:
            self._create_programbench_probe_definition(w, aid, p, res, tick)
            return
        if at in self._PRODUCT_CREATE:
            atype, tmpl = self._PRODUCT_CREATE[at]
            n = len(arts)
            path = tmpl.format(n=n)
            aid_art = f"art_{path.replace('/', '_').replace('.', '_')}"
            existed = aid_art in arts
            if not existed:
                arts[aid_art] = ProductArtifact(artifact_id=aid_art, artifact_type=atype, title=path,
                                                status="active", owner_agent_id=aid, linked_file_path=path,
                                                summary=p.get("summary", f"{at} by {aid}"),
                                                created_at_tick=tick, updated_at_tick=tick)
                if ps is not None:
                    ps.artifact_ids.append(aid_art)
                res.created_objects.append(aid_art)
                res.graph_edges.append((aid, "created", aid_art))
            res.state_delta["target_artifact"] = aid_art
            res.events.append({"type": "product_event", "subtype": at, "artifact_id": aid_art,
                               "agent_id": aid, "tick": tick})
            if not existed:
                # v4 §1: a freshly created doc/eval is populated by a concrete doc_create /
                # stub patch (execution LLM or grounded template), validated then applied —
                # not just a bare summary.
                self._patch_new_artifact(w, aid, at, arts[aid_art], p, res, tick)
            return
        # edit / close / audit / update existing artifact
        art = self._find_artifact(w, p)
        if art is None and at == "edit_repo_file":
            # An untargeted repo edit is a decision to work on the backlog, not
            # a mistake. Choosing to fix the product is the policy's call;
            # choosing which module is the model's, and it is made here from
            # the open issues rather than pre-decided by whichever three the
            # affordance happened to deal. That division also keeps the two
            # selection modes on equal terms: an agent that picks by name and
            # an agent whose action is scored from a shortlist reach the same
            # set of modules.
            art = self._choose_open_issue_module(w, aid, p, res, tick)
            if not res.success:
                return
            if art is None and p.get("_create_repo_file") is True:
                self._create_and_patch_repo_file(w, aid, p, res, tick)
                return
        if art is None:
            res.success = False
            res.failure_reason = "no_target_artifact"
            res.events.append({"type": "product_event", "subtype": at, "agent_id": aid, "tick": tick})
            return
        res.state_delta.setdefault("target_artifact", art.artifact_id)   # per-object cooldown
        if at == "close_issue":
            art.revision += 1
            art.updated_at_tick = tick
            art.status = "closed"
            iid = art.artifact_id
            # v4 review §3: record WHY + WHAT resolved the issue (no fake closure)
            art.close_reason = p.get("reason") or p.get("close_reason") or "resolved"
            if res.action_id not in art.resolved_by_action_ids:
                art.resolved_by_action_ids.append(res.action_id)
            rtasks = [t for t in w.tasks.values() if iid in getattr(t, "linked_issues", [])]
            art.resolved_by_task_ids = [t.task_id for t in rtasks]
            for t in rtasks:
                for la in getattr(t, "linked_artifacts", []):
                    la_art = arts.get(la)
                    for pid in (getattr(la_art, "patch_history_ids", []) or []):
                        if pid not in art.resolved_by_patch_ids:
                            art.resolved_by_patch_ids.append(pid)
            if ps is not None and iid in ps.open_issue_ids:
                ps.open_issue_ids.remove(iid)
            res.modified_objects.append(iid)
            res.events.append({"type": "product_event", "subtype": at, "artifact_id": iid,
                               "agent_id": aid, "tick": tick, "close_reason": art.close_reason,
                               "resolved_by_patches": list(art.resolved_by_patch_ids)})
            res.graph_edges.append((aid, "edited", iid))
            return
        # v4 §2/§5: an artifact edit/audit MUST go through the patch layer — the execution
        # LLM (or a grounded template) produces a concrete patch, the PatchValidator gates
        # it, and only then does world.apply_product_patch bump the revision.
        if at in ("edit_repo_file", "edit_doc", "audit_readme_claims", "update_claim_tracker",
                  "update_source_tracker", "propose_product_direction"):
            self._patch_artifact(w, aid, at, art, p, res, tick)
            return
        art.revision += 1
        art.updated_at_tick = tick
        res.modified_objects.append(art.artifact_id)
        res.events.append({"type": "product_event", "subtype": at, "artifact_id": art.artifact_id,
                           "agent_id": aid, "tick": tick})
        res.graph_edges.append((aid, "edited", art.artifact_id))

    def _create_and_patch_repo_file(self, w, aid, p, res, tick):
        """Mint one provisional repository artifact and run the ordinary patch gate.

        Registration, issue/component/task binding and patch application form one
        transaction. Validator, editor or adopted-protocol failure removes the
        provisional object and every repository-lifecycle side effect, while the
        failed action remains observable. A successful creation is represented by
        the same patch/branch pipeline as an edit; only its creation metadata and
        result/event accounting differ.
        """
        from environments.org_env.product.objects import ProductArtifact
        from environments.org_env.product.repo_paths import (
            InvalidRepoPath,
            normalize_repo_relative_path,
            repo_artifact_id,
        )

        try:
            path = normalize_repo_relative_path(p.get("new_file_path"))
        except InvalidRepoPath as error:
            self._fail_repo_target_choice(
                res,
                "invalid_new_file_path",
                str(error),
                agent_id=aid,
                detail_code="invalid_path",
                tick=tick,
            )
            return None
        if p.get("_unbound_reconstruction") is True:
            first_component = path.split("/", 1)[0].casefold()
            if first_component in _RECONSTRUCTION_NON_IMPLEMENTATION_ROOTS:
                self._fail_repo_target_choice(
                    res,
                    "invalid_reconstruction_source_path",
                    f"{path} is outside the candidate implementation surface",
                    agent_id=aid,
                    detail_code="nonimplementation_surface",
                    tick=tick,
                )
                return None
        # Re-check after the model choice and immediately before registration.
        # This is the transaction's create/create race guard, and casefolding in
        # both this lookup and repo_artifact_id makes Windows aliases fail closed.
        conflict = self._repo_artifact_at_path(w, path)
        if conflict is not None:
            self._fail_repo_target_choice(
                res, "new_file_path_conflict",
                f"{path} aliases existing {getattr(conflict, 'artifact_id', '')}",
                agent_id=aid,
                detail_code="path_conflict",
                tick=tick,
            )
            return None
        artifact_id = repo_artifact_id(path)
        artifacts = getattr(w, "product_artifacts", None)
        if artifacts is None:
            artifacts = {}
            w.product_artifacts = artifacts
        if artifact_id in artifacts:
            self._fail_repo_target_choice(
                res,
                "new_file_path_conflict",
                f"artifact id already exists: {artifact_id}",
                agent_id=aid,
                detail_code="path_conflict",
                tick=tick,
            )
            return None

        product = getattr(w, "product", None)
        result_before = {
            "created": list(res.created_objects),
            "modified": list(res.modified_objects),
            "events": list(res.events),
            "messages": list(res.messages),
            "edges": list(res.graph_edges),
            "state": dict(res.state_delta),
        }
        product_ids_before = list(getattr(product, "artifact_ids", []) or []) if product is not None else None
        task_links_before = {
            task_id: list(getattr(task, "linked_artifacts", []) or [])
            for task_id, task in (getattr(w, "tasks", {}) or {}).items()
        }
        component_present = "_oss_component_map" in getattr(w, "__dict__", {})
        component_before = {
            key: (list(value) if isinstance(value, (list, tuple)) else value)
            for key, value in (w.__dict__.get("_oss_component_map", {}) or {}).items()
        }
        patches_before = dict(getattr(w, "patches", {}) or {})
        reject_present = "_patch_reject" in w.__dict__
        reject_before = dict(w.__dict__.get("_patch_reject", {}) or {})
        skips_present = "_code_editor_skipped" in w.__dict__
        skips_before = list(w.__dict__.get("_code_editor_skipped", []) or [])
        # apply_product_patch routes an accepted patch onto a branch. Snapshot
        # that delivery state too: if a custom/failed implementation raises
        # after recording the patch, rolling back only the artifact would leave
        # a branch carrying an orphan patch id.
        repo_system = getattr(w, "repo_system", None)
        repo_before = copy.deepcopy(getattr(repo_system, "repo", None)) if repo_system is not None else None
        repo_seq_before = getattr(repo_system, "_seq", None) if repo_system is not None else None
        pending_present = "_pending_by_branch" in w.__dict__
        pending_before = copy.deepcopy(w.__dict__.get("_pending_by_branch", {}) or {})
        personal_branches_before = {
            agent_id: list(getattr(personal, "local_branch_ids", []) or [])
            for agent_id, personal in (getattr(w, "personal", {}) or {}).items()
            if hasattr(personal, "local_branch_ids")
        }

        artifact = None
        patch = None
        try:
            artifact = ProductArtifact(
                artifact_id=artifact_id,
                artifact_type="repo_file",
                title=path,
                status="active",
                owner_agent_id=aid,
                linked_repo_id=getattr(product, "repo_id", None),
                linked_file_path=path,
                summary=f"New repository file for {p.get('_oss_issue') or 'implementation work'}",
                content="",
                mainline_content="",
                revision=0,
                mainline_revision=0,
                created_as_new_file=True,
                created_at_tick=tick,
                updated_at_tick=tick,
            )
            # Protocol enforcement emitted while the object is provisional must
            # refer to the stable attempted path, never to an artifact id that
            # will disappear if the transaction rolls back.
            artifact.__dict__["_provisional_creation_path"] = path
            artifacts[artifact_id] = artifact
            if product is not None and artifact_id not in product.artifact_ids:
                product.artifact_ids.append(artifact_id)
            p["artifact_id"] = artifact_id
            p["file_path"] = path
            res.state_delta["target_artifact"] = artifact_id
            self._bind_new_repo_artifact(w, artifact, p)
            patch = self._patch_artifact(
                w, aid, "edit_repo_file", artifact, p, res, tick, surface=False)
        except Exception as error:  # the transaction, not execute(), owns rollback
            res.success = False
            res.failure_reason = f"new_file_patch_failed:{type(error).__name__}"
            res.state_delta["new_file_patch_error"] = str(error)[:300]

        applied = (
            patch is not None
            and res.success
            and (getattr(w, "patches", {}) or {}).get(getattr(patch, "patch_id", "")) is patch
            and getattr(patch, "validation_status", "") == "accepted"
        )
        if applied and repo_system is not None:
            pending = w.__dict__.get("_pending_by_branch", {}) or {}
            routed = any(
                any(
                    str(row[0]) == str(getattr(patch, "patch_id", ""))
                    and str(row[1]) == artifact_id
                    for row in (rows or [])
                    if isinstance(row, (list, tuple)) and len(row) >= 2
                )
                for rows in pending.values()
            )
            if not routed:
                res.success = False
                res.failure_reason = "new_file_delivery_route_unavailable"
                res.state_delta["new_file_patch_error"] = (
                    "accepted patch was not routed to an active delivery branch"
                )
                applied = False
        if not applied:
            failure_reason = res.failure_reason or "new_file_patch_not_applied"
            failure_detail = dict(res.state_delta)
            audit_events = []
            for event in res.events[len(result_before["events"]):]:
                if event.get("subtype") not in {
                    "patch_rejected",
                    "patch_infrastructure_error",
                    "patch_refused_by_protocol",
                }:
                    continue
                safe_event = {
                    key: value for key, value in event.items()
                    if key not in ("artifact_id", "object_id", "patch_id")
                }
                safe_event["attempted_file_path"] = path
                audit_events.append(safe_event)
            artifacts.pop(artifact_id, None)
            if product is not None and product_ids_before is not None:
                if isinstance(getattr(product, "artifact_ids", None), list):
                    product.artifact_ids[:] = product_ids_before
                else:
                    product.artifact_ids = list(product_ids_before)
            for task_id, before in task_links_before.items():
                task = (getattr(w, "tasks", {}) or {}).get(task_id)
                if task is not None:
                    if isinstance(getattr(task, "linked_artifacts", None), list):
                        task.linked_artifacts[:] = before
                    else:
                        task.linked_artifacts = list(before)
            if component_present:
                component_map = w.__dict__.get("_oss_component_map")
                if isinstance(component_map, dict):
                    _restore_snapshot_in_place(component_map, component_before)
                else:
                    w.__dict__["_oss_component_map"] = component_before
            else:
                w.__dict__.pop("_oss_component_map", None)
            patches = getattr(w, "patches", None)
            if isinstance(patches, dict):
                patches.clear()
                patches.update(patches_before)
            if reject_present:
                reject = w.__dict__.get("_patch_reject")
                if isinstance(reject, dict):
                    _restore_snapshot_in_place(reject, reject_before)
                else:
                    w.__dict__["_patch_reject"] = reject_before
            else:
                w.__dict__.pop("_patch_reject", None)
            if skips_present:
                skips = w.__dict__.get("_code_editor_skipped")
                if isinstance(skips, list):
                    skips[:] = skips_before
                else:
                    w.__dict__["_code_editor_skipped"] = skips_before
            else:
                w.__dict__.pop("_code_editor_skipped", None)
            if repo_system is not None and repo_before is not None:
                repo_system.repo = _restore_snapshot_in_place(
                    repo_system.repo, repo_before
                )
                if repo_seq_before is not None:
                    repo_system._seq = repo_seq_before
            if pending_present:
                pending = w.__dict__.get("_pending_by_branch")
                if isinstance(pending, dict):
                    _restore_snapshot_in_place(pending, pending_before)
                else:
                    w.__dict__["_pending_by_branch"] = pending_before
            else:
                w.__dict__.pop("_pending_by_branch", None)
            for agent_id, before in personal_branches_before.items():
                personal = (getattr(w, "personal", {}) or {}).get(agent_id)
                if personal is not None:
                    personal.local_branch_ids[:] = before
            res.created_objects[:] = result_before["created"]
            res.modified_objects[:] = result_before["modified"]
            res.events[:] = result_before["events"]
            res.messages[:] = result_before["messages"]
            res.graph_edges[:] = result_before["edges"]
            res.state_delta.clear()
            res.state_delta.update(result_before["state"])
            res.events.extend(audit_events)
            # Keep the failure useful without retaining a pointer to the rolled
            # back artifact. Protocol/rejection details are action evidence, not
            # repository state, and are copied under a neutral key.
            for key in ("repo_target_choice_error", "new_file_patch_error", "protocol_refusal"):
                if key in failure_detail:
                    res.state_delta[key] = failure_detail[key]
            res.state_delta["attempted_file_path"] = path
            res.state_delta["creation_rolled_back"] = True
            res.success = False
            res.failure_reason = failure_reason
            res.events.append({
                "type": "repo_event", "subtype": "file_creation_failed",
                "file_path": path, "issue_id": p.get("_oss_issue"),
                "agent_id": aid, "tick": tick, "reason": failure_reason,
            })
            return None

        artifact.__dict__.pop("_provisional_creation_path", None)
        # apply_product_patch accounts for an edit; creation has a different
        # object-lifecycle meaning, so move this artifact to created_objects.
        res.modified_objects[:] = [oid for oid in res.modified_objects if oid != artifact_id]
        if artifact_id not in res.created_objects:
            res.created_objects.append(artifact_id)
        res.graph_edges.append((aid, "created", artifact_id))
        res.events.append({
            "type": "repo_event", "subtype": "created_file",
            "artifact_id": artifact_id, "file_path": path,
            "patch_id": patch.patch_id, "issue_id": p.get("_oss_issue"),
            "agent_id": aid, "tick": tick, "mainline": False,
        })
        try:
            self._maybe_surface_patch(w, aid, artifact, patch, res, tick, is_code=True)
        except Exception:
            # Communication is best-effort and occurs outside the repository
            # transaction; a failed announcement must not un-create valid code.
            pass
        return artifact

    @staticmethod
    def _programbench_probe_contract(w) -> Dict[str, Any] | None:
        product = getattr(w, "product", None)
        contract = (getattr(product, "substrate_meta", {}) or {}).get(
            "public_probe_authoring"
        )
        if not isinstance(contract, dict):
            return None
        if contract.get("schema_version") != "programbench_public_probe_cases_v1":
            return None
        return contract

    def _create_programbench_probe_definition(self, w, aid, p, res, tick) -> None:
        """Create an agent-authored declarative probe through normal repo gates."""
        contract = self._programbench_probe_contract(w)
        assert contract is not None
        surface = contract.get("definition_surface") or {}
        exact_paths = [str(item) for item in (surface.get("exact_paths") or [])]
        chosen = next(
            (path for path in exact_paths if self._repo_artifact_at_path(w, path) is None),
            "",
        )
        patterns = [str(item) for item in (surface.get("path_patterns") or [])]
        if not chosen and r"^eval/eval_[0-9]+\.py$" in patterns:
            for index in range(1, 10_000):
                candidate = f"eval/eval_{index}.py"
                if self._repo_artifact_at_path(w, candidate) is None:
                    chosen = candidate
                    break
        if not chosen:
            res.success = False
            res.failure_reason = "programbench_probe_definition_surface_exhausted"
            return

        limits = contract.get("limits") or {}
        max_cases = int(limits.get("max_cases") or 1)
        env_allowlist = ", ".join(
            str(item) for item in (contract.get("env_allowlist") or [])
        )
        root_entry = next(
            (
                entry
                for entry in (w.__dict__.get("_oss_issue_stream", []) or [])
                if str(entry.get("component") or "").casefold()
                == "reconstruction"
            ),
            None,
        )
        root_issue_id = str((root_entry or {}).get("issue_id") or "")
        root_component = str((root_entry or {}).get("component") or "")
        root_acceptance = str(
            (root_entry or {}).get("acceptance")
            or (root_entry or {}).get("acceptance_hint")
            or (root_entry or {}).get("body")
            or ""
        ).strip()[:600]
        goal = (
            "Create a standalone Python public-probe definition. Write exactly one UTF-8 "
            "JSON object to stdout and no logs or other text. The top-level object must have "
            "only schema_version='programbench_public_probe_cases_v1' and a non-empty cases "
            f"array (at most {max_cases}). Every case must have only argv (list of strings), "
            "stdin (string), input_files (list of objects with only path and base64 "
            "content_base64), and env (object). Allowed env keys: "
            f"{env_allowlist or '(none)'}. Every case must exercise a behavior documented in "
            "the public knowledge and must carry at least one non-empty, task-relevant stimulus "
            "in argv, stdin, or input_files. An empty/default-invocation-only case, generic "
            "placeholder, or case unrelated to the root reconstruction issue is not useful. "
            "Do not execute any product implementation while authoring the definition, and do "
            "not read evaluator-owned, oracle, hidden-test, or host paths."
        )
        if root_acceptance:
            goal += f" Root public issue acceptance: {root_acceptance}"
        goal = _append_public_reconstruction_knowledge(goal, w)
        params = dict(p)
        params.update({
            "new_file_path": chosen,
            "edit_goal": goal,
            "_programbench_public_probe": True,
            "_oss_issue": root_issue_id,
            "_oss_component": root_component,
        })
        artifact = self._create_and_patch_repo_file(w, aid, params, res, tick)
        if artifact is None:
            return
        artifact.artifact_type = "eval"
        artifact.summary = "Agent-authored ProgramBench declarative public probes"
        res.events.append({
            "type": "product_event",
            "subtype": "create_eval_stub",
            "artifact_id": artifact.artifact_id,
            "file_path": artifact.linked_file_path,
            "agent_id": aid,
            "tick": tick,
        })

    @staticmethod
    def _bind_new_repo_artifact(w, artifact, p) -> None:
        """Bind a chosen path to the visible issue component and its task rows."""
        issue_id = str(p.get("_oss_issue") or "")
        component = str(p.get("_oss_component") or "")
        if not component and issue_id:
            for entry in (w.__dict__.get("_oss_issue_stream", []) or []):
                if str(entry.get("issue_id") or "") == issue_id:
                    component = str(entry.get("component") or "")
                    break
        if issue_id:
            artifact.__dict__["oss_issue_id"] = issue_id
        if component:
            artifact.__dict__["oss_component"] = component
            # The component map is the implementation-target resolver.  A
            # support artifact such as eval/eval_1.py is still bound to the root
            # issue/task for provenance and delivery, but adding it to this map
            # before the first source file exists would make reconstruction look
            # path-bound and permanently suppress the cold-start source action.
            if p.get("_programbench_public_probe") is not True:
                component_map = w.__dict__.setdefault("_oss_component_map", {})
                paths = component_map.setdefault(component, [])
                if not isinstance(paths, list):
                    paths = list(paths or [])
                    component_map[component] = paths
                if artifact.linked_file_path not in paths:
                    paths.append(artifact.linked_file_path)
        for task_id, task in (getattr(w, "tasks", {}) or {}).items():
            if issue_id not in (getattr(task, "linked_issues", []) or []):
                continue
            if artifact.artifact_id not in task.linked_artifacts:
                task.linked_artifacts.append(artifact.artifact_id)
            if task_id not in artifact.linked_task_ids:
                artifact.linked_task_ids.append(task_id)

    def _choose_open_issue_module(self, w, aid, p, res, tick):
        """Choose an existing module or a new repository path in one model call.

        The policy has already chosen *coding work*. This call makes the
        architectural choice: extend a module that exists, or introduce a path
        named by the model. Only agent-visible issue text, organization-authored
        planning material and adopted rules are rendered. Evaluator assets and
        the seeded starter are never read here.

        A missing, broken or invalid model response is an explicit failed
        action. For compatibility with callers that use this helper to render a
        diagnostic, an existing least-attempted module may still be returned,
        but ``res.success`` remains false and the execution handler never edits
        it. No executable fallback path is invented.
        """
        already_named = self._find_artifact(w, p)
        if already_named is not None:
            return already_named
        try:
            from environments.org_env.product.substrates.issue_stream import (
                unpatched_coding_issues,
            )
            items = list(unpatched_coding_issues(w))
        except Exception as error:
            self._fail_repo_target_choice(
                res, "open_coding_issue_inventory_failed",
                f"{type(error).__name__}: {str(error)[:160]}",
                agent_id=aid,
                detail_code="inventory_failed",
                tick=tick,
            )
            return None
        artifacts = getattr(w, "product_artifacts", {}) or {}
        options = []
        # Every module the issue names, not just the first one that happens to
        # have a path. Taking the first collapses an issue onto whichever file
        # sorts earliest in its component map: on traffic_watch six of seven
        # public issues list alerts.py first, so across twelve arm-runs
        # alerts.py took 263 revisions and pipeline.py, detection.py,
        # violations.py and reports.py took one each -- and the six contracts
        # that live in those four files were never once reached. That reads as
        # a hard task and is a fixed menu.
        modules: dict[int, list] = {}
        for item in items:
            named = []
            for artifact_id in item.get("artifact_ids") or ():
                candidate = artifacts.get(artifact_id)
                path = str(getattr(candidate, "linked_file_path", "") or "")
                if candidate is not None and path:
                    named.append((path, candidate))
            artifact = named[0][1] if named else None
            if artifact is not None or item.get("unbound_reconstruction") is True:
                modules[len(options)] = named
                options.append((item, artifact))
        if not options:
            self._fail_repo_target_choice(
                res,
                "no_open_coding_issue",
                agent_id=aid,
                detail_code="no_open_coding_issue",
                tick=tick,
            )
            return None

        client = getattr(w, "llm_client", None)
        if client is None:
            self._fail_repo_target_choice(
                res,
                "repo_target_choice_requires_llm",
                agent_id=aid,
                detail_code="llm_required",
                tick=tick,
            )
            res.state_delta["issue_choice_fallback"] = "no_llm_client"
            return self._diagnostic_existing_issue_choice(options, p, res, aid, tick)

        def _where(index: int, artifact) -> str:
            named = [path for path, _art in modules.get(index) or []]
            if not named:
                return "no implementation file yet"
            if len(named) == 1:
                return f"existing file {named[0]}"
            return "files it names: " + ", ".join(named)

        menu = "\n".join(
            f"{index}. [{item.get('issue_id')}] {item.get('title', '')} "
            f"({_where(index, artifact)}, "
            f"{int(item.get('attempts') or 0)} attempts so far)\n"
            f"   wanted: {(item.get('acceptance') or '').strip()[:400]}"
            for index, (item, artifact) in enumerate(options)
        )
        planning = self._visible_repo_planning_context(w, aid)
        build_contract = _bounded_organization_build_contract(w)
        if build_contract:
            planning += (
                "\nCURRENT ORGANIZATION-AUTHORED BUILD CONTRACT (bounded working version; "
                "coordinate a new source path with it):\n"
                f"{build_contract}"
            )
        allowed_operations: List[str] = []
        if any(artifact is not None for _item, artifact in options):
            allowed_operations.append("edit")
        if any(item.get("unbound_reconstruction") is True for item, _artifact in options):
            allowed_operations.append("create")
        schema = {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": allowed_operations},
                "choice": {"type": "integer"},
                # Which of the chosen issue's modules to open. Absent means the
                # first, which is the old behaviour and the reason one file
                # absorbed every edit.
                "file_path": {"type": "string"},
                "new_file_path": {"type": "string"},
                "edit_goal": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": (["operation", "choice", "new_file_path", "edit_goal"]
                         if allowed_operations == ["create"] else
                         ["operation", "choice"]),
        }
        operation_instruction = (
            "The only legal operation is 'create': the selected reconstruction issue has no "
            "implementation file. Set choice=0, provide one non-empty repository-relative "
            "new_file_path, and provide a concrete edit_goal."
            if allowed_operations == ["create"] and len(options) == 1 else
            ("The only legal operation is 'edit'; select an issue that already has a file and "
             "do not provide a new_file_path."
             if allowed_operations == ["edit"] else
             "Use operation='edit' only for an option with an existing file. Use "
             "operation='create' only for an intentionally unbound reconstruction option, "
             "with a non-empty new_file_path."))
        try:
            answer = client.generate_json(
                "Choose the repository target for coding work. In this same JSON response, "
                "either select an existing issue/module with operation='edit', or choose "
                "operation='create' and name one new repository-relative text-file path. "
                "Never invent a default path. Use only the visible material below; hidden "
                "tests, evaluator reference code, and starter-source snapshots are unavailable. "
                f"{operation_instruction}",
                f"Open coding problems:\n{menu}\n\n"
                f"Organization-authored visible design/interface material and adopted rules:\n"
                f"{planning}\n\n"
                f"{operation_instruction}\n"
                "Return one issue index in `choice`. When the issue you choose names more "
                "than one file, also return `file_path` with the one you mean to open; "
                "it must be one of the files that issue names. Keep a new source path "
                "compatible with the organization's current build contract when one is shown.",
                schema,
            )
        except Exception as error:
            detail = f"{type(error).__name__}: {str(error)[:160]}"
            self._fail_repo_target_choice(
                res,
                "repo_target_choice_failed",
                detail,
                agent_id=aid,
                detail_code="provider_failed",
                tick=tick,
            )
            res.state_delta["issue_choice_fallback"] = detail
            return self._diagnostic_existing_issue_choice(options, p, res, aid, tick)

        if not isinstance(answer, dict):
            self._fail_repo_target_choice(
                res,
                "invalid_repo_target_choice",
                "response_not_object",
                agent_id=aid,
                detail_code="response_not_object",
                tick=tick,
            )
            return self._diagnostic_existing_issue_choice(options, p, res, aid, tick)
        raw_choice = answer.get("choice")
        if raw_choice is None and len(options) == 1:
            index = 0
        else:
            try:
                index = int(raw_choice)
            except (TypeError, ValueError):
                index = -1
        if not 0 <= index < len(options):
            detail = f"out_of_range:{raw_choice}"
            self._fail_repo_target_choice(
                res,
                "invalid_repo_target_choice",
                detail,
                agent_id=aid,
                detail_code="choice_out_of_range",
                tick=tick,
            )
            res.state_delta["issue_choice_fallback"] = detail
            return self._diagnostic_existing_issue_choice(options, p, res, aid, tick)

        item, artifact = options[index]
        operation = str(answer.get("operation") or "").strip().lower()
        # Older target-chooser clients returned only `choice`. Treat that as an
        # edit when (and only when) the selected issue already has a module.
        if not operation and artifact is not None and not answer.get("new_file_path"):
            operation = "edit"
        if operation == "edit":
            if artifact is None or answer.get("new_file_path"):
                self._fail_repo_target_choice(
                    res,
                    "invalid_repo_target_choice",
                    "edit_has_no_existing_file",
                    agent_id=aid,
                    detail_code="edit_has_no_existing_file",
                    tick=tick,
                )
                return None
            # Which of the issue's own modules, when it names more than one. A
            # path outside the issue's list is refused rather than honoured:
            # the issue is what bounds the edit, and widening that here would
            # let the chooser reach any file in the repository.
            wanted = str(answer.get("file_path") or "").strip().replace("\\", "/")
            if wanted:
                named = dict(modules.get(index) or [])
                if wanted in named:
                    artifact = named[wanted]
                else:
                    res.state_delta["issue_choice_file_ignored"] = wanted[:120]
            self._apply_issue_choice(item, artifact, p, res, aid, tick, len(options))
            res.state_delta["issue_choice_reason"] = str(answer.get("why") or "")[:200]
            res.state_delta["issue_choice_file"] = str(
                getattr(artifact, "linked_file_path", "") or "")[:120]
            return artifact
        if operation != "create":
            self._fail_repo_target_choice(
                res,
                "invalid_repo_target_choice",
                f"unknown_operation:{operation}",
                agent_id=aid,
                detail_code="unknown_operation",
                tick=tick,
            )
            return None
        if artifact is not None or item.get("unbound_reconstruction") is not True:
            self._fail_repo_target_choice(
                res, "new_file_creation_not_allowed",
                "only an intentionally unbound reconstruction issue may introduce a path",
                agent_id=aid,
                detail_code="create_not_unbound",
                tick=tick,
            )
            return None

        raw_path = answer.get("new_file_path")
        try:
            from environments.org_env.product.repo_paths import normalize_repo_relative_path
            path = normalize_repo_relative_path(raw_path)
        except Exception as error:
            self._fail_repo_target_choice(
                res,
                "invalid_new_file_path",
                f"{type(error).__name__}: {str(error)[:160]}",
                agent_id=aid,
                detail_code="invalid_path",
                tick=tick,
            )
            return None
        if path.split("/", 1)[0].casefold() in _RECONSTRUCTION_NON_IMPLEMENTATION_ROOTS:
            self._fail_repo_target_choice(
                res,
                "invalid_reconstruction_source_path",
                f"{path} is outside the candidate implementation surface",
                agent_id=aid,
                detail_code="nonimplementation_surface",
                tick=tick,
            )
            return None
        conflict = self._repo_artifact_at_path(w, path)
        if conflict is not None:
            self._fail_repo_target_choice(
                res, "new_file_path_conflict",
                f"{path} aliases existing {getattr(conflict, 'artifact_id', '')}",
                agent_id=aid,
                detail_code="path_conflict",
                tick=tick,
            )
            return None

        p["new_file_path"] = path
        p["file_path"] = path
        p["_create_repo_file"] = True
        p["_oss_issue"] = item.get("issue_id")
        p["_oss_component"] = item.get("component")
        p["_unbound_reconstruction"] = True
        p["edit_goal"] = str(
            answer.get("edit_goal") or item.get("acceptance") or item.get("title") or ""
        ).strip()
        res.state_delta["issue_choice_reason"] = str(answer.get("why") or "")[:200]
        res.events.append({
            "type": "product_event", "subtype": "issue_selected",
            "agent_id": aid, "tick": tick,
            "issue_id": item.get("issue_id"),
            "operation": "create", "file_path": path,
            "chosen_from": len(options),
        })
        return None

    @staticmethod
    def _fail_repo_target_choice(
            res, reason: str, detail: str = "", *, agent_id: str,
            detail_code: str, tick: int) -> None:
        res.success = False
        res.failure_reason = reason
        if detail:
            res.state_delta["repo_target_choice_error"] = detail[:300]
        # Persist only fixed semantic codes. ``detail`` can contain a model
        # choice, proposed path or provider exception and must never enter the
        # replay/event stream.
        safe_detail_code = (
            detail_code
            if detail_code in _REPO_TARGET_FAILURE_DETAIL_CODES
            else "unspecified_failure"
        )
        event = {"type": "repo_event", "subtype": "repo_target_choice_failed",
                 "failure_reason": reason, "detail_code": safe_detail_code,
                 "agent_id": str(agent_id), "tick": int(tick)}
        res.events.append(event)

    @staticmethod
    def _apply_issue_choice(item, artifact, p, res, aid, tick, chosen_from: int,
                            *, diagnostic_only: bool = False) -> None:
        p.setdefault("artifact_id", artifact.artifact_id)
        p.setdefault("file_path", getattr(artifact, "linked_file_path", ""))
        p.setdefault("_oss_issue", item.get("issue_id"))
        p.setdefault("_oss_component", item.get("component"))
        p.setdefault("edit_goal",
                     (item.get("acceptance") or item.get("title") or "").strip())
        res.events.append({
            "type": "product_event", "subtype": "issue_selected",
            "agent_id": aid, "tick": tick,
            "issue_id": item.get("issue_id"),
            "artifact_id": artifact.artifact_id,
            "operation": "edit", "chosen_from": chosen_from,
            "diagnostic_only": bool(diagnostic_only),
        })

    def _diagnostic_existing_issue_choice(self, options, p, res, aid, tick):
        existing = [(item, artifact) for item, artifact in options if artifact is not None]
        if not existing:
            return None
        item, artifact = min(existing, key=lambda pair: int(pair[0].get("attempts") or 0))
        self._apply_issue_choice(
            item, artifact, p, res, aid, tick, len(options), diagnostic_only=True)
        return artifact

    @staticmethod
    def _repo_artifact_at_path(w, path: str):
        """An artifact whose canonical path aliases ``path`` on any host."""
        from environments.org_env.product.repo_paths import (
            InvalidRepoPath,
            normalize_repo_relative_path,
        )
        wanted = normalize_repo_relative_path(path).casefold()
        for artifact in (getattr(w, "product_artifacts", {}) or {}).values():
            existing = getattr(artifact, "linked_file_path", None)
            if not existing:
                continue
            try:
                canonical = normalize_repo_relative_path(existing)
            except InvalidRepoPath:
                continue
            if canonical.casefold() == wanted:
                return artifact
        return None

    def _visible_repo_planning_context(self, w, aid: str) -> str:
        """Bounded organization-authored material visible to this member."""
        cues = ("design", "architecture", "interface", "api", "contract", "schema", "spec")
        rows: List[str] = []
        patches = getattr(w, "patches", {}) or {}
        for artifact in (getattr(w, "product_artifacts", {}) or {}).values():
            kind = str(getattr(artifact, "artifact_type", "") or "").lower()
            if kind not in ("doc", "template", "report"):
                continue
            # Seed artifacts are public input, not something the organization
            # produced. Excluding them also prevents the target chooser from
            # becoming a second, implicit starter-source channel. Once an
            # accepted organization patch has actually rewritten one, its
            # current version *is* organization-authored planning material.
            authored_here = int(getattr(artifact, "created_at_tick", 0) or 0) > 0
            if not authored_here:
                for patch_id in (getattr(artifact, "patch_history_ids", []) or []):
                    patch = patches.get(patch_id)
                    if (getattr(patch, "validation_status", "") == "accepted"
                            and int(getattr(patch, "applied_tick", 0) or 0) > 0):
                        authored_here = True
                        break
            if not authored_here:
                continue
            label = " ".join((
                str(getattr(artifact, "artifact_id", "") or ""),
                str(getattr(artifact, "title", "") or ""),
                str(getattr(artifact, "linked_file_path", "") or ""),
                str(getattr(artifact, "summary", "") or ""),
            ))
            if not any(cue in label.lower() for cue in cues):
                continue
            body = str(getattr(artifact, "content", "") or getattr(artifact, "summary", "") or "")
            rows.append(f"- [{getattr(artifact, 'artifact_id', '')}] {label[:180]}\n  {body[:1200]}")

        # Delegate privacy to the workspace's real visibility resolver. A
        # private design note owned by someone else is consequently absent.
        try:
            visible_files = list(w.visible_files_for(aid))
        except Exception:
            visible_files = []
        for obj in visible_files:
            if int(getattr(obj, "created_tick", 0) or 0) <= 0:
                continue
            label = f"{getattr(obj, 'file_type', '')} {getattr(obj, 'title', '')}"
            if not any(cue in label.lower() for cue in cues):
                continue
            body = getattr(obj, "raw_payload", None)
            if body is None:
                body = getattr(obj, "content_summary", "")
            rows.append(f"- [{getattr(obj, 'object_id', '')}] {label[:180]}\n  {str(body)[:1200]}")

        # Some design/API notes live in the task-document registry rather than
        # product_artifacts/company files. Include only a version updated during
        # the run and only when this member may see it.
        for doc_id, doc in (getattr(w, "documents", {}) or {}).items():
            visibility = getattr(doc, "visibility", "team")
            visibility = str(getattr(visibility, "value", visibility) or "").lower()
            owner = getattr(doc, "owner_id", None)
            author = getattr(doc, "author_id", None)
            if visibility not in ("team", "public") and aid not in (owner, author):
                continue
            if int(getattr(doc, "last_updated_tick", 0) or 0) <= 0:
                continue
            label = f"{getattr(doc, 'doc_type', '')} {getattr(doc, 'title', '')}"
            if not any(cue in label.lower() for cue in cues):
                continue
            rows.append(
                f"- [{doc_id}] {label[:180]}\n  "
                f"{str(getattr(doc, 'content_summary', '') or '')[:1200]}"
            )

        rules = []
        for spec in self._adopted_specs(w):
            rules.append(
                f"- [{getattr(spec, 'protocol_id', '')}] {getattr(spec, 'name', '')}: "
                f"when {str(getattr(spec, 'trigger_condition', '') or '')[:250]}; "
                f"require {str(getattr(spec, 'enforcement_rule', '') or '')[:500]}"
            )
        authored = "\n".join(rows[:8]) or "- (no organization-authored design/interface material yet)"
        adopted = "\n".join(rules[:8]) or "- (no adopted rules)"
        return f"AUTHORED MATERIAL:\n{authored}\nADOPTED RULES:\n{adopted}"

    @staticmethod
    def _oss_goal_for_artifact(w, art):
        """Return ``(goal, issue_id)`` for the OPEN OSS historical issue this artifact is linked to.

        ``goal`` is the issue's agent-visible behavioral acceptance (handed to the code editor so a
        freely-chosen edit on an OSS module isn't given the useless 'improve <file>'). ``issue_id`` is
        the authoritative issue this file maps to, used to bind patch provenance. When the file is
        shared by MORE THAN ONE open issue (e.g. git_utils.py is mapped by both ``http`` and
        ``windows``), ``issue_id`` is None so a freely-chosen edit is not mis-attributed to an
        arbitrary one; the caller then prefers the backlog candidate's ``_oss_issue``. Anti-scripting
        safe: only the user-facing acceptance, never the hidden test."""
        try:
            from environments.org_env.product.substrates.issue_stream import unpatched_coding_issues
            aidi = getattr(art, "artifact_id", None)
            matches = [it for it in unpatched_coding_issues(w)
                       if aidi in it.get("artifact_ids", [])]
        except Exception:
            return None, None
        if not matches:
            return None, None
        goal = (matches[0].get("acceptance") or matches[0].get("title") or "").strip() or None
        issue_id = matches[0].get("issue_id") if len(matches) == 1 else None
        return goal, issue_id

    def _patch_artifact(self, w, aid, at, art, p, res, tick, *, surface: bool = True):
        """Generate -> validate -> apply a concrete patch for an artifact edit (v4 §2.7)."""
        from environments.org_env.llm.code_editor import CodeEditorLLM
        from environments.org_env.llm.doc_editor import DocEditorLLM
        from environments.org_env.product.patch_validator import PatchValidator
        d = self.__dict__
        doc_ed = d.setdefault("_doc_editor", DocEditorLLM())
        code_ed = d.setdefault("_code_editor", CodeEditorLLM())
        validator = d.setdefault("_patch_validator", PatchValidator())
        oid = art.artifact_id
        path = str(getattr(art, "linked_file_path", "") or "").lower()
        if at == "audit_readme_claims" or path.endswith(_DOC_FILE_SUFFIXES):
            is_code = False                              # markdown / audits -> document patch
        elif at == "edit_repo_file":
            # Repository edits are implementation work unless the path is an
            # explicitly recognized prose format. This keeps uncommon languages
            # and extensionless build files on the language-neutral CodeEditor.
            is_code = True
        elif path.endswith(_CODE_FILE_SUFFIXES):
            is_code = True
        else:
            is_code = getattr(art, "artifact_type", "") in ("tool_stub", "eval")
        # goal handoff (review fix): once edit_repo_file is SELECTED on a file, give the code editor a
        # CONCRETE behavioral goal. Priority: explicit edit_goal (pre-built OSS candidate) -> the open
        # OSS issue this file is linked to (so a freely-chosen target still gets its issue's goal, not
        # "improve <file>") -> the artifact's known gap -> a generic fallback. No selection-time
        # steering; this only fires after the edit action is chosen.
        oss_goal, oss_issue_id = self._oss_goal_for_artifact(w, art)
        goal = (p.get("edit_goal") or oss_goal or (art.known_gaps[0] if getattr(art, "known_gaps", None)
                else f"improve {art.title}"))
        issue_component = str(p.get("_oss_component") or "").casefold()
        if not issue_component and p.get("_oss_issue"):
            issue_component = next(
                (
                    str(entry.get("component") or "").casefold()
                    for entry in (w.__dict__.get("_oss_issue_stream", []) or [])
                    if str(entry.get("issue_id") or "")
                    == str(p.get("_oss_issue") or "")
                ),
                "",
            )
        if (
            not p.get("_programbench_compile_contract")
            and not p.get("_programbench_public_probe")
            and (
                p.get("_unbound_reconstruction") is True
                or issue_component == "reconstruction"
            )
        ):
            goal = _append_public_reconstruction_knowledge(str(goal), w)
        rationale = p.get("rationale") or p.get("reason") or (
            (f"resolve the open issue linked to {path}") if oss_goal else f"{at} on {oid}")
        client = getattr(w, "llm_client", None)
        pid = f"patch_{len(getattr(w, 'patches', {})) + 1}_{tick}"
        base_mainline_revision = int(getattr(art, "mainline_revision", 0) or 0)
        creates_file = bool(
            getattr(art, "created_as_new_file", False)
            and base_mainline_revision == 0
            and int(getattr(art, "revision", 0) or 0) == 0
            and not (getattr(art, "patch_history_ids", []) or [])
        )

        def mark_creation(candidate):
            if candidate is not None:
                candidate.creates_file = creates_file
                candidate.base_mainline_revision = base_mainline_revision
            return candidate

        def prepare_candidate(candidate):
            candidate = mark_creation(candidate)
            if (
                candidate is not None
                and p.get("_programbench_compile_contract") is True
            ):
                # A syntactically valid compile.sh edit does not prove that the
                # required executable was produced.  Only the subsequent build
                # check may establish that; do not let an editor claim clear the
                # artifact gap merely because this patch passed static gates.
                candidate.resolved_gaps = []
            if (
                candidate is not None
                and p.get("_programbench_public_probe") is True
                and client is None
            ):
                candidate.new_content = _PROGRAMBENCH_PROBE_FALLBACK
                candidate.pseudo_diff = "+ emit one declarative ProgramBench probe case"
                candidate.change_summary = "Define one bounded public behavior probe."
                candidate.changed_behavior = [
                    "the definition emits the strict ProgramBench probe JSON contract"
                ]
            return candidate

        if is_code:
            patch = prepare_candidate(code_ed.generate_patch(
                actor_id=aid, target_object_id=oid, edit_goal=goal,
                rationale=rationale, world=w, tick=tick, patch_id=pid, client=client))
            if patch is None:
                res.success = False
                res.failure_reason = "code_editor_returned_no_patch"
                return None
            if p.get("_programbench_compile_contract") is not True:
                self._mark_resolved_gap(patch, art, goal)
            vr = validator.validate_code_patch(patch, w)
        else:
            patch = prepare_candidate(doc_ed.generate_patch(
                actor_id=aid, target_object_id=oid, edit_goal=goal,
                rationale=rationale, world=w, tick=tick, patch_id=pid, client=client))
            if patch is None:
                res.success = False
                res.failure_reason = "doc_editor_returned_no_patch"
                return None
            self._mark_resolved_gap(patch, art, goal)
            vr = validator.validate_doc_patch(patch, w)
        # provenance + retry accounting (cross-talk fix): related_issue_ids / related_task_ids drive
        # per-issue attempt counting (issue_stream._oss_patch_attempts), PR routing and resolution, so
        # for a CODE edit they MUST reflect the ONE issue this edit actually targets — the backlog
        # candidate's ``_oss_issue`` (authoritative) or, for a freely-chosen edit, the single open OSS
        # issue this file maps to. The code-editor LLM otherwise fills related_issue_ids/task_ids
        # freely and adds issues that merely SHARE the target file (git_utils.py is mapped by BOTH
        # ``http`` and ``windows``), so a windows-longpaths patch gets mis-counted as an httpx attempt
        # and can even carry httpx's task to a false "resolved". We OVERRIDE (not append) for code and
        # bind the task to the SAME issue; docs keep the editor's linkage. Visible — never hidden test.
        _oss_iss = p.get("_oss_issue") or oss_issue_id
        if _oss_iss and is_code:
            patch.related_issue_ids = [_oss_iss]
            _tasks = [tid for tid, t in (getattr(w, "tasks", {}) or {}).items()
                      if _oss_iss in (getattr(t, "linked_issues", []) or [])]
            if _tasks:
                patch.related_task_ids = _tasks
        elif _oss_iss:
            _rel = list(getattr(patch, "related_issue_ids", []) or [])
            if _oss_iss not in _rel:
                _rel.append(_oss_iss)
                patch.related_issue_ids = _rel
        if not vr.passed:
            patch.validation_status = "rejected"
            patch.rejection_reason = vr.reason
            w.patches[patch.patch_id] = patch          # v6 P0.5: persist rejected patches too
            res.success = False
            res.failure_reason = f"patch_rejected: {vr.reason}"
            from environments.org_env.product.patch_validator import (
                is_infrastructure_rejection,
            )
            if is_infrastructure_rejection(patch):
                # The model never answered; the edit was never actually tried.
                # Feeding this into re-selection suppression would teach the
                # policy to avoid a file because the NETWORK failed - measured:
                # two such rejections masked every agent off the exact file two
                # failing oracles needed edited. Distinct subtype so the
                # attractor guard's windowed counter never sees it either.
                res.events.append({"type": "product_event",
                                   "subtype": "patch_infrastructure_error",
                                   "artifact_id": oid, "agent_id": aid,
                                   "tick": tick, "reason": vr.reason})
                return None
            # v5 §P0-4: remember the rejection so the policy stops re-selecting this edit
            w.__dict__.setdefault("_patch_reject", {})[(aid, oid)] = tick
            res.events.append({"type": "product_event", "subtype": "patch_rejected",
                               "artifact_id": oid, "agent_id": aid, "tick": tick, "reason": vr.reason})
            return None
        # The organization's own rules get to refuse a change before it lands. An
        # institution that exists only as a registry row and a counter cannot be
        # told apart from one that governs the work: an arm carried a rule about
        # preserving a module's published callable surface for a hundred ticks,
        # with 118 recorded enforcements, while writing code that dropped a
        # required class and renamed a public method. With nothing adopted there is
        # nothing to check, so this is the difference institutionalizing makes
        # rather than a bar the environment holds everyone to.
        patch = self._revise_until_its_own_rules_allow_it(
            w, aid, art, patch, res, tick,
            rewrite=(lambda revision: prepare_candidate(code_ed.generate_patch(
                actor_id=aid, target_object_id=oid, edit_goal=goal, rationale=rationale,
                world=w, tick=tick, patch_id=pid, client=client, revision=revision)))
            if is_code else None,
            validate=(lambda cand: validator.validate_code_patch(cand, w)) if is_code else None)
        if patch is None:
            return None
        patch = prepare_candidate(patch)
        # A protocol rewrite is a fresh patch object. Re-assert authoritative
        # provenance after review so its routing cannot disappear on the
        # compliant attempt.
        if _oss_iss and is_code:
            patch.related_issue_ids = [_oss_iss]
            _tasks = [tid for tid, task in (getattr(w, "tasks", {}) or {}).items()
                      if _oss_iss in (getattr(task, "linked_issues", []) or [])]
            if _tasks:
                patch.related_task_ids = _tasks
        # option A (IDE "update references"): this code edit is ALLOWED to drop a public symbol other
        # modules import — but flag every importer for a coordinated update BEFORE apply overwrites the
        # old content, so a refactor cascades to callers instead of leaving the tree ImportError-red.
        _repairs, _mod = {}, ""
        if is_code:
            try:
                from environments.org_env.product.interface_guard import importers_needing_update, module_base
                _repairs = importers_needing_update(w, art, getattr(patch, "new_content", "") or "")
                _mod = module_base(getattr(art, "linked_file_path", "") or "")
            except Exception:
                _repairs, _mod = {}, ""
        if not w.apply_product_patch(patch, res, aid):
            res.success = False
            res.failure_reason = "patch_apply_failed"
            return None
        if at == "edit_repo_file":
            res.events.append({"type": "repo_event", "subtype": "patch_accepted",
                               "artifact_id": oid, "patch_id": patch.patch_id,
                               "agent_id": aid, "tick": tick, "mainline": False})
        if _repairs:
            reg = w.__dict__.setdefault("_interface_repairs", {})
            for imp_id, syms in _repairs.items():
                reg[imp_id] = {"module": _mod, "symbols": list(syms), "tick": tick,
                               "from": getattr(art, "artifact_id", "")}
            res.events.append({"type": "product_event", "subtype": "interface_repair_flagged",
                               "artifact_id": getattr(art, "artifact_id", ""), "tick": tick,
                               "importers": sorted(_repairs.keys())})
        if surface:
            self._maybe_surface_patch(w, aid, art, patch, res, tick, is_code=is_code)
        return patch

    # How many times an author may answer its own organization's refusal before
    # the change is dropped. A refusal with no rewrite is not review, it is a wall:
    # one arm sent 213 edits, 199 were refused, and all but a handful renamed the
    # same public symbol in the same file because every rewrite began from nothing.
    _MAX_PROTOCOL_REVISIONS = 3

    def _revise_until_its_own_rules_allow_it(self, w, aid, art, patch, res, tick,
                                             *, rewrite=None, validate=None):
        """Let the author answer its own organization's refusals; return what lands.

        Returns the patch to apply, or None when the change is dropped. Each
        rewrite is told which attempt it is, the whole patch that was refused, and
        every reason given so far, so it answers the refusal instead of repeating
        the change that drew it.
        """
        history: List[Dict[str, Any]] = []
        for _round in range(self._MAX_PROTOCOL_REVISIONS + 1):
            verdict = self._protocol_refusal(w, art, patch)
            if verdict is None:
                if history:
                    res.events.append({
                        "type": "product_event", "subtype": "patch_revised_to_comply",
                        "artifact_id": art.artifact_id, "agent_id": aid, "tick": tick,
                        "refusals": len(history),
                        "protocol_id": history[-1].get("protocol_id", "")})
                return patch
            history.append(verdict)
            res.events.append({"type": "product_event", "subtype": "patch_refused_by_protocol",
                               "artifact_id": art.artifact_id, "agent_id": aid, "tick": tick,
                               "protocol_id": verdict["protocol_id"],
                               "attempt": len(history),
                               "reason": f"{verdict['rule'][:120]} — {verdict['reason']}"[:300]})
            self._credit_the_rule_that_refused(w, aid, art, verdict, tick)
            if rewrite is None or len(history) > self._MAX_PROTOCOL_REVISIONS:
                break
            from environments.org_env.llm.protocol_review import revision_brief
            previous = str(getattr(patch, "unified_diff", "")
                           or getattr(patch, "new_content", "") or "")
            candidate = rewrite(revision_brief(history, previous))
            if candidate is None:
                break
            if validate is not None and not validate(candidate).passed:
                break        # a rewrite that cannot pass the ordinary gate is not an answer
            patch = candidate
        # Every answer was refused too, so the change is dropped and the author is
        # told what for.
        last = history[-1]
        patch.validation_status = "rejected"
        patch.decline_reason = f"refused by {last['protocol_id'] or 'an adopted rule'}"
        w.__dict__.setdefault("_patch_reject", {})[(aid, art.artifact_id)] = tick
        res.success = False
        res.failure_reason = "refused_by_adopted_protocol"
        res.state_delta["protocol_refusal"] = (
            f"{len(history)} attempts refused; last: {last['rule'][:100]} — {last['reason']}")[:300]
        return None

    @staticmethod
    def _credit_the_rule_that_refused(w, aid, art, verdict, tick) -> None:
        try:
            # A caught non-compliance that blocked something records both halves —
            # the violation and the enforcement — which is the pair the strong bar
            # needs; asking for the violation separately would double-count it.
            provisional_path = getattr(art, "_provisional_creation_path", "")
            governed_object = (
                f"attempted_repo_path:{provisional_path}"
                if provisional_path else art.artifact_id
            )
            w.note_protocol_enforcement(
                verdict["keywords"], tick, obj=governed_object, agent=aid,
                actions=("edit_repo_file", "review_pr"), blocked=True,
                protocol_id=verdict.get("protocol_id") or None)
        except Exception:  # noqa: BLE001  bookkeeping must not undo the refusal
            pass

    def _protocol_refusal(self, w, art, patch):
        """The refusal this change draws from the adopted rules, or None."""
        try:
            from environments.org_env.llm.protocol_review import review_change
            return review_change(
                w,
                file_path=str(getattr(art, "linked_file_path", "") or art.artifact_id),
                diff=str(getattr(patch, "unified_diff", "") or getattr(patch, "new_content", "") or ""),
                client=getattr(w, "llm_client", None))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _mark_resolved_gap(patch, art, goal: str) -> None:
        """Default the patch's resolved_gaps to the gap it targeted (the edit goal), so
        apply_product_patch clears exactly that gap rather than blindly popping #0 (v4 §4).
        The LLM may also set resolved_gaps; the PatchValidator strips invalid claims."""
        if getattr(patch, "resolved_gaps", None):
            return
        gaps = list(getattr(art, "known_gaps", []) or [])
        if goal and goal in gaps:
            patch.resolved_gaps = [goal]

    # v4 §1: creation goals + grounded content for create-class product actions.
    _CREATE_GOALS = {
        "create_report_quality_checklist": (
            "create a report quality checklist with concrete acceptance items",
            {"checklist_items": ["every claim links to >=1 tracked source",
                                 "no overclaiming language (no 'fully'/'guaranteed')",
                                 "an explicit limitations section is present",
                                 "eval metrics are reported alongside the result"]}),
        "create_onboarding_doc": (
            "create an onboarding doc with a step-by-step user workflow",
            {"workflow_sections": ["setup & install", "run a cheap pilot",
                                   "read and interpret results", "known limitations"]}),
        "create_report_template": (
            "create a report template that separates claims from their evidence", {}),
        "write_design_note": (
            "write a design note clarifying scope, trade-offs and open questions", {}),
        "create_product_demo": (
            "draft an honest demo script walking through the core flow", {}),
        "create_eval_stub": (
            "define concrete evaluation metrics instead of placeholders", {}),
    }

    def _patch_new_artifact(self, w, aid, at, art, p, res, tick):
        """v4 §1: a create-class action produces a concrete doc_create / stub patch
        (execution LLM or grounded template), validated, then applied — so a new doc /
        checklist / eval has real content (sections / checklist_items / workflow), a
        patch history, a change summary and awaiting_review, not just a bare summary."""
        from environments.org_env.llm.code_editor import CodeEditorLLM
        from environments.org_env.llm.doc_editor import DocEditorLLM
        from environments.org_env.product.patch_validator import PatchValidator
        d = self.__dict__
        doc_ed = d.setdefault("_doc_editor", DocEditorLLM())
        code_ed = d.setdefault("_code_editor", CodeEditorLLM())
        validator = d.setdefault("_patch_validator", PatchValidator())
        oid = art.artifact_id
        client = getattr(w, "llm_client", None)
        pid = f"patch_{len(getattr(w, 'patches', {})) + 1}_{tick}"
        goal, extras = self._CREATE_GOALS.get(at, (p.get("edit_goal") or f"create {art.title}", {}))
        rationale = p.get("rationale") or p.get("reason") or f"{at} by {aid}"
        path = str(getattr(art, "linked_file_path", "") or "").lower()
        is_code = at == "create_eval_stub" or path.endswith(_CODE_FILE_SUFFIXES)
        if is_code:
            patch = code_ed.generate_patch(actor_id=aid, target_object_id=oid, edit_goal=goal,
                                           rationale=rationale, world=w, tick=tick, patch_id=pid, client=client)
            patch.patch_type = "stub_update"
            if client is None:
                patch.change_summary = f"Created {art.title}: {goal}."
            vr = validator.validate_code_patch(patch, w)
        else:
            patch = doc_ed.generate_patch(actor_id=aid, target_object_id=oid, edit_goal=goal,
                                          rationale=rationale, world=w, tick=tick, patch_id=pid, client=client)
            patch.patch_type = "doc_create"
            if extras.get("checklist_items"):
                patch.checklist_items = list(extras["checklist_items"])
            if extras.get("workflow_sections"):
                patch.workflow_sections = list(extras["workflow_sections"])
            if client is None:
                patch.change_summary = f"Created {art.title}: {goal}."
            if not (patch.added_requirements or patch.changed_sections
                    or patch.checklist_items or patch.workflow_sections):
                patch.added_requirements = [f"Document must cover: {goal}."]
            vr = validator.validate_doc_patch(patch, w)
        if not vr.passed:                       # keep the (symbolic) artifact; record why
            patch.validation_status = "rejected"
            patch.rejection_reason = vr.reason
            w.patches[patch.patch_id] = patch          # v6 P0.5: persist rejected patches too
            res.events.append({"type": "product_event", "subtype": "patch_rejected",
                               "artifact_id": oid, "agent_id": aid, "tick": tick, "reason": vr.reason})
            return
        w.apply_product_patch(patch, res, aid)
        self._maybe_surface_patch(w, aid, art, patch, res, tick, is_code=is_code)

    def _maybe_surface_patch(self, w, aid, art, patch, res, tick, *, is_code: bool = False) -> None:
        """v4 §6: after a product patch lands, post a short, concrete update so a case
        study reads like a team (per-agent cooldown to avoid noise)."""
        summary = (getattr(patch, "change_summary", "") or "").strip()
        if not summary or res.messages:         # don't double up if the handler already spoke
            return
        cd = self.__dict__.setdefault("_patch_surface_cd", {})
        if cd.get(aid) is not None and tick - cd[aid] < 4:
            return
        cd[aid] = tick
        channel = "engineering" if is_code else "team_general"
        m = self._send(w, aid, channel, summary, tick, importance="decision_relevant")
        res.messages.append(m.message_id)
        res.events.append({"type": "communication_event", "subtype": "product_update",
                           "agent_id": aid, "tick": tick, "object_id": m.message_id})

    def _h_use_tool(self, w, aid, p, res, tick):
        """Invoke an adopted composed tool: run its required actions (best-effort) so a
        tool created from a proposal actually changes behavior (closes the loop)."""
        pm = getattr(w, "proposal_manager", None)
        tool = (pm.tools.get(p.get("tool_id")) if pm else None)
        if tool is None or tool.status != "active":
            res.success = False
            res.failure_reason = "no_active_tool"
            res.events.append({"type": "action_event", "action_type": "use_tool", "agent_id": aid, "tick": tick})
            return
        # run the composed steps FIRST, then emit the use event carrying what it touched, so
        # the tool company-skill gets a real affected task/issue/artifact chain (v8h P1).
        bm, bc = len(res.modified_objects), len(res.created_objects)
        for a in (tool.required_actions or [])[:3]:
            h = getattr(self, f"_h_{a}", None)
            if h is not None:
                try:
                    h(w, aid, dict(p), res, tick)
                except Exception:
                    pass
        touched = list(dict.fromkeys(res.modified_objects[bm:] + res.created_objects[bc:]))
        if not touched:
            # Nothing in the world changed, so there is no use to record.
            # repeated_protocol_use_rate counts these events and is a component
            # of the capability composite, so a tool that never worked could
            # read as an adopted, repeatedly-used capability. Invoking it is a
            # real attempt; it is just not evidence of a capability.
            #
            # The two reasons are different facts. A tool that declares no steps
            # can never change anything — the no-LLM proposal path emits one
            # whenever the wish text matches no keyword in _NEED_ACTIONS, and a
            # composed_action_tool that composes no actions is not a tool. Steps
            # that ran and touched nothing is a fact about this invocation.
            res.success = False
            res.failure_reason = (
                "tool_declares_no_steps" if not (tool.required_actions or [])
                else "tool_steps_changed_nothing"
            )
            res.events.append({"type": "action_event", "action_type": "use_tool",
                               "tool_id": tool.tool_id, "agent_id": aid, "tick": tick,
                               "failure_reason": res.failure_reason})
            return
        res.events.append({"type": "tool_use_event", "subtype": tool.tool_type, "tool_id": tool.tool_id,
                           "agent_id": aid, "tick": tick,
                           "object_id": touched[0], "affected": touched[:5]})
        res.graph_edges.append((aid, "used_protocol", tool.tool_id))

    # -- governance: explicit proposal approval / rejection (§13.x) --------
    def _h_approve_proposal(self, w, aid, p, res, tick):
        pm = getattr(w, "proposal_manager", None)
        pid = p.get("proposal_id")
        prop = pm.proposals.get(pid) if pm else None
        if prop is None or prop.status != "under_review" or aid not in (prop.approval_required_from or []):
            res.success = False
            res.failure_reason = "not_an_approver_or_not_pending"
            return
        pm.approve_proposal(pid, aid, w)             # adopts once all approvers sign off
        res.events.append({"type": "governance_event", "subtype": "approved", "proposal_id": pid,
                           "agent_id": aid, "tick": tick, "adopted": prop.status == "adopted"})
        res.graph_edges.append((aid, "performed", res.action_id))

    def _h_reject_proposal(self, w, aid, p, res, tick):
        pm = getattr(w, "proposal_manager", None)
        pid = p.get("proposal_id")
        prop = pm.proposals.get(pid) if pm else None
        if prop is None or prop.status != "under_review" or aid not in (prop.approval_required_from or []):
            res.success = False
            res.failure_reason = "not_an_approver_or_not_pending"
            return
        pm.reject_proposal(pid, aid, p.get("reason", "not convinced this is worth the cost"), w)
        res.events.append({"type": "governance_event", "subtype": "rejected", "proposal_id": pid,
                           "agent_id": aid, "tick": tick})

    def _h_request_proposal_changes(self, w, aid, p, res, tick):
        pm = getattr(w, "proposal_manager", None)
        pid = p.get("proposal_id")
        prop = pm.proposals.get(pid) if pm else None
        if prop is None or prop.status != "under_review":
            res.success = False
            res.failure_reason = "not_pending"
            return
        log = w.__dict__.setdefault("_proposal_change_log", {}).setdefault(pid, [])
        # fix the v8-run request_changes loop (Calvin re-requesting on proposal_14 every tick):
        if any(r == aid and tick - t < PROPOSAL_CHANGES_COOLDOWN for r, t in log):
            res.success = False
            res.failure_reason = "already requested changes recently; await revision"
            return
        if len(log) >= PROPOSAL_MAX_CHANGES:
            res.success = False
            res.failure_reason = "max revisions reached; approve or reject instead"
            return
        log.append((aid, tick))
        note = p.get("comment") or "please add evidence / scope it down before I approve"
        prop.suggested_revision = note
        prop.updated_at_tick = tick                  # resets the deadlock timer (still pending)
        res.events.append({"type": "governance_event", "subtype": "changes_requested", "proposal_id": pid,
                           "agent_id": aid, "tick": tick, "note": note,
                           "change_round": len(log)})

    def _h_edit_repo_file(self, w, aid, p, res, tick): self._h_product_action(w, aid, "edit_repo_file", p, res, tick)
    def _h_open_issue(self, w, aid, p, res, tick): self._h_product_action(w, aid, "open_issue", p, res, tick)
    def _h_close_issue(self, w, aid, p, res, tick): self._h_product_action(w, aid, "close_issue", p, res, tick)
    def _h_create_eval_stub(self, w, aid, p, res, tick): self._h_product_action(w, aid, "create_eval_stub", p, res, tick)
    def _h_run_eval_stub(self, w, aid, p, res, tick):
        res.events.append({"type": "experiment_event", "subtype": "run_eval_stub", "agent_id": aid, "tick": tick})

    def _h_dogfood_product(self, w, aid, p, res, tick):
        """v11 dogfooding: actually RUN the company's product (the materialized mainline) and
        experience the real output first-hand — a working report, or credibility=0 / eval_failed
        / a crash — so breakage is felt from USE (and routed into the debugging loop), not only
        discovered at the abstract release gate."""
        from environments.org_env.product.materialize import (
            release_smoke, smoke_error_brief, smoke_quality_issue)
        query = p.get("query") or "What are the leading open-source vector databases and their tradeoffs?"
        sm = release_smoke(w)
        crash = smoke_error_brief(sm)
        quality = smoke_quality_issue(sm)
        metrics = sm.get("metrics") if isinstance(sm.get("metrics"), dict) else {}
        cred = (metrics or {}).get("credibility_score")
        if crash:
            outcome, finding = "crash", crash
            res.success = False
            res.failure_reason = "product_crashed"
            w.__dict__["_build_error"] = crash
        elif quality:
            outcome, finding = "broken_output", quality
            w.__dict__["_build_error"] = quality
        else:
            outcome, finding = "ok", f"works — credibility_score={cred}"
            w.__dict__["_build_error"] = ""        # a clean dogfood clears the stale build error
        res.events.append({"type": "product_event", "subtype": "dogfood", "agent_id": aid, "tick": tick,
                           "outcome": outcome, "finding": str(finding)[:300], "query": query,
                           "credibility_score": cred, "success": outcome == "ok"})
        res.state_delta["dogfood_outcome"] = outcome
        res.state_delta["dogfood_finding"] = str(finding)[:200]
        if outcome != "ok":     # link the suspected file so a follow-up fix targets it
            art = OrgActionMapper._artifact_for_build_error(getattr(w, "product_artifacts", {}) or {}, finding)
            if art is not None:
                res.modified_objects.append(art.artifact_id)
    def _h_create_report_template(self, w, aid, p, res, tick): self._h_product_action(w, aid, "create_report_template", p, res, tick)
    def _h_update_claim_tracker(self, w, aid, p, res, tick): self._h_product_action(w, aid, "update_claim_tracker", p, res, tick)
    def _h_update_source_tracker(self, w, aid, p, res, tick): self._h_product_action(w, aid, "update_source_tracker", p, res, tick)
    def _h_create_product_demo(self, w, aid, p, res, tick): self._h_product_action(w, aid, "create_product_demo", p, res, tick)
    def _h_create_onboarding_doc(self, w, aid, p, res, tick): self._h_product_action(w, aid, "create_onboarding_doc", p, res, tick)
    def _h_write_design_note(self, w, aid, p, res, tick): self._h_product_action(w, aid, "write_design_note", p, res, tick)
    def _h_propose_product_direction(self, w, aid, p, res, tick): self._h_product_action(w, aid, "propose_product_direction", p, res, tick)
    def _h_audit_readme_claims(self, w, aid, p, res, tick): self._h_product_action(w, aid, "audit_readme_claims", p, res, tick)
    def _h_create_report_quality_checklist(self, w, aid, p, res, tick): self._h_product_action(w, aid, "create_report_quality_checklist", p, res, tick)

    # -- generic fallback --------------------------------------------------
    def _h_generic(self, w, aid, at, p, res, tick):
        res.events.append({"type": "action_event", "action_type": at, "agent_id": aid, "tick": tick})

    # -- DomainAdapter-protocol shim --------------------------------------
    def execute_from_state(self, *, agent_id: str, action, state) -> Dict[str, Any]:
        raise NotImplementedError("O1 execution is world-driven; use execute(agent_id, action, org_world)")


__all__ = ["ExecutionResult", "OrgActionMapper", "OrgExecutionAdapter", "MAX_CANDIDATES"]
