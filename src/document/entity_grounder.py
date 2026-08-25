"""
entity_grounder.py — Attach PDF coordinate provenance to extracted entities.

For each entity in paper["entities"], searches the TEIDocument sentence index
for the first sentence containing the entity name (exact then partial match).
Adds a provenance record:

    entity["provenance"] = {
        "page":    int,              # PDF page number (1-based, 0 = unknown)
        "bbox":    [x, y, w, h],    # PDF points, or None if coords absent
        "section": str,             # section title the sentence belongs to
        "match":   "exact" | "partial",
    }

Designed to run after build_paper_json() while the TEIDocument is still alive
(inside the Ray worker, before del doc in process_paper.py step 6).

Never raises — entity dicts without a provenance match are left unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.document.models import TEIDocument

log = logging.getLogger(__name__)

# Entity type keys in paper["entities"] that carry a "name" field
_ENTITY_KEYS = ("methods", "satellites", "dems", "metrics", "sensors")


def ground_entities(paper: dict, doc: "TEIDocument") -> dict:
    """
    Attach PDF provenance to all entities in paper["entities"].

    Modifies entity dicts in-place; returns the same paper dict.
    """
    index = _build_sentence_index(doc)
    if not index:
        return paper

    entities = paper.get("entities", {})
    if not isinstance(entities, dict):
        return paper

    grounded = 0
    for key in _ENTITY_KEYS:
        ent_list = entities.get(key, [])
        if not isinstance(ent_list, list):
            continue
        for ent in ent_list:
            name = ent.get("name", "").strip()
            if not name or "provenance" in ent:
                continue
            prov = _find_provenance(name, index)
            if prov:
                ent["provenance"] = prov
                grounded += 1

    log.debug("entity_grounder: grounded %d / %d entities", grounded,
              sum(len(entities.get(k, [])) for k in _ENTITY_KEYS if isinstance(entities.get(k), list)))
    return paper


# ── Internal ──────────────────────────────────────────────────────────────────

def _build_sentence_index(doc: "TEIDocument") -> list[dict]:
    """
    Flat list of {text_lower, page, bbox, section} for every sentence in doc.
    Also includes paragraph text for fallback when sentence segmentation is absent.
    """
    index: list[dict] = []

    for sec in doc.sections:
        _collect_section(sec, sec.title, index)

    return index


def _collect_section(sec, section_title: str, index: list[dict]) -> None:
    for para in sec.paragraphs:
        for sent in para.sentences:
            if not sent.text:
                continue
            page, bbox = _layout(sent.coords)
            index.append({
                "text_lower": sent.text.lower(),
                "page":       page,
                "bbox":       bbox,
                "section":    section_title,
            })
        # paragraph-level fallback (some parsers produce no sentences)
        if not para.sentences and para.text:
            page, bbox = _layout(para.coords)
            index.append({
                "text_lower": para.text.lower(),
                "page":       page,
                "bbox":       bbox,
                "section":    section_title,
            })
    for sub in sec.subsections:
        _collect_section(sub, sub.title or section_title, index)


def _layout(coords) -> tuple[int, list[float] | None]:
    if coords is None or not coords.boxes:
        return 0, None
    b = coords.boxes[0]
    return b.page, [b.x, b.y, b.w, b.h]


def _find_provenance(name: str, index: list[dict]) -> dict | None:
    name_lower = name.lower()

    # Exact containment first
    for entry in index:
        if name_lower in entry["text_lower"]:
            return {
                "page":    entry["page"],
                "bbox":    entry["bbox"],
                "section": entry["section"],
                "match":   "exact",
            }

    # Partial match: all tokens of name must appear in the sentence
    tokens = name_lower.split()
    if len(tokens) >= 2:
        for entry in index:
            t = entry["text_lower"]
            if all(tok in t for tok in tokens):
                return {
                    "page":    entry["page"],
                    "bbox":    entry["bbox"],
                    "section": entry["section"],
                    "match":   "partial",
                }

    return None
