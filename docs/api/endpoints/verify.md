# Verify — the human verification layer

A person's judgement of parsed or extracted data: a Nougat region, a formula, a table, a metric fact, an entity edge, a thesis evidence row, a paper's metadata. These are the only **human** labels in the system.

All endpoints: **Scope** `read` · **Mode** S, except `POST /verifications` (`verify`).

**Backing data**: `verify.human_check` in Postgres (migration 0006). The table is append-only: a database trigger refuses UPDATE and DELETE. `verify.current` is the latest judgement per `(target_kind, target_id, field)`.

**The `verify` scope belongs to people.** A key gets it only on a person's decision (`python -m src.api.keys create --consumer <person> --scopes read,verify`). Every row carries `labeler_kind = "human"` and the key's consumer as `labeler` (R-ACC-7).

---

## `POST /verifications`
- **Status**: implemented (2026-10-03) · **Scope** `verify` · S
- **Purpose**: record one or more judgements.

**Request**: `{"checks": [{"target_kind": "formula", "target_id": "10.1029_jc095ic08p13129_p7_for_004", "field": "latex", "paper_id": "10.1029_jc095ic08p13129", "verdict": "incorrect", "problem": "formula_broken", "corrected": {"latex": "Q = …"}, "note": "subscript lost", "shown": {"latex": "…"}, "supersedes": null}]}` (1–200 checks).

- `target_kind` ∈ `paper | section | region | formula | table | figure | metric_fact | entity_edge | claim_evidence | thesis | reference | location`.
- `verdict` ∈ `correct | incorrect | partial | unsure`.
- `problem` ∈ `text_missing | text_garbled | table_broken | formula_broken | crop_wrong` (parser problems) and `value_wrong | unit_wrong | entity_wrong | location_wrong | metadata_wrong | evidence_not_supporting | other`.
- `shown` keeps what the person saw, so a later re-parse can be compared with the judgement.

**Response 201**: `{"items": [Check + {"check_id", "labeler_kind": "human", "labeler", "created_at"}], "count", "provenance"}`

**Errors**: `403 FORBIDDEN_SCOPE` without `verify`; `422` for an unknown kind, verdict or problem; `503`.

**Agent notes**:
- You **MUST NOT** call this endpoint. Agents never hold the `verify` scope, and a model judgement written as human would break R-SCI-6 (R-ACC-7).

---

## `GET /verifications`
- **Status**: implemented (2026-10-03) · `read` · S
- **Purpose**: the judgements for given targets, a paper or a project.

**Query**: `target_kind`, `target_id` (repeatable), `paper_id`, `project_id`, `verdict`, `problem`, `history` (false: latest judgement per target and field), `limit` ≤ 5000.

**Response 200**: `{"items": [Check], "count", "provenance"}`

**Agent notes**:
- A `correct` judgement is the only human verification in the corpus: cite it as "verified by <labeler>". Anything else stays model-assessed (R-SCI-6).

---

## `GET /verifications/summary`
- **Status**: implemented (2026-10-03) · `read` · S
- **Purpose**: where the parsing fails — counts of the current judgements and the papers with the most parser problems.

**Query**: `project_id` (optional).

**Response 200**: `{"total", "by_verdict": {"correct": 120, "incorrect": 14}, "by_problem": {"table_broken": 6}, "by_target_kind": {"formula": {"correct": 40, "incorrect": 5}}, "parse_problem_papers": [{"paper_id", "problems": {"table_broken": 3}, "total": 3}], "provenance"}`

**Agent notes**:
- These counts describe what a person has looked at, not the corpus. Do not extrapolate an error rate without the sampling design (R-SCI-7).
