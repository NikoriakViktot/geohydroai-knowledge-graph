"""paper_3 — evidence-grounded literature gap analysis for the Kakhovka manuscript.

Turns 24 thesis cards (T01–T24) into a corpus-grounded Gap Evidence Matrix:
per thesis, how many papers in the corpus SUPPORT / CONTRADICT the claim, supply a
METHOD it depends on, or establish it as an ANALOGUE in another system — and where
the resulting verdict falls on the nine-value scale in `gap_matrix.VERDICTS`
(KNOWN · SUPPORTED_BUT_SPARSE · CONTESTED · METHOD_ONLY · ANALOGUE_ONLY ·
NOT_FOUND · RETRIEVAL_UNVALIDATED · RETRIEVAL_INCOMPLETE · CANDIDATE_GAP).

Only CANDIDATE_GAP is eligible for a novelty claim, and only after a human has read
the closest precedents. The two RETRIEVAL_* verdicts exist so that "we found
nothing" can never be mistaken for "there is nothing to find".

Entry point: ``python -m src.paper_3.cli --help``
Design doc: ``src/paper_3/PAPER_3_GAP_PLAN.md``
"""
