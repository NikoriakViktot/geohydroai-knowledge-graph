"""Thematic evidence briefs — what the literature knows about a methodological aspect.

A separate pipeline from the thesis adjudication, answering a different question:

    thesis pipeline   paper → relation to a thesis → gap analysis
                      "has anyone already claimed this?"

    thematic pipeline paper → structured method/fact extraction → evidence synthesis
                      "what does the field actually know about this aspect?"

They share passages and the quote verifier; they share nothing else. A brief never
reads a thesis verdict, and a verdict never reads a brief.

**The rule the whole package exists to enforce:**

    an abstract may say   "this method exists"
    full text may say     "this method gave RMSE 0.12 m"

    and never the other way round.

Both sources are used — the 9 148 harvested abstracts give breadth that the old
flood-oriented corpus does not have, and the old corpus's full text carries the
numbers that abstracts never do — but they are not mixed as equivalent evidence.
Every record carries `evidence_level` and `evidence_role`, and
`numeric_claim_allowed` is computed from them rather than set by hand.
"""
