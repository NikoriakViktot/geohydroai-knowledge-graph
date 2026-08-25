"""
parse_stage.py — TEI/XML parsing utilities extracted from pipeline.py.

Provides: first_text, all_text, parse_authors, parse_metadata,
          section_tags, split_inline_sections, parse_sections,
          _reclassify_from_other, normalize_country_name.

pipeline.py re-exports these for backward compatibility.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from src.ingestion.utils import clean_text, doi_to_url, scholar_url

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

INLINE_HEADINGS: list[tuple[str, str]] = [
    ("introduction",  r"\bIntroduction\.\s+"),
    ("methods",       r"\bMaterials and methods\.\s+"),
    ("methods",       r"\bMaterial and methods\.\s+"),
    ("methods",       r"\bMethods\.\s+"),
    ("results",       r"\bResults(?: and discussion)?\.\s+"),
    ("discussion",    r"\bDiscussion\.\s+"),
    ("conclusion",    r"\bConclusions?\.\s+"),
]

_METHOD_PHRASES: tuple[str, ...] = (
    "calibrat", "validat", "parameter", "hydraulic", "hydrologic",
    "satellite image", "remote sensing", "dem ", "digital elevation",
    "classification", "regression", "neural network", "machine learning",
    "deep learning", "random forest", "support vector", "insar", "sar ",
    "landsat", "sentinel", "modis", "hec-ras", "hec-hms", "swat ", "mike ",
    "flood map", "inundation", "runoff", "discharge", "rainfall",
    "threshold", "accuracy", "precision", "recall", "f1", "rmse", "nse",
    "bias", "correlation", "simulation", "equation", "formula",
    "spatial resolution", "pixel", "band ratio", "ndwi", "ndvi",
    "cross-section", "manning", "friction", "roughness", "bathymetr",
    "algorithm", "approach", "procedure", "framework", "workflow",
    "frequency analysis", "flood frequency", "hydrograph", "return period",
    "manning's n", "flow resistance", "resistance coefficient",
    "rating curve", "stage-discharge", "geostatistic", "interpolat",
    "classification accuracy", "confusion matrix", "training", "testing",
)

_STUDY_AREA_PHRASES: tuple[str, ...] = (
    "study area", "study site", "study region", "study watershed",
    "study basin", "study catchment", "located in", "located at",
    "catchment area", "drainage area", "river basin", "watershed area",
    "latitude", "longitude", "coordinates", "elevation range",
    "climate", "topograph", "land use", "land cover", "geographic",
    "municipality", "province", "district", "annual rainfall",
    "mean annual", "humid", "arid", "semi-arid",
)

_RESULTS_PHRASES: tuple[str, ...] = (
    "accuracy of", "overall accuracy", "f1-score", "rmse of", "nse of",
    "kappa coefficient", "coefficient of determination", "r-squared",
    "results show", "results indicate", "results suggest",
    "performed well", "achieved", "outperform", "better than",
    "table ", "figure ", "confusion matrix", "comparison of methods",
    "the proposed method", "our method", "the model achieved",
)

_METHOD_PHRASE_THRESHOLD  = 5    # hits per 1 000 chars to consider method-rich
_STUDY_AREA_THRESHOLD     = 3    # hits per 1 000 chars to consider geo-rich
_RESULTS_THRESHOLD        = 3    # hits per 1 000 chars to consider results-rich
_OTHER_MIN_CHARS          = 3_000   # don't reclassify tiny other blocks
_METHODS_SHORT_THRESHOLD  = 2_000   # methods < this → try to rescue from other


def normalize_country_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return clean_text(name).replace(";", "").strip()


def first_text(root, xpath: str) -> Optional[str]:
    values = root.xpath(xpath, namespaces=NS)
    if not values:
        return None
    return clean_text(str(values[0]))


def all_text(node, xpath: str) -> str:
    return clean_text(" ".join(node.xpath(xpath, namespaces=NS)))


def parse_authors(root) -> list[dict]:
    authors = []
    for a in root.xpath("//tei:sourceDesc//tei:analytic/tei:author", namespaces=NS):
        first = clean_text(" ".join(a.xpath(".//tei:forename/text()", namespaces=NS)))
        last  = clean_text(" ".join(a.xpath(".//tei:surname/text()",  namespaces=NS)))
        email = first_text(a, ".//tei:email/text()")
        orcid = first_text(a, ".//tei:idno[@type='ORCID']/text()")
        affiliation = clean_text(" ".join(a.xpath(".//tei:affiliation//text()", namespaces=NS)))
        countries = [
            normalize_country_name(c)
            for c in a.xpath(".//tei:country/text()", namespaces=NS)
        ]
        countries = [c for c in countries if c]
        full_name = clean_text(f"{first} {last}") if first or last else None
        if full_name:
            authors.append({
                "first_name": first or None, "last_name": last or None,
                "full_name": full_name, "email": email, "orcid": orcid,
                "affiliation": affiliation or None,
                "affiliation_countries": countries,
            })
    return authors


def parse_metadata(root, xml_path: Path) -> dict:
    title = (
        first_text(root, "//tei:titleStmt/tei:title/text()")
        or first_text(root, "//tei:sourceDesc//tei:analytic/tei:title/text()")
    )
    doi     = first_text(root, "//tei:idno[@type='DOI']/text()")
    year    = first_text(root, "//tei:date/@when")
    if year:
        year = year[:4]
    journal   = first_text(root, "//tei:sourceDesc//tei:monogr/tei:title/text()")
    publisher = (
        first_text(root, "//tei:sourceDesc//tei:monogr//tei:publisher/text()")
        or first_text(root, "//tei:publicationStmt/tei:publisher/text()")
    )
    url = doi_to_url(doi) or first_text(root, "//tei:ptr/@target") or scholar_url(title)
    return {
        "paper_id":   xml_path.stem.replace(".tei", ""),
        "source_xml": str(xml_path),
        "title":      title, "doi": doi, "url": url,
        "year":       year,  "journal": journal, "publisher": publisher,
        "authors":    parse_authors(root),
    }


def section_tags(head_text: Optional[str], n_attr: Optional[str], content: str) -> set[str]:
    head = (head_text or "").lower()
    n    = (n_attr or "").strip()
    tags: set[str] = set()

    if "summary" in head or "abstract" in head:
        tags.add("abstract")
    if "intro" in head or n == "1" or n.startswith("1."):
        tags.add("introduction")
    if any(k in head for k in ["study area", "study region", "study site",
                                 "area of interest", "study area and data",
                                 "study region and data", "study watershed",
                                 "study basin", "study catchment",
                                 "catchment description", "watershed description"]):
        tags.add("study_area")
    if any(k in head for k in ["dataset", "datasets", "data sources",
                                 "data", "materials", "data collection",
                                 "remote sensing data", "satellite data"]):
        tags.add("data_sources")
    if any(k in head for k in ["method", "methods", "methodology", "workflow",
                                 "processing", "model", "models", "simulation",
                                 "algorithm", "algorithms",
                                 "change detection algorithms",
                                 "design", "approach", "framework",
                                 "calibration", "implementation", "technique",
                                 "procedure", "experiment", "analysis",
                                 "hydraulic", "hydrological", "hydrologic",
                                 "frequency analysis", "flood frequency",
                                 "flow resistance", "discharge estimation",
                                 "runoff estimation", "rating curve",
                                 "numerical", "computation", "estimation",
                                 "classification method", "mapping method"]):
        tags.add("methods")
    if "data and methods" in head or "materials and methods" in head:
        tags.update({"methods", "data_sources"})
    if "data and study area" in head or "study area and data" in head:
        tags.update({"study_area", "data_sources"})
    if any(k in head for k in ["result", "results", "accuracy",
                                 "evaluation", "assessment", "performance",
                                 "validation results", "model performance",
                                 "comparison of"]):
        tags.add("results")
    if "discussion" in head:
        tags.add("discussion")
    if "conclusion" in head:
        tags.add("conclusion")
    if not tags:
        tags.add("other")
    return tags


def split_inline_sections(content: str) -> dict:
    content = content or ""
    found: list[tuple[int, int, str]] = []
    for name, pattern in INLINE_HEADINGS:
        for m in re.finditer(pattern, content, flags=re.I):
            found.append((m.start(), m.end(), name))
    if not found:
        return {"other": content}
    found.sort()
    result: dict[str, str] = {}
    prefix = clean_text(content[:found[0][0]])
    if prefix:
        result.setdefault("other", "")
        result["other"] += prefix + "\n"
    for i, (start, end, name) in enumerate(found):
        next_start = found[i + 1][0] if i + 1 < len(found) else len(content)
        block = clean_text(content[end:next_start])
        if block:
            result.setdefault(name, "")
            result[name] += block + "\n"
    return result


def parse_sections(root) -> dict:
    sections = {
        "abstract": "", "introduction": "", "study_area": "",
        "data_sources": "", "methods": "", "results": "",
        "discussion": "", "conclusion": "", "other": "",
    }
    abstract = root.xpath("//tei:abstract//tei:p//text()", namespaces=NS)
    if abstract:
        sections["abstract"] += clean_text(" ".join(abstract)) + "\n"
    for div in root.xpath("//tei:body//tei:div", namespaces=NS):
        head    = all_text(div, "./tei:head//text()")
        n_attr  = div.get("n")
        content = clean_text(" ".join(div.xpath(".//tei:p//text()", namespaces=NS)))
        if not content:
            continue
        tags = section_tags(head, n_attr, content)
        if tags == {"other"}:
            inline_sections = split_inline_sections(content)
            for tag, block in inline_sections.items():
                if tag in sections:
                    sections[tag] += block + "\n"
        else:
            for tag in tags:
                if tag in sections:
                    sections[tag] += content + "\n"
    return _reclassify_from_other(sections)


def _reclassify_from_other(sections: dict) -> dict:
    """
    Rescue method/study_area/results content from the 'other' catch-all bucket.

    Two-pass strategy
    -----------------
    Pass 1 — bulk methods rescue:
      When methods is short (<2000 chars) and 'other' is large and method-dense,
      append the entire 'other' block to methods.  This handles the common case
      where a non-standard section header ("Flow Resistance", "Flood Frequency
      Analysis") sent the whole methodology chapter to 'other'.

    Pass 2 — paragraph-level rescue:
      When methods already has substantial content (>= 2000 chars), scan each
      paragraph of 'other' individually and append geo-rich paragraphs to
      study_area and results-rich paragraphs to results.  Paragraphs that don't
      match any signal stay in 'other'.
    """
    other = sections.get("other", "")
    if len(other) < _OTHER_MIN_CHARS:
        return sections

    # ── Pass 1: bulk methods rescue ───────────────────────────────────────────
    methods_empty = not sections.get("methods", "").strip()
    if methods_empty:
        other_lc = other.lower()
        n_chars  = max(len(other_lc), 1)
        hits     = sum(other_lc.count(p) for p in _METHOD_PHRASES)
        density  = hits / n_chars * 1_000
        if density >= _METHOD_PHRASE_THRESHOLD:
            existing = sections.get("methods", "")
            sections["methods"] = (existing + "\n" + other).strip()
            sections["other"]   = ""
            return sections

    # ── Pass 2: paragraph-level rescue (study_area and results) ──────────────
    # Only when methods is substantial — we don't want to split a method-rich
    # other block into tiny fragments when the bulk rescue above already fired.
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", other) if len(p.strip()) > 150]
    if not paragraphs:
        return sections

    rescued_study_area: list[str] = []
    rescued_results:    list[str] = []
    remaining:          list[str] = []

    for para in paragraphs:
        para_lc = para.lower()
        n_p     = max(len(para_lc), 1)
        geo_d   = sum(para_lc.count(p) for p in _STUDY_AREA_PHRASES) / n_p * 1_000
        res_d   = sum(para_lc.count(p) for p in _RESULTS_PHRASES)    / n_p * 1_000
        met_d   = sum(para_lc.count(p) for p in _METHOD_PHRASES)     / n_p * 1_000

        if geo_d >= _STUDY_AREA_THRESHOLD and geo_d >= res_d and geo_d >= met_d:
            rescued_study_area.append(para)
        elif res_d >= _RESULTS_THRESHOLD and res_d >= met_d:
            rescued_results.append(para)
        else:
            remaining.append(para)

    if rescued_study_area:
        existing = sections.get("study_area", "")
        sections["study_area"] = (existing + "\n" + "\n\n".join(rescued_study_area)).strip()
    if rescued_results:
        existing = sections.get("results", "")
        sections["results"] = (existing + "\n" + "\n\n".join(rescued_results)).strip()
    if rescued_study_area or rescued_results:
        sections["other"] = "\n\n".join(remaining)

    return sections
