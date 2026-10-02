# Jobs — asynchronous work

**Backing**: `ops.job`, `ops.job_step`, `ops.job_event` in Postgres. One worker process claims jobs (`SELECT … FOR UPDATE SKIP LOCKED`) and owns every write to Neo4j, Chroma and the file store.

**Resource classes** serialise scarce resources across all consumers:
- `gpu` = 1
- `grobid` = 1
- `network` = 4
- `llm` — bounded by the quota

---

## `GET /jobs/{job_id}`
- **Status**: planned (phase 2, WP 2.3) · **Scope** `read` · S
- **Response 200**: `Job` ([SCHEMAS](../SCHEMAS.md#job)).
- **Errors**: `404 NOT_FOUND`. A job of another consumer is visible only to `admin`.
- **Polling**: at most every 5 s; prefer the event stream.

## `GET /jobs`
- **Status**: planned (phase 2) · **Scope** `read` · S
- **Query**: `type`, `status`, `project_id`, `owner` (`admin` only), `created_after`, `limit`, `cursor`.
- **Response 200**: `{"items": [Job (without steps)], "next_cursor", "provenance"}`.

## `GET /jobs/{job_id}/events`
- **Status**: planned (phase 2) · **Scope** `read` · SSE
- **Stream**: `text/event-stream`; each event is `data: JobEvent` ([SCHEMAS](../SCHEMAS.md#job)). Reconnect with `Last-Event-ID: <seq>` to resume without gaps. The stream closes after the terminal status.

```text
data: {"job_id":"01J9…","seq":14,"at":"2026-10-02T09:12:03Z","step":"grobid","status":"succeeded","progress":0.21,"message":"10.1029_2025gl120832: 19 s"}
```

## `DELETE /jobs/{job_id}`
- **Status**: planned (phase 2) · **Scope** `write` · S
- **Behaviour**:
  - Cancellation is cooperative: the current step finishes. A step writing to Neo4j or Chroma always completes, so stores never hold half a paper.
  - Status becomes `cancelled`; finished artifacts remain.
- **Response 202**: `Job`.

## `POST /jobs/{job_id}/retry`
- **Status**: planned (phase 2) · **Scope** `write` · S
- **Request**: `{"from_step": string?}` (default: the first failed step).
- **Behaviour**:
  - Steps whose inputs are unchanged are `skipped_up_to_date`.
  - Deterministic failures (corrupt PDF, invalid TEI) fail again with the same error; fix the input instead.
- **Response 202**: `Job`.

---

## Job lifecycle

```text
queued ─► running ─► succeeded
                  ├► partial    (some papers/steps failed or a budget ran out)
                  ├► failed     (retries exhausted on a required step)
                  └► cancelled
```

**Retries per step**:
- network steps (OpenAlex, Crossref, download): 3 attempts with exponential backoff;
- GPU/LLM steps: 1 retry;
- deterministic failures: no retry.
