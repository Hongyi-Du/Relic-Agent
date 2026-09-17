"""Narrow namespace for source-native runtime-adapter modules.

Release execution imports concrete adapters only where the original world does
so.  The upstream aggregator is intentionally excluded because it eagerly
pulls unrelated host and evaluator surfaces into the import graph.
"""

__all__: list[str] = []
