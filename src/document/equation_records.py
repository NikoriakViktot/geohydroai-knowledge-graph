"""
equation_records.py — one record per equation of a paper, with its parameters,
a hash and a PNG.

The unit is the equation GROBID marks in TEI (<formula xml:id=...>), not the Nougat
region (a region often merges several equations). For every equation:

  text_grobid    GROBID's flattened text ("dS c dt = P -P t -E c ,(2)")
  latex          Nougat's LaTeX from the page markdown, matched by equation number
                 (or reading order when the counts agree); None if Nougat has none
  parameters     symbol, description, unit, value — from the definition clause next
                 to the equation (Nougat LaTeX or GROBID text), then from the paper
                 glossary (nomenclature section/table, definitions elsewhere in the
                 paper) for symbols of the equation that are still undefined
  formula_hash   sha256 of the normalised LaTeX (or of GROBID's text, prefixed "tei:")
  image          PNG of the equation's own GROBID box (300 dpi) and its sha256

Each parameter carries param_hash = sha256(formula_hash|symbol) and a quantity name
("precipitation") so the graph can be searched from a parameter to its formulas.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.document import pdf_io, tei_io
from src.document.formula_parameters import (
    Parameter, equation_symbols, formula_parameters, glossary_from_lines, glossary_from_table,
    merge_glossary, norm_symbol, parse_clause, parse_plain_clause, plain_key, quantity_name,
    _NOMENCLATURE_HEAD, _plain_has,
)
from src.document.nougat_page_mapper import split_blocks

NS = "{http://www.tei-c.org/ns/1.0}"
_LABEL = re.compile(r"\(\s*(\d+[a-z]?)\s*\)\s*$")
PNG_DPI = 300
PAD = 3.0


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _coords(coords: str) -> tuple[int, tuple[float, float, float, float]] | None:
    boxes: dict[int, list] = {}
    for chunk in (coords or "").split(";"):
        parts = chunk.split(",")
        if len(parts) != 5:
            continue
        try:
            pg, x, y, w, h = int(parts[0]), *map(float, parts[1:])
        except ValueError:
            continue
        boxes.setdefault(pg, []).append((x, y, x + w, y + h))
    if not boxes:
        return None
    pg = min(boxes)
    b = boxes[pg]
    return pg, (min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b))


def canonical_latex(latex: str) -> str:
    s = re.sub(r"\\\[|\\\]|\(\d+[a-z]?\)\s*$", "", latex.strip())
    s = re.sub(r"\\(?:text|mathrm|rm|mathit|operatorname)\s*", "", s)
    s = re.sub(r"[\s{}]|\\[,;!]|\\quad", "", s)
    return s.rstrip(".,;")


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── TEI side ──────────────────────────────────────────────────────────────────

def tei_equations(root) -> list[dict]:
    out = []
    for f in root.iter(f"{NS}formula"):
        label_el = f.find(f"{NS}label")
        label = _text(label_el)
        full = _text(f)
        body = full[: len(full) - len(label)].strip() if label and full.endswith(label) else full
        num = _LABEL.search(label or full)
        c = _coords(f.get("coords", ""))
        prev, nxt = f.getprevious(), f.getnext()
        # a definition clause may follow a run of equations
        while nxt is not None and tei_io.localname(nxt) == "formula":
            nxt = nxt.getnext()
        lead = _text(prev) if prev is not None and tei_io.localname(prev) == "p" else ""
        out.append({
            "xml_id":   f.get("{http://www.w3.org/XML/1998/namespace}id") or "",
            "text":     body,
            "number":   num.group(1) if num else None,
            "page":     c[0] if c else None,
            "bbox":     c[1] if c else None,
            "lead_in":  re.split(r"(?<=[.!?])\s+(?=[A-Z])", lead)[-1] if lead else "",
            "clause":   _text(nxt)[:1500] if nxt is not None and tei_io.localname(nxt) == "p" else "",
        })
    return out


def tei_glossary(root) -> list[Parameter]:
    params: list[Parameter] = []
    for div in root.iter(f"{NS}div"):
        head = div.find(f"{NS}head")
        if head is not None and _NOMENCLATURE_HEAD.search(_text(head)):
            lines = []
            for el in div:
                if tei_io.localname(el) in ("p", "list", "item", "label"):
                    lines += [x for x in re.split(r"\n|;\s+(?=[A-Za-zα-ω])", "".join(el.itertext())) if x.strip()]
            params += glossary_from_lines(lines, "nomenclature")
    for fig in root.iter(f"{NS}figure"):
        if fig.get("type") != "table":
            continue
        rows = [[_text(c) for c in r.iter(f"{NS}cell")] for r in fig.iter(f"{NS}row")]
        params += glossary_from_table(rows, "nomenclature_table")
    for p in root.iter(f"{NS}p"):
        params += parse_plain_clause(_text(p), None, "paper_text")
    return params


# ── Nougat side ───────────────────────────────────────────────────────────────

def _tabular_rows(block: str) -> list[list[str]]:
    m = re.search(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", block, re.S)
    if not m:
        return []
    body = re.sub(r"\\hline|\\toprule|\\midrule|\\bottomrule", "", m.group(1))
    return [[c.strip() for c in r.split("&")] for r in re.split(r"\\\\", body) if r.strip()]


def nougat_glossary(page_md: dict[int, str]) -> list[Parameter]:
    params: list[Parameter] = []
    for md in page_md.values():
        for b in split_blocks(md):
            if b.kind == "table":
                params += glossary_from_table(_tabular_rows(b.text), "nomenclature_table")
        for para in md.split("\n\n"):
            if "\\(" in para:
                params += parse_clause(para, None, "paper_text")
    return params


# ── records ───────────────────────────────────────────────────────────────────

def build_equation_records(paper_id: str, pdf_path: Path, tei_path: Path,
                           page_md: dict[int, str], out_dir: Path) -> list[dict]:
    root = tei_io.parse_file(tei_path)
    eqs = tei_equations(root)
    if not eqs:
        return []
    nougat_g = nougat_glossary(page_md)
    tei_g = tei_glossary(root)
    glossary = merge_glossary([p for p in nougat_g + tei_g if p.source.startswith("nomenclature")],
                              [p for p in nougat_g if p.source == "paper_text"],
                              [p for p in tei_g if p.source == "paper_text"])

    # Nougat equation blocks per page
    blocks_of = {pg: [b for b in split_blocks(md) if b.kind == "equation"] for pg, md in page_md.items()}
    tei_by_page: dict[int, list[dict]] = {}
    for e in eqs:
        if e["page"]:
            tei_by_page.setdefault(e["page"], []).append(e)

    # equations without a printed number in Nougat's markdown (some publishers) are
    # matched by the PDF text layer inside GROBID's box of the equation
    from src.document.nougat_page_mapper import formula_precision, layer_tokens
    by_layer: dict[str, object] = {}
    for pg, tlist in tei_by_page.items():
        blocks = blocks_of.get(pg, [])
        if not blocks:
            continue
        numbered = {e["number"] for e in tlist if e["number"]} & {b.number for b in blocks if b.number}
        try:
            words = pdf_io.extract_page_words(pdf_path, pg)
        except Exception:
            continue
        pairs = []
        for e in tlist:
            if e["number"] in numbered or not e["bbox"]:
                continue
            lw, ln, _ = layer_tokens(words, e["bbox"])
            for i, b in enumerate(blocks):
                if b.number and b.number in numbered:
                    continue
                pairs.append((formula_precision(b.text, lw | ln), e["xml_id"], i))
        used: set[int] = set()
        for sc, xid, i in sorted(pairs, reverse=True):
            if sc < 0.5 or xid in by_layer or i in used:
                continue
            by_layer[xid] = blocks[i]
            used.add(i)

    img_dir = out_dir / "equations"
    img_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = []
    for e in eqs:
        if len(re.sub(r"[^A-Za-zα-ωΑ-Ω]", "", e["text"])) < 2:
            continue                                   # a stray label or ")" is not an equation
        latex, block, md = None, None, page_md.get(e["page"] or -1, "")
        blocks = blocks_of.get(e["page"] or -1, [])
        if e["number"]:
            block = next((b for b in blocks if b.number == e["number"]), None)
        if block is None:
            block = by_layer.get(e["xml_id"])
        if block is None and blocks and len(blocks) == len(tei_by_page.get(e["page"], [])):
            block = blocks[tei_by_page[e["page"]].index(e)]
        if block is not None:
            latex = block.text

        # parameters: local clause first (Nougat LaTeX, then GROBID text), then glossary
        found: dict[str, dict] = {}
        if latex:
            s0 = md.find(block.text, block.start)
            for p in formula_parameters(md, s0, s0 + len(block.text), block.text):
                found.setdefault(p["symbol"], p)
        same = {k.replace("_", "").replace(",", "") for k in found}
        for p in parse_plain_clause(e["clause"], e["text"], "after_tei"):
            if p.symbol.replace("_", "").replace(",", "") not in same:
                found.setdefault(p.symbol, p.__dict__.copy())
        lead = re.search(r"([A-Za-z][\w\s\-]{3,80}?)\s+(?P<sym>[A-Za-zα-ω][\w′']{0,5}(?:\s[\w,]{1,8})?)\s*"
                         r"(?:\((?P<u>[^()]{1,25})\))?\s*(?:is|are|can be)\s+(?:calculated|computed|given|defined|expressed|estimated|obtained|determined)",
                         e["lead_in"])
        if lead and _plain_has(e["text"], lead.group("sym")):
            k = plain_key(lead.group("sym"))
            found.setdefault(k, {"symbol": k, "symbol_tex": lead.group("sym"),
                                 "description": re.sub(r"^(?:The|the)\s+", "", lead.group(1).strip()),
                                 "unit": lead.group("u"), "value": None, "source": "before_tei"})
        symbols = equation_symbols(latex) if latex else \
            {k for k in glossary if _plain_has(e["text"], k.replace("_", " ").replace(",", ","))} | set(found)
        have = {k.replace("_", "").replace(",", "") for k in found}
        for k in sorted(symbols - set(found)):
            if k.replace("_", "").replace(",", "") in have:
                continue
            g = glossary.get(k)
            # GROBID's flattened text cannot tell the "g" of KGE_g from a symbol g:
            # without LaTeX, one-character symbols come only from the local clause
            if g is not None and not latex and len(k) == 1:
                continue
            if g is not None:
                d = g.__dict__.copy()
                d["source"] = f"glossary:{g.source}"
                found[k] = d

        formula_hash = sha16(canonical_latex(latex)) if latex else "tei:" + sha16(re.sub(r"\s+", "", e["text"]))
        params = []
        for p in found.values():
            p = dict(p)
            p["param_hash"] = sha16(f"{formula_hash}|{p['symbol']}")
            p["quantity"] = quantity_name(p.get("description") or "")
            params.append(p)

        img_path = img_sha = None
        if e["bbox"] and e["page"]:
            x0, y0, x1, y1 = e["bbox"]
            try:
                img = pdf_io.render_region_image(pdf_path, e["page"], (x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD), dpi=PNG_DPI)
                f = img_dir / f"{e['xml_id'] or 'eq'}_{formula_hash.replace(':', '')}.png"
                img.save(f)
                img_path = str(f)
                img_sha = hashlib.sha256(f.read_bytes()).hexdigest()
            except Exception:
                pass

        rows.append({
            "equation_id":     f"{paper_id}:{e['xml_id']}",
            "paper_id":        paper_id,
            "xml_id":          e["xml_id"],
            "equation_number": e["number"],
            "page":            e["page"],
            "bbox":            json.dumps(e["bbox"]) if e["bbox"] else None,
            "text_grobid":     e["text"],
            "latex":           latex,
            "latex_source":    "nougat_page" if latex else None,
            "formula_hash":    formula_hash,
            "image_path":      img_path,
            "image_sha256":    img_sha,
            "parameters":      json.dumps(params, ensure_ascii=False),
            "n_parameters":    len(params),
            "n_symbols":       len(symbols),
            "lead_in":         e["lead_in"] or None,
            "clause":          e["clause"] or None,
            "created_at":      now,
        })
    return rows


def write_equations_parquet(rows: list[dict], paper_id: str, sodb_dir: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from src.analytics.parquet_schema import EQUATION_RECORDS_SCHEMA
    out = sodb_dir / paper_id / "equation_records.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    for r in rows:
        if r.get("image_path"):
            try:
                r["image_path"] = str(Path(r["image_path"]).resolve().relative_to(root))
            except ValueError:
                pass
    table = pa.Table.from_pylist(rows, schema=EQUATION_RECORDS_SCHEMA)
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.replace(out)
    return out
