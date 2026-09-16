from organization_core import (
    OrganizationEpisodeState,
    OrganizationFormationRuntime,
    OrganizationMemberState,
    OrganizationEvent,
    OrganizationEventType,
    OrganizationProposalState,
    OrganizationReflectionState,
    OrganizationStateBundle,
    OrganizationSynthesisKind,
    OrganizationSynthesisResult,
    OrganizationProvenance,
    OrganizationWishState,
)


def _state():
    return OrganizationStateBundle(
        organization_id="org-1",
        source_repository_id="repo-1",
        source_run_id="source-run",
        source_seed=1401,
        source_step=0,
        members=(
            OrganizationMemberState(member_id="member-1", role="engineer"),
        ),
    )


def _episode():
    return OrganizationEpisodeState(
        episode_id="episode-1",
        episode_type="debugging",
        status="closed",
        start_step=1,
        end_step=5,
        participant_ids=("member-1",),
        primary_member_id="member-1",
        attributes={"outcome": "failed", "evidence": ["test-1"]},
    )


def test_formation_runtime_owns_shared_records_and_roundtrips_state():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")

    assert runtime.record_episode(_episode()) is True
    assert runtime.record_episode(_episode()) is False
    context = runtime.query_context(member_id="member-1")
    bundle = runtime.state_bundle(source_step=5)

    assert context["members"][0]["member_id"] == "member-1"
    assert bundle.episodes == (_episode(),)
    assert bundle.source_run_id == "run-1"
    assert OrganizationStateBundle.from_dict(bundle.as_dict()) == bundle


def test_reflection_and_wishes_are_an_atomic_lineage_transaction():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    reflection = OrganizationReflectionState(
        reflection_id="reflection-1",
        member_id="member-1",
        step=6,
        source_refs={"episode": ("episode-1",)},
        created_wish_ids=("wish-1",),
        attributes={"assessment": "review evidence before merge"},
    )
    wish = OrganizationWishState(
        wish_id="wish-1",
        member_id="member-1",
        wish_type="coordination_need",
        status="open",
        source_refs={"reflection": ("reflection-1",)},
        attributes={"urgency": 0.9, "target_problem": "missing review"},
    )

    assert runtime.record_reflection(reflection, wishes=(wish,)) is True
    bundle = runtime.state_bundle()

    assert bundle.reflections == (reflection,)
    assert bundle.wishes == (wish,)


def test_scheduler_requests_harness_synthesis_and_applies_validated_results():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    runtime.record_episode(_episode())

    class Harness:
        def synthesize(self, request):
            assert request.kind is OrganizationSynthesisKind.REFLECTION
            return OrganizationSynthesisResult(
                request_id=request.request_id,
                status="completed",
                host="codex-app-server",
                model="gpt-5.6-terra",
                thread_id="thread-member-1",
                turn_id="turn-6",
                output={
                    "reflection": {
                        "reflection_id": "reflection-1",
                        "member_id": "member-1",
                        "step": 6,
                        "source_refs": {"episode": ["episode-1"]},
                        "created_wish_ids": ["wish-1"],
                        "attributes": {"assessment": "missing review"},
                    },
                    "wishes": [
                        {
                            "wish_id": "wish-1",
                            "member_id": "member-1",
                            "wish_type": "coordination_need",
                            "status": "open",
                            "source_refs": {"reflection": ["reflection-1"]},
                            "attributes": {
                                "urgency": 0.9,
                                "target_problem": "missing review",
                            },
                        }
                    ],
                },
            )

    results = runtime.run_due_synthesis(Harness(), step=6)

    assert len(results) == 1
    assert results[0].host == "codex-app-server"
    assert runtime.state_bundle().reflections[0].reflection_id == "reflection-1"
    followups = runtime.due_synthesis_requests(step=6)
    assert [request.kind for request in followups] == [
        OrganizationSynthesisKind.PROPOSAL
    ]


def test_stable_wish_becomes_reviewable_proposal_not_auto_adopted():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    reflection = OrganizationReflectionState(
        reflection_id="reflection-1",
        member_id="member-1",
        step=6,
        created_wish_ids=("wish-1",),
    )
    wish = OrganizationWishState(
        wish_id="wish-1",
        member_id="member-1",
        wish_type="tool_need",
        status="open",
        source_refs={"reflection": ("reflection-1",)},
        attributes={"urgency": 0.9},
    )
    runtime.record_reflection(reflection, wishes=(wish,))
    requests = runtime.due_synthesis_requests(step=12)
    proposal_request = next(
        request
        for request in requests
        if request.kind is OrganizationSynthesisKind.PROPOSAL
    )
    result = OrganizationSynthesisResult(
        request_id=proposal_request.request_id,
        status="completed",
        host="codex-app-server",
        output={
            "proposal": OrganizationProposalState(
                proposal_id="proposal-1",
                proposal_type="tool_proposal",
                status="draft",
                proposer_member_id="member-1",
                source_refs={"wish": ("wish-1",)},
                created_step=12,
                updated_step=12,
            ).as_dict()
        },
    )

    assert runtime.apply_synthesis_result(proposal_request, result) is True
    bundle = runtime.state_bundle()

    assert bundle.proposals[0].status == "draft"
    assert bundle.wishes[0].status == "proposed"
    assert bundle.wishes[0].generated_proposal_ids == ("proposal-1",)


def test_tool_surface_is_harness_neutral_and_idempotent():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    tools = {tool.name: tool for tool in runtime.tool_definitions()}

    result = tools["record_episode"].handler({"episode": _episode().as_dict()})
    replay = tools["record_episode"].handler({"episode": _episode().as_dict()})

    assert result == {"recorded": True, "episode_id": "episode-1"}
    assert replay == {"recorded": False, "episode_id": "episode-1"}
    assert {"query_context", "record_episode", "record_reflection", "submit_proposal"} == set(tools)


def test_legacy_projection_reconciliation_tracks_add_update_remove_delta():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    first = runtime.reconcile_state(
        OrganizationStateBundle.from_dict(
            {
                **_state().as_dict(),
                "source_run_id": "run-1",
                "source_step": 5,
                "episodes": [_episode().as_dict()],
            }
        )
    )
    updated_episode = OrganizationEpisodeState(
        **{
            **_episode().as_dict(),
            "attributes": {"outcome": "resolved"},
        }
    )
    second_state = _state()
    second_state = OrganizationStateBundle.from_dict(
        {
            **second_state.as_dict(),
            "source_run_id": "run-1",
            "source_step": 6,
            "episodes": [updated_episode.as_dict()],
        }
    )
    second = runtime.reconcile_state(second_state)

    assert first["episodes"] == {"added": 1, "updated": 0, "removed": 0}
    assert second["episodes"] == {"added": 0, "updated": 1, "removed": 0}
    assert runtime.state_bundle().episodes == (updated_episode,)


def test_policy_harm_event_schedules_repair_through_harness_port():
    runtime = OrganizationFormationRuntime(_state(), run_id="run-1")
    event = OrganizationEvent(
        event_id="harm-1",
        event_type=OrganizationEventType.POLICY_HARM_DETECTED,
        step=48,
        provenance=OrganizationProvenance("test", "fake-harness", "run-1"),
        subject_refs=("protocol-1",),
        payload={
            "protocol_id": "protocol-1",
            "reason": "blocked without delivery",
            "blocked_requests": 4,
        },
    )

    assert runtime.publish(event) is True
    requests = runtime.due_synthesis_requests(step=48)
    repairs = [
        request
        for request in requests
        if request.kind is OrganizationSynthesisKind.REPAIR
    ]

    assert len(repairs) == 1
    assert repairs[0].source_refs == ("protocol-1",)
    assert repairs[0].context["harm_signal"]["blocked_requests"] == 4
