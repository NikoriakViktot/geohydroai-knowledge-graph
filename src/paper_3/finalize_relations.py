"""FINAL phase — verify what the model said, apply human overrides, decide nothing else.

Deterministic on top of `relations_raw.jsonl`. Every SUPPORTS and CONTRADICTS must
carry a quote that survives `evidence.verify_quote` against the passages that were
actually offered for that call; anything else goes to `relations_rejected.csv` and
is excluded from every count in the matrix.

`OVERRIDES` is where human judgement is written down rather than applied invisibly.
Each entry carries a justification that is rendered as a footnote in the published
matrix, so a reader can see which rows a person changed and why.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.classify_relation import RAW_FILE, load_raw, normalise_parsed, parse_response
from src.paper_3.evidence import verify_quote
from src.paper_3.theses import NOT_RELEVANT, RELATIONS

logger = logging.getLogger(__name__)

#: (thesis_id, doi) → (relation, confidence, justification).
#: Populated by hand after reading the paper. Never populated by a script.
OVERRIDES: dict[tuple[str, str], tuple[str, float, str]] = {}

#: Relations that assert what a paper FOUND, and therefore need a verified quote.
#: METHOD_RELEVANT and ANALOGUE describe what a paper IS, which the passages
#: establish on their own.
QUOTE_REQUIRED = ("SUPPORTS", "CONTRADICTS")

RELATION_COLUMNS = [
    "thesis_id", "paper_id", "doi", "title", "year", "relation", "confidence",
    "passage_id", "evidence_quote", "quote_verified", "quote_similarity",
    "quote_repaired", "quote_section", "quote_source_file", "rationale",
    "system_class", "is_own_result", "model",
    "evidence_level", "quote_source_authority", "prompt_sha256",
    "override_applied", "override_note", "parse_ok",
]

REJECTED_COLUMNS = [
    "thesis_id", "paper_id", "doi", "relation", "model", "reason",
    "claimed_quote", "best_passage_id", "similarity",
]


def _passage_map(row: dict) -> dict[str, str]:
    return {p["passage_id"]: p["text"] for p in row.get("passages_offered", [])}


def _passage_meta(row: dict, passage_id: str) -> dict:
    for p in row.get("passages_offered", []):
        if p.get("passage_id") == passage_id:
            return p
    return {}


def finalize_row(raw: dict) -> tuple[dict, dict | None]:
    """Turn one raw record into a judged row, plus a rejection row when it fails."""
    base = {
        "thesis_id": raw.get("thesis_id", ""),
        "paper_id": raw.get("paper_id", ""),
        "doi": raw.get("doi", "") or "",
        "title": raw.get("title", "") or "",
        "year": raw.get("year"),
        "model": raw.get("model", ""),
        # Defaults keep rows written before the abstract path existed valid.
        "evidence_level": raw.get("evidence_level", "full_text"),
        "quote_source_authority": raw.get("quote_source_authority", "grobid_tei"),
        "prompt_sha256": raw.get("prompt_sha256", ""),
        "relation": NOT_RELEVANT,
        "confidence": 0.0,
        "passage_id": "",
        "evidence_quote": "",
        "quote_verified": False,
        "quote_similarity": 0.0,
        "quote_repaired": False,
        "quote_section": "",
        "quote_source_file": "",
        "rationale": "",
        "system_class": "not_stated",
        "is_own_result": False,
        "override_applied": False,
        "override_note": "",
        "parse_ok": False,
    }

    parsed = parse_response(raw.get("raw_response", ""))
    if parsed is None:
        base["rationale"] = "model response could not be parsed as JSON"
        return base, {
            **{k: base[k] for k in ("thesis_id", "paper_id", "doi", "relation", "model")},
            "reason": "unparseable response",
            "claimed_quote": (raw.get("raw_response") or "")[:200],
            "best_passage_id": "", "similarity": 0.0,
        }

    fields = normalise_parsed(parsed)
    base.update({
        "parse_ok": True,
        "relation": fields["relation"],
        "confidence": fields["confidence"],
        "passage_id": fields["passage_id"],
        "rationale": fields["rationale"],
        "system_class": fields["system_class"],
        "is_own_result": fields["is_own_result"],
    })

    if fields["relation"] == NOT_RELEVANT:
        return base, None

    passages = _passage_map(raw)
    verdict = verify_quote(fields["evidence_quote"], passages)
    base["quote_verified"] = verdict.verified
    base["quote_similarity"] = verdict.similarity
    base["quote_repaired"] = verdict.repaired
    base["evidence_quote"] = verdict.text if verdict.verified else ""
    if verdict.passage_id:
        base["passage_id"] = verdict.passage_id
        meta = _passage_meta(raw, verdict.passage_id)
        base["quote_section"] = meta.get("section", "")
        base["quote_source_file"] = meta.get("source_file", "")

    if fields["relation"] in QUOTE_REQUIRED and not verdict.verified:
        return base, {
            "thesis_id": base["thesis_id"], "paper_id": base["paper_id"],
            "doi": base["doi"], "relation": fields["relation"], "model": base["model"],
            "reason": verdict.reason,
            "claimed_quote": (fields["evidence_quote"] or "")[:300],
            "best_passage_id": verdict.passage_id or "",
            "similarity": verdict.similarity,
        }
    return base, None


def apply_overrides(frame: pd.DataFrame,
                    overrides: dict | None = None) -> pd.DataFrame:
    """Replace adjudications a human has reviewed. Records that it happened."""
    overrides = OVERRIDES if overrides is None else overrides
    if frame.empty or not overrides:
        return frame

    frame = frame.copy()
    for (thesis_id, doi), (relation, confidence, note) in overrides.items():
        mask = (frame["thesis_id"] == thesis_id) & (frame["doi"] == doi)
        if not mask.any():
            logger.warning("override (%s, %s) matched no row", thesis_id, doi)
            continue
        frame.loc[mask, "relation"] = relation
        frame.loc[mask, "confidence"] = confidence
        frame.loc[mask, "override_applied"] = True
        frame.loc[mask, "override_note"] = note
        # A human who has read the paper is the authority; the automated quote
        # check no longer gates this row.
        if relation in QUOTE_REQUIRED:
            frame.loc[mask, "quote_verified"] = True
    return frame


def rejection_rate_by_model(rejected: pd.DataFrame,
                            relations: pd.DataFrame) -> dict[str, float]:
    """Share of quote-bearing adjudications each model failed to substantiate.

    Above roughly 15% is the signal to change model or shorten the passages —
    it means the model is writing quotes rather than copying them.
    """
    if relations.empty:
        return {}
    out = {}
    for model, rows in relations.groupby("model"):
        attempted = int(rows["relation"].isin(QUOTE_REQUIRED).sum())
        failed = int((rejected["model"] == model).sum()) if not rejected.empty else 0
        denominator = attempted + failed
        if denominator:
            out[str(model)] = round(failed / denominator, 4)
    return out


def run(out_dir: Path | None = None,
        overrides: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the raw file, judge it, write relations.parquet + rejections."""
    target = Path(out_dir) if out_dir else OUT_DIR
    raw_path = target / RAW_FILE
    raw_rows = load_raw(raw_path)
    if not raw_rows:
        raise FileNotFoundError(
            f"{raw_path} is empty or missing — run --step classify first")

    judged, rejected = [], []
    for raw in raw_rows:
        row, rejection = finalize_row(raw)
        judged.append(row)
        if rejection:
            rejected.append(rejection)

    relations = pd.DataFrame(judged, columns=RELATION_COLUMNS)
    relations = apply_overrides(relations, overrides)
    rejections = pd.DataFrame(rejected, columns=REJECTED_COLUMNS)

    target.mkdir(parents=True, exist_ok=True)
    relations.to_parquet(target / "relations.parquet", index=False)
    rejections.to_csv(target / "relations_rejected.csv", index=False)

    rates = rejection_rate_by_model(rejections, relations)
    logger.info("Finalised %d adjudications; %d quotes rejected", len(relations),
                len(rejections))
    logger.info("Relations: %s",
                relations["relation"].value_counts().to_dict())
    for model, rate in rates.items():
        level = logging.WARNING if rate > 0.15 else logging.INFO
        logger.log(level, "quote rejection rate for %s: %.1f%%", model, rate * 100)
    return relations, rejections
