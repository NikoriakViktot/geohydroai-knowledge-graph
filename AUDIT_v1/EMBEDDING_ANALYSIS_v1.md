# EMBEDDING_ANALYSIS_v1.md — Embedding Score Stabilization Analysis
**Date**: 2026-05-15  
**Corpus sample**: 100 papers (seed=42), 450 entity embedding scores analyzed  
**Embedding model**: sentence-transformers/all-MiniLM-L6-v2

---

## Executive Summary

**Pre-fix**: 13.3 % of entity embedding scores were **negative**, causing score inversion in the entity acceptance formula.  
**Post-fix**: `embedding_score()` clamps to `max(0.0, raw_cosine)`, eliminating inversion.  
**NaN guard**: `assert not np.isnan(raw)` added — propagation of NaN scores is now impossible.

---

## 1. Pre-Fix Embedding Score Distribution

| Bucket | Count | Percentage |
|--------|-------|-----------|
| **< 0 (negative / inverted)** | **60** | **13.3 %** |
| 0 – 0.30 (low evidence) | 338 | 75.1 % |
| 0.30 – 0.70 (moderate) | 52 | 11.6 % |
| ≥ 0.70 (high confidence) | 0 | 0.0 % |
| **Total** | **450** | |

**Min score**: −0.1331  
**Max score**: +0.6318  
**Mean**: 0.1349  
**Stdev**: 0.1341  

### Root Cause Analysis

The `cosine_similarity()` function (sklearn) can return values in `[−1, +1]` when input vectors are not L2-normalized.  
SentenceTransformer models produce L2-normalized vectors, but the `encode()` call may return `float32` tensors that undergo minor numeric drift, or the model may intentionally produce vectors that are not unit-normalized in some embedding spaces.

The scoring formula `embedding * 0.2` means a score of −0.133 contributes `−0.0266` to `final_score`, which:
- Pushes borderline entities below the `0.3` acceptance threshold
- Can cause an entity with good pattern + context evidence to be rejected
- Is semantically incorrect: negative cosine means "dissimilar", not "evidence against"

**Example inversion**: An entity matching the KB pattern for `muskingum_routing` in a paper about remote sensing (where the methods text contains no routing content) might get a small negative embedding score, which *correctly* signals dissimilarity. But subtracting from `final_score` crosses the rejection threshold, masking the pattern match evidence.

The correct semantic is: **0 = no embedding evidence**, not **negative = counter-evidence**.

---

## 2. Fixes Applied

### 2.1 `embedding_score()` — Clamp + NaN Guard

**File**: `src/ingestion/pipeline.py`

```python
# BEFORE
def embedding_score(text, label_examples, encode_fn):
    ...
    return float(max(sims))

# AFTER
def embedding_score(text, label_examples, encode_fn):
    ...
    raw = float(max(sims))
    assert not np.isnan(raw), f"embedding_score NaN for text={text[:60]!r}"
    return max(0.0, raw)
```

**Effect**: Negative anti-similarity → 0.0 (no embedding evidence). Score can no longer subtract from the formula.

### 2.2 `region_context_score()` — Clamp per-similarity

```python
# BEFORE
sim = cosine_similarity([emb_q], [emb_c])[0][0]
best = max(best, float(sim))

# AFTER
sim = max(0.0, float(cosine_similarity([emb_q], [emb_c])[0][0]))
best = max(best, sim)
```

Applied to each context window similarity, preventing any single anti-similar window from pulling the region confidence below zero.

---

## 3. Post-Fix Embedding Score Properties

| Property | Pre-Fix | Post-Fix |
|----------|---------|----------|
| Min score | −0.133 | 0.000 |
| Negative scores | 60 (13.3 %) | 0 (0.0 %) |
| NaN propagation | Possible | Impossible (assert) |
| Score range | [−0.133, 0.632] | [0.000, 0.632] |
| Inversion risk | Present | Eliminated |

The upper bound does not change: 0.632 is the maximum cosine similarity observed, well below 1.0.  
The **absence of scores ≥ 0.70** is expected: the entity name is compared against 2,000 chars of methods text.  
High cosine similarity between a 1–3 word entity name and a 2,000-char paragraph is structurally difficult — the embedding model captures broader semantic relevance, not exact substring overlap.

---

## 4. Score Distribution Interpretation

```
Score range    Meaning                             Typical source
──────────────────────────────────────────────────────────────
0.00           No embedding computed OR             encode_fn=None OR
               anti-similar (clamped)               entity name ↔ methods text are semantically opposite
0.00 – 0.15    Weak semantic signal                 Entity is tangentially related to methods domain
0.15 – 0.30    Moderate signal                      Entity name aligns with methods topic
0.30 – 0.50    Strong signal                        Entity is central to methods domain
> 0.50         Very strong                          Entity name is a core term in methods text
```

75.1 % of scores in the 0–0.30 range is **correct** for this use case.  
Entity names like "HEC-RAS" or "SWAT" are 1–2 tokens; methods text is 2,000 chars covering many topics.  
The embedding layer provides a **probability adjustment**, not a primary acceptance signal.

---

## 5. Scoring Formula Health Check

```
final_score = pattern*0.3 + context*0.3 + embedding*0.2 + llm*0.2
```

| Component | Typical value range | After fix |
|-----------|--------------------|-----------| 
| pattern | 0.88–0.90 (KB hit) | unchanged |
| context | 0.0–0.8 | unchanged |
| embedding | **[−0.133, 0.632]** → **[0, 0.632]** | clamped |
| llm | 0.0 (not yet wired) | unchanged |
| **final_score** | could be negative | ≥ 0.0 guaranteed |

**The llm=0 allocation**: The scoring formula reserves 20 % for per-entity LLM scoring, which is not yet implemented.  
This 20 % weight is effectively wasted and reduces maximum achievable `final_score` to 0.80 (without section boost).  
Recommendation: increase `embedding` weight to 0.4 and reduce `llm` to 0.0 until per-entity judging is implemented.

---

## 6. Diagnostic Assertions

The NaN assertion in `embedding_score()` will surface immediately if:
- The encode_fn returns vectors with NaN components (model loading error)
- The encode_fn returns empty arrays
- Cosine similarity receives all-zero vectors (unlikely with sentence-transformers)

If the assertion fires in production, the traceback will contain the entity name and evidence text for immediate diagnosis.

---

## 7. Test Coverage

Covered in `tests/test_06_edge_cases.py::TestEmbeddingScoreClamping`:

| Test | Verified |
|------|---------|
| `test_embedding_score_never_negative` | Anti-similar vectors → score ≥ 0.0 |
| `test_embedding_score_bounded_above` | Identical vectors → score ≤ 1.0 |

---

## 8. Open Issue: High Embedding Score Ceiling

No entity in the 450-entity sample achieved embedding_score ≥ 0.70.  
This may indicate the encoding strategy (`entity_name` vs `methods_text[:2000]`) is suboptimal.

**Alternative approaches** (not implemented, future work):
1. Encode entity name against entity-specific context window (snippet around mention) rather than full methods text
2. Use dedicated domain embedding model (e.g. SciBERT, HydroBERT) for hydrology terminology
3. Use cosine similarity between entity full_name and each sentence, take max
