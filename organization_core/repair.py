"""Portable policy-harm evaluation for organizational self-repair."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolRepairPolicy:
    block_window_steps: int = 48
    blocked_requests_min: int = 3
    violations_min: int = 4
    violation_share_min: float = 0.5

    def __post_init__(self) -> None:
        for name in (
            "block_window_steps",
            "blocked_requests_min",
            "violations_min",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"repair policy {name} must be positive")
        if not 0.0 <= float(self.violation_share_min) <= 1.0:
            raise ValueError("violation_share_min must be in [0, 1]")

    def evaluate(self, signal: "ProtocolHarmSignal") -> "ProtocolRepairDecision":
        total = max(signal.violations + signal.compliant_uses, 1)
        if (
            signal.violations >= self.violations_min
            and signal.violations / total >= self.violation_share_min
        ):
            return ProtocolRepairDecision(
                harmful=True,
                reason_code="unmeetable_rule",
                reason=(
                    f"it is broken {signal.violations} times against "
                    f"{signal.compliant_uses} compliant uses — nobody can meet "
                    "it as written"
                ),
            )
        if (
            signal.blocked_requests >= self.blocked_requests_min
            and signal.blocked_requests > signal.delivered_requests
        ):
            landed = (
                f"only {signal.delivered_requests} reached the mainline"
                if signal.delivered_requests
                else "nothing reached the mainline"
            )
            return ProtocolRepairDecision(
                harmful=True,
                reason_code="blocked_without_delivery",
                reason=(
                    f"it refused {signal.blocked_requests} requests in the last "
                    f"{self.block_window_steps} ticks while {landed} in that time "
                    "— it is being followed, and it is what the work is waiting on"
                ),
            )
        return ProtocolRepairDecision(
            harmful=False,
            reason_code="rule_within_guardrails",
        )


DEFAULT_PROTOCOL_REPAIR_POLICY = ProtocolRepairPolicy()


@dataclass(frozen=True)
class ProtocolHarmSignal:
    protocol_id: str
    violations: int = 0
    compliant_uses: int = 0
    blocked_requests: int = 0
    delivered_requests: int = 0

    def __post_init__(self) -> None:
        if not str(self.protocol_id or "").strip():
            raise ValueError("protocol harm signal protocol_id is required")
        for name in (
            "violations",
            "compliant_uses",
            "blocked_requests",
            "delivered_requests",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"protocol harm signal {name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class ProtocolRepairDecision:
    harmful: bool
    reason_code: str
    reason: str = ""


__all__ = [
    "DEFAULT_PROTOCOL_REPAIR_POLICY",
    "ProtocolHarmSignal",
    "ProtocolRepairDecision",
    "ProtocolRepairPolicy",
]
