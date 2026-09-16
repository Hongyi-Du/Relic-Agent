import ast
import math
from pathlib import Path

import pytest

from organization_core import (
    BindingDirective,
    BindingLevel,
    BindingReceipt,
    DecisionRequest,
    DecisionDisposition,
    HarnessCapabilities,
    HostDecisionResult,
    OrganizationDecision,
    OrganizationEvent,
    OrganizationEventSink,
    OrganizationEventType,
    OrganizationHostAdapter,
    OrganizationProvenance,
    ShadowOrganizationRuntime,
)


def _event(event_id: str, event_type: OrganizationEventType, step: int, **payload):
    return OrganizationEvent(
        event_id=event_id,
        event_type=event_type,
        step=step,
        provenance=OrganizationProvenance(
            source="test",
            host="fake_harness",
            run_id="run-1",
        ),
        payload=payload,
    )


def test_event_payload_is_detached_and_json_compatible():
    source = {"nested": ["a"]}
    event = _event("e1", OrganizationEventType.OBSERVATION_RECEIVED, 0, **source)
    source["nested"].append("mutated")
    assert event.payload == {"nested": ["a"]}
    exported = event.as_dict()
    exported["payload"]["nested"].append("export-mutated")
    assert event.payload == {"nested": ["a"]}

    with pytest.raises(TypeError, match="JSON-compatible"):
        _event("e2", OrganizationEventType.OBSERVATION_RECEIVED, 0, bad=object())
    with pytest.raises(TypeError, match="finite JSON numbers"):
        _event(
            "e3",
            OrganizationEventType.OBSERVATION_RECEIVED,
            0,
            bad=math.nan,
        )


def test_enforcement_cannot_be_claimed_without_host_acknowledgement():
    with pytest.raises(ValueError, match="host acknowledgement"):
        BindingReceipt(
            receipt_id="receipt-1",
            rule_id="rule-1",
            level=BindingLevel.ENFORCED,
            decision=DecisionDisposition.DENY,
            host_acknowledged=False,
        )

    with pytest.raises(ValueError, match="host acknowledgement"):
        BindingReceipt(
            receipt_id="receipt-string-level",
            rule_id="rule-1",
            level="enforced",
            decision="deny",
            host_acknowledged=False,
        )

    with pytest.raises(ValueError, match="outcome_ref"):
        BindingReceipt(
            receipt_id="receipt-2",
            rule_id="rule-1",
            level=BindingLevel.VERIFIED_ENFORCED,
            decision=DecisionDisposition.REQUIRE_APPROVAL,
            host_acknowledged=True,
        )


def test_external_host_can_implement_the_adapter_without_core_subclassing():
    class FakeHost:
        def capabilities(self):
            return HarnessCapabilities(pre_execution_interception=True)

        def apply_decision(self, request, decision):
            return HostDecisionResult(
                request_id=request.request_id,
                decision_applied=True,
                action_permitted=(
                    decision.disposition is DecisionDisposition.ALLOW
                ),
            )

    host = FakeHost()
    request = DecisionRequest("request-1", "agent-1", "edit_file")
    decision = OrganizationDecision(request_id=request.request_id)
    assert isinstance(host, OrganizationHostAdapter)
    assert host.capabilities().pre_execution_interception is True
    result = host.apply_decision(request, decision)
    assert result.request_id == "request-1"
    assert result.decision_applied
    assert result.action_permitted


def test_binding_directive_is_distinct_from_host_acknowledgement():
    directive = BindingDirective(
        rule_id="review-rule",
        level=BindingLevel.ENFORCED,
        decision=DecisionDisposition.DENY,
        reason="review is missing",
        subject_refs=("pr-1",),
    )
    decision = OrganizationDecision(
        request_id="request-1",
        disposition=DecisionDisposition.DENY,
        binding_directives=(directive,),
    )
    assert decision.binding_directives == (directive,)
    assert decision.binding_receipts == ()
    assert decision.as_dict()["binding_directives"] == [directive.as_dict()]


def test_shadow_runtime_is_idempotent_and_replayable():
    events = [
        _event(
            "e1",
            OrganizationEventType.ORGANIZATION_INITIALIZED,
            0,
            members=[{"member_id": "a", "role": "builder"}],
        ),
        OrganizationEvent(
            event_id="e2",
            event_type=OrganizationEventType.ACTION_EXECUTED,
            step=1,
            provenance=OrganizationProvenance("test", "fake_harness", "run-1"),
            actor_id="a",
            payload={"action_type": "edit", "success": True},
        ),
    ]
    first = ShadowOrganizationRuntime("org-1", "run-1")
    assert isinstance(first, OrganizationEventSink)
    first.replay(events)
    assert first.publish(events[-1]) is False

    second = ShadowOrganizationRuntime("org-1", "run-1")
    second.replay(first.events)
    assert second.snapshot().as_dict() == first.snapshot().as_dict()
    assert second.snapshot().successful_actions == 1


def test_shadow_runtime_rejects_conflicting_ids_and_backward_steps():
    runtime = ShadowOrganizationRuntime("org-1", "run-1")
    runtime.publish(_event("e1", OrganizationEventType.TICK_COMPLETED, 2))
    with pytest.raises(ValueError, match="different content"):
        runtime.publish(_event("e1", OrganizationEventType.TICK_COMPLETED, 3))
    with pytest.raises(ValueError, match="backwards"):
        runtime.publish(_event("e2", OrganizationEventType.TICK_COMPLETED, 1))


def test_organization_core_has_no_environment_harness_or_provider_imports():
    root = Path(__file__).resolve().parents[2] / "organization_core"
    forbidden = ("environments", "agent_sdk.harness", "openai", "litellm")
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.startswith(forbidden):
                    violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []
