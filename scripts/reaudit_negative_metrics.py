"""
reaudit_negative_metrics.py — ре-аудит корпусу після виправлення F-EXT-1 (Фаза 2.2).

До 2026-06-11 всі шляхи екстракції метрик губили від'ємні значення:
  - regex-патерни без "-?" (NSE/KGE/R2/PBIAS/Kappa/R)
  - нормалізатори з жорстким [0,1]-клампом

Скрипт сканує results/conclusion-секції всіх paper.json sign-aware патернами
і рахує, скільки ВІД'ЄМНИХ значень існує в текстах — кожне з них було
гарантовано втрачене старим екстрактором. Результат:
  - data/analytics/negative_metrics_reaudit.csv (paper_id, metric, value, snippet)
  - консольний підсумок для звіту AUDIT_v1

Запуск: .venv/bin/python3 scripts/reaudit_negative_metrics.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PAPER_JSON_DIR = PROJECT_ROOT / "data" / "literature" / "paper_json"
OUT_CSV        = PROJECT_ROOT / "data" / "analytics" / "negative_metrics_reaudit.csv"

# Sign-aware патерни знакових метрик (узгоджені з entity_extractor Фази 2.1).
# Вимагаємо явний мінус — шукаємо саме ВТРАЧЕНІ значення.
NEG_PATTERNS: dict[str, re.Pattern] = {
    "NSE":   re.compile(r"\bnse\b\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)", re.I),
    "KGE":   re.compile(r"\bkge\b\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)", re.I),
    "R2":    re.compile(r"\br[\^²]?2\b\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)", re.I),
    "Kappa": re.compile(r"\bkappa\b\s*(?:coefficient)?\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)", re.I),
    "PBIAS": re.compile(r"\bpbias\b\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)\s*%?", re.I),
    "Bias":  re.compile(r"\bbias\b\s*(?:=|:|of|was|is)?\s*(-\d+(?:\.\d+)?)\s*%?", re.I),
}

# Хибні збіги типу "NSE values range from -1 to 1" (опис шкали, не результат)
SCALE_DESCRIPTION = re.compile(
    r"rang(?:e|ing)|between|from\s+-?\d|varies|theoretical|can\s+(?:range|vary|be\s+negative)",
    re.I,
)


def _snippet(text: str, start: int, end: int, ctx: int = 70) -> str:
    return " ".join(text[max(0, start - ctx): end + ctx].split())


def main() -> None:
    papers = sorted(PAPER_JSON_DIR.glob("*.paper.json"))
    print(f"Сканую {len(papers)} paper.json ...")

    rows: list[dict] = []
    mention_counter: Counter = Counter()   # скільки paper'ів взагалі згадують метрику
    err = 0

    for pj in papers:
        try:
            data = json.loads(pj.read_text())
        except Exception:
            err += 1
            continue
        sections = data.get("sections") or {}
        text = " ".join(
            sections.get(k) or "" for k in ("results", "discussion", "conclusion")
        )
        if not text.strip():
            continue

        for metric, pat in NEG_PATTERNS.items():
            if metric in ("NSE", "KGE") and re.search(rf"\b{metric.lower()}\b", text, re.I):
                mention_counter[metric] += 1
            for m in pat.finditer(text):
                snip = _snippet(text, m.start(), m.end())
                kind = "scale_description" if SCALE_DESCRIPTION.search(snip) else "lost_value"
                rows.append({
                    "paper_id": pj.name.replace(".tei.paper.json", ""),
                    "metric":   metric,
                    "value":    m.group(1),
                    "kind":     kind,
                    "snippet":  snip[:240],
                })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["paper_id", "metric", "value", "kind", "snippet"])
        w.writeheader()
        w.writerows(rows)

    lost = [r for r in rows if r["kind"] == "lost_value"]
    scale = [r for r in rows if r["kind"] == "scale_description"]
    print(f"\n{'='*64}")
    print(f"Paper.json просканованo : {len(papers)} (помилки читання: {err})")
    print(f"Згадки NSE у results+   : {mention_counter['NSE']} papers")
    print(f"Згадки KGE у results+   : {mention_counter['KGE']} papers")
    print(f"\nВІД'ЄМНІ значення знайдені (раніше гарантовано втрачались):")
    by_metric = Counter(r["metric"] for r in lost)
    for metric, n in by_metric.most_common():
        n_papers = len({r['paper_id'] for r in lost if r['metric'] == metric})
        print(f"  {metric:6} : {n:4d} значень у {n_papers} статтях")
    print(f"  {'РАЗОМ':6} : {len(lost)} значень у "
          f"{len({r['paper_id'] for r in lost})} статтях")
    print(f"  (+ {len(scale)} збігів класифіковано як опис шкали — не результати)")
    print(f"\nCSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
