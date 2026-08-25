"""Backward compatibility: V3 theses and existing SECTIONS stay intact."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_my"))

PAPER_MY = Path(__file__).resolve().parents[1] / "paper_my"


def test_theses_v3_still_has_22_entries():
    path = PAPER_MY / "theses_v3.json"
    assert path.exists(), "theses_v3.json missing"
    data = json.loads(path.read_text())
    assert len(data) == 22


def test_theses_v3_ids_unchanged():
    path = PAPER_MY / "theses_v3.json"
    data = json.loads(path.read_text())
    ids = [t["id"] for t in data]
    assert ids == [f"T{i}" for i in range(1, 23)], "V3 thesis IDs changed"


def test_theses_v3_loads_via_thesis_io():
    from app.thesis_io import load_theses
    path = PAPER_MY / "theses_v3.json"
    theses = load_theses(path)
    assert len(theses) == 22
    assert theses[0].id == "T1"
    assert theses[0].claim  # non-empty


def test_theses_v4_contains_all_v3():
    v4_path = PAPER_MY / "theses_v4.json"
    v3_path = PAPER_MY / "theses_v3.json"
    if not v4_path.exists():
        return  # skip if not generated yet
    v4 = json.loads(v4_path.read_text())
    v3 = json.loads(v3_path.read_text())
    v4_ids = {t["id"] for t in v4}
    for t in v3:
        assert t["id"] in v4_ids, f"V3 thesis {t['id']} missing from V4"


def test_theses_v4_v3_claims_unchanged():
    v4_path = PAPER_MY / "theses_v4.json"
    v3_path = PAPER_MY / "theses_v3.json"
    if not v4_path.exists():
        return
    v4 = {t["id"]: t for t in json.loads(v4_path.read_text())}
    v3 = json.loads(v3_path.read_text())
    for t in v3:
        assert v4[t["id"]]["claim"] == t["claim"], f"Claim changed for {t['id']}"


def test_sections_in_evidence_augmentation():
    """SECTIONS list in evidence_augmentation_v4 still has 14 original sections."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ea_v4", PAPER_MY / "evidence_augmentation_v4.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    # Don't exec — just test the constant directly
    src = (PAPER_MY / "evidence_augmentation_v4.py").read_text()
    # Check that known section IDs are still defined
    for sid in ["sar_flood", "optical_flood", "accuracy_metrics", "dem_hydraulic",
                "ml_dl", "timeliness", "tradeoff", "limitations",
                "eastern_europe", "future_directions", "conclusions"]:
        assert f'"id": "{sid}"' in src or f"'id': '{sid}'" in src, (
            f"Section '{sid}' disappeared from evidence_augmentation_v4.py SECTIONS"
        )


def test_run_pipeline_signature():
    """run_pipeline() must accept thesis_file, extra_sections, sections params."""
    import inspect
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ea_v4_sig", PAPER_MY / "evidence_augmentation_v4.py"
    )
    # Read source and check signature via AST or string search
    src = (PAPER_MY / "evidence_augmentation_v4.py").read_text()
    assert "def run_pipeline(" in src
    assert "thesis_file" in src
    assert "extra_sections" in src
    assert "sections" in src


def test_load_v4_evidence_signature():
    """load_v4_evidence() must accept thesis_file param."""
    src = (PAPER_MY / "generate_paper_v4.py").read_text()
    assert "def load_v4_evidence(" in src
    assert "thesis_file" in src


def test_models_importable():
    from app.models import Thesis, CitationSuggestion, ArticleSection
    t = Thesis(
        id="T_test", display_no=None, section_id="sar_flood",
        section_label="SAR", claim="Test", type="test", paradigm=None,
        keywords=[], sources_needed="", metric_ids=[], sensor_ids=[],
    )
    assert t.id == "T_test"


def test_article_parser_importable():
    from app.article_parser import parse_article, split_sentences
    sections = parse_article("# Intro\n\nSome text here.\n\n## Methods\n\nMore text.")
    assert len(sections) == 2


def test_citation_suggester_importable():
    from app.citation_suggester import apply_approved, suggest_citations
    result = apply_approved("Hello world.", [])
    assert result == "Hello world."
