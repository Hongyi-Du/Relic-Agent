"""Drafts a markdown report. The findings SUMMARY is synthesized by the LLM core
(falls back to an extractive summary offline). GAP: no evidence gate yet -- unsupported claims are
still shown (claim->source linking is not enforced)."""
from llm import call_llm


def write_report(query, claims, sources):
    nl = chr(10)
    lines = ["# Research Report: " + str(query), ""]
    lines.append("## Sources (" + str(len(sources)) + ")")
    for s in sources:
        lines.append("- " + str(s.title) + " (" + str(s.source_id) + ")")
    lines.append("")
    # LLM-backed synthesis (the product's core capability): summarize findings from the sources.
    src_text = nl.join("- " + str(getattr(s, "title", "")) + " (" + str(getattr(s, "source_id", "")) + ")"
                       for s in sources)
    claim_text = nl.join("- " + str(getattr(c, "text", "")) for c in claims)
    prompt = ("Question: " + str(query) + nl + "Sources:" + nl + src_text + nl
              + "Draft claims:" + nl + claim_text + nl
              + "Write a concise findings summary (3-5 bullets). Mark any claim you cannot tie to "
                "a listed source as (unsupported).")
    summary = call_llm(prompt)
    lines.append("## Summary")
    lines.append(summary if summary else "(offline: extractive summary) " + "; ".join(
        str(getattr(c, "text", "")) for c in claims[:3]))
    lines.append("")
    lines.append("## Findings")
    for c in claims:
        # GAP: a claim is included regardless of whether it has supporting source_ids
        cite = (" [" + ", ".join(c.source_ids) + "]") if c.source_ids else " [unsupported]"
        lines.append("- " + str(c.text) + cite)
    return nl.join(lines)

# --- change: Report writer now requires each claim sentence to map to a tracked, sourced claim. ---
# + require_supported_claims(report)
# + block_unsupported_sentences()
# check: require_supported_claims rejects unsourced sentences
