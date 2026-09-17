# Sample task: market research

Input query:
"What are the leading open-source vector databases and their tradeoffs?"

Expected trace:
plan -> search(corpus) -> track sources -> make claims -> write report -> eval

Expected report shape:
# Research Report: <query>
## Sources (N)
- <title> (<source_id>)
## Findings
- <claim> [<source_ids> | unsupported]

Quality bar (target):
- unsupported_claim_rate should drop toward 0 as claim->source linking is enforced
- claim_evidence_coverage should rise toward 1.0
