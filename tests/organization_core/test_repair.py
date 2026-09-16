import pytest

from organization_core import (
    ProtocolHarmSignal,
    ProtocolRepairPolicy,
)


@pytest.mark.parametrize(
    ("signal", "harmful", "reason_code"),
    [
        (
            ProtocolHarmSignal("protocol-1", violations=4, compliant_uses=3),
            True,
            "unmeetable_rule",
        ),
        (
            ProtocolHarmSignal(
                "protocol-1",
                violations=1,
                compliant_uses=20,
                blocked_requests=4,
                delivered_requests=1,
            ),
            True,
            "blocked_without_delivery",
        ),
        (
            ProtocolHarmSignal(
                "protocol-1",
                violations=1,
                compliant_uses=20,
                blocked_requests=3,
                delivered_requests=8,
            ),
            False,
            "rule_within_guardrails",
        ),
    ],
)
def test_policy_harm_decision_is_portable(signal, harmful, reason_code):
    decision = ProtocolRepairPolicy().evaluate(signal)

    assert decision.harmful is harmful
    assert decision.reason_code == reason_code
