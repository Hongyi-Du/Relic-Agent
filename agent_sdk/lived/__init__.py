"""Narrow namespace for the vendored source-native organization host.

Upstream re-exports the whole experimental SDK here. Python executes this
initializer before importing a child module, so retaining those re-exports
would load unrelated provider, harness, and visualization paths before the
standalone ``OrgWorld`` is built. The release host uses explicit source paths
(for example ``agent_sdk.lived.domain.interfaces``) instead.

This is an import-boundary adapter only: source modules that participate in
the B3 lifecycle remain unmodified. The broad upstream facade is deliberately
not a public Relic-Agent API.
"""

__all__: list[str] = []
