"""
test_import_invariants.py — механічне забезпечення архітектурних інваріантів.

Інваріанти з CLAUDE.md (Architectural Invariants):
  1. lxml імпортується лише в src/document/{parser.py, tei_io.py}
  2. fitz (PyMuPDF) — лише в src/document/{parser.py, nougat_parser.py, pdf_io.py}
  3. Cypher у src/graph/ — MERGE-only: жодного CREATE-вузла поза
     CREATE CONSTRAINT / CREATE INDEX; деструктивний wipe ізольований
     у graph_constraints.WIPE_STATEMENT (за явним прапорцем --wipe)

Тести — чисті AST/regex-сканери, без виконання модулів.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"

ALLOWED_LXML = {
    SRC / "document" / "parser.py",
    SRC / "document" / "tei_io.py",
}
ALLOWED_FITZ = {
    SRC / "document" / "parser.py",
    SRC / "document" / "nougat_parser.py",
    SRC / "document" / "pdf_io.py",
}


def _imports_of(path: Path) -> set[str]:
    """Top-level імена всіх import/from-import у файлі (AST, без виконання)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:           # зламаний файл — теж порушення
        raise AssertionError(f"{path}: SyntaxError {exc}") from exc
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _all_src_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_lxml_only_in_document_layer():
    violators = [
        str(p.relative_to(PROJECT_ROOT))
        for p in _all_src_files()
        if "lxml" in _imports_of(p) and p not in ALLOWED_LXML
    ]
    assert not violators, (
        "lxml дозволений лише у src/document/{parser.py, tei_io.py}; "
        f"порушники: {violators}. Використовуйте фасад src.document.tei_io."
    )


def test_fitz_only_in_document_layer():
    violators = [
        str(p.relative_to(PROJECT_ROOT))
        for p in _all_src_files()
        if "fitz" in _imports_of(p) and p not in ALLOWED_FITZ
    ]
    assert not violators, (
        "fitz дозволений лише у src/document/{parser.py, nougat_parser.py, pdf_io.py}; "
        f"порушники: {violators}. Використовуйте src.document.pdf_io."
    )


# ── MERGE-only Cypher у графовому шарі ────────────────────────────────────────

# CREATE-вузол: "CREATE (" або "CREATE (e:Label"; дозволені DDL-форми нижче
_CREATE_NODE_RE = re.compile(r"\bCREATE\s*\(", re.IGNORECASE)
_ALLOWED_CREATE_RE = re.compile(
    r"\bCREATE\s+(CONSTRAINT|INDEX|FULLTEXT|TEXT|VECTOR)\b", re.IGNORECASE)
# case-sensitive: Cypher-ключові слова в кодовій базі — UPPERCASE;
# "Delete ALL nodes" у docstring/help — проза, не запит
_DELETE_RE = re.compile(r"\bDETACH\s+DELETE\b|\bDELETE\b")

# Єдине дозволене місце деструктивного wipe (за явним --wipe прапорцем)
WIPE_ALLOWED_FILE = SRC / "graph" / "graph_constraints.py"


def _graph_files() -> list[Path]:
    return [p for p in (SRC / "graph").glob("*.py") if p.name != "__init__.py"]


def test_no_create_nodes_in_graph_layer():
    violators = []
    for p in _graph_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _CREATE_NODE_RE.search(line) and not _ALLOWED_CREATE_RE.search(line):
                violators.append(f"{p.name}:{i}: {line.strip()[:80]}")
    assert not violators, (
        "Вузли в src/graph/ створюються лише через MERGE (ідемпотентність); "
        f"знайдено CREATE-вузли: {violators}"
    )


def test_delete_only_in_gated_wipe():
    violators = []
    for p in _graph_files():
        if p == WIPE_ALLOWED_FILE:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _DELETE_RE.search(stripped) and "WIPE_STATEMENT" not in stripped:
                violators.append(f"{p.name}:{i}: {stripped[:80]}")
    assert not violators, (
        "DELETE у src/graph/ дозволений лише як WIPE_STATEMENT у "
        f"graph_constraints.py (за прапорцем --wipe); знайдено: {violators}"
    )


def test_deleted_packages_stay_deleted():
    """Фаза 1 видалила дублікати — регресія повертати їх заборонена."""
    assert not (SRC / "graphstore").exists(), (
        "src/graphstore/ був видалений (дублікат graph/ з CREATE Evidence) — "
        "fact-centric writer тепер src/graph/fact_writer.py"
    )
