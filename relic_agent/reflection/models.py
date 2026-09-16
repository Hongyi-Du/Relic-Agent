"""Compatibility imports for source-pinned HCI reflection contracts.

The former release-shell dataclasses were a locally invented approximation.
Keep this legacy import path for callers, but expose the exact HCI reflection
objects instead.
"""

from relic_agent.source_b3.reflection.objects import (
    AgentLogEntry,
    AgentMemory,
    AgentReflection,
    NEED_TYPE_MAP,
    SUPPORT_TYPES,
    WISH_STATUS,
    WISH_TYPES,
    Wish,
    canon_wish_type,
    make_wish_fingerprint,
    support_type_for,
)

__all__ = [
    "AgentLogEntry",
    "AgentMemory",
    "AgentReflection",
    "NEED_TYPE_MAP",
    "SUPPORT_TYPES",
    "WISH_STATUS",
    "WISH_TYPES",
    "Wish",
    "canon_wish_type",
    "make_wish_fingerprint",
    "support_type_for",
]
