"""
kb_keywords.py — міст між Knowledge Base та keyword-банками regex_extractor.

Контекст (Фаза 2.6, F-EXT-4): статичні списки супутників/методів у
regex_extractor і KB (1,118 сутностей) — два джерела істини, що дрейфують.

ЧЕСНЕ ОБМЕЖЕННЯ (виявлено 2026-06-11): поточна KB НЕ МОЖЕ замінити статичні
банки супутників, бо:
  1. KB має лише generic 'SENTINEL' — без місій Sentinel-1/-2, критичних для
     повеней (SAR проникає крізь хмари, optical — ні);
  2. KB не кодує SAR/optical класифікацію сенсорів.

Тому модель інтеграції:
  - статичні банки лишаються curated-ядром (точність, sensor-типізація);
  - KB-патерни ДОПОВНЮЮТЬ детекцію сутностями, яких у банках немає
    (152 сенсори з готовими \\b-патернами);
  - audit_static_banks_vs_kb() механічно показує розрив — план збагачення KB.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def kb_satellite_patterns() -> tuple[tuple[str, re.Pattern], ...]:
    """
    (acronym, compiled pattern) для всіх satellite/sensor-сутностей KB.

    Патерни беруться з KB as-is (вони вже з \\b-межами — без substring-пасток
    на кшталт 'sar' усередині 'necessary'). Кешовано на процес.
    """
    from src.ingestion.knowledge.knowledge_loader import get_global_kb
    out: list[tuple[str, re.Pattern]] = []
    for rec in get_global_kb().get_satellites():
        if not rec.pattern:
            continue
        try:
            out.append((rec.acronym, re.compile(rec.pattern, re.IGNORECASE)))
        except re.error as exc:
            log.warning("KB pattern для %s не компілюється: %s", rec.acronym, exc)
    return tuple(out)


# Технологічні/родові записи KB — не місії; як "знайдений супутник" це шум
# ("SAR" — технологія; "SENTINEL" — серія, не Sentinel-1 vs -2).
_GENERIC_TECH_ACRONYMS = frozenset({
    "SAR", "RADAR", "LIDAR", "ALTIMETER", "OPTICAL",
    "SENTINEL", "LANDSAT", "GNSS", "GPS", "UAV",
})


def detect_kb_satellites(text: str, exclude: set[str] | None = None) -> list[str]:
    """
    Сенсори/супутники з KB, знайдені в тексті — ДОПОВНЕННЯ до статичних банків.

    exclude — нормалізовані (lower) імена, вже знайдені статичними списками,
    щоб не дублювати. Родові технологічні записи (SAR/RADAR/SENTINEL…)
    пропускаються — вони не є конкретними місіями.
    """
    excl = {e.lower() for e in (exclude or set())}
    found: list[str] = []
    for acronym, pat in kb_satellite_patterns():
        if acronym.upper() in _GENERIC_TECH_ACRONYMS:
            continue
        if any(acronym.lower() in e or e.startswith(acronym.lower()) for e in excl):
            continue
        if pat.search(text):
            found.append(acronym)
    return found


def audit_static_banks_vs_kb() -> dict[str, list[str]]:
    """
    Розбіжності між статичними банками regex_extractor і KB.

    Returns:
        {"static_not_in_kb": [...],   ← кандидати на додавання в KB
         "kb_not_in_static": [...]}   ← сенсори, які бачить лише KB-аугментація
    """
    from src.extraction.regex_extractor import _SAR_SATELLITES, _OPT_SATELLITES
    from src.ingestion.knowledge.knowledge_loader import get_global_kb

    kb = get_global_kb()
    static_names = set(_SAR_SATELLITES) | set(_OPT_SATELLITES)

    static_not_in_kb = sorted(
        name for name in static_names if kb.resolve(name) is None
    )
    kb_acronyms = {a.lower() for a, _ in kb_satellite_patterns()}
    static_lower = {s.lower() for s in static_names}
    kb_not_in_static = sorted(
        a for a, _ in kb_satellite_patterns()
        if a.lower() not in static_lower
        and not any(a.lower() in s for s in static_lower)
    )
    return {
        "static_not_in_kb": static_not_in_kb,
        "kb_not_in_static": kb_not_in_static,
    }
