"""Source HCI proposal/tool family classifier."""
from __future__ import annotations

from typing import Any


FAMILIES = (
    "evidence_governance", "release_engineering", "debugging", "budget_governance",
    "customer", "ownership", "experiment", "review_merge", "docs", "other",
)

_FAMILY_KEYWORDS = {
    "evidence_governance": ("evidence", "claim", "source credib", "credibility", "citation",
                            "traceab", "verif", "fact-check", "provenance", "unsupported"),
    "release_engineering": ("release", "launch", "readiness", "rollback", "ship it",
                            "deploy", "publish", "release gate"),
    "debugging": ("debug", "smoke", "ci fail", "stack trace", "traceback", "localize",
                  "bug fix", "regression", "integration error", "broken build", "blocker"),
    "budget_governance": ("budget", "token", "cost", "spend", "expensive", "quota", "pricing"),
    "customer": ("customer", "user feedback", "complaint", "triage", "support ticket", "churn"),
    "ownership": ("owner", "ownership", "assign owner", "accountab", "responsib", "raci"),
    "experiment": ("experiment", "pilot", "baseline", "ablation", "benchmark", "reproduc"),
    "review_merge": ("review", "approve", "sign-off", "signoff", "merge", "pull request",
                     "before merge"),
    "docs": ("readme", "documentation", "report quality", "changelog", "writeup", "doc clarity"),
}


def _text(proposal: Any) -> str:
    parts = [
        getattr(proposal, "title", "") or getattr(proposal, "name", ""),
        getattr(proposal, "summary", "") or getattr(proposal, "description", ""),
        getattr(proposal, "target_problem", ""),
        getattr(proposal, "proposed_solution", "") or getattr(proposal, "enforcement_rule", ""),
        " ".join(getattr(proposal, "required_actions", []) or []),
        " ".join(getattr(proposal, "required_artifacts", []) or getattr(proposal, "required_fields", []) or []),
    ]
    return " ".join(str(item) for item in parts if item).lower()


def classify_family(proposal: Any) -> str:
    text = _text(proposal)
    best, best_score = "other", 0
    for family, keywords in _FAMILY_KEYWORDS.items():
        score = sum(2 if " " in keyword else 1 for keyword in keywords if keyword in text)
        if score > best_score:
            best, best_score = family, score
    return best


__all__ = ["FAMILIES", "classify_family"]
