"""Assemble draft v2 from the immutable v1 plus harvested literature.

Two products, and they are not the same thing:

**A — `v2_literature_overlay.md`.** v1 byte-identical, with citations, §7.8 and
PENDING markers appended. Nothing deleted, nothing reworded, no number touched.
This is what this package builds today, and it is *not* a submission candidate:
a claim the SWOT-DNIPRO audit found unsupported is still there, now merely
annotated.

**B — `v2_manuscript.md`.** The corrected manuscript, where a claim may be
rewritten — but only through a recorded patch carrying `claim_id`, `old_status`,
`old_text_hash`, `new_text` and the audit finding that justifies it. Blocked
until the audit is imported and its required actions are done.

Between the two sits `scientific_status`: the knowledge graph answers *what does
the literature say*, the audit answers *what do our own data support*, and a
claim reaches product B only when both layers agree.

`literature_matrix` adds the L-series — claims about the literature for §7.8,
resolved by rule from the gap matrix and the slice denominators, joined to the
audit's V/M/X rows only through `backs` and never written back to them.
"""
