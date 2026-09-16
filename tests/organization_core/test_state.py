import json

import pytest

from organization_core import (
    BindingLevel,
    DecisionDisposition,
    DecisionRequest,
    FactPredicate,
    GateClause,
    LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION,
    OrganizationCarrierState,
    OrganizationEpisodeState,
    OrganizationMemberState,
    OrganizationModule,
    OrganizationProposalState,
    OrganizationProtocolState,
    OrganizationReflectionState,
    OrganizationStateBundle,
    OrganizationToolState,
    OrganizationWishState,
    TypedGateRule,
)


def _bundle() -> OrganizationStateBundle:
    return OrganizationStateBundle(
        organization_id="organization:run-1",
        source_repository_id="source-repo",
        source_run_id="run-1",
        source_seed=7,
        source_step=48,
        members=(
            OrganizationMemberState(
                member_id="paul",
                role="reliability",
                skills={"review": 0.8},
                authority={"repo": 0.7},
                go_to_tags=("review",),
            ),
        ),
        protocols=(
            OrganizationProtocolState(
                protocol_id="proto_review",
                protocol_type="review_before_merge",
                rule_summary="review before merge",
                supporters=("paul", "mei"),
                capability="review_gate",
                evidence_refs={
                    "problem": ("episode-1",),
                    "use": ("use-1", "use-2"),
                    "enforcement": ("enforcement-1",),
                },
                gate_rules=(
                    TypedGateRule(
                        rule_id="proto_review",
                        family="review_before_merge",
                        governed_actions=frozenset({"repository.merge"}),
                        clauses=(
                            GateClause(
                                all_of=(
                                    FactPredicate(
                                        "repository.pull_request.reviewed", False
                                    ),
                                ),
                                disposition=DecisionDisposition.DENY,
                                reason="review is required",
                            ),
                        ),
                        binding_level=BindingLevel.VERIFIED_ENFORCED,
                    ),
                ),
            ),
        ),
        carriers=(
            OrganizationCarrierState(
                carrier_id="doc-review",
                carrier_type="review_checklist",
                title="Review checklist",
                capability="review_gate",
                linked_protocol_refs=("proto_review",),
            ),
        ),
        episodes=(
            OrganizationEpisodeState(
                episode_id="episode-1",
                episode_type="debugging",
                status="resolved",
                start_step=10,
                end_step=20,
                participant_ids=("paul",),
                lineage_refs={"event": ("event-1",)},
                attributes={"resolution": "add regression test"},
            ),
        ),
        reflections=(
            OrganizationReflectionState(
                reflection_id="reflection-1",
                member_id="paul",
                step=21,
                source_refs={"episode": ("episode-1",)},
                created_wish_ids=("wish-1",),
            ),
        ),
        wishes=(
            OrganizationWishState(
                wish_id="wish-1",
                member_id="paul",
                wish_type="protocol_need",
                status="converted_to_proposal",
                source_refs={"reflection": ("reflection-1",)},
                generated_proposal_ids=("proposal-1",),
            ),
        ),
        proposals=(
            OrganizationProposalState(
                proposal_id="proposal-1",
                proposal_type="protocol_proposal",
                status="adopted",
                proposer_member_id="paul",
                source_refs={"wish": ("wish-1",)},
                created_step=22,
                updated_step=24,
                created_object_id="proto_review",
            ),
        ),
        tools=(
            OrganizationToolState(
                tool_id="tool-review",
                name="Review workflow",
                tool_type="workflow_tool",
                source_refs={
                    "created_from_proposal_id": ("proposal-1",),
                    "source_wish_id": ("wish-1",),
                    "source_episode_ids": ("episode-1",),
                },
                required_actions=("create_doc", "request_review"),
                required_capabilities=("repo",),
                required_permissions=("write",),
                validation_rules=("document must exist",),
                callable_by_roles=("reliability",),
                supporters=("paul",),
                support_count=1,
                created_step=23,
                adopted_step=24,
                updated_step=24,
            ),
        ),
        capabilities=("review_gate",),
    )


def test_organization_state_is_json_round_trip_stable():
    source = _bundle()
    encoded = json.dumps(source.as_dict(), sort_keys=True)
    restored = OrganizationStateBundle.from_dict(json.loads(encoded))
    assert restored == source
    assert restored.canonical_sha256() == source.canonical_sha256()


def test_legacy_projection_preserves_old_schema_without_new_treatment_fields():
    legacy = _bundle().as_legacy_capability_bundle()
    assert legacy["schema_version"] == LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION
    assert legacy["source_tick"] == 48
    assert legacy["roster"][0]["agent_id"] == "paul"
    assert "authority" not in legacy["roster"][0]
    assert "evidence_refs" not in legacy["protocols"][0]
    assert "tools" not in legacy

    parsed = OrganizationStateBundle.from_legacy_capability_bundle(legacy)
    assert parsed.members[0].member_id == "paul"
    assert parsed.protocols[0].protocol_id == "proto_review"


def test_state_rejects_duplicate_portable_ids():
    protocol = _bundle().protocols[0]
    with pytest.raises(ValueError, match="duplicate protocol"):
        OrganizationStateBundle(
            organization_id="organization:run-1",
            source_repository_id="source-repo",
            source_seed=7,
            source_step=0,
            protocols=(protocol, protocol),
        )


def test_module_activates_executable_rules_restored_from_state():
    source = _bundle()
    restored = OrganizationStateBundle.from_dict(
        json.loads(json.dumps(source.as_dict()))
    )
    module = OrganizationModule(
        organization_id=restored.organization_id,
        run_id="target-host-run",
        state=restored,
    )
    candidate = module.candidate_from_state(
        DecisionRequest(
            request_id="native-request-1",
            actor_id="paul",
            action_type="codex.command_execution",
        ),
        organization_action_type="repository.merge",
        facts={"repository.pull_request.reviewed": False},
    )

    decision = module.decisions.decide_candidate(candidate)

    assert candidate.decision.action_type == "codex.command_execution"
    assert candidate.decision.effective_action_type == "repository.merge"
    assert decision.disposition is DecisionDisposition.DENY
    assert decision.binding_directives[0].rule_id == "proto_review"
