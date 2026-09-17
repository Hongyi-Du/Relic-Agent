"""Narrow namespace for source-native scenario modules.

The standalone host imports ``config.scenarios`` explicitly.  Avoid eager
metric and experiment imports from this package initializer so unsupported
evaluator paths cannot become incidental runtime dependencies.
"""

__all__: list[str] = []
