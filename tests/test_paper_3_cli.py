"""CLI orchestration: idempotency, step isolation, and loud failure.

A gap analysis that quietly ran without Neo4j or GROBID would report absences that
are really outages, so the tests here care most about steps failing audibly.
"""
from __future__ import annotations

import json

import pytest

from src.paper_3 import cli
from src.paper_3.cli import (
    ALL_STEPS,
    ANALYSE_STEPS,
    HARVEST_STEPS,
    _step,
    load_manifest,
    render_manifest_md,
    update_manifest,
)


# ── the idempotency guard ─────────────────────────────────────────────────────

def test_step_runs_when_the_guard_file_is_missing(tmp_path):
    assert _step("x", tmp_path / "absent.parquet", force=False)


def test_step_skips_when_the_guard_file_exists(tmp_path):
    guard = tmp_path / "present.parquet"
    guard.write_text("x")
    assert not _step("x", guard, force=False)


def test_force_overrides_the_guard(tmp_path):
    guard = tmp_path / "present.parquet"
    guard.write_text("x")
    assert _step("x", guard, force=True)


# ── step sets ─────────────────────────────────────────────────────────────────

def test_phases_partition_all_steps():
    assert set(HARVEST_STEPS) | set(ANALYSE_STEPS) == set(ALL_STEPS)
    assert not set(HARVEST_STEPS) & set(ANALYSE_STEPS)


def test_harvest_precedes_analysis_in_the_step_order():
    """Retrieval must not run before the corpus it is meant to search exists."""
    assert ALL_STEPS.index("discover") < ALL_STEPS.index("retrieve")
    assert ALL_STEPS.index("parquet") < ALL_STEPS.index("index")
    assert ALL_STEPS.index("classify") < ALL_STEPS.index("finalize")
    assert ALL_STEPS.index("finalize") < ALL_STEPS.index("matrix")
    assert ALL_STEPS.index("matrix") < ALL_STEPS.index("novelty")
    assert ALL_STEPS[-1] == "publish"


def test_coverage_runs_before_the_matrix_that_depends_on_it():
    assert ALL_STEPS.index("coverage") < ALL_STEPS.index("matrix")


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "artefacts"
    assert cli.main(["--dry-run", "--out", str(out)]) == 0
    assert not out.exists(), "a dry run must not create the artefact directory"


def test_dry_run_reports_only_the_requested_phase(tmp_path, caplog):
    with caplog.at_level("INFO"):
        cli.main(["--dry-run", "--phase", "harvest", "--out", str(tmp_path / "a")])
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "discover" in logged
    assert "retrieve" not in logged


def test_dry_run_with_explicit_steps(tmp_path, caplog):
    with caplog.at_level("INFO"):
        cli.main(["--dry-run", "--step", "matrix", "--step", "publish",
                  "--out", str(tmp_path / "b")])
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "matrix" in logged and "publish" in logged
    assert "discover" not in logged


def test_an_invalid_theses_file_stops_the_run(tmp_path):
    bad = tmp_path / "theses.yaml"
    bad.write_text("- id: T01\n  block: A\n  statement: only one thesis\n",
                   encoding="utf-8")
    assert cli.main(["--dry-run", "--theses", str(bad),
                     "--out", str(tmp_path / "c")]) == 2


# ── manifest ──────────────────────────────────────────────────────────────────

def test_manifest_round_trips_and_merges(tmp_path):
    update_manifest(tmp_path, run_id="r1", n_downloaded=5)
    update_manifest(tmp_path, n_downloaded=7, kg_expansion_available=False)
    manifest = load_manifest(tmp_path)
    assert manifest["run_id"] == "r1"
    assert manifest["n_downloaded"] == 7
    assert manifest["kg_expansion_available"] is False
    assert "updated_at" in manifest


def test_manifest_of_a_missing_file_is_empty(tmp_path):
    assert load_manifest(tmp_path) == {}


def test_manifest_survives_a_corrupt_file(tmp_path):
    (tmp_path / "run_manifest.json").write_text("{not json", encoding="utf-8")
    assert load_manifest(tmp_path) == {}


def test_manifest_markdown_renders_nested_values(tmp_path):
    update_manifest(tmp_path, chroma={"before": 1, "after": 2, "delta": 1})
    text = render_manifest_md(tmp_path).read_text(encoding="utf-8")
    assert "chroma" in text and "delta" in text


def test_manifest_markdown_on_an_empty_run(tmp_path):
    assert "No run recorded" in render_manifest_md(tmp_path).read_text(encoding="utf-8")


# ── failing loudly ────────────────────────────────────────────────────────────

def test_grobid_being_down_raises_step_error(monkeypatch, tmp_path):
    """The step must stop, not continue with unparsed PDFs."""
    import pandas as pd

    from src.paper_3 import harvest_ingest

    harvest = tmp_path / "harvest"
    harvest.mkdir(parents=True)
    pd.DataFrame([{"slug": "10.1_x", "doi": "10.1/x", "selected": True,
                   "title": "T", "year": 2024, "pdf_url": "u",
                   "matched_thesis_ids": ["T01"]}]).to_parquet(
        harvest / "harvest_candidates.parquet")

    class _DeadClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def is_alive(self):
            return False

    monkeypatch.setattr(harvest_ingest, "PDF_MISSING_DIR", tmp_path / "pdf")
    (tmp_path / "pdf").mkdir()
    (tmp_path / "pdf" / "10.1_x.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(harvest_ingest, "XML_DIR", tmp_path / "xml")
    (tmp_path / "xml").mkdir()

    import src.ingestion.grobid_client as gc
    monkeypatch.setattr(gc, "GROBIDClient", lambda *a, **k: _DeadClient())

    with pytest.raises(harvest_ingest.StepError, match="GROBID"):
        harvest_ingest.step_grobid(tmp_path)


def test_ollama_being_down_raises_step_error(monkeypatch, tmp_path):
    import pandas as pd

    from src.paper_3 import harvest_ingest

    harvest = tmp_path / "harvest"
    harvest.mkdir(parents=True)
    pd.DataFrame([{"slug": "10.1_x", "doi": "10.1/x", "selected": True,
                   "title": "T", "year": 2024, "pdf_url": "u",
                   "matched_thesis_ids": ["T01"]}]).to_parquet(
        harvest / "harvest_candidates.parquet")

    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "10.1_x.tei.xml").write_text("<TEI/>")
    monkeypatch.setattr(harvest_ingest, "XML_DIR", xml_dir)
    monkeypatch.setattr(harvest_ingest, "PAPER_JSON_DIR", tmp_path / "json")
    (tmp_path / "json").mkdir()
    monkeypatch.setattr(harvest_ingest, "_ollama_alive", lambda: False)

    with pytest.raises(harvest_ingest.StepError, match="Ollama"):
        harvest_ingest.step_pipeline(tmp_path)


def test_a_missing_candidates_file_is_a_named_error(tmp_path):
    from src.paper_3 import harvest_ingest
    with pytest.raises(FileNotFoundError, match="discover"):
        harvest_ingest._todo_frame(tmp_path)


def test_workers_are_capped_at_the_machine_limit():
    from src.paper_3.harvest_ingest import MAX_WORKERS
    assert MAX_WORKERS == 3, "CLAUDE.md: --workers 3 is the stable ceiling here"


def test_ray_environment_is_set_for_subprocesses():
    from src.paper_3.harvest_ingest import RAY_ENV
    assert RAY_ENV["TOKENIZERS_PARALLELISM"] == "false"
    assert RAY_ENV["RAYON_NUM_THREADS"] == "1"
    assert RAY_ENV["OMP_NUM_THREADS"] == "2"


# ── publish ───────────────────────────────────────────────────────────────────

def test_publish_copies_only_what_exists(tmp_path):
    from src.paper_3 import publish

    source = tmp_path / "data"
    source.mkdir()
    (source / "GAP_MATRIX.md").write_text("# matrix\n", encoding="utf-8")
    (source / "GAP_MATRIX.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    target = tmp_path / "published"
    out = publish.run(out_dir=source, publish_dir=target, run_id="r1")

    assert {p.name for p in out} == {"GAP_MATRIX.md", "GAP_MATRIX.csv"}
    assert not (target / "NOVELTY_STATEMENT.md").exists()


def test_published_markdown_records_its_provenance(tmp_path):
    from src.paper_3 import publish

    source = tmp_path / "data"
    source.mkdir()
    (source / "GAP_MATRIX.md").write_text("# matrix\n", encoding="utf-8")
    target = tmp_path / "published"
    publish.run(out_dir=source, publish_dir=target, run_id="run-42")

    text = (target / "GAP_MATRIX.md").read_text(encoding="utf-8")
    assert "run-42" in text
    assert "Do not edit by hand" in text
    assert "# matrix" in text


def test_publish_never_ships_parquet():
    """*.parquet is git-ignored, so publishing one would lose it silently."""
    from src.paper_3.publish import PUBLISHED
    assert not any(name.endswith(".parquet") for name in PUBLISHED)
