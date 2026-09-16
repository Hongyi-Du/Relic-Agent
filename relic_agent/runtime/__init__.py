"""Compatibility runtime entry point; active HCI execution remains fail-closed."""

from relic_agent.runtime.engine import OrganizationRuntime, RunResult

__all__ = ["OrganizationRuntime", "RunResult"]
