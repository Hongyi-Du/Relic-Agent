"""LanternScout research loop: plan -> search -> track sources -> make claims -> write report.

Intentionally incomplete: claims are NOT linked to their sources yet (see GAP markers), so the
eval metrics start weak. Closing these gaps is the organization's job.
"""
from tools.search_stub import search
from tools.source_tracker import SourceTracker
from tools.claim_tracker import ClaimTracker
from tools.report_writer import write_report


def run_research(query, corpus_dir="corpus"):
    plan = [query]                      # single-step plan (GAP: no multi-step planning)
    st = SourceTracker()
    ct = ClaimTracker()
    for step in plan:
        for rec in search(step, corpus_dir=corpus_dir):
            st.add(rec)
            # claims ARE linked to the search-hit source (structural grounding). GAPs that remain:
            # the link is NOT semantically validated (the source may not actually support the
            # claim), claims can be near-duplicates, and source titles/provenance are weak.
            ct.add(rec["snippet"] or ("about " + step), source_ids=[rec["source_id"]])
    sources = st.all()
    claims = ct.all()
    report = write_report(query, claims, sources)
    return {"query": query, "sources": sources, "claims": claims, "report": report}

# --- change: Made a small, inspectable change addressing: claims not linked to sources. ---
# # address: claims not linked to sources
# + TODO marker replaced with explicit handling
# check: explicit handling for: claims not linked to sources
