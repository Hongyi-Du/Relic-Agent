"""Vendored runtime namespace from SocioGenesis.

The upstream package initializer eagerly imports its unrelated ``quick``
facade.  That facade in turn imports provider SDK code that is deliberately
outside the Relic-Agent B3 runtime closure.  Keeping this namespace inert is
necessary for importing the source-native ``agent_sdk.lived`` implementation
without silently pulling a second, unsupported runtime into a release run.

The carried source modules retain their upstream top-level import paths
(``agent_sdk.lived.*``).  The public quick API is intentionally unavailable in
this extraction.
"""

__all__: list[str] = []
