"""Narrow namespace for the vendored source-native organization host.

Upstream's package initializer eagerly imports ``runtime_adapter``.  That is
the broad experiment/harness façade, not an input to the standalone
``backend.simulation.OrgWorld`` run path.  Importing it from a release CLI
would unnecessarily make optional evaluator and provider-adjacent modules
part of the process import closure.

Keep this initializer inert so imports of the real host use their explicit
module paths.  This is an import-boundary adapter only: no source world code
is changed, and callers needing an upstream-only adapter must import it
explicitly (it is intentionally outside the release surface).
"""

__all__: list[str] = []
