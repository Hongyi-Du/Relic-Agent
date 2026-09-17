"""Pinned behavior and fail-closed checks for HCI growth and policy ports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from relic_agent.source_b3.coding.profile import (
    coding_affinity,
    coding_policy_bonus,
)
from relic_agent.source_b3.growth.lifecycle import (
    SourceB3GrowthHostUnavailableError,
    SourceB3GrowthLifecycleAdapter,
)
from relic_agent.source_b3.growth.objects import effective_skill
from relic_agent.source_b3.growth.provenance import (
    SOURCE_B3_COMMIT as GROWTH_SOURCE_B3_COMMIT,
    SOURCE_B3_GROWTH_FILE_BLOBS,
    SOURCE_B3_GROWTH_IMPORT_REWRITES,
    SOURCE_B3_GROWTH_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY as GROWTH_SOURCE_B3_REPOSITORY,
    SOURCE_B3_UNPORTED_CAPABILITY_EXPERIMENTS,
    source_b3_growth_provenance,
)
from relic_agent.source_b3.policy import (
    SourceB3PolicyHostUnavailableError,
    SourceB3PolicyLifecycleAdapter,
)
from relic_agent.source_b3.policy.provenance import (
    SOURCE_B3_COMMIT as POLICY_SOURCE_B3_COMMIT,
    SOURCE_B3_POLICY_FILE_BLOBS,
    SOURCE_B3_POLICY_IMPORT_REWRITES,
    SOURCE_B3_POLICY_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY as POLICY_SOURCE_B3_REPOSITORY,
    SOURCE_B3_POLICY_UNPORTED_COMPONENTS,
    source_b3_policy_provenance,
)


ROOT = Path(__file__).resolve().parents[1]


def _blob_id(path: str) -> str:
    completed = subprocess.run(
        ["git", "hash-object", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _install_mounted_source_type_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """Install minimal named source modules without constructing an HCI host.

    The adapters require exact class identity against a mounted source module.
    These types only exercise that gate and source-port behavior; they do not
    construct a scenario, candidate pool, selector, or execution adapter.
    """

    environments = ModuleType("environments")
    environments.__path__ = []
    org_env = ModuleType("environments.org_env")
    org_env.__path__ = []
    backend = ModuleType("environments.org_env.backend")
    backend.__path__ = []
    simulation = ModuleType("environments.org_env.backend.simulation")
    simulation.__path__ = []
    world_module = ModuleType("environments.org_env.backend.simulation.world")
    runtime_adapter = ModuleType("environments.org_env.runtime_adapter")
    runtime_adapter.__path__ = []
    execution_module = ModuleType("environments.org_env.runtime_adapter.execution")
    product = ModuleType("environments.org_env.product")
    product.__path__ = []
    product_objects = ModuleType("environments.org_env.product.objects")
    product_objects.artifact_purpose = lambda _value: ""

    agent_sdk = ModuleType("agent_sdk")
    agent_sdk.__path__ = []
    lived = ModuleType("agent_sdk.lived")
    lived.__path__ = []
    core = ModuleType("agent_sdk.lived.core")
    core.__path__ = []
    contracts_module = ModuleType("agent_sdk.lived.core.contracts")

    orgworld_type = type(
        "OrgWorld",
        (),
        {"__module__": world_module.__name__},
    )
    execution_result_type = type(
        "ExecutionResult",
        (),
        {"__module__": execution_module.__name__},
    )
    action_candidate_type = type(
        "ActionCandidate",
        (),
        {"__module__": contracts_module.__name__},
    )
    world_module.OrgWorld = orgworld_type
    execution_module.ExecutionResult = execution_result_type
    contracts_module.ActionCandidate = action_candidate_type

    environments.org_env = org_env
    org_env.backend = backend
    org_env.runtime_adapter = runtime_adapter
    org_env.product = product
    backend.simulation = simulation
    simulation.world = world_module
    runtime_adapter.execution = execution_module
    product.objects = product_objects
    agent_sdk.lived = lived
    lived.core = core
    core.contracts = contracts_module

    for module in (
        environments,
        org_env,
        backend,
        simulation,
        world_module,
        runtime_adapter,
        execution_module,
        product,
        product_objects,
        agent_sdk,
        lived,
        core,
        contracts_module,
    ):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return SimpleNamespace(
        orgworld=orgworld_type,
        execution_result=execution_result_type,
        action_candidate=action_candidate_type,
    )


def _growth_world(orgworld_type: type, *, tick: int = 4) -> tuple[Any, SimpleNamespace]:
    world = orgworld_type()
    agent = SimpleNamespace(
        id="paul",
        skills={"debugging": 0.3, "rapid_prototyping": 0.3},
        profile={"process_commitment": 0.91},
        reputation={},
        authority={},
        go_to_tags=[],
    )
    world.world_tick = tick
    world.agents = {"paul": agent}
    world.tasks = {}
    world.product_artifacts = {}
    world.repo_system = SimpleNamespace(repo=SimpleNamespace(pull_requests={}))
    world.growth_events = []
    return world, agent


def _growth_result(execution_result_type: type) -> Any:
    result = execution_result_type()
    result.action_id = "act_paul_4_edit_repo_file"
    result.agent_id = "paul"
    result.action_type = "edit_repo_file"
    result.success = True
    result.failure_reason = ""
    result.created_objects = []
    result.modified_objects = []
    result.events = [
        {
            "event_id": "repo_event_4",
            "type": "repo_event",
            "subtype": "patch_applied",
        }
    ]
    result.cost_events = []
    result.messages = []
    result.state_delta = {"duration": 2}
    result.memory_delta = []
    result.graph_edges = []
    return result


def _policy_world(orgworld_type: type, *, tick: int = 9) -> Any:
    world = orgworld_type()
    world.world_tick = tick
    world.agents = {"paul": SimpleNamespace(id="paul")}
    world.condition_spec = SimpleNamespace(protocol_masking_enabled=True)
    world.repo_system = SimpleNamespace(
        repo=SimpleNamespace(
            pull_requests={
                "pr_1": SimpleNamespace(pr_id="pr_1", reviewed=False),
            }
        )
    )
    spec = SimpleNamespace(
        status="adopted",
        protocol_id="proto_review_before_merge",
        name="Review before merge",
        trigger_condition="",
        enforcement_rule="",
        violation_condition="",
        declared_action_ids=lambda: {"merge_pr"},
    )
    world.proposal_manager = SimpleNamespace(protocol_specs={spec.protocol_id: spec})
    world._protocol_mirror_is_live_or_absent = lambda _spec: True
    world.events = []
    return world


def _candidate(action_candidate_type: type, action_type: str, **parameters: object) -> Any:
    candidate = action_candidate_type()
    candidate.action_type = action_type
    candidate.parameters = dict(parameters)
    candidate.source = "environment"
    candidate.rationale = ""
    candidate.target_uid = None
    return candidate


@pytest.mark.unit
def test_source_growth_and_policy_ports_are_pinned_to_hci_blobs() -> None:
    assert GROWTH_SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert POLICY_SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert GROWTH_SOURCE_B3_COMMIT == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert POLICY_SOURCE_B3_COMMIT == GROWTH_SOURCE_B3_COMMIT
    assert SOURCE_B3_GROWTH_FILE_BLOBS == {
        "environments/org_env/coding/__init__.py": "f9d1bf6c2477b8e7d42174c26a23388a8c224f89",
        "environments/org_env/coding/profile.py": "eb9ba17cdb2c4ddada42d3c5ef16a359c8254c7b",
        "environments/org_env/growth/__init__.py": "fcc18c08e208ddeed6222012a7386ec5f772fa8c",
        "environments/org_env/growth/objects.py": "b61c7f5f856ce6172d5b7ec4b2de42fc272a9954",
        "environments/org_env/growth/authority.py": "e123ccc0047a41890d120f85c5b0a35e079af72f",
        "environments/org_env/growth/appraiser.py": "66813251464b8b8157ea0601c3bb761cd5073cc9",
        "environments/org_env/growth/reconciler.py": "f404e0fc36d9950f44b5e32938a58411a11c44e4",
    }
    assert SOURCE_B3_POLICY_FILE_BLOBS == {
        "environments/org_env/policy/__init__.py": "147acb6e745defa3040ff575e4291053dc71dc7a",
        "environments/org_env/policy/protocol_affordance.py": "5b21f55f28df0c3c76fd352341b3f202b9f937ca",
        "environments/org_env/policy/attractor_guard.py": "73046ab902a8f74cebf0c17c9ab40b026502baa8",
    }
    assert SOURCE_B3_GROWTH_IMPORT_REWRITES == {
        "environments.org_env.coding.profile": "relic_agent.source_b3.coding.profile",
        "environments.org_env.growth.appraiser": "relic_agent.source_b3.growth.appraiser",
        "environments.org_env.growth.authority": "relic_agent.source_b3.growth.authority",
        "environments.org_env.growth.objects": "relic_agent.source_b3.growth.objects",
        "environments.org_env.growth.reconciler": "relic_agent.source_b3.growth.reconciler",
    }
    assert SOURCE_B3_POLICY_IMPORT_REWRITES == {}
    for path, expected_blob in SOURCE_B3_GROWTH_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob
    for path, expected_blob in SOURCE_B3_POLICY_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob
    assert (
        SOURCE_B3_UNPORTED_CAPABILITY_EXPERIMENTS[
            "environments/org_env/experiments/capability_carriers.py"
        ]["source_blob"]
        == "5c57d68bf28d6015cb97bdfcc13b3a8063081a8e"
    )
    assert (
        SOURCE_B3_POLICY_UNPORTED_COMPONENTS[
            "environments/org_env/runtime_adapter/policy.py"
        ]["source_blob"]
        == "9d8795b85e0ad0a891d0089774f9796ad2a926af"
    )
    assert source_b3_growth_provenance()["port_file_blobs"] == (
        SOURCE_B3_GROWTH_PORT_FILE_BLOBS
    )
    assert source_b3_policy_provenance()["port_file_blobs"] == (
        SOURCE_B3_POLICY_PORT_FILE_BLOBS
    )


@pytest.mark.unit
def test_source_coding_profile_and_growth_reconciliation_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_types = _install_mounted_source_type_modules(monkeypatch)
    assert coding_affinity("fast_engineer", "edit_repo_file") == 0.9
    assert coding_policy_bonus(
        SimpleNamespace(role="fast_engineer"),
        "edit_repo_file",
    ) == pytest.approx(0.12)
    assert effective_skill(
        {"eval_design": 0.64, "experimental_design": 0.88},
        "eval_design",
    ) == 0.88

    adapter = SourceB3GrowthLifecycleAdapter()
    world, agent = _growth_world(source_types.orgworld)
    before_profile = dict(agent.profile)

    signals = adapter.collect_source_result(
        _growth_result(source_types.execution_result),
        actor_id="paul",
        world=world,
    )
    assert len(signals) == 1
    assert signals[0].skill_weights["patch_generation"] > 0
    assert signals[0].rep_weights["engineering_execution"] > 0

    outcome = adapter.reconcile_source_growth(world=world)
    assert outcome["signals"] == 1
    assert outcome["growth_events"] >= 1
    assert agent.skills["debugging"] > 0.3
    assert agent.profile == before_profile
    assert world._growth_signals == []
    status = adapter.status().as_dict()
    assert status["source_host_binding"] == "explicit_source_orgworld_seen"
    assert status["collected_signal_count"] == 1
    assert status["applied_growth_event_count"] >= 1


@pytest.mark.unit
def test_source_growth_adapter_rejects_shell_inputs() -> None:
    adapter = SourceB3GrowthLifecycleAdapter()
    with pytest.raises(SourceB3GrowthHostUnavailableError, match="orgworld_required"):
        adapter.reconcile_source_growth(world=None)
    with pytest.raises(SourceB3GrowthHostUnavailableError, match="requires_hci_orgworld"):
        adapter.reconcile_source_growth(world=SimpleNamespace())
    assert not hasattr(adapter, "observe")
    assert adapter.status().as_dict()["source_host_binding"] == "unbound_no_source_orgworld"


@pytest.mark.unit
def test_source_host_gates_require_mounted_exact_class_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_types = _install_mounted_source_type_modules(monkeypatch)
    growth = SourceB3GrowthLifecycleAdapter()
    world, _ = _growth_world(source_types.orgworld)
    lookalike_world = type(
        "OrgWorld",
        (),
        {"__module__": "environments.org_env.backend.simulation.world"},
    )()
    lookalike_result = type(
        "ExecutionResult",
        (),
        {"__module__": "environments.org_env.runtime_adapter.execution"},
    )()

    with pytest.raises(SourceB3GrowthHostUnavailableError, match="requires_hci_orgworld"):
        growth.reconcile_source_growth(world=lookalike_world)
    with pytest.raises(
        SourceB3GrowthHostUnavailableError,
        match="requires_hci_execution_result",
    ):
        growth.collect_source_result(lookalike_result, actor_id="paul", world=world)

    policy = SourceB3PolicyLifecycleAdapter()
    lookalike_candidate = type(
        "ActionCandidate",
        (),
        {"__module__": "agent_sdk.lived.core.contracts"},
    )()
    lookalike_candidate.action_type = "merge_pr"
    lookalike_candidate.parameters = {"pr_id": "pr_1"}
    with pytest.raises(
        SourceB3PolicyHostUnavailableError,
        match="requires_hci_action_candidate",
    ):
        policy.filter_source_candidates(
            [lookalike_candidate],
            agent_id="paul",
            world=_policy_world(source_types.orgworld),
        )


@pytest.mark.unit
def test_source_protocol_affordance_masks_only_explicit_hci_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_types = _install_mounted_source_type_modules(monkeypatch)
    adapter = SourceB3PolicyLifecycleAdapter()
    world = _policy_world(source_types.orgworld)
    merge = _candidate(source_types.action_candidate, "merge_pr", pr_id="pr_1")
    search = _candidate(
        source_types.action_candidate,
        "internal_search",
        query="review evidence",
    )

    kept, blocked = adapter.filter_source_candidates(
        [merge, search],
        agent_id="paul",
        world=world,
    )

    assert kept == [search]
    assert blocked[0][0] is merge
    assert "proto_review_before_merge forbids merge_pr" in blocked[0][1]
    assert world.events == [
        {
            "type": "protocol_prevented_action_event",
            "tick": 9,
            "agent_id": "paul",
            "action": "merge_pr",
            "reason": blocked[0][1],
        }
    ]
    status = adapter.status().as_dict()
    assert status["source_host_binding"] == "explicit_source_orgworld_seen"
    assert status["prevented_action_count"] == 1


@pytest.mark.unit
def test_source_policy_adapter_rejects_shell_world_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_types = _install_mounted_source_type_modules(monkeypatch)
    adapter = SourceB3PolicyLifecycleAdapter()
    with pytest.raises(SourceB3PolicyHostUnavailableError, match="orgworld_required"):
        adapter.filter_source_candidates([], agent_id="paul", world=None)
    with pytest.raises(SourceB3PolicyHostUnavailableError, match="requires_hci_orgworld"):
        adapter.filter_source_candidates([], agent_id="paul", world=SimpleNamespace())
    with pytest.raises(
        SourceB3PolicyHostUnavailableError,
        match="requires_hci_action_candidate",
    ):
        adapter.filter_source_candidates(
            [SimpleNamespace(action_type="merge_pr", parameters={"pr_id": "pr_1"})],
            agent_id="paul",
            world=_policy_world(source_types.orgworld),
        )
    assert not hasattr(adapter, "select")
    status = adapter.status().as_dict()
    assert status["source_host_binding"] == "unbound_no_source_orgworld"
    assert "source_attractor_guard" in status["unavailable_fail_closed"]
