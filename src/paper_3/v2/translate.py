"""Ukrainian version of the assembled manuscript, with every number locked.

The only prose a model writes in this pipeline. It is allowed to because the
things that could be fabricated are taken away from it first: numbers with
their units, DOIs, claim ids, cite keys, code spans, URLs and whole ``[PENDING]``
markers are replaced by opaque tokens (``⟦12⟧``) before the paragraph is sent,
and put back afterwards. A paragraph whose returned tokens are not exactly the
sent set — one missing, one invented, one reordered into a different count — is
rejected and retried once; a second failure leaves the English paragraph in
place under a marker, never a guess.

Glossary terms (``translate_glossary.yaml``) are supplied in the prompt so that
EVRF2019, БС-77, НПГ/УНС, Theil–Sen and the like are rendered consistently.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Callable

import yaml

from src.paper_3._utils import OUT_DIR, resolve_gemini_key
from src.paper_3.v2 import markers
from src.paper_3.v2.assemble import MANUSCRIPT_EN, manuscript_name

logger = logging.getLogger(__name__)

MANUSCRIPT_UK = "v2_manuscript_uk.md"
GLOSSARY_PATH = Path(__file__).with_name("translate_glossary.yaml")
CALL_DELAY_SECONDS = 4.5
_GENERATION_CONFIG = {"temperature": 0.0, "max_output_tokens": 2000}

#: What gets locked, in priority order (longer/more specific first).
_LOCK_PATTERNS = [
    r"`[^`\n]+`",                                             # code spans
    r"\[[^\]\n]*\]\(https?://[^)\s]+\)",                        # markdown links
    r"https?://\S+",                                            # bare URLs
    r"\b10\.\d{4,9}/[^\s,;)\]]+",                                # DOIs
    r"\b[A-Z]{1,2}-?\d{1,3}(?:\.\d{1,2}[a-z]?)?\b",              # claim ids V10.1, C-14, S1.1a, T25
    r"\([A-Z][\w’'\-]+(?: (?:&|et al\.) ?[A-Z]?[\w’'\-]*)?, \d{4}[a-z]?\)",  # (Author et al., 2024)
    r"[−+-]?\d[\d\s.,]*\s?(?:%|‰|km²|km2|km|m³/s|m|cm/km|cm|mm|pp|d|h|°|×)?"  # numbers with units
    r"(?:\s?\[[^\]]*\])?",
]
_LOCK_RE = re.compile("|".join(f"(?:{p})" for p in _LOCK_PATTERNS))
_TOKEN_RE = re.compile(r"⟦(\d+)⟧")


def lock(text: str) -> tuple[str, list[str]]:
    """Replace every locked span by ⟦i⟧; return the masked text and the spans."""
    spans: list[str] = []

    def _sub(m: re.Match) -> str:
        s = m.group(0)
        if not s.strip() or s.strip() in {"-", "+", "−"}:
            return s
        spans.append(s)
        return f"⟦{len(spans) - 1}⟧"
    return _LOCK_RE.sub(_sub, text), spans


def unlock(masked: str, spans: list[str]) -> str:
    return _TOKEN_RE.sub(lambda m: spans[int(m.group(1))], masked)


def verify_locked_tokens(sent: str, received: str) -> bool:
    """Every token exactly once, none invented — order may change with syntax."""
    want = sorted(_TOKEN_RE.findall(sent))
    got = sorted(_TOKEN_RE.findall(received))
    return want == got


def load_glossary(path: Path = GLOSSARY_PATH) -> dict[str, str]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_prompt(masked: str, glossary: dict[str, str]) -> str:
    terms = "\n".join(f"- {en} → {uk}" for en, uk in glossary.items())
    return (
        "Translate the following scientific paragraph from British English into Ukrainian "
        "for a peer-reviewed hydrology/geodesy journal. Rules:\n"
        "1. Tokens like ⟦7⟧ are locked placeholders (numbers, DOIs, identifiers). Copy every "
        "token exactly, once each, and add none.\n"
        "2. Keep Markdown formatting (headings, emphasis, tables, block quotes) unchanged.\n"
        "3. Use the glossary terms exactly as given.\n"
        "4. Do not add, remove or soften any statement. Do not add commentary.\n"
        "5. Return only the translated paragraph.\n\n"
        f"GLOSSARY:\n{terms}\n\nPARAGRAPH:\n{masked}"
    )


def call_gemini(prompt: str, api_key: str) -> str | None:
    from src.dashboard_dash.ai_gateway import call_ai_with_gateway
    res = call_ai_with_gateway(prompt, _GENERATION_CONFIG, api_key)
    return res.get("text") if res.get("ok") else None


def translate_paragraph(par: str, glossary: dict[str, str], call: Callable[[str], str | None],
                        attempts: int = 2) -> tuple[str, bool]:
    """(translated_or_original, ok). Headings/markers/tables are never sent."""
    stripped = par.strip()
    if not stripped:
        return par, True
    if stripped.startswith(("#", ">", "|")) or markers.find_marker_ids(stripped):
        return par, True                      # structure, markers and tables stay verbatim
    masked, spans = lock(stripped)
    prompt = build_prompt(masked, glossary)
    for _ in range(attempts):
        out = call(prompt)
        if out and verify_locked_tokens(masked, out.strip()):
            return unlock(out.strip(), spans), True
    return par, False


def translate_text(text: str, glossary: dict[str, str], call: Callable[[str], str | None],
                   delay: float = CALL_DELAY_SECONDS) -> tuple[str, list[int]]:
    out: list[str] = []
    failed: list[int] = []
    for i, par in enumerate(re.split(r"\n\s*\n", text)):
        translated, ok = translate_paragraph(par, glossary, call)
        if not ok:
            failed.append(i)
            translated = (markers.render(markers.Marker(
                id=f"OPEN{90 + len(failed) % 10}",
                title="translation rejected: locked tokens did not round-trip; English kept",
                fields={"section": f"paragraph {i}", "status": "blocked", "blocks_submission": "yes"}))
                + "\n\n" + par)
        out.append(translated)
        if translated is not par and delay:
            time.sleep(delay)
    return "\n\n".join(out) + "\n", failed


def run(out_dir: Path = OUT_DIR, call: Callable[[str], str | None] | None = None,
        paper: int = 1) -> Path:
    src_name, uk_name = manuscript_name(paper, "en"), manuscript_name(paper, "uk")
    src = (out_dir / src_name).read_text(encoding="utf-8")
    if call is None:
        key = resolve_gemini_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY not set")
        call = lambda p: call_gemini(p, key)  # noqa: E731
    text, failed = translate_text(src, load_glossary(), call)
    target = out_dir / uk_name
    target.write_text(text, encoding="utf-8")
    logger.info("translated %s → %s (%d paragraph(s) rejected)", src_name, uk_name, len(failed))
    return target
