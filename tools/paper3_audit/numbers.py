"""kakhovka_numbers.csv — every km² / m³ s⁻¹ / km³ statement in the Kakhovka papers of
the corpus, with the semantic cues found in its ±250-character window. A human fills
``human_verified``; export refuses to place an unverified number in the matrix.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from src.paper_3._utils import PROJECT_ROOT, load_paper_json, paper_sections
from tools.paper3_audit import corpus as corpus_mod
from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, WORK_DIR

logger = logging.getLogger(__name__)

SODB_DIR = PROJECT_ROOT / "data" / "sodb"

_NUM = r"(\d{1,3}(?:[ , ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_SCI = r"(?:\s*(?:±|\+/-|\+-)\s*[\d.,]+)?(?:\s*(?:[x×]\s*10\s*\^?\s*\d|e\d))?"
_UNIT = (r"(km\s?²|km\s?2|km\^2|sq\.?\s*km|square\s+kilomet\w*|hectares?|\bha\b|"
         r"m\s?³\s?/\s?s|m\s?3\s?/\s?s|m\s?³\s?s\s?[-−⁻]\s?[1¹]|m\s?3\s?s\s?-1|cubic\s+met\w*\s+per\s+second|cumecs?|"
         r"km\s?³|km\s?3|km\^3|hm\s?³|hm\s?3|million\s+(?:cubic\s+)?m|mln\s+m3|billion\s+(?:cubic\s+)?m|bcm|"
         r"m\s?³|m\s?3\b)")
NUMBER_RE = re.compile(_NUM + _SCI + r"\s*" + _UNIT, re.IGNORECASE)

CUES = {
    "dates": re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(june|july|may)\b|\b(june|july|may)\s+(\d{1,2})(?:st|nd|rd|th)?\b|2023-0[567]-\d\d|\d\d\.0[567]\.2023", re.I),
    "aoi": re.compile(r"kherson|oleshky|left bank|right bank|downstream|upstream|reservoir|delta|liman|estuary|inhulets|floodplain|corridor|zaporizh|nikopol|mykolaiv|dnipro-buh|dnieper", re.I),
    "temporal": re.compile(r"cumulative|maximum|peak|snapshot|on \d{1,2} june|as of|between|persisten|over the period|total|by \d{1,2} june|within", re.I),
    "reference_water": re.compile(r"pre-existing water|permanent water|reference water|normal water|pre-flood|pre-event|pre-breach|before the (?:breach|collapse|dam)|excluding (?:the )?(?:reservoir|river|water)", re.I),
    "sensor": re.compile(r"sentinel-?1|sentinel-?2|sentinel-?3|sentinel-?6|landsat|modis|viirs|planet|iceye|capella|swot|icesat|sar|optical|radar|grace|jason", re.I),
    "land_or_total": re.compile(r"flooded land|inundated land|flooded area|inundated area|flood extent|water surface|water area|water extent|surface water|drained|exposed|dried", re.I),
    "quantity": re.compile(r"discharge|outflow|inflow|breach flow|release|volume|storage|capacity|water loss|area|extent|depth|level drop|drawdown", re.I),
}


def _norm_value(raw: str) -> str:
    return raw.replace(" ", " ").strip()


def _sodb_rows(pid: str) -> list[dict]:
    p = SODB_DIR / pid / "numeric_facts.parquet"
    if not p.exists():
        return []
    try:
        df = pd.read_parquet(p)
    except Exception:
        return []
    out = []
    unit_col = next((c for c in df.columns if c.lower() == "unit"), None)
    val_col = next((c for c in df.columns if c.lower() in ("value", "value_num", "raw_value")), None)
    if unit_col is None or val_col is None:
        return []
    for r in df.to_dict("records"):
        unit = str(r.get(unit_col, ""))
        if re.search(r"km|m3|m³|hm|ha|cumec", unit, re.I):
            ctx = " ".join(str(r.get(c, "")) for c in df.columns if c.lower() in ("row_context", "col_header", "context", "cell_context"))
            out.append({"value": str(r.get(val_col, "")), "unit": unit, "window_text": ctx[:500],
                        "section": f"table:{r.get('table_id', '')}", "page": r.get("page", ""), "source": "sodb"})
    return out


def extract_paper(pid: str, meta: dict) -> list[dict]:
    paper = load_paper_json(pid)
    if not paper:
        return []
    rows = []
    for section, text in paper_sections(paper).items():
        for m in NUMBER_RE.finditer(text):
            w = text[max(0, m.start() - 250): m.end() + 250]
            cues = {k: "; ".join(sorted({(" ".join(x for x in (t if isinstance(t, tuple) else (t,)) if x)) for t in rx.findall(w)}))[:200]
                    for k, rx in CUES.items()}
            rows.append({"paper_id": pid, "citation_key": "", "title": meta.get("title", ""), "year": meta.get("year", ""),
                         "doi": meta.get("doi_clean", ""), "value": _norm_value(m.group(1)), "unit": m.group(0)[len(m.group(1)):].strip(),
                         "raw_match": m.group(0), "quantity_name": cues["quantity"], "aoi": cues["aoi"],
                         "dates": cues["dates"], "temporal_semantics": cues["temporal"],
                         "reference_water": cues["reference_water"], "sensor": cues["sensor"],
                         "land_or_total": cues["land_or_total"], "section": section, "page": "",
                         "source": "regex", "window_text": " ".join(w.split()), "human_verified": "",
                         "verified_by": "", "note": ""})
    for r in _sodb_rows(pid):
        rows.append({"paper_id": pid, "citation_key": "", "title": meta.get("title", ""), "year": meta.get("year", ""),
                     "doi": meta.get("doi_clean", ""), "value": r["value"], "unit": r["unit"], "raw_match": "",
                     "quantity_name": "", "aoi": "", "dates": "", "temporal_semantics": "", "reference_water": "",
                     "sensor": "", "land_or_total": "", "section": r["section"], "page": r["page"], "source": "sodb",
                     "window_text": r["window_text"], "human_verified": "", "verified_by": "", "note": ""})
    return rows


COLUMNS = ["paper_id", "citation_key", "title", "year", "doi", "value", "unit", "raw_match", "quantity_name", "aoi",
           "dates", "temporal_semantics", "reference_water", "sensor", "land_or_total", "section", "page", "source",
           "window_text", "human_verified", "verified_by", "note"]


def run(work_dir: Path = WORK_DIR, out_dir: Path = OUT_DIR, force: bool = False, min_mentions: int = 1) -> int:
    out = out_dir / DELIVERABLES["numbers"]
    if out.exists() and not force:
        # never overwrite a file that may carry human_verified marks
        logger.info("%s exists — keeping it (human_verified marks); use --force to rebuild", out.name)
        return 0
    index = corpus_mod.load_runtime_index(work_dir)
    canonical = index[(index["duplicate_of"] == "")]
    targets = canonical[canonical["kakhovka_mentions"] >= min_mentions]
    judged_path = work_dir / "screen_judged.parquet"
    extra = set()
    if judged_path.exists():
        j = pd.read_parquet(judged_path)
        extra = set(j.loc[j["role"] == "COMPARATOR", "paper_id"])
    targets = pd.concat([targets, canonical[canonical["paper_id"].isin(extra)]]).drop_duplicates("paper_id")
    # Gemini quantity blocks from screening
    gem = []
    if judged_path.exists():
        for r in j[j["quantity_json"].fillna("") != ""].itertuples():
            try:
                q = json.loads(r.quantity_json)
            except Exception:
                continue
            gem.append({"paper_id": r.paper_id, "citation_key": "", "title": r.title, "year": r.year, "doi": r.doi,
                        "value": q.get("value", ""), "unit": q.get("unit", ""), "raw_match": "",
                        "quantity_name": q.get("quantity_name", ""), "aoi": q.get("aoi", ""), "dates": q.get("date_or_period", ""),
                        "temporal_semantics": q.get("temporal", ""), "reference_water": q.get("reference_water", ""),
                        "sensor": q.get("sensor", ""), "land_or_total": "", "section": r.quote_section, "page": r.quote_page,
                        "source": f"gemini_quantity:{r.atomic_claim_id}", "window_text": r.evidence_quote,
                        "human_verified": "", "verified_by": "", "note": r.rationale})
    rows = []
    for meta in targets.to_dict("records"):
        rows.extend(extract_paper(meta["paper_id"], meta))
    rows.extend(gem)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if len(frame):
        frame = frame.sort_values(["paper_id", "section"]).reset_index(drop=True)
        frame.insert(0, "number_id", [f"N{i:05d}" for i in range(1, len(frame) + 1)])
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    logger.info("kakhovka_numbers.csv: %d statements from %d papers (%d Gemini quantity blocks)",
                len(frame), targets.shape[0], len(gem))
    return 0
