"""
registry  —  GeoHydroAI pipeline state registry
================================================

DuckDB-backed orchestration metadata layer for the ingestion pipeline.

Quick start
-----------
    from src.registry import PipelineRegistry, PaperStatus
    from src.config.settings import REGISTRY_DIR

    reg = PipelineRegistry(REGISTRY_DIR)
    reg.init_registry()

    reg.register_paper("paper_001", source_xml="/data/xml/paper_001.tei.xml")
    reg.start_processing("paper_001", worker_id="worker-1")
    reg.mark_success("paper_001", output_json="/data/out/paper_001.paper.json",
                     output_hash="abc123", runtime_sec=2.4)

    pending = reg.get_unprocessed_papers()
    report  = reg.build_runtime_report()
    reg.export_parquet()
"""

from src.registry.registry_db import PipelineRegistry
from src.registry.constants  import PaperStatus, SCHEMA_VERSION

__all__ = ["PipelineRegistry", "PaperStatus", "SCHEMA_VERSION"]
