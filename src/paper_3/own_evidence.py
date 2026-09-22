"""Own-data evidence package: every manuscript number bound to a snapshot row.

Two sources feed one registry:

* **imports** — whole tables that already carry one claim per row with a value
  and a status (`manuscript_evidence_matrix.csv` V/M/X ids; the audit's
  `claim_evidence_matrix.csv` C ids). They are taken row by row, statuses mapped
  onto the eight-value manuscript scale.
* **claims** — hand-declared rows for result families the matrices never saw
  (vegetation, woody cover, channel width, roughness, zone DEMs, indices, SAR,
  sea posts). Each names a snapshot table, a row selector and the columns that
  hold the value, its uncertainty and its n. The value is *read* from the row at
  build time; a selector that matches zero or several rows raises. That is the
  whole discipline: nothing here can be typed.

Output: ``OWN_EVIDENCE.{csv,parquet}`` plus ``ARTICLE_THESES.csv`` (AT rows →
claim ids) and the refreshed ``v2/scientific_claim_status.yaml``.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd
import yaml

from src.paper_3 import snapshot
from src.paper_3._utils import OUT_DIR

logger = logging.getLogger(__name__)

CLAIMS_PATH = Path(__file__).with_name("own_claims.yaml")
EVIDENCE_FILE = "OWN_EVIDENCE.csv"
THESES_FILE = "ARTICLE_THESES.csv"

STATUSES = ("SUPPORTED", "SUPPORTED_WITH_LIMITATION", "REVISE_UNCERTAINTY",
            "REQUIRES_REANALYSIS", "NOT_TESTABLE", "UNSUPPORTED", "CONTRADICTED",
            "UNKNOWN")

#: How the two source vocabularies map onto the manuscript scale.
STATUS_MAPS: dict[str, dict[str, str]] = {
    "matrix": {"VALIDATED": "SUPPORTED", "SUPPORTED": "SUPPORTED_WITH_LIMITATION",
               "PRELIMINARY": "REVISE_UNCERTAINTY", "REJECTED": "UNSUPPORTED",
               "UNRESOLVED": "NOT_TESTABLE"},
    "audit": {"SUPPORTED": "SUPPORTED",
              "SUPPORTED_WITH_LIMITATIONS": "SUPPORTED_WITH_LIMITATION",
              "UNSUPPORTED": "UNSUPPORTED", "CONTRADICTED": "CONTRADICTED",
              "UNRESOLVED": "NOT_TESTABLE"},
    "manual": {s: s for s in STATUSES},
}

COLUMNS = [
    "claim_id", "family", "claim_type", "claim_text", "manuscript_section",
    "article_thesis", "scientific_importance", "audit_status", "status_source",
    "source_table", "snapshot_path", "snapshot_sha256", "row_selector",
    "value_field", "value_resolved", "unit", "uncertainty_resolved", "n_resolved",
    "time_period", "spatial_domain", "figure_id", "table_id",
    "literature_thesis_ids", "literature_claim_ids",
    "scientific_caveat", "remaining_action", "blocks_submission",
    "paper", "role", "reuse_in_paper_2", "downstream_use",
]

#: ``{col}`` or ``{col:fmt}``; dots allowed because summary.json is flattened
#: with dotted keys (``E1_shares_by_year.2025.share_veg_5_6_7_median``).
_FIELD_RE = re.compile(r"\{([^{}:]+)(?::([^}]+))?\}")


class SelectorError(LookupError):
    """A row selector matched zero or more than one row."""


def load_claims(path: Path = CLAIMS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_table(local: Path) -> pd.DataFrame:
    if local.suffix == ".json":
        data = json.loads(local.read_text(encoding="utf-8"))
        return pd.json_normalize(data, sep=".")
    # strings only: '+3.31' must keep its sign and '' must stay '' (never NaN)
    return pd.read_csv(local, dtype=str, keep_default_na=False)


def select_row(frame: pd.DataFrame, selector: dict) -> pd.Series:
    """Return the single row matching every (column == value) pair."""
    mask = pd.Series(True, index=frame.index)
    for col, want in selector.items():
        if col not in frame.columns:
            raise SelectorError(f"column {col!r} not in table; have {list(frame.columns)[:12]}")
        mask &= frame[col].astype(str).str.strip() == str(want)
    hits = frame[mask]
    if len(hits) != 1:
        raise SelectorError(f"selector {selector} matched {len(hits)} rows")
    return hits.iloc[0]


def render_value(template: str, row: pd.Series) -> str:
    """Fill ``{col}`` / ``{col:fmt}`` from the row; a missing column raises."""
    def _sub(m: re.Match) -> str:
        col, fmt = m.group(1), m.group(2)
        if col not in row.index:
            raise SelectorError(f"value field {col!r} not in row; have {list(row.index)[:12]}")
        v = row[col]
        if fmt:
            try:
                return format(float(v), fmt)
            except (TypeError, ValueError):
                return str(v)
        return str(v)
    return _FIELD_RE.sub(_sub, template)


def map_status(raw: str, source: str) -> str:
    table = STATUS_MAPS[source]
    key = str(raw).strip().upper()
    if key not in table:
        logger.warning("status %r (%s) unmapped → UNKNOWN", raw, source)
        return "UNKNOWN"
    return table[key]


def _blank() -> dict:
    return {c: "" for c in COLUMNS}


def build_imports(spec: dict, snapshot_dir: Path) -> list[dict]:
    """Take a whole table as claims: one row per id."""
    entry = snapshot.entry_for(spec["table"], snapshot_dir)
    frame = _read_table(snapshot_dir / entry["local_path"])
    rows: list[dict] = []
    cols = spec["columns"]
    for _, r in frame.iterrows():
        cid = str(r[cols["id"]]).strip()
        if not cid or cid == "nan":
            continue
        row = _blank()
        row.update({
            "claim_id": cid, "family": spec["family"],
            "claim_type": str(r.get(cols.get("type", ""), "")) if cols.get("type") else spec.get("claim_type", ""),
            "claim_text": str(r[cols["statement"]]),
            "audit_status": map_status(r[cols["status"]], spec["status_map"]),
            "status_source": spec["status_map"],
            "source_table": entry["rel_path"],
            "snapshot_path": str(entry["local_path"]),
            "snapshot_sha256": entry["sha256"], "row_selector": json.dumps({cols["id"]: cid}),
            "value_field": cols.get("value", ""),
            "value_resolved": str(r[cols["value"]]) if cols.get("value") else "",
            "uncertainty_resolved": str(r[cols["uncertainty"]]) if cols.get("uncertainty") else "",
            "n_resolved": str(r[cols["n"]]) if cols.get("n") else "",
            "figure_id": str(r[cols["figure"]]) if cols.get("figure") else "",
            "table_id": str(r[cols["table"]]) if cols.get("table") else "",
            "scientific_caveat": str(r[cols["limitations"]]) if cols.get("limitations") else "",
            "remaining_action": str(r[cols["action"]]) if cols.get("action") else "",
        })
        for k in ("value_resolved", "uncertainty_resolved", "n_resolved", "figure_id",
                  "table_id", "scientific_caveat", "remaining_action"):
            if row[k] == "nan":
                row[k] = ""
        row["paper"] = str(spec.get("paper", ""))
        rows.append(row)
    return rows


def build_claim(spec: dict, snapshot_dir: Path) -> dict:
    """One hand-declared claim, value read from its snapshot row."""
    row = _blank()
    row.update({k: spec.get(k, "") for k in COLUMNS if k in spec})
    row["claim_id"] = spec["id"]
    row["claim_text"] = spec["statement"]
    row["status_source"] = spec.get("status_source", "manual")
    if spec.get("derived"):
        # Computed by src.paper_3.derived from a snapshot table, not taken from
        # upstream; the derived manifest records which input it came from, so
        # the value stays as traceable as a snapshot one.
        from src.paper_3 import derived as _derived
        local = _derived.DERIVED_DIR / spec["derived"]
        if not local.exists():
            raise FileNotFoundError(f"derived table missing: {local} — run derived.build()")
        entry = {"rel_path": f"derived/{spec['derived']}", "local_path": str(local),
                 "sha256": _derived._sha256(local)}
        frame = _read_table(local)
    elif spec.get("table"):
        entry = snapshot.entry_for(spec["table"], snapshot_dir)
        frame = _read_table(snapshot_dir / entry["local_path"])
        r = select_row(frame, spec.get("select", {})) if spec.get("select") else frame.iloc[0]
        row.update({
            "source_table": entry["rel_path"],
            "snapshot_path": str(entry["local_path"]),
            "snapshot_sha256": entry["sha256"],
            "row_selector": json.dumps(spec.get("select", {})),
            "value_field": spec.get("value", ""),
            "value_resolved": render_value(spec["value"], r) if spec.get("value") else "",
            "uncertainty_resolved": render_value(spec["uncertainty"], r) if spec.get("uncertainty") else "",
            "n_resolved": render_value(spec["n"], r) if spec.get("n") else "",
        })
        if spec.get("status_source") in ("matrix", "audit"):
            row["audit_status"] = map_status(r[spec["status_col"]], spec["status_source"])
    if spec.get("status_source", "manual") == "manual":
        row["audit_status"] = map_status(spec.get("audit_status", "UNKNOWN"), "manual")
    for k in ("literature_thesis_ids", "literature_claim_ids", "article_thesis"):
        v = spec.get(k, "")
        row[k] = ";".join(v) if isinstance(v, list) else v
    if isinstance(spec.get("downstream_use"), dict):
        row["downstream_use"] = json.dumps(spec["downstream_use"])
    row["paper"] = str(spec.get("paper", ""))
    return row


def apply_overrides(rows: list[dict], overrides: dict) -> None:
    by_id = {r["claim_id"]: r for r in rows}
    for cid, patch in (overrides or {}).items():
        if cid not in by_id:
            raise KeyError(f"override for unknown claim {cid}")
        for k, v in patch.items():
            if k == "audit_status" and v not in STATUSES:
                raise ValueError(f"{cid}: illegal status {v}")
            by_id[cid][k] = v


def build(snapshot_dir: Path = snapshot.SNAPSHOT_DIR,
          claims_path: Path = CLAIMS_PATH) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = load_claims(claims_path)
    rows: list[dict] = []
    for imp in spec.get("imports", []):
        rows.extend(build_imports(imp, snapshot_dir))
    for c in spec.get("claims", []):
        rows.append(build_claim(c, snapshot_dir))
    apply_overrides(rows, spec.get("overrides", {}))
    ids = [r["claim_id"] for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate claim ids: {dupes}")
    evidence = pd.DataFrame(rows, columns=COLUMNS)

    at_rows = []
    known = set(ids)
    for at in spec.get("article_theses", []):
        missing = [c for c in at["claims"] if c not in known]
        if missing:
            raise KeyError(f"{at['id']}: claims not in registry: {missing}")
        sub = evidence[evidence.claim_id.isin(at["claims"])]
        at_rows.append({
            "article_thesis_id": at["id"], "paper": at.get("paper", 1), "scope": at.get("scope", "main"),
            "status": at.get("status", "from_claims"), "thesis": at["thesis"],
            "claim_ids": ";".join(at["claims"]),
            "literature_layers": at.get("literature_layers", ""),
            "n_claims": len(sub),
            "n_supported": int(sub.audit_status.isin(["SUPPORTED", "SUPPORTED_WITH_LIMITATION"]).sum()),
            "n_blocking": int(sub.audit_status.isin(["UNKNOWN", "UNSUPPORTED", "CONTRADICTED"]).sum()),
            "figures": at.get("figures", ""), "tables": at.get("tables", ""),
            "publication_readiness": at.get("publication_readiness", ""),
        })
    return evidence, pd.DataFrame(at_rows)


def write_status_yaml(evidence: pd.DataFrame) -> Path:
    from src.paper_3.v2 import scientific_status as ss
    status = {}
    for r in evidence.itertuples():
        status[r.claim_id] = ss.ClaimStatus(
            claim_id=r.claim_id, audit_status=r.audit_status,
            findings=(r.scientific_caveat,) if r.scientific_caveat else (),
            required_actions=(r.remaining_action,) if r.remaining_action and r.remaining_action != "none" else (),
            note=f"{r.status_source}: {r.source_table}" if r.source_table else "")
    return ss.write_status(status)


def run(out_dir: Path = OUT_DIR, snapshot_dir: Path = snapshot.SNAPSHOT_DIR,
        claims_path: Path = CLAIMS_PATH, write_status: bool = True) -> Path:
    problems = snapshot.verify(snapshot_dir)
    if problems:
        raise RuntimeError(f"snapshot not intact: {problems[:5]}")
    evidence, theses = build(snapshot_dir, claims_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence.to_csv(out_dir / EVIDENCE_FILE, index=False)
    evidence.to_parquet(out_dir / EVIDENCE_FILE.replace(".csv", ".parquet"), index=False)
    theses.to_csv(out_dir / THESES_FILE, index=False)
    if write_status:
        write_status_yaml(evidence)
    counts = evidence.audit_status.value_counts().to_dict()
    logger.info("own evidence: %d claims %s; %d article theses", len(evidence), counts, len(theses))
    return out_dir / EVIDENCE_FILE
