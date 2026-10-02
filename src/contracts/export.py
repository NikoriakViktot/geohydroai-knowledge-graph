"""Export contract JSON Schemas for consumers: ``python -m src.contracts.export``.

Writes contracts/schemas/<Name>.v<major>.json. The files are committed so that a
consumer repository can validate its data without installing this package.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.contracts import api, identity

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
