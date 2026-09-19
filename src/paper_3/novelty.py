"""The gap statement, the §7.8 positioning, and the reference candidates.

The manuscript's §7.8 currently reads "Deferred. No systematic literature review
has been carried out, and this draft deliberately does not assert priority." This
module produces the replacement — but only from papers that are actually in the
corpus, with quotes that survived verification.

Two guards make that real:

  * every citation is emitted as `[doi:10.xxxx/...]` taken from `relations.parquet`,
    so a DOI that is not in the corpus cannot appear;
  * theses whose corpus coverage is `absent` or `thin` are listed separately as
    claims the analysis is **not** in a position to make, rather than being quietly
    folded into the novelty argument.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR, REFUSAL, resolve_gemini_key
from src.paper_3.classify_relation import call_gemini, call_ollama, parse_response
from src.paper_3.gap_matrix import (
    ABSENT,
    ANALOGUE_ONLY,
    CANDIDATE_GAP,
    CONTESTED,
    KNOWN,
    METHOD_ONLY,
    NOT_FOUND,
    RETRIEVAL_INCOMPLETE,
    RETRIEVAL_UNVALIDATED,
    SUPPORTED_BUT_SPARSE,
    THIN,
)
from src.paper_3.theses import RELATIONS, Thesis, load_theses

logger = logging.getLogger(__name__)

#: The verdict scale widened from four values to nine; this module groups them
#: back into the three bands the narrative actually distinguishes. Written as
#: frozensets so the grouping is explicit rather than a chain of `or`s.
ESTABLISHED = frozenset({KNOWN})
PARTIAL = frozenset({SUPPORTED_BUT_SPARSE, METHOD_ONLY, ANALOGUE_ONLY, CONTESTED})
#: Null results — including the two that mean "we cannot tell", which is why
#: they are never presented as an absence of literature.
UNRESOLVED = frozenset({NOT_FOUND, RETRIEVAL_UNVALIDATED, RETRIEVAL_INCOMPLETE})
#: The only band eligible for a novelty claim, and only after human review.
CONTRIBUTION = frozenset({CANDIDATE_GAP})

_GENERATION_CONFIG = {
    "temperature": 0.1,
    "max_output_tokens": 700,
    "response_mime_type": "application/json",
}

_SUMMARY_PROMPT = """STRICT RULES:
1. Use ONLY the evidence listed below. Do not add anything you know independently.
2. British spelling throughout (harmonised, characterisation, generalisability).
3. If the evidence does not establish anything, set what_is_established to exactly:
   "{refusal}"
4. Output a single JSON object and nothing else.

THESIS {thesis_id}: {statement}

VERDICT ASSIGNED BY RULE: {verdict} ({rationale})
CORPUS COVERAGE: {adequacy}

EVIDENCE FROM THE CORPUS:
{evidence}

Return exactly:
{{"what_is_established": "at most 60 words, citing DOIs as [doi:10.xxxx/yyyy]",
  "what_remains_missing": "at most 60 words"}}"""


def _evidence_block(rows: pd.DataFrame, max_items: int = 12) -> str:
    """Render one thesis's verified evidence for the prompt."""
    judged = rows[rows["relation"].isin(RELATIONS)]
    if judged.empty:
        return "(no evidence found in the corpus)"

    lines = []
    for r in judged.head(max_items).itertuples():
        quote = (r.evidence_quote or "").strip()
        lines.append(
            f"- {r.relation} | doi:{r.doi or 'none'} | {r.title or 'untitled'} "
            f"({r.year or 'n.d.'}) | system: {r.system_class} | "
            f"own result: {bool(r.is_own_result)}"
            + (f'\n  quote: "{quote}"' if quote else "")
        )
    return "\n".join(lines)


def summarise_theses(
    out_dir: Path | None = None,
    theses: list[Thesis] | None = None,
    llm: str = "gemini",
) -> dict[str, dict[str, str]]:
    """Per-thesis prose for the matrix. Returns {thesis_id: {field: text}}.

    A failure to produce prose is not a failure of the run: the matrix's numbers
    and verdicts stand on their own, and an empty summary is better than a
    fabricated one.
    """
    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR

    relations_path = target / "relations.parquet"
    if not relations_path.exists():
        logger.warning("relations.parquet missing — matrix will carry no prose")
        return {}
    relations = pd.read_parquet(relations_path)

    coverage_path = target / "corpus_coverage.parquet"
    coverage = {}
    if coverage_path.exists():
        coverage = {r["thesis_id"]: r
                    for r in pd.read_parquet(coverage_path).to_dict("records")}

    from src.paper_3.gap_matrix import assign_verdict, count_relations

    api_key = resolve_gemini_key() if llm == "gemini" else ""
    if llm == "gemini" and not api_key:
        logger.warning("no Gemini key — skipping prose generation")
        return {}

    out: dict[str, dict[str, str]] = {}
    for t in theses:
        rows = relations[relations["thesis_id"] == t.id]
        counts = count_relations(rows)
        cov = coverage.get(t.id, {})
        adequacy = cov.get("corpus_adequacy", THIN)
        verdict, rationale = assign_verdict(counts, adequacy, cov)

        prompt = _SUMMARY_PROMPT.format(
            refusal=REFUSAL, thesis_id=t.id,
            statement=" ".join(t.statement.split()),
            verdict=verdict, rationale=rationale, adequacy=adequacy,
            evidence=_evidence_block(rows))

        text, _model = (call_ollama(prompt) if llm == "ollama"
                        else call_gemini(prompt, api_key))
        parsed = parse_response(text) or {}
        out[t.id] = {
            "what_is_established": str(parsed.get("what_is_established", "") or "").strip(),
            "what_remains_missing": str(parsed.get("what_remains_missing", "") or "").strip(),
        }
    logger.info("Prose generated for %d theses", len(out))
    return out


# ── reference candidates ──────────────────────────────────────────────────────

def reference_candidates(out_dir: Path | None = None) -> pd.DataFrame:
    """Map the draft's informal citations to DOIs, where one can be found.

    The draft's §REFERENCES lists 11 documents by name ("SWOT PIXC Product
    Description Document D-56411 Rev C", "IAG Resolution 16 (1983)") rather than
    by citation. Several are product documentation with no DOI at all; those are
    marked as such rather than given a plausible-looking one.
    """
    target = Path(out_dir) if out_dir else OUT_DIR
    anchors_path = target / "draft_anchors.json"
    if not anchors_path.exists():
        logger.warning("draft_anchors.json missing — run --step draft first")
        return pd.DataFrame(columns=["citation_text", "doi", "status"])

    payload = json.loads(anchors_path.read_text(encoding="utf-8"))
    stubs = payload.get("reference_stubs", [])

    index_path = target / "corpus_index.parquet"
    corpus_titles = {}
    if index_path.exists():
        index = pd.read_parquet(index_path)
        corpus_titles = {str(t).lower(): d
                         for t, d in zip(index["title"], index["doi"]) if t and d}

    rows = []
    for stub in stubs:
        text = stub["citation_text"]
        doi = stub.get("doi", "")
        status = "doi in draft" if doi else "— (not resolved)"
        if not doi:
            # Only an unambiguous full-title containment counts as a match; a
            # fuzzy guess here would put a wrong citation in the manuscript.
            key = text.lower()
            hits = [d for title, d in corpus_titles.items()
                    if len(title) > 25 and title in key]
            if len(hits) == 1:
                doi, status = hits[0], "matched to corpus title"
        rows.append({"citation_text": text, "doi": doi, "status": status})

    frame = pd.DataFrame(rows, columns=["citation_text", "doi", "status"])
    target.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target / "REFERENCES_CANDIDATES.csv", index=False)
    logger.info("Reference candidates: %d of %d resolved",
                int((frame["doi"] != "").sum()), len(frame))
    return frame


# ── the statement ─────────────────────────────────────────────────────────────

def render_statement(matrix: pd.DataFrame, relations: pd.DataFrame) -> str:
    """Assemble NOVELTY_STATEMENT.md from the matrix. No model involved."""
    if matrix.empty:
        return "# Novelty and gap statement\n\nNo theses were evaluated.\n"

    def band(verdicts: frozenset) -> pd.DataFrame:
        return matrix[matrix["verdict"].isin(verdicts)]

    unsupported = matrix[matrix["corpus_adequacy"].isin((ABSENT, THIN))]

    lines = [
        "# Novelty and gap statement — Paper 3",
        "",
        "Drafted from `GAP_MATRIX` for §7.8 of the manuscript, which currently "
        "reads *\"Deferred. No systematic literature review has been carried out\"*. "
        "Every citation below is a paper present in this corpus with a quote that "
        "passed verbatim verification; nothing is cited from memory.",
        "",
        "## What the corpus establishes",
        "",
    ]

    known = band(ESTABLISHED)
    if known.empty:
        lines.append("No thesis reached the KNOWN threshold "
                     "(three supporting papers, at least one reporting its own "
                     "result in a directly comparable system).")
    else:
        for r in known.itertuples():
            lines.append(f"- **{r.thesis_id}** — {r.statement.strip()} "
                         f"({r.n_supporting_papers} supporting papers; closest: "
                         f"[doi:{r.closest_existing_study_doi}])")
    lines.append("")

    lines += ["## Where this manuscript contributes", ""]
    contribution = band(CONTRIBUTION)
    if contribution.empty:
        lines.append("No thesis reached CANDIDATE_GAP — the only verdict eligible "
                     "for a novelty claim (retrieval validated by a recovered "
                     "positive control, enough related papers examined, and none "
                     "bearing on the thesis either way). On this corpus, the "
                     "manuscript must not assert priority for any single thesis.")
    else:
        for r in contribution.itertuples():
            lines += [
                f"### {r.thesis_id}",
                "",
                f"> {r.statement.strip()}",
                "",
                f"{r.papers_found} related papers are present, "
                f"{r.n_method_relevant_papers} of them supplying methods this thesis "
                f"depends on, and none tests it. {r.what_remains_missing}",
                "",
            ]
    lines.append("")

    lines += ["## Claims this analysis cannot yet make", ""]
    if unsupported.empty:
        lines.append("Corpus coverage was adequate for every thesis.")
    else:
        lines.append(
            "For the theses below, corpus coverage is too weak to distinguish "
            "*the literature is silent* from *we could not see the literature*. "
            "They must not be used to support a novelty claim in their current state.")
        lines.append("")
        for r in unsupported.itertuples():
            lines.append(
                f"- **{r.thesis_id}** ({r.corpus_adequacy}) — "
                f"{r.openalex_hits_in_corpus} of {r.openalex_worldwide_hits} "
                f"candidate works present, coverage "
                f"{float(r.coverage_ratio or 0):.2f}. {r.statement.strip()[:120]}…")
    lines.append("")

    contested = matrix[matrix["n_contradicting_papers"] > 0]
    lines += ["## Contradicted or contested", ""]
    if contested.empty:
        lines.append("No thesis was contradicted by a paper in the corpus.")
    else:
        for r in contested.itertuples():
            lines.append(
                f"- **{r.thesis_id}** — {r.n_contradicting_papers} contradicting paper(s). "
                f"Closest: [doi:{r.closest_existing_study_doi}] "
                f"({r.closest_existing_study_relation}). Read before citing.")
    lines.append("")

    lines += ["## Papers that must be cited in §7.8", ""]
    if relations is None or relations.empty:
        lines.append("— (no adjudicated relations)")
    else:
        judged = relations[relations["relation"].isin(RELATIONS)]
        if judged.empty:
            lines.append("— (no adjudicated relations)")
        else:
            top = (judged.groupby("doi")
                   .agg(n_theses=("thesis_id", "nunique"),
                        title=("title", "first"), year=("year", "first"))
                   .reset_index().sort_values("n_theses", ascending=False))
            for r in top.head(30).itertuples():
                if not r.doi:
                    continue
                lines.append(f"- [doi:{r.doi}] — {r.title} ({r.year or 'n.d.'}) "
                             f"— bears on {r.n_theses} thesis/theses")
    return "\n".join(lines) + "\n"


def run(out_dir: Path | None = None, theses: list[Thesis] | None = None,
        llm: str = "gemini") -> Path:
    """Write NOVELTY_STATEMENT.md and REFERENCES_CANDIDATES.csv."""
    target = Path(out_dir) if out_dir else OUT_DIR
    matrix_path = target / "gap_matrix.parquet"
    if not matrix_path.exists():
        raise FileNotFoundError(f"{matrix_path} missing — run --step matrix first")
    matrix = pd.read_parquet(matrix_path)

    relations_path = target / "relations.parquet"
    relations = pd.read_parquet(relations_path) if relations_path.exists() else pd.DataFrame()

    reference_candidates(target)
    path = target / "NOVELTY_STATEMENT.md"
    path.write_text(render_statement(matrix, relations), encoding="utf-8")
    logger.info("Novelty statement → %s", path)
    return path
