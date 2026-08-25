"""
test_13_stage2.py — Unit tests for Stage 2 scientific object engineering.

Coverage:
  - ScientificObjectClassifier: semantic type classification for tables, figures,
    equations, sections; typed boolean flags; candidate extraction
  - ScientificObject / ObjectEdge: to_row() contract
  - Stage2Engineer: error propagation, already-complete skip, object count,
    FOLLOWS edge generation, parquet write, manifest write, idempotency
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq
import pytest

from src.document.object_classifier import ScientificObjectClassifier
from src.document.scientific_objects import ObjectEdge, ScientificObject, SemanticType
from src.ingestion.stage1.parser_runner import Stage1Result
from src.ingestion.stage2.engineer import Stage2Engineer, Stage2Result

PIPELINE_HASH = "test-hash-xyz"
PAPER_ID      = "cafebabe" * 8


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stage1_ok(parsed_root: Path) -> Stage1Result:
    return Stage1Result(
        paper_id=PAPER_ID, status="ok",
        parsed_root=parsed_root / PAPER_ID,
    )


def _stage1_error() -> Stage1Result:
    return Stage1Result(
        paper_id=PAPER_ID, status="error",
        parsed_root=Path("/tmp"),
    )


def _write_parquet_artifacts(parsed_root: Path, *, sections=(), figures=(), tables=(), equations=(), references=()):
    """Write minimal parquet files into a parsed store directory."""
    from src.analytics.parquet_schema import (
        EQUATIONS_SCHEMA, PARSED_FIGURES_SCHEMA, PARSED_REFS_SCHEMA, SECTIONS_SCHEMA, TABLES_SCHEMA,
    )
    from src.ingestion.stage1.parsed_store import ParsedStore

    store = ParsedStore(PAPER_ID, PIPELINE_HASH, parsed_root)
    store.write_tei("<TEI/>")
    store.write_sections(list(sections))
    store.write_figures(list(figures))
    store.write_tables(list(tables))
    store.write_equations(list(equations))
    store.write_references(list(references))
    store.write_captions([])
    store.write_manifest({})


def _section_row(n: int, title: str = "Introduction", text: str = "Body text.") -> dict:
    return {
        "section_id":    f"{PAPER_ID}_sec_{n:03d}",
        "paper_id":      PAPER_ID,
        "title":         title,
        "level":         1,
        "n":             str(n),
        "text":          text,
        "word_count":    len(text.split()),
        "page":          n + 1,
        "bbox_x0":       None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PIPELINE_HASH,
        "created_at":    __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }


def _figure_row(n: int, label: str = "Figure 1", caption: str = "") -> dict:
    return {
        "figure_id":     f"{PAPER_ID}_fig_{n:03d}",
        "paper_id":      PAPER_ID,
        "xml_id":        f"fig_{n}",
        "label":         label,
        "caption":       caption,
        "page":          n + 1,
        "bbox_x0":       None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "graphic_page":  None,
        "graphic_x0":    None, "graphic_y0": None, "graphic_x1": None, "graphic_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PIPELINE_HASH,
        "created_at":    __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }


def _table_row(n: int, label: str = "Table 1", caption: str = "", header=None) -> dict:
    return {
        "table_id":        f"{PAPER_ID}_tbl_{n:03d}",
        "paper_id":        PAPER_ID,
        "region_id":       None,
        "xml_id":          f"tab_{n}",
        "page":            n + 1,
        "label":           label,
        "caption":         caption,
        "header_row":      header or [],
        "row_count":       2,
        "col_count":       len(header) if header else 0,
        "has_numeric_data":True,
        "source_parser":   "GROBID",
        "pipeline_hash":   PIPELINE_HASH,
        "created_at":      __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }


def _equation_row(n: int, text: str = "NSE = 1 - sum(...)") -> dict:
    return {
        "equation_id":   f"{PAPER_ID}_eq_{n:03d}",
        "paper_id":      PAPER_ID,
        "xml_id":        f"eq_{n}",
        "text":          text,
        "page":          n + 1,
        "bbox_x0":       None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PIPELINE_HASH,
        "created_at":    __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }


def _ref_row(n: int) -> dict:
    return {
        "ref_id":        f"{PAPER_ID}_ref_b{n}",
        "paper_id":      PAPER_ID,
        "xml_id":        f"b{n}",
        "title":         f"Reference {n}",
        "authors":       "Author A.",
        "journal":       "J. Water",
        "year":          2020 + n,
        "doi":           f"10.1234/ref{n}",
        "raw":           f"Author A. Reference {n}. J. Water, 2020.",
        "source_parser": "GROBID",
        "pipeline_hash": PIPELINE_HASH,
        "created_at":    __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObjectClassifier — tables
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierTables:
    clf = ScientificObjectClassifier()

    def test_nse_kge_caption_is_metrics_table(self):
        sem, score, src, _ = self.clf.classify_table({
            "label": "Table 2",
            "caption": "Performance metrics: NSE, KGE, RMSE for each model.",
            "header_row": ["Model", "NSE", "KGE"],
        })
        assert sem == SemanticType.METRICS_TABLE
        assert score >= 0.85

    def test_performance_keyword_is_metrics_table(self):
        sem, *_ = self.clf.classify_table({
            "label": "Table 3",
            "caption": "Model performance evaluation for calibration period.",
            "header_row": [],
        })
        assert sem == SemanticType.METRICS_TABLE

    def test_comparison_keyword_is_comparison_table(self):
        sem, *_ = self.clf.classify_table({
            "label": "Table 4",
            "caption": "Comparison of LSTM vs SWAT model outputs.",
            "header_row": [],
        })
        assert sem == SemanticType.COMPARISON_TABLE

    def test_parameter_keyword_is_parameter_table(self):
        sem, *_ = self.clf.classify_table({
            "label": "Table 5",
            "caption": "Calibration parameters for HEC-HMS.",
            "header_row": [],
        })
        assert sem == SemanticType.PARAMETER_TABLE

    def test_station_keyword_is_station_table(self):
        sem, *_ = self.clf.classify_table({
            "label": "Table 1",
            "caption": "Gauging station metadata for the Dnipro basin.",
            "header_row": [],
        })
        assert sem == SemanticType.STATION_TABLE

    def test_empty_caption_is_data_table(self):
        sem, score, _, _ = self.clf.classify_table({
            "label": "Table 1",
            "caption": "",
            "header_row": [],
        })
        assert sem == SemanticType.DATA_TABLE
        assert score < 0.6

    def test_classifier_source_is_rules(self):
        _, _, src, _ = self.clf.classify_table({"label": "", "caption": "", "header_row": []})
        assert src == "rules"


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObjectClassifier — figures
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierFigures:
    clf = ScientificObjectClassifier()

    def test_hydrograph_caption(self):
        sem, score, _, _ = self.clf.classify_figure({
            "label": "Figure 3",
            "caption": "Observed and simulated hydrograph at station X, 2015-2020.",
        })
        assert sem == SemanticType.HYDROGRAPH
        assert score >= 0.90

    def test_flood_extent_map(self):
        sem, *_ = self.clf.classify_figure({
            "label": "Figure 4",
            "caption": "Flood inundation extent map derived from Sentinel-1 SAR.",
        })
        assert sem == SemanticType.FLOOD_EXTENT_MAP

    def test_scatter_plot(self):
        sem, *_ = self.clf.classify_figure({
            "label": "Figure 5",
            "caption": "Scatter plot of observed vs simulated discharge.",
        })
        assert sem == SemanticType.SCATTER_PLOT

    def test_satellite_image(self):
        sem, *_ = self.clf.classify_figure({
            "label": "Figure 2",
            "caption": "Sentinel-1 SAR image of the study area.",
        })
        assert sem == SemanticType.SATELLITE_IMAGE

    def test_watershed_map(self):
        sem, *_ = self.clf.classify_figure({
            "label": "Figure 1",
            "caption": "Digital elevation model of the watershed boundary.",
        })
        assert sem == SemanticType.WATERSHED_MAP

    def test_flowchart(self):
        sem, *_ = self.clf.classify_figure({
            "label": "Figure 1",
            "caption": "Methodology flowchart for the proposed framework.",
        })
        assert sem == SemanticType.FLOWCHART

    def test_generic_fallback(self):
        sem, score, _, _ = self.clf.classify_figure({"label": "Figure 9", "caption": "Illustration."})
        assert sem == SemanticType.GENERIC_FIGURE
        assert score < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObjectClassifier — equations
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierEquations:
    clf = ScientificObjectClassifier()

    def test_nse_is_objective_function(self):
        sem, score, _, flags = self.clf.classify_equation({
            "text": "NSE = 1 - sum(Qobs - Qsim)^2 / sum(Qobs - Qmean)^2"
        })
        assert sem == SemanticType.OBJECTIVE_FUNCTION
        assert score >= 0.90
        assert flags.get("equation_name") == "NSE"

    def test_kge_is_objective_function(self):
        sem, _, _, flags = self.clf.classify_equation({
            "text": "KGE = 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)"
        })
        assert sem == SemanticType.OBJECTIVE_FUNCTION
        assert flags.get("equation_name") == "KGE"

    def test_rmse_equation_name(self):
        _, _, _, flags = self.clf.classify_equation({"text": "RMSE = sqrt(mean((Qo - Qs)^2))"})
        assert flags.get("equation_name") == "RMSE"

    def test_manning_is_physical(self):
        sem, *_ = self.clf.classify_equation({"text": "Manning's equation Q = (1/n) * A * R^(2/3) * S^(1/2)"})
        assert sem == SemanticType.PHYSICAL_EQUATION

    def test_regression_is_regression(self):
        sem, *_ = self.clf.classify_equation({"text": "Linear regression: y = ax + b, R^2 = 0.95"})
        assert sem == SemanticType.REGRESSION_FORMULA

    def test_unknown_text_is_generic(self):
        sem, score, _, _ = self.clf.classify_equation({"text": "x = y + z"})
        assert sem == SemanticType.GENERIC_EQUATION
        assert score < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObjectClassifier — sections
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierSections:
    clf = ScientificObjectClassifier()

    def test_methods_section(self):
        sem, score, _, _ = self.clf.classify_section({"title": "Methodology", "text": ""})
        assert sem == SemanticType.METHODS_SECTION
        assert score >= 0.85

    def test_results_section(self):
        sem, *_ = self.clf.classify_section({"title": "Results and Discussion", "text": ""})
        assert sem == SemanticType.RESULTS_SECTION

    def test_data_section(self):
        sem, *_ = self.clf.classify_section({"title": "Data and Datasets", "text": ""})
        assert sem == SemanticType.DATA_SECTION

    def test_study_area_section(self):
        sem, *_ = self.clf.classify_section({"title": "Study Area", "text": ""})
        assert sem == SemanticType.STUDY_AREA_SECTION

    def test_intro_section(self):
        sem, *_ = self.clf.classify_section({"title": "Introduction", "text": ""})
        assert sem == SemanticType.INTRO_SECTION

    def test_conclusion_section(self):
        sem, *_ = self.clf.classify_section({"title": "Conclusions", "text": ""})
        assert sem == SemanticType.CONCLUSION_SECTION

    def test_generic_fallback(self):
        sem, score, _, _ = self.clf.classify_section({"title": "Acknowledgements", "text": ""})
        assert sem == SemanticType.GENERIC_SECTION
        assert score < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObjectClassifier — flags
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierFlags:
    clf = ScientificObjectClassifier()

    def test_contains_models_lstm(self):
        _, _, _, flags = self.clf.classify_table({
            "label": "", "caption": "LSTM model performance.", "header_row": [],
        })
        assert flags["contains_models"] is True

    def test_contains_models_swat_and_vic(self):
        _, _, _, flags = self.clf.classify_figure({
            "label": "", "caption": "Comparison of SWAT and VIC models.",
        })
        assert flags["contains_models"] is True
        candidates = json.loads(flags["candidate_models"])
        assert "SWAT" in candidates
        assert "VIC" in candidates

    def test_contains_metrics_true_for_nse_kge(self):
        _, _, _, flags = self.clf.classify_table({
            "label": "", "caption": "NSE=0.87, KGE=0.85.", "header_row": [],
        })
        assert flags["contains_metrics"] is True
        metrics = json.loads(flags["candidate_metrics"])
        assert "NSE" in metrics

    def test_contains_satellite_sentinel(self):
        _, _, _, flags = self.clf.classify_figure({
            "label": "", "caption": "Sentinel-1 SAR-derived flood map.",
        })
        assert flags["contains_satellite"] is True

    def test_contains_coordinates_latitude(self):
        _, _, _, flags = self.clf.classify_section({
            "title": "Study Area",
            "text": "Located at latitude 48.5°N, longitude 35.2°E.",
        })
        assert flags["contains_coordinates"] is True

    def test_contains_hydrograph_discharge(self):
        _, _, _, flags = self.clf.classify_figure({
            "label": "", "caption": "Simulated discharge hydrograph at outlet.",
        })
        assert flags["contains_hydrograph"] is True

    def test_contains_timeseries_daily(self):
        _, _, _, flags = self.clf.classify_section({
            "title": "Results",
            "text": "Daily streamflow time series was analysed over 10 years.",
        })
        assert flags["contains_timeseries"] is True

    def test_no_flags_when_empty(self):
        _, _, _, flags = self.clf.classify_table({"label": "", "caption": "", "header_row": []})
        assert flags["contains_models"] is False
        assert flags["contains_metrics"] is False
        assert flags["candidate_models"] is None


# ─────────────────────────────────────────────────────────────────────────────
# ScientificObject.to_row + ObjectEdge.to_row
# ─────────────────────────────────────────────────────────────────────────────

class TestScientificObjectToRow:
    def _obj(self, **overrides) -> ScientificObject:
        defaults = dict(
            object_id="obj_001", paper_id=PAPER_ID,
            object_type="table", semantic_type=SemanticType.METRICS_TABLE,
            source_id="tbl_001", page=2,
            raw_text="NSE performance.", classifier_score=0.90,
            classifier_source="rules",
        )
        defaults.update(overrides)
        return ScientificObject(**defaults)

    def test_to_row_has_required_keys(self):
        row = self._obj().to_row()
        for key in ("object_id", "paper_id", "object_type", "semantic_type",
                    "source_id", "page", "raw_text", "classifier_score", "classifier_source"):
            assert key in row

    def test_to_row_none_flags_are_none(self):
        row = self._obj().to_row()
        assert row["contains_models"] is None
        assert row["candidate_models"] is None

    def test_to_row_with_flags(self):
        obj = self._obj(contains_models=True, candidate_models='["LSTM"]')
        row = obj.to_row()
        assert row["contains_models"] is True
        assert row["candidate_models"] == '["LSTM"]'


class TestObjectEdgeToRow:
    def test_to_row_has_required_keys(self):
        edge = ObjectEdge(
            edge_id="e001", paper_id=PAPER_ID,
            source_id="sec_001", target_id="sec_002",
            relation="FOLLOWS", confidence=1.0,
        )
        row = edge.to_row()
        for key in ("edge_id", "paper_id", "source_id", "target_id", "relation", "confidence"):
            assert key in row

    def test_confidence_none_is_preserved(self):
        edge = ObjectEdge(
            edge_id="e001", paper_id=PAPER_ID,
            source_id="a", target_id="b",
            relation="IN_SECTION",
        )
        assert edge.to_row()["confidence"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Stage2Engineer
# ─────────────────────────────────────────────────────────────────────────────

class TestStage2EngineerErrors:
    def test_run_with_error_stage1_returns_error(self, tmp_path):
        eng = Stage2Engineer(
            sodb_root=tmp_path / "sodb",
            parsed_root=tmp_path / "parsed",
            pipeline_hash=PIPELINE_HASH,
        )
        result = eng.run(_stage1_error())
        assert result.status == "error"

    def test_run_missing_parsed_artifacts_still_runs(self, tmp_path):
        parsed_root = tmp_path / "parsed"
        s1 = _stage1_ok(parsed_root)
        # parsed directory empty — engineer should write 0 objects without error
        (parsed_root / PAPER_ID).mkdir(parents=True, exist_ok=True)
        eng = Stage2Engineer(
            sodb_root=tmp_path / "sodb",
            parsed_root=parsed_root,
            pipeline_hash=PIPELINE_HASH,
        )
        result = eng.run(s1)
        assert result.status == "ok"
        assert result.object_count == 0


class TestStage2EngineerIdempotency:
    def test_second_run_returns_skipped(self, tmp_path):
        parsed_root = tmp_path / "parsed"
        _write_parquet_artifacts(parsed_root)
        s1 = _stage1_ok(parsed_root)
        sodb_root = tmp_path / "sodb"
        eng = Stage2Engineer(
            sodb_root=sodb_root, parsed_root=parsed_root, pipeline_hash=PIPELINE_HASH,
        )
        eng.run(s1)
        result2 = eng.run(s1)
        assert result2.status == "skipped"

    def test_force_reruns_when_complete(self, tmp_path):
        parsed_root = tmp_path / "parsed"
        _write_parquet_artifacts(parsed_root)
        s1 = _stage1_ok(parsed_root)
        sodb_root = tmp_path / "sodb"
        eng = Stage2Engineer(
            sodb_root=sodb_root, parsed_root=parsed_root,
            pipeline_hash=PIPELINE_HASH, force=True,
        )
        eng.run(s1)
        result2 = eng.run(s1)
        assert result2.status == "ok"


class TestStage2EngineerObjectBuilding:
    def _run(self, tmp_path, *, sections=(), figures=(), tables=(), equations=(), references=()):
        parsed_root = tmp_path / "parsed"
        _write_parquet_artifacts(
            parsed_root,
            sections=sections, figures=figures,
            tables=tables, equations=equations, references=references,
        )
        s1 = _stage1_ok(parsed_root)
        eng = Stage2Engineer(
            sodb_root=tmp_path / "sodb",
            parsed_root=parsed_root,
            pipeline_hash=PIPELINE_HASH,
        )
        return eng.run(s1)

    def test_sections_produce_objects(self, tmp_path):
        result = self._run(tmp_path, sections=[_section_row(0), _section_row(1)])
        assert result.object_count >= 2

    def test_figures_produce_objects(self, tmp_path):
        result = self._run(tmp_path, figures=[_figure_row(0), _figure_row(1)])
        assert result.object_count >= 2

    def test_tables_produce_objects(self, tmp_path):
        result = self._run(tmp_path, tables=[_table_row(0)])
        assert result.object_count >= 1

    def test_equations_produce_objects(self, tmp_path):
        result = self._run(tmp_path, equations=[_equation_row(0)])
        assert result.object_count >= 1

    def test_references_produce_objects(self, tmp_path):
        result = self._run(tmp_path, references=[_ref_row(0), _ref_row(1)])
        assert result.object_count >= 2

    def test_follows_edges_between_sections(self, tmp_path):
        result = self._run(tmp_path, sections=[
            _section_row(0, "Introduction"),
            _section_row(1, "Methods"),
            _section_row(2, "Results"),
        ])
        # 3 sections → 2 FOLLOWS edges
        assert result.edge_count == 2

    def test_no_edges_with_single_section(self, tmp_path):
        result = self._run(tmp_path, sections=[_section_row(0)])
        assert result.edge_count == 0

    def test_metrics_table_classified(self, tmp_path):
        result = self._run(tmp_path, tables=[
            _table_row(0, "Table 1", "Model NSE KGE RMSE performance table.")
        ])
        sodb_dir = tmp_path / "sodb" / PAPER_ID
        rows = pq.read_table(sodb_dir / "scientific_objects.parquet").to_pylist()
        tbl = next(r for r in rows if r["object_type"] == "table")
        assert tbl["semantic_type"] == SemanticType.METRICS_TABLE

    def test_hydrograph_figure_classified(self, tmp_path):
        result = self._run(tmp_path, figures=[
            _figure_row(0, "Figure 2", "Observed and simulated discharge hydrograph.")
        ])
        sodb_dir = tmp_path / "sodb" / PAPER_ID
        rows = pq.read_table(sodb_dir / "scientific_objects.parquet").to_pylist()
        fig = next(r for r in rows if r["object_type"] == "figure")
        assert fig["semantic_type"] == SemanticType.HYDROGRAPH


class TestStage2EngineerParquetOutput:
    def _run_with_sections(self, tmp_path):
        parsed_root = tmp_path / "parsed"
        _write_parquet_artifacts(
            parsed_root,
            sections=[_section_row(0, "Methods"), _section_row(1, "Results")],
            tables=[_table_row(0, "Table 1", "NSE model performance.")],
        )
        s1 = _stage1_ok(parsed_root)
        eng = Stage2Engineer(
            sodb_root=tmp_path / "sodb",
            parsed_root=parsed_root,
            pipeline_hash=PIPELINE_HASH,
        )
        return eng.run(s1), tmp_path / "sodb" / PAPER_ID

    def test_scientific_objects_parquet_exists(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        assert (sodb_dir / "scientific_objects.parquet").exists()

    def test_object_graph_parquet_exists(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        assert (sodb_dir / "object_graph.parquet").exists()

    def test_object_manifest_json_exists(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        assert (sodb_dir / "object_manifest.json").exists()

    def test_manifest_has_correct_counts(self, tmp_path):
        result, sodb_dir = self._run_with_sections(tmp_path)
        manifest = json.loads((sodb_dir / "object_manifest.json").read_text())
        assert manifest["object_count"] == result.object_count
        assert manifest["edge_count"]   == result.edge_count

    def test_objects_parquet_has_pipeline_hash(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        rows = pq.read_table(sodb_dir / "scientific_objects.parquet").to_pylist()
        assert all(r["pipeline_hash"] == PIPELINE_HASH for r in rows)

    def test_edges_parquet_relation_is_follows(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        rows = pq.read_table(sodb_dir / "object_graph.parquet").to_pylist()
        assert all(r["relation"] == "FOLLOWS" for r in rows)

    def test_no_tmp_files_remain(self, tmp_path):
        _, sodb_dir = self._run_with_sections(tmp_path)
        tmp_files = list(sodb_dir.glob("*.tmp"))
        assert tmp_files == []


class TestStage2Result:
    def test_should_continue_true_only_for_ok(self):
        for status in ("skipped", "error"):
            r = Stage2Result(paper_id="x", status=status, sodb_root=Path("/tmp"))
            assert r.should_continue is False

    def test_should_continue_true_for_ok(self):
        r = Stage2Result(paper_id="x", status="ok", sodb_root=Path("/tmp"))
        assert r.should_continue is True
