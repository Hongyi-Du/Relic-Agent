import math

import pytest

from organization_core import (
    OrganizationSynthesisKind,
    OrganizationSynthesisPort,
    OrganizationSynthesisRequest,
    OrganizationSynthesisResult,
    OrganizationSynthesisStatus,
    validate_synthesis_result,
)


def _request():
    return OrganizationSynthesisRequest(
        request_id="reflection:run-1:6:member-1",
        organization_id="org-1",
        run_id="run-1",
        kind=OrganizationSynthesisKind.REFLECTION,
        step=6,
        actor_id="member-1",
        source_refs=("episode-1", "episode-1"),
        instructions="Reflect on the bounded episode evidence.",
        context={"episode": {"outcome": "failed"}},
        output_schema={
            "type": "object",
            "required": ["assessment"],
        },
    )


def test_synthesis_request_is_provider_neutral_and_detached():
    request = _request()

    assert request.kind is OrganizationSynthesisKind.REFLECTION
    assert request.source_refs == ("episode-1",)
    assert "provider" not in request.as_dict()
    assert request.as_dict()["context"]["episode"]["outcome"] == "failed"

    with pytest.raises(TypeError, match="finite JSON numbers"):
        OrganizationSynthesisRequest(
            request_id="bad",
            organization_id="org-1",
            run_id="run-1",
            kind="reflection",
            step=1,
            instructions="reflect",
            context={"score": math.nan},
        )


def test_external_harness_implements_synthesis_without_core_subclassing():
    class FakeHarness:
        def synthesize(self, request):
            return OrganizationSynthesisResult(
                request_id=request.request_id,
                status="completed",
                host="fake-harness",
                model="fake-model",
                thread_id="thread-1",
                turn_id="turn-1",
                output={"assessment": "review evidence before merge"},
            )

    harness = FakeHarness()
    request = _request()
    assert isinstance(harness, OrganizationSynthesisPort)
    result = validate_synthesis_result(request, harness.synthesize(request))

    assert result.status is OrganizationSynthesisStatus.COMPLETED
    assert result.completed
    assert result.host == "fake-harness"


def test_synthesis_result_must_match_request_and_preserve_failure():
    request = _request()
    failure = OrganizationSynthesisResult(
        request_id=request.request_id,
        status="failed",
        host="codex-app-server",
        error="turn failed",
    )
    assert validate_synthesis_result(request, failure) is failure

    with pytest.raises(ValueError, match="does not match"):
        validate_synthesis_result(
            request,
            OrganizationSynthesisResult(
                request_id="other",
                status="completed",
                host="codex-app-server",
                output={},
            ),
        )
