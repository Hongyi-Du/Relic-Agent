"""Explicit capability boundaries for the source proposal-manager port.

The HCI manager normally receives a full ``OrgWorld``.  Relic Agent does not
pretend that its compatibility runtime is that world.  These small checks are
the only host seam used by the port: missing action discovery, a live source
protocol registry, or the optional ProgramBench repair machinery is never
silently substituted with a local policy.
"""
from __future__ import annotations

from typing import Any


class SourceProposalCapabilityUnavailableError(RuntimeError):
    """A source proposal operation needs an OrgWorld capability not mounted here."""


def registered_action_types(world: Any) -> tuple[str, ...]:
    """Return an explicitly mounted action catalogue, or no catalogue.

    The source validator deliberately accepts an empty catalogue when source
    action discovery is unavailable.  This preserves its validation fallback;
    a host that wants action validation must mount the authoritative list.
    """

    values = getattr(world, "known_actions", ())
    if callable(values):
        values = values()
    if values is None:
        return ()
    return tuple(str(value) for value in values)


def protocol_materialization_enabled(world: Any) -> bool:
    """Whether this host explicitly mounted the source registry boundary."""

    return bool(getattr(world, "source_protocol_materialization_enabled", False))


def institutionalization_enabled(world: Any) -> bool:
    """Port the source ablation gate without importing paper-run machinery."""

    return bool(getattr(world, "institutionalization_enabled", False)) and not bool(
        getattr(world, "institutionalization_ablated", False)
    )


def unavailable_programbench_registry_repair(*_args: Any, **_kwargs: Any) -> None:
    """Keep ProgramBench-only repair materialization explicitly unavailable."""

    raise SourceProposalCapabilityUnavailableError(
        "source_programbench_registry_repair_unavailable"
    )


__all__ = [
    "SourceProposalCapabilityUnavailableError",
    "institutionalization_enabled",
    "protocol_materialization_enabled",
    "registered_action_types",
    "unavailable_programbench_registry_repair",
]
