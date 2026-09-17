"""Evaluation metrics for a research run (real, simple)."""


def run_eval(claims, sources):
    n = len(claims)
    supported = sum(1 for c in claims if getattr(c, "source_ids", None))
    used = set()
    for c in claims:
        for sid in (getattr(c, "source_ids", None) or []):
            used.add(sid)
    nsrc = max(1, len(sources))
    return {
        "n_claims": n,
        "n_sources": len(sources),
        "claim_evidence_coverage": round(supported / n, 3) if n else 0.0,
        "unsupported_claim_rate": round((n - supported) / n, 3) if n else 0.0,
        "source_coverage": round(len(used) / nsrc, 3),
    }
