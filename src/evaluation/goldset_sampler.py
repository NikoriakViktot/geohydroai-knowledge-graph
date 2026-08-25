"""
goldset_sampler.py — стратифікована вибірка для gold set (Фаза 2.4).

Передумова оцінки judge та semantic-matcher (F-EXT-3, F-NORM): без розміченого
еталона precision/recall LLM-судді невідомі, а defensive-ремонт приховує
деградацію. Цей модуль будує ВІДТВОРЮВАНУ (seed) стратифіковану вибірку і
шаблони для ручної розмітки; самі лейбли ставить людина.

Виходи (data/evaluation/):
  goldset_judge_template.csv    — 100 статей, стратифіковано за study_type:
                                  pred_* заповнені з paper.json, true_* порожні
  goldset_linking_template.csv  — 300 пар mention→canonical_id (methods/
                                  satellites/dems), стратифіковано за match-
                                  джерелом; true_canonical_id порожній

Запуск:
  python -m src.evaluation.goldset_sampler [--n-papers 100] [--n-mentions 300]
                                           [--seed 42]

Після розмітки: evaluator.py рахує field-level precision/recall
(goldset_judge), а linking-пари йдуть і в eval, і в тренувальний датасет (T4).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPER_JSON_DIR = _PROJECT_ROOT / "data" / "literature" / "paper_json"
OUT_DIR        = _PROJECT_ROOT / "data" / "evaluation"

JUDGE_COLUMNS = [
    "paper_id", "title", "doi", "year",
    # predictions (auto)
    "pred_study_type", "pred_study_type_conf", "pred_study_type_source",
    "pred_task", "pred_task_conf",
    "pred_primary_country", "pred_rivers",
    "judge_used", "judge_verdict_repairs",
    # ground truth (розмітка людиною)
    "true_study_type",      # case_study|regional|multi_site|global_algorithmic|review|unknown
    "true_task",            # closed enum із judge-промпта
    "true_primary_country", # країна ДОСЛІДЖЕННЯ (не афіліації!)
    "true_rivers",          # лише фактичний study watershed, через кому
    "annotator_notes",
    # context для анотатора
    "abstract_snippet",
]

LINKING_COLUMNS = [
    "paper_id", "entity_type", "mention", "context_snippet",
    "pred_canonical_id", "pred_confidence", "pred_source",
    "true_canonical_id",    # розмітка: канонічний ID з KB або UNKNOWN/WRONG
    "annotator_notes",
]


def _load_papers() -> list[tuple[Path, dict]]:
    out = []
    for pj in sorted(PAPER_JSON_DIR.glob("*.paper.json")):
        try:
            out.append((pj, json.loads(pj.read_text())))
        except Exception:
            continue
    return out


def _study_type(paper: dict) -> str:
    st = (((paper.get("entities") or {}).get("geo") or {})
          .get("study_type") or {})
    return st.get("label") or "unknown"


def sample_judge_goldset(papers, n: int, rng: random.Random) -> list[dict]:
    """Стратифікація за study_type пропорційно корпусу (мін. 5 на страту)."""
    strata: dict[str, list] = defaultdict(list)
    for pj, paper in papers:
        strata[_study_type(paper)].append((pj, paper))

    total = sum(len(v) for v in strata.values())
    rows: list[dict] = []
    for label, items in sorted(strata.items()):
        k = max(5, round(n * len(items) / total)) if len(items) >= 5 else len(items)
        k = min(k, len(items))
        for pj, paper in rng.sample(items, k):
            meta = paper.get("metadata") or {}
            ents = paper.get("entities") or {}
            geo  = (ents.get("geo") or {})
            st   = geo.get("study_type") or {}
            task = ents.get("task") or {}
            sg   = geo.get("study_geo") or {}
            prov = paper.get("provenance") or {}
            abstract = (paper.get("sections") or {}).get("abstract") or ""
            rows.append({
                "paper_id": pj.name.replace(".tei.paper.json", ""),
                "title":    meta.get("title", ""),
                "doi":      meta.get("doi", ""),
                "year":     meta.get("year", ""),
                "pred_study_type":        st.get("label", ""),
                "pred_study_type_conf":   st.get("confidence", ""),
                "pred_study_type_source": st.get("source", ""),
                "pred_task":              task.get("label", ""),
                "pred_task_conf":         task.get("confidence", ""),
                "pred_primary_country":   sg.get("primary_country", ""),
                "pred_rivers":            ", ".join(
                    r.get("name", "") for r in (sg.get("rivers") or [])[:5]),
                "judge_used":             prov.get("judge_used", False),
                "judge_verdict_repairs":  prov.get("judge_verdict_repairs", ""),
                "true_study_type": "", "true_task": "",
                "true_primary_country": "", "true_rivers": "",
                "annotator_notes": "",
                "abstract_snippet": " ".join(abstract.split())[:500],
            })
    rng.shuffle(rows)
    return rows[: n + 10]   # невеликий запас понад n


def sample_linking_pairs(papers, n: int, rng: random.Random) -> list[dict]:
    """Пари mention→canonical_id з methods/satellites/dems, стратифіковано."""
    pools: dict[str, list[dict]] = defaultdict(list)
    for pj, paper in papers:
        ents = paper.get("entities") or {}
        pid  = pj.name.replace(".tei.paper.json", "")
        for etype in ("methods", "satellites", "dems"):
            for e in ents.get(etype) or []:
                name = (e.get("name") or "").strip()
                if not name:
                    continue
                ev = e.get("evidence")
                if isinstance(ev, dict):
                    snippet = ev.get("snippet") or ""
                else:                       # evidence буває рядком у legacy paper.json
                    snippet = str(ev or "")
                pools[etype].append({
                    "paper_id":        pid,
                    "entity_type":     etype,
                    "mention":         name,
                    "context_snippet": (snippet or str(e.get("used_for") or ""))[:240],
                    "pred_canonical_id": e.get("canonical_id", ""),
                    "pred_confidence":   e.get("confidence", ""),
                    "pred_source":       e.get("source", ""),
                    "true_canonical_id": "",
                    "annotator_notes":   "",
                })
    rows: list[dict] = []
    per_type = max(1, n // max(len(pools), 1))
    for etype, pool in sorted(pools.items()):
        # дедуп за (mention, pred_canonical_id) — різноманіття замість повторів
        seen, uniq = set(), []
        for r in pool:
            key = (r["mention"].lower(), r["pred_canonical_id"])
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        rows.extend(rng.sample(uniq, min(per_type, len(uniq))))
    rng.shuffle(rows)
    return rows[:n]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-papers",   type=int, default=100)
    ap.add_argument("--n-mentions", type=int, default=300)
    ap.add_argument("--seed",       type=int, default=42)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    papers = _load_papers()
    log.info("Завантажено %d paper.json", len(papers))

    judge_rows = sample_judge_goldset(papers, args.n_papers, rng)
    p1 = OUT_DIR / "goldset_judge_template.csv"
    with open(p1, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JUDGE_COLUMNS)
        w.writeheader(); w.writerows(judge_rows)
    log.info("Judge goldset: %d статей → %s", len(judge_rows), p1)

    link_rows = sample_linking_pairs(papers, args.n_mentions, rng)
    p2 = OUT_DIR / "goldset_linking_template.csv"
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINKING_COLUMNS)
        w.writeheader(); w.writerows(link_rows)
    log.info("Linking goldset: %d пар → %s", len(link_rows), p2)

    strata = defaultdict(int)
    for r in judge_rows:
        strata[r["pred_study_type"] or "unknown"] += 1
    log.info("Стратифікація judge-вибірки: %s", dict(strata))


if __name__ == "__main__":
    main()
