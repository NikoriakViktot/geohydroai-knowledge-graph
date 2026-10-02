"""Single source for API documentation: the Markdown files in docs/api/.

The FastAPI app builds every route's OpenAPI summary/description, its
``x-agent-rules`` and ``x-status`` extensions, and the documentation endpoints
(/v1/agent-rules, /v1/docs/…) from these files, so the reference a human reads,
the Swagger page and what an agent gets by request are the same text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs" / "api"

_HEADING = re.compile(r"^## `(GET|POST|PUT|PATCH|DELETE) ([^`]+)`\s*$", re.M)
_RULE_ID = re.compile(r"\bR-[A-Z]{2,4}-\d+\b")
_SCOPES = ("read", "llm", "write", "admin")


@dataclass(frozen=True)
class EndpointDoc:
    method: str
    path: str                      # as documented, relative to /v1, query part stripped
    variant: str | None            # e.g. "mode=llm" for "POST /metrics/extract?mode=llm"
    group: str                     # file stem: system, papers, search …
    summary: str
    markdown: str                  # the full section, verbatim
    status: str                    # "implemented" | "planned"
    status_text: str
    scope: str | None              # read | llm | write | admin | None (public)
    mode: str                      # "sync" | "job" | "sse" | "sync_or_job"
    rules: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentRule:
    id: str
    category: str                  # ACC, SCI, DATA, LLM, SEC
    text: str
    why: str | None
    level: str                     # MUST | MUST NOT | SHOULD | SHOULD NOT | MIXED


def _clean_md(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`]", "", text)).strip()


def _status(section: str) -> tuple[str, str]:
    m = re.search(r"\*\*Status\*\*:\s*([^·\n]+)", section)
    text = m.group(1).strip() if m else "planned"
    return ("implemented" if text.lower().startswith("implemented") else "planned"), text


def _scope(section: str, group_default: str | None) -> str | None:
    m = re.search(r"\*\*Scope\*\*:?\s*(?:`(\w+)`|none)", section)
    if m:
        return m.group(1) if m.group(1) in _SCOPES else None
    m = re.search(r"·\s*`(read|llm|write|admin)`\s*·", section)
    if m:
        return m.group(1)
    return group_default


def _mode(section: str, group_default: str) -> str:
    head = section.split("\n**", 1)[0][:600]
    if "SSE" in head:
        return "sse"
    has_s, has_j = bool(re.search(r"(·|\*\*Mode\*\*:?)\s*S\b", head)), bool(re.search(r"\*\*J\*\*", head))
    if has_s and has_j:
        return "sync_or_job"
    if has_j:
        return "job"
    if has_s:
        return "sync"
    return group_default


def _summary(section: str, method: str, path: str) -> str:
    m = re.search(r"\*\*Purpose\*\*:\s*(.+)", section)
    if m:
        text = _clean_md(m.group(1)).replace("e.g.", "e․g․").replace("i.e.", "i․e․")
        first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        return first.replace("․", ".")[:120]
    return f"{method} {path}"


@lru_cache(maxsize=1)
def load_group_rules(docs_dir: Path = DOCS_DIR) -> dict[str, tuple[str, ...]]:
    """AGENT_RULES.md §9 'Rules by endpoint group': group → rule ids ('all' = every endpoint)."""
    text = (docs_dir / "AGENT_RULES.md").read_text(encoding="utf-8")
    m = re.search(r"## 9\. Rules by endpoint group(.*?)\n## ", text, re.S)
    out: dict[str, tuple[str, ...]] = {}
    for line in (m.group(1) if m else "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and _RULE_ID.search(cells[1]):
            out[cells[0]] = tuple(_RULE_ID.findall(cells[1]))
    return out


def _ordered_rules(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Union of rule ids, in the order they appear in AGENT_RULES.md."""
    wanted = {r for g in groups for r in g}
    order = [r.id for r in load_agent_rules()]
    known = [r for r in order if r in wanted]
    return tuple(known + sorted(wanted - set(order)))


def _group_defaults(text: str) -> tuple[str | None, str]:
    """Defaults declared at the top of a group file, e.g. 'All endpoints: **Scope** `read` · **Mode** S'."""
    head = text.split("\n---", 1)[0]
    scope = None
    m = re.search(r"\*\*Scope\*\*\s*`(\w+)`", head)
    if m and m.group(1) in _SCOPES:
        scope = m.group(1)
    mode = "job" if re.search(r"\*\*Mode\*\*\s*\*\*J\*\*", head) else "sync"
    return scope, mode


@lru_cache(maxsize=1)
def load_endpoint_docs(docs_dir: Path = DOCS_DIR) -> dict[tuple[str, str], list[EndpointDoc]]:
    """(METHOD, path) → documented variants (most endpoints have exactly one)."""
    out: dict[tuple[str, str], list[EndpointDoc]] = {}
    group_rules = load_group_rules(docs_dir)
    for f in sorted((docs_dir / "endpoints").glob("*.md")):
        text = f.read_text(encoding="utf-8")
        g_scope, g_mode = _group_defaults(text)
        matches = list(_HEADING.finditer(text))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[m.start():end].rstrip().removesuffix("---").rstrip()
            method, raw_path = m.group(1), m.group(2).strip()
            path, _, variant = raw_path.partition("?")
            status, status_text = _status(section)
            doc = EndpointDoc(
                method=method, path=path, variant=variant or None, group=f.stem,
                summary=_summary(section, method, path), markdown=section,
                status=status, status_text=status_text,
                scope=_scope(section, g_scope), mode=_mode(section, g_mode),
                rules=_ordered_rules(group_rules.get("all", ()), group_rules.get(f.stem, ()),
                                     tuple(_RULE_ID.findall(section))),
            )
            out.setdefault((method, path), []).append(doc)
    return out


_MODALS = (("MUST", r"\bMUST\b(?!\s+NOT)"), ("MUST NOT", r"\bMUST NOT\b"),
            ("SHOULD", r"\bSHOULD\b(?!\s+NOT)"), ("SHOULD NOT", r"\bSHOULD NOT\b"))


def _level(text: str) -> str:
    """All RFC 2119 modals the rule uses, e.g. 'MUST + MUST NOT'."""
    found = [name for name, pattern in _MODALS if re.search(pattern, text)]
    return " + ".join(found) if found else "MUST"


@lru_cache(maxsize=1)
def load_agent_rules(docs_dir: Path = DOCS_DIR) -> tuple[AgentRule, ...]:
    """Rules from the tables of AGENT_RULES.md (| R-XXX-n | rule | why? |)."""
    rules: list[AgentRule] = []
    for line in (docs_dir / "AGENT_RULES.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if not cells:
            continue
        rid = _RULE_ID.search(cells[0])
        if not rid or _RULE_ID.sub("", _clean_md(cells[0])).strip():
            continue  # first cell must be just the id (plain or bold)
        text = _clean_md(cells[1]) if len(cells) > 1 else ""
        why = _clean_md(cells[2]) if len(cells) > 2 and cells[2] else None
        level = _level(cells[1])
        rules.append(AgentRule(id=rid.group(0), category=rid.group(0).split("-")[1],
                               text=text, why=why, level=level))
    return tuple(rules)


def read_page(name: str, docs_dir: Path = DOCS_DIR) -> str:
    """Markdown of a documentation page by name: 'README', 'AGENT_RULES', 'endpoints/search' …"""
    if not re.fullmatch(r"(endpoints/)?[A-Za-z_]+", name):
        raise FileNotFoundError(name)
    path = docs_dir / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def page_names(docs_dir: Path = DOCS_DIR) -> list[str]:
    names = [p.stem for p in sorted(docs_dir.glob("*.md"))]
    names += [f"endpoints/{p.stem}" for p in sorted((docs_dir / "endpoints").glob("*.md"))]
    return names


def top_rules_markdown(docs_dir: Path = DOCS_DIR) -> str:
    """Section 0 of AGENT_RULES.md ('The five rules that matter most')."""
    text = (docs_dir / "AGENT_RULES.md").read_text(encoding="utf-8")
    m = re.search(r"## 0\..*?\n(.*?)\n---", text, re.S)
    return m.group(1).strip() if m else ""
