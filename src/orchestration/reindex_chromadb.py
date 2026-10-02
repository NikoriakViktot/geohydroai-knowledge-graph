"""
reindex_chromadb.py — Standalone ChromaDB re-indexer for GeoHydroAI.

Rebuilds the ChromaDB vectorstore from GROBID TEI XML files without
re-running any heavy pipeline stages (no GROBID, Nougat, SpaCy, Ollama, Ray).

Workflow per paper:
  1. Locate TEI XML via {paper_id}.tei.xml naming convention
  2. Parse to TEIDocument with TEIParser (same call as process_paper.py:323)
  3. Chunk with LayoutAwareChunker(strategy="sentence") (same as process_paper.py:409)
  4. Encode chunk texts with SPECTER2 (768-dim) via SentenceTransformer
  5. Upsert DocumentChunks to VectorStore (idempotent on chunk_id)

Safe to re-run: upserts are idempotent. Never modifies paper.json files.

--chunk-types abstract,figure,table re-embeds only those chunks, in place (their
chunk_ids do not depend on the text): used after the 2026-10-02 parser fix that stopped
GROBID sentences being glued together in abstracts and captions.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)


def _paper_id(json_path: Path) -> str:
    """veilleux2011.tei.paper.json  →  veilleux2011"""
    return json_path.name.replace(".tei.paper.json", "")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Rebuild ChromaDB vectorstore from GROBID TEI XML files."
    )
    ap.add_argument(
        "--xml-dir",
        default="data/literature/grobid_xml",
        type=Path,
        help="Directory of *.tei.xml files (default: data/literature/grobid_xml)",
    )
    ap.add_argument(
        "--json-dir",
        default="data/literature/paper_json",
        type=Path,
        help="Directory of *.tei.paper.json files (default: data/literature/paper_json)",
    )
    ap.add_argument(
        "--collection-name",
        default=None,
        help="ChromaDB collection to write into (default: COLLECTION_NAME from src.config)",
    )
    ap.add_argument(
        "--batch-size",
        default=32,
        type=int,
        help="SentenceTransformer encoding batch size (default: 32)",
    )
    ap.add_argument(
        "--limit",
        default=None,
        type=int,
        help="Stop after N papers (smoke test mode)",
    )
    ap.add_argument(
        "--chunk-types",
        default=None,
        help="comma list, e.g. abstract,figure,table: upsert only these chunk types "
             "(refresh text that changed without re-embedding everything)",
    )
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    wanted = {t.strip() for t in args.chunk_types.split(",") if t.strip()} if args.chunk_types else None

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Lazy imports — keep sentence_transformers load before project imports
    # so model download failures surface early.
    model_name = os.getenv("EMBEDDING_MODEL", "allenai/specter2_base")
    log.info("Loading embedding model: %s", model_name)
    model = SentenceTransformer(model_name)
    dim = model.get_embedding_dimension()
    log.info("Embedding model ready: model=%s  dim=%d", model_name, dim)

    from src.document import LayoutAwareChunker, TEIParser
    from src.vectorstore.chroma_store import VectorStore

    if args.collection_name is None:
        from src.config import COLLECTION_NAME
        args.collection_name = COLLECTION_NAME
    vs = VectorStore(collection_name=args.collection_name)
    log.info(
        "VectorStore ready: collection=%s  existing=%d",
        args.collection_name,
        vs.count(),
    )

    json_files = sorted(args.json_dir.glob("*.tei.paper.json"))
    if args.limit:
        json_files = json_files[: args.limit]
    log.info("Papers to index: %d", len(json_files))

    ok = fail = skip = 0
    parser  = TEIParser()
    chunker = LayoutAwareChunker(strategy="sentence")

    for i, jf in enumerate(json_files):
        pid = _paper_id(jf)
        xml = args.xml_dir / f"{pid}.tei.xml"

        if not xml.exists():
            log.warning("[skip-no-xml] %s", pid)
            skip += 1
            continue

        try:
            doc    = parser.parse_file(xml, pid)
            chunks = chunker.chunk(doc)
            if wanted is not None:
                chunks = [c for c in chunks if c.chunk_type in wanted]

            if not chunks:
                log.warning("[skip-no-chunks] %s", pid)
                skip += 1
                continue

            # Deduplicate by chunk_id — LayoutAwareChunker can produce duplicate
            # IDs when identical text appears in multiple positions; ChromaDB
            # rejects any upsert batch that contains the same ID twice.
            seen: set[str] = set()
            deduped = [c for c in chunks if not (c.chunk_id in seen or seen.add(c.chunk_id))]  # type: ignore[func-returns-value]
            if len(deduped) < len(chunks):
                log.debug("[dedup] %s: %d → %d chunks", pid, len(chunks), len(deduped))
            chunks = deduped

            texts = [c.text for c in chunks]
            vecs  = model.encode(
                texts,
                batch_size=args.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            vs.upsert_document_chunks(chunks, np.array(vecs, dtype=np.float32))
            ok += 1

        except Exception as exc:
            log.error("[fail] %s — %s", pid, exc)
            fail += 1

        if (i + 1) % 100 == 0:
            log.info(
                "Progress %d/%d  ok=%d fail=%d skip=%d total_indexed=%d",
                i + 1, len(json_files), ok, fail, skip, vs.count(),
            )

    final_count = vs.count()
    log.info(
        "DONE  ok=%d fail=%d skip=%d total_in_collection=%d",
        ok, fail, skip, final_count,
    )

    # Write sentinel so VectorStore can detect HNSW reload corruption on startup
    from src.config import CHROMA_DIR
    sentinel = CHROMA_DIR / ".index_verified"
    sentinel.write_text(
        json.dumps({"count": final_count, "verified_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    log.info("Wrote HNSW sentinel: %s (count=%d)", sentinel, final_count)


if __name__ == "__main__":
    main()
