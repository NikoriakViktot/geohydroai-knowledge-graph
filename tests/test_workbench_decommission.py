"""P6 freeze, inventory and deletion gate (src/workbench/decommission.py), on a temporary tree."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from src.workbench import decommission as d


@pytest.mark.parametrize("pattern, path, hit", [
    ("a/**", "a/b/c.md", True),
    ("a/*.md", "a/c.md", True),
    ("a/*.md", "a/b/c.md", False),
    ("**/*:Zone.Identifier", "x/y/z.csv:Zone.Identifier", True),
    ("**/*:Zone.Identifier", "z.csv:Zone.Identifier", True),
    ("src/x/**/*.tiff*", "src/x/f/a.tiff.bak_1", True),
    ("src/x/**/*.tiff*", "src/x/a.tiff", True),
    ("paper_my/theses*.json", "paper_my/theses_v3.json", True),
    ("paper_my/theses*.json", "paper_my/sub/theses.json", False),
])
def test_glob_segments(pattern, path, hit):
    assert bool(d.glob_regex(pattern).match(path)) is hit


def test_first_rule_wins_and_destinations():
    rules = (
        d.Rule("p/_work/**", "archive"),
        d.Rule("p/article/*.docx", "private", "x", "dst/article/"),
        d.Rule("p/**", "deliver", "x", "dst/"),
        d.Rule("q/one.md", "deliver", "y", "elsewhere/ONE.md"),
    )
    assert d.classify("p/_work/a/b.parquet", rules)[1].disposition == "archive"
    i, r = d.classify("p/article/final.docx", rules)
    assert (i, r.destination("p/article/final.docx")) == (1, "dst/article/final.docx")
    assert d.classify("p/valid/m.md", rules)[1].destination("p/valid/m.md") == "dst/valid/m.md"
    assert d.classify("q/one.md", rules)[1].destination("q/one.md") == "elsewhere/ONE.md"
    assert d.classify("r/z.md", rules) == (-1, None)


def test_real_rules_keep_the_article1_theses_and_protect_public_repos():
    def disp(p):
        return d.classify(p)[1].disposition

    assert disp("paper_my/theses_v4.json") == "deliver"
    assert d.classify("paper_my/theses_v4.json")[1].destination("paper_my/theses_v4.json") \
        == "articles/flood_mapping_methods_review/literature/theses_v4.json"
    assert disp("paper_my/evidence_hits.json") == "archive"
    for p in ("src/paper_audit/article_1_final/Article_1_Flood_Mapping_Methods_FULL.pdf",
              "src/paper_audit/article_1_final/Article_1_Flood_Mapping_Methods.docx",
              "paper_unet-case-kakhovka/literature_audit_paper3/article/Paper3_final.docx",
              "data/paper_3_audit/paper1_manuscript_en.docx"):
        assert disp(p) == "private", p
    for p in ("data/paper_3_audit/briefs/geodesy/pdf/x.pdf",
              "data/paper_3_audit/missing_paper_23-09-2026/baptist2004.pdf",
              "paper_unet-case-kakhovka/literature_audit_paper3/_work/semantic_hits.parquet"):
        assert disp(p) == "archive", p
    assert disp("paper_unet-case-kakhovka/literature_audit_paper3/valid_artsclt/open_citations.json") == "deliver"
    assert disp("paper_terrain-case-kakhovka/publication/literature/theses.json") == "mirror"
    assert disp("src/paper_3/evidence.py") == "here"


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "repo"
    (root / "pp" / "sub").mkdir(parents=True)
    (root / "pp" / "__pycache__").mkdir()
    (root / "pp" / "a.md").write_text("alpha\n", encoding="utf-8")
    (root / "pp" / "sub" / "b c.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (root / "pp" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\0")
    (root / "pp" / "tool.pyc").write_bytes(b"\0")
    (root / "corpus.xml").write_text("<TEI/>", encoding="utf-8")
    os.symlink(root / "corpus.xml", root / "pp" / "link.xml")
    (root / "loose.bib").write_text("@misc{a}\n", encoding="utf-8")
    return root


ROOTS = ("pp", "loose.bib", "missing_dir")
RULES = (d.Rule("pp/sub/**", "deliver", "x", "out/"), d.Rule("pp/**", "archive"), d.Rule("loose.bib", "retire"))


@pytest.fixture()
def frozen_dest(tmp_path):
    dest = tmp_path / "frozen" / "f1"
    yield dest
    if dest.exists():
        d._make_writable(dest)         # read-only trees would outlive pytest's tmp cleanup


def _frozen(tree, dest):
    info = d.freeze(dest, root=tree, roots=ROOTS)
    rows = d.build_inventory(dest, root=tree, imported={"pp/a.md"}, rules=RULES)
    d.write_inventory(dest, rows)
    d.make_readonly(dest)
    return dest, info, rows


def test_freeze_copies_bytes_skips_caches_keeps_symlinks(tree, frozen_dest):
    dest, info, rows = _frozen(tree, frozen_dest)
    assert info["files"] == 3 and info["symlinks"] == 1
    assert (dest / "pp/sub/b c.csv").read_bytes() == (tree / "pp/sub/b c.csv").read_bytes()
    assert not (dest / "pp/__pycache__").exists() and not (dest / "pp/tool.pyc").exists()
    assert os.readlink(dest / "pp/link.xml") == str(tree / "corpus.xml")
    if shutil.which("sha256sum"):
        proc = subprocess.run(["sha256sum", "-c", "--quiet", d.SUMS], cwd=dest, capture_output=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    with pytest.raises(PermissionError):
        (dest / "pp/a.md").write_text("changed", encoding="utf-8")
    with pytest.raises(FileExistsError):
        d.freeze(dest, root=tree, roots=ROOTS)


def test_inventory_classifies_and_keeps_verified_status(tree, frozen_dest):
    dest, _, rows = _frozen(tree, frozen_dest)
    by = {r["path"]: r for r in rows}
    assert by["pp/sub/b c.csv"]["destination"] == "out/b c.csv"
    assert by["pp/a.md"]["imported"] == "yes" and by["pp/a.md"]["disposition"] == "archive"
    assert by["pp/link.xml"]["kind"] == "symlink" and by["pp/link.xml"]["note"].startswith("->")
    assert {r["status"] for r in rows} == {"frozen"}

    by["pp/sub/b c.csv"].update(status="delivered", checked_at="2026-10-02T00:00:00+00:00")
    d.write_inventory(dest, list(by.values()))
    again = {r["path"]: r for r in d.build_inventory(dest, root=tree, imported=set(), rules=RULES)}
    assert again["pp/sub/b c.csv"]["status"] == "delivered"


def test_gate_blocks_until_every_disposition_is_satisfied(tree, frozen_dest):
    dest, _, rows = _frozen(tree, frozen_dest)
    blockers = d.gate(rows)
    assert [r["path"] for r in blockers] == ["pp/sub/b c.csv"]
    for r in rows:
        if r["path"] == "pp/sub/b c.csv":
            r["status"] = "present"
    assert d.gate(rows) == []
    assert d.gate([{"disposition": "unclassified", "status": "frozen"}])


def test_verify_reports_drift_of_the_originals(tree, frozen_dest):
    dest, _, _ = _frozen(tree, frozen_dest)
    (tree / "pp/a.md").write_text("edited after the freeze\n", encoding="utf-8")
    (tree / "pp/new.md").write_text("new\n", encoding="utf-8")
    (tree / "loose.bib").unlink()
    rep = d.verify(dest, root=tree, roots=ROOTS)
    assert rep["corrupt"] == []
    assert rep["changed"] == ["pp/a.md"] and rep["new"] == ["pp/new.md"] and rep["deleted"] == ["loose.bib"]
