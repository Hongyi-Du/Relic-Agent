"""Example generic-application SDL scorer. No model or external I/O is needed."""


def execute(features, context):
    """Return a finite utility for one action candidate.

    The application can replace this function without modifying Relic Agent.
    ``context`` is a snapshot, not a mutable organization world.
    """
    progress = float(features.get("progress_gain", 0.0))
    review = float(features.get("review_quality_gain", 0.0))
    weights = context["config"]
    score = weights.get("progress", 1.0) * progress + weights.get("review", 1.0) * review
    if context["action"] == "use_tool":
        score += weights.get("tool_bonus", 0.0)
    return score
