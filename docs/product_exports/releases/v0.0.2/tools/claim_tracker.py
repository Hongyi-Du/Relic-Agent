"""Records claims. GAP: does not enforce source_ids / uncertainty / evidence."""


class Claim:
    def __init__(self, claim_id, text, source_ids=None, uncertainty_note=""):
        self.claim_id = claim_id
        self.text = text
        self.source_ids = list(source_ids or [])
        self.uncertainty_note = uncertainty_note


class ClaimTracker:
    def __init__(self):
        self.claims = []

    def add(self, text, source_ids=None, uncertainty_note=""):
        # GAP: accepts a claim with no source_ids; no validate_claim_evidence yet
        c = Claim("claim_" + str(len(self.claims)), text, source_ids, uncertainty_note)
        self.claims.append(c)
        return c

    def all(self):
        return list(self.claims)

# --- change: Added source_ids + uncertainty_note fields and an evidence-validation check to Claim. ---
# + Claim.source_ids: list[str]
# + Claim.uncertainty_note: str
# + validate_claim_evidence(claim)
# field: source_ids
# field: uncertainty_note
# check: validate_claim_evidence requires at least one source id
