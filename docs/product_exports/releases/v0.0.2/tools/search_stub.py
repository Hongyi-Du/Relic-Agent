"""Local mock search over corpus/*.md -- returns real source records (no network)."""
import glob
import os


def search(query, k=5, corpus_dir="corpus"):
    terms = [w for w in query.lower().split() if len(w) > 2]
    out = []
    for i, path in enumerate(sorted(glob.glob(os.path.join(corpus_dir, "*.md")))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        score = sum(text.lower().count(t) for t in terms)
        snippet = ""
        for ln in text.splitlines():
            if ln.strip() and not ln.startswith("#"):
                snippet = ln.strip()[:200]
                break
        out.append({"source_id": "src_" + str(i), "title": os.path.basename(path),
                    "path": path, "snippet": snippet, "score": score})
    out.sort(key=lambda r: -r["score"])
    return out[:k]
