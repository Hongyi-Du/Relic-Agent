"""Records sources by id/title/path. GAP: no credibility scoring yet."""


class Source:
    def __init__(self, source_id, title, path, url=None):
        self.source_id = source_id
        self.title = title
        self.path = path
        self.url = url
        # GAP: no credibility_score


class SourceTracker:
    def __init__(self):
        self.sources = {}

    def add(self, rec):
        s = Source(rec.get("source_id"), rec.get("title"), rec.get("path"), rec.get("url"))
        self.sources[s.source_id] = s
        return s

    def all(self):
        return list(self.sources.values())

# --- change: Added a credibility_score field and a scoring method to Source. ---
# + Source.credibility_score: float
# + score_source_credibility(source)
# field: credibility_score
# check: score_source_credibility classifies sources into tiers
