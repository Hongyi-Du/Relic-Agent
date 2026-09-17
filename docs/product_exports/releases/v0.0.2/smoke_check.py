"""Smoke test: run the sample task end-to-end; print the report + a metrics JSON line.
Exits non-zero only if the pipeline crashes. The in-world release gate runs this."""
import json
import sys

from research_loop import run_research
from eval.eval_stub import run_eval

QUERY = "What are the leading open-source vector databases and their tradeoffs?"


def main():
    res = run_research(QUERY)
    metrics = run_eval(res["claims"], res["sources"])
    print(res["report"])
    print(json.dumps({"ok": True, "query": QUERY, "metrics": metrics}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
