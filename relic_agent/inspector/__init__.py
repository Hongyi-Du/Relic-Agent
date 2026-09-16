"""Public, trace-only Relic Inspector."""

from relic_agent.inspector.server import (
    InspectorError,
    create_inspector_server,
    inspector_static_root,
    serve_inspector,
)

__all__ = [
    "InspectorError",
    "create_inspector_server",
    "inspector_static_root",
    "serve_inspector",
]
