"""LanternScout mini CLI. Usage: python agent.py "your research question"."""
import sys

from research_loop import run_research
from eval.eval_stub import run_eval


def main(argv):
    query = argv[1] if len(argv) > 1 else "What are the leading open-source vector databases?"
    res = run_research(query)
    print(res["report"])
    print("--- metrics ---")
    print(run_eval(res["claims"], res["sources"]))


if __name__ == "__main__":
    main(sys.argv)
