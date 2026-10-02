# Admin — rebuilds, integrity, manifests

**Scope** `admin` for every endpoint. AI agents are never issued this scope.

---

## `POST /admin/manifest/freeze`
- **Status**: planned (phase 1) · S
- **Purpose**: freeze the current corpus manifest so that an article's retrieval run stays reproducible.
- **Request**: `{"label": "floodstate-eo:paper3 submission", "project_id": string?}`.
- **Response 200**: `{"corpus_manifest_id", "frozen": true, "pinned": [{"path", "sha256"}]}`.
- **Behaviour**:
  - Records the identity snapshot (`ops.run`), the collection, the model revision and the hash of each pinned input.
  - Copies pinned files into `data/frozen/<manifest_id>/` so that a later rebuild cannot overwrite them. This was the lesson of 2026-09-23, when a pinned `references.parquet` was lost.

## `POST /admin/rebuild/graph`
- **Status**: planned (phase 2) · **J**
- **Request**: `{"wipe": false, "identity": "postgres"}`.
- **Behaviour**: takes an offline dump first when `wipe = true`, then runs `build_graph` and `table_kg_loader`. Records `core.projection_state`.

## `POST /admin/rebuild/vectors`
- **Status**: planned (phase 2) · **J**
- **Request**: `{"collection": "flood_papers_768d_v3", "switch_on_success": false}`.
- **Behaviour**:
  - Always builds into a **new** collection.
  - Verifies the result before any switch: count, an abstract for every paper whose TEI has one, and 20 control queries.
  - The old collection stays for rollback.

## `POST /admin/rebuild/analytics`
- **Status**: planned (phase 2) · **J**
- **Behaviour**: rebuilds `data/parquet` and `data/analytics`. Pinned inputs are frozen first (see `manifest/freeze`).

## `POST /admin/integrity/check`
- **Status**: planned (phase 2; available today as the CLI `python -m src.etl.identity`) · **J**
- **Purpose**: rescan the file store, reclassify identity and report changes.
- **Reported changes**:
  - new duplicates;
  - title/DOI mismatches;
  - truncated `paper.json`;
  - PDF copies;
  - papers that vanished from disk.
- **Result**: the `ops.run` counts plus a diff against the previous run.

## Raw Cypher
See `POST /graph/cypher` in [graph.md](graph.md#post-graphcypher).
