"""Export contract JSON Schemas for consumers: ``python -m src.contracts.export``.

Writes contracts/schemas/<Name>.v<major>.json. The files are committed so that a
consumer repository can validate its data without installing this package.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.contracts import api, identity, research

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "contracts" / "schemas"

#: (model, major version) — bump the major when a change breaks existing documents.
CONTRACTS = (
    (identity.PaperIdentity, 1),
    (identity.PaperAlias, 1),
    (identity.PaperFile, 1),
    (identity.CohortMember, 1),
    (api.Problem, 1),
    (api.Provenance, 1),
    (api.PaperRef, 1),
    (api.ResolveResponse, 1),
    (api.ResolveBatchRequest, 1),
    (api.ResolveBatchResponse, 1),
    (api.EvidenceSpan, 1),
    (api.QuoteVerifyRequest, 1),
    (api.QuoteVerifyResponse, 1),
    (api.SectionsResponse, 1),
    (api.TextResponse, 1),
    (api.ReferencesResponse, 1),
    (api.TablesResponse, 1),
    (api.DoiMetadata, 1),
    (api.DoiResponse, 1),
    (api.DoiVerifyRequest, 1),
    (api.DoiVerifyResponse, 1),
    (api.GraphPaperResponse, 1),
    (api.CitationsResponse, 1),
    (api.EntityPapersResponse, 1),
    (api.NamedQueriesResponse, 1),
    (api.GraphQueryRequest, 1),
    (api.CypherRequest, 1),
    (api.TabularResponse, 1),
    (api.MetricFact, 1),
    (api.MetricsExtractRequest, 1),
    (api.MetricsExtractResponse, 1),
    (api.MetricFactsResponse, 1),
    (api.MetricOntologyResponse, 1),
    (api.NormalizeRequest, 1),
    (api.NormalizeResponse, 1),
    (api.OntologyEntitiesResponse, 1),
    (api.ChunkSearchRequest, 1),
    (api.ChunkSearchResponse, 1),
    (api.PaperSearchRequest, 1),
    (api.PaperSearchResponse, 1),
    (api.SimilarRequest, 1),
    (api.LocateResponse, 1),
    (api.ManuscriptCitationsRequest, 1),
    (api.ManuscriptCitationsResponse, 1),
    (api.BibFormatRequest, 1),
    (api.BibFormatResponse, 1),
    (api.BibRenderRequest, 1),
    (api.BibRenderResponse, 1),
    (api.BibAuditRequest, 1),
    (api.BibAuditResponse, 1),
    (api.PaperEntitiesResponse, 1),
    (api.ThesesValidateRequest, 1),
    (api.ThesesValidateResponse, 1),
    (research.Labeler, 1),
    (research.Thesis, 1),
    (research.ThesisRef, 1),
    (research.AtomicClaim, 1),
    (research.PositiveControl, 1),
    (research.CitationOccurrence, 1),
    (research.ScreeningLabel, 1),
    (research.QuoteCheck, 1),
    (research.LiteratureNumber, 1),
    (research.TechnicalSource, 1),
    (research.BibVerification, 1),
)


def export(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for model, major in CONTRACTS:
        schema = model.model_json_schema()
        schema["$id"] = f"https://github.com/NikoriakViktot/geohydroai-knowledge-graph/contracts/{model.__name__}.v{major}.json"
        path = out_dir / f"{model.__name__}.v{major}.json"
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in export():
        print(p.relative_to(ROOT))
