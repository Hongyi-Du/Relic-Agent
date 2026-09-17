# LanternScout (mini research agent)

LanternScout is an early CLI research agent prototype. Given a question, it searches a local
corpus, tracks sources, makes claims, writes a markdown report, and runs a small evaluation.

## Run
    python smoke_check.py          # run the sample task + print metrics
    python agent.py "your question"

## Current status (honest)
- runnable beta: `python smoke_check.py` runs end-to-end (exit 0) on the sample task
- the report SUMMARY is synthesized by an LLM (llm.py); offline it falls back to an extractive summary
- claims ARE linked to their search-hit source (STRUCTURAL grounding), but the link is NOT
  semantically validated — the source may not actually support the claim
- claims can be near-duplicates; source titles / provenance are weak
- the LLM summary is NOT grounded to specific source_ids
- search is a LOCAL STUB over corpus/*.md (no real web retrieval)
- report writer has NO evidence gate yet (weakly-supported claims are shown, not gated)
- source credibility scoring is minimal; eval metrics are basic and not human-validated
- NO market validation yet (no real users, trials, or willingness-to-pay)

## Known limitations
Reports may contain unsupported claims until claim->source linking and an evidence gate are
enforced. Do not treat the output as verified.

<!-- change: Softened the README's evidence-guarantee overclaim and recorded the current limitation. -->
- (softened overclaim) 'evidence-grounded reports' implied a guarantee the claim tracker does not enforce yet
- Limitation: Reports may contain unsupported claims until evidence links are enforced.
