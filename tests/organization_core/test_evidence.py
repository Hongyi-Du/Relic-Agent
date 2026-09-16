import json

from organization_core import (
    CapabilityEnforcementEvidence,
    CapabilityEvidenceBundle,
    CapabilityTransferEvidence,
    CapabilityUseEvidence,
)


def test_capability_evidence_is_json_round_trip_stable():
    source = CapabilityEvidenceBundle(
        shared_artifact_refs=("protocol-review",),
        origin_episode_id="episode-1",
        use_events=(
            CapabilityUseEvidence(
                event_id="use-1",
                actor_id="reviewer",
                tick=12,
                shared_artifact_ref="protocol-review",
                episode_id="episode-2",
                support_refs=("task-1",),
            ),
        ),
        enforcement_events=(
            CapabilityEnforcementEvidence(
                event_id="enforce-1",
                violation_event_id="violation-1",
                state_impact_ref="pull-request-1",
                support_refs=("before-1", "after-1"),
            ),
        ),
        transfer_events=(
            CapabilityTransferEvidence(
                event_id="transfer-1",
                held_out_target_id="repo-2",
                capability_invocation_ref="invocation-1",
                baseline_outcome=0.3,
                capability_outcome=0.7,
            ),
        ),
    )

    transported = json.loads(json.dumps(source.as_dict(), sort_keys=True))

    assert CapabilityEvidenceBundle.from_dict(transported) == source


def test_society_core_reexports_the_shared_evidence_types():
    from society_core.organizational_capabilities import (
        CapabilityEvidenceBundle as SocietyEvidenceBundle,
        CapabilityUseEvidence as SocietyUseEvidence,
    )

    assert SocietyEvidenceBundle is CapabilityEvidenceBundle
    assert SocietyUseEvidence is CapabilityUseEvidence
