"""Workbench core on a local git repository standing in for a paper repository (LocalRunner).

No live stores and no wsl.exe: the same bash scripts run here with the repository as cwd.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from src.workbench import delivery as DV
from src.workbench import manifest as M
from src.workbench.remote import LocalRunner, RemoteRepo
from src.workbench.steps import init as I

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("sha256sum")), reason="needs git")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "paper-repo"
    (r / "docs").mkdir(parents=True)
    (r / "CLAUDE.md").write_text("# Repo rules\n", encoding="utf-8")
    (r / "docs" / "clean.md").write_text("v1\n", encoding="utf-8")
    (r / "docs" / "edited.md").write_text("v1\n", encoding="utf-8")
    (r / ".gitignore").write_text("*.docx\n_work/\n", encoding="utf-8")
    git(r, "init", "-q")
    git(r, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    (r / "docs" / "edited.md").write_text("local edit, not committed\n", encoding="utf-8")
    (r / "docs" / "mine.md").write_text("untracked work\n", encoding="utf-8")
    return r


@pytest.fixture
def remote(repo):
    return RemoteRepo("local", str(repo), runner=LocalRunner())


def test_remote_reports_content_and_git_state(remote, repo):
    st = remote.files(["CLAUDE.md", "docs/edited.md", "docs/mine.md", "x/build.docx", "docs/new.md",
                       "path with space/ünï.md"])
    assert st["CLAUDE.md"]["git"] == "tracked" and st["CLAUDE.md"]["sha256"] == DV.sha256(b"# Repo rules\n")
    assert st["docs/edited.md"]["git"] == "modified"
    assert st["docs/mine.md"]["git"] == "untracked"
    assert st["x/build.docx"]["git"] == "ignored" and st["x/build.docx"]["sha256"] is None
    assert st["docs/new.md"]["git"] == "absent" and st["path with space/ünï.md"]["git"] == "absent"
    assert remote.ignored(["a/_work/b.csv", "README.md"]) == {"a/_work/b.csv"}
    assert remote.git_state()["git"] and remote.git_state()["dirty_paths"] == 2
    assert remote.read("CLAUDE.md") == b"# Repo rules\n" and remote.read("nope.md") is None
    assert set(remote.pull(["docs", "nope.md"])) == {"docs/clean.md", "docs/edited.md", "docs/mine.md"}


def test_plan_never_overwrites_uncommitted_work_and_blocks_secrets(remote):
    items = [
        DV.Item("docs/clean.md", b"v2\n", "t"),                         # clean tracked -> update
        DV.Item("docs/edited.md", b"v2\n", "t"),                        # modified -> conflict
        DV.Item("docs/mine.md", b"other\n", "t"),                       # untracked -> conflict
        DV.Item("CLAUDE.md", b"# Repo rules\n", "t"),                   # identical -> same
        DV.Item("docs/new.md", b"new\n", "t"),                          # new
        DV.Item("docs/leak.md", b"GHAI_API_KEY=ghai_" + b"x" * 40 + b"\n", "t"),
        DV.Item("docs/url.md", b"https://api.openalex.org/works?mailto=someone@example.org\n", "t"),
        DV.Item("paper/build.docx", b"PK\x03\x04", "t"),                # public binary, not private
        DV.Item("paper/private/build.pdf", b"%PDF-1.7", "t", "private"),   # not ignored: gets its .gitignore
        DV.Item("paper/private/old.docx", b"PK\x03\x04", "t", "private"),  # already ignored by *.docx
        DV.Item("a/_work/cache.csv", b"x\n", "t"),                      # never_deliver
    ]
    plan = DV.make_plan("p:x", remote, items, public=True, never=("**/_work/**",))
    kinds = {a.item.dest: (a.kind, a.reason) for a in plan.actions}
    assert kinds["docs/clean.md"][0] == "update"
    assert kinds["docs/edited.md"][0] == "conflict" and "modified" in kinds["docs/edited.md"][1]
    assert kinds["docs/mine.md"][0] == "conflict"
    assert kinds["CLAUDE.md"][0] == "same" and kinds["docs/new.md"][0] == "new"
    assert kinds["docs/leak.md"][0] == "blocked" and "GHAI API key" in kinds["docs/leak.md"][1]
    assert kinds["docs/url.md"][0] == "blocked" and "mailto" in kinds["docs/url.md"][1]
    assert kinds["paper/build.docx"][0] == "blocked" and "private" in kinds["paper/build.docx"][1]
    assert kinds["paper/private/build.pdf"][0] == "new" and kinds["paper/private/old.docx"][0] == "new"
    assert kinds["a/_work/cache.csv"][0] == "blocked"
    assert plan.extra == {"paper/private/.gitignore": DV.PRIVATE_GITIGNORE}
    forced = DV.make_plan("p:x", remote, items[:3], public=True, force=True)
    assert [a.kind for a in forced.actions] == ["update", "update", "update"]


def test_write_delivers_verifies_and_leaves_conflicts_alone(remote, repo):
    items = [DV.Item("docs/clean.md", b"v2\n", "t"), DV.Item("docs/edited.md", b"v2\n", "t"),
             DV.Item("pub/new.md", b"new\n", "t"), DV.Item("pub/private/f.pdf", b"%PDF", "t", "private")]
    plan = DV.make_plan("swot-dnipro:paper1", remote, items, public=True)
    result = DV.write(plan, remote, stamp="20261002T000000Z")
    assert result["manifest"] == ".ghai/deliveries/20261002T000000Z_swot-dnipro__paper1.json"
    assert (repo / "docs/clean.md").read_text() == "v2\n"
    assert (repo / "docs/edited.md").read_text() == "local edit, not committed\n"
    assert (repo / "pub/new.md").read_text() == "new\n"
    sums = (repo / ".ghai/deliveries/20261002T000000Z_swot-dnipro__paper1.sha256").read_text()
    assert "pub/private/.gitignore" in sums and "docs/edited.md" not in sums
    check = subprocess.run(["sha256sum", "-c", "--quiet", ".ghai/deliveries/20261002T000000Z_swot-dnipro__paper1.sha256"],
                           cwd=repo, capture_output=True)
    assert check.returncode == 0
    status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True).stdout
    assert "pub/private/f.pdf" not in status and "pub/private/.gitignore" in status   # git sees only the ignore file
    manifest = json.loads((repo / result["manifest"]).read_text())
    assert manifest["counts"] == {"update": 1, "conflict": 1, "new": 2}
    again = DV.make_plan("swot-dnipro:paper1", remote, items, public=True)
    assert {a.item.dest: a.kind for a in again.actions}["pub/new.md"] == "same"


def test_provenance_header_keeps_the_shebang_first():
    out = DV.provenance_header(b"#!/usr/bin/env python3\nimport os\n", "scripts/a.py", "f" * 64, "abc1234def", "2026-10-02")
    lines = out.decode().splitlines()
    assert lines[0] == "#!/usr/bin/env python3" and lines[1].startswith("# Provenance: knoweledg_graf:scripts/a.py")
    assert lines[-1] == "import os"


def test_init_sections_are_idempotent_and_merge_mcp_servers():
    existing = json.dumps({"mcpServers": {"other": {"type": "http", "url": "http://x"}}}).encode()
    merged = json.loads(I.mcp_json(existing))
    assert set(merged["mcpServers"]) == {"other", "ghai"}
    assert merged["mcpServers"]["ghai"]["headers"]["X-API-Key"] == "${GHAI_API_KEY}"
    assert I.mcp_json(I.mcp_json(existing)) == I.mcp_json(existing)
    by_hand = b'{ "mcpServers": { "ghai": { "type": "http", "url": "http://127.0.0.1:8090/mcp",\n' \
              b'  "headers": { "X-API-Key": "${GHAI_API_KEY}" } } } }\n'
    assert I.mcp_json(by_hand) == by_hand                     # already right: left byte for byte
    first = I.claude_md(b"# Rules\n\nkeep me\n", [("a:p1", "a/ghai.project.yaml")])
    second = I.claude_md(first, [("a:p1", "a/ghai.project.yaml"), ("b", "b/ghai.project.yaml")])
    assert second.count(I.BEGIN.encode()) == 1 and b"keep me" in second and b"`b`: manifest" in second
    assert I.claude_md(second, [("a:p1", "a/ghai.project.yaml"), ("b", "b/ghai.project.yaml")]) == second


def test_manifest_paths_stay_inside_the_repository():
    ok = M.ProjectManifest(project_id="a:p1", repo={"path": "/r"}, publication_dir="pub", paths={"bib": "docs/r.bib"})
    assert M.load(M.dump(ok)) == ok
    for bad in ({"publication_dir": "../x"}, {"publication_dir": "/abs"}, {"paths": {"bib": "a/../../b"}},
                {"repo": {"path": "relative"}}, {"unknown": 1}):
        data = {"project_id": "a:p1", "repo": {"path": "/r"}, "publication_dir": "pub", **bad}
        with pytest.raises(ValueError):
            M.ProjectManifest.model_validate(data)


def test_a_newer_copy_in_the_repository_is_never_replaced_by_an_older_source(remote):
    committed = remote.files(["docs/clean.md"])["docs/clean.md"]["committed"]
    older = DV.Item("docs/clean.md", b"old frozen copy\n", "frozen:x", mtime=committed - 3600, inventory="x")
    plan = DV.make_plan("p:x", remote, [older], public=True)
    act = plan.actions[0]
    assert act.kind == "conflict" and act.superseded and "newer" in act.reason
    newer = DV.Item("docs/clean.md", b"new build\n", "workbench:out", mtime=committed + 3600)
    assert DV.make_plan("p:x", remote, [newer], public=True).actions[0].kind == "update"
    forced = DV.make_plan("p:x", remote, [older], public=True, force=True).actions[0]
    assert forced.kind == "update" and forced.reason.startswith("--force over")


def test_a_file_changed_since_the_last_delivery_is_a_conflict(remote):
    built = DV.Item("docs/clean.md", b"next build\n", "workbench:out")
    assert DV.make_plan("p:x", remote, [built], public=True,
                        last_delivered={"docs/clean.md": DV.sha256(b"v1\n")}).actions[0].kind == "update"
    plan = DV.make_plan("p:x", remote, [built], public=True, last_delivered={"docs/clean.md": DV.sha256(b"v0\n")})
    assert plan.actions[0].kind == "conflict" and "since the last delivery" in plan.actions[0].reason


def test_an_edit_computed_from_the_current_file_is_safe_even_when_untracked(remote, repo):
    current = (repo / "docs/mine.md").read_bytes()                      # untracked
    edit = DV.Item("docs/mine.md", current + b"appended\n", "init", base_sha=DV.sha256(current))
    assert DV.make_plan("p:x", remote, [edit], public=True).actions[0].kind == "update"
    (repo / "docs/mine.md").write_text("changed meanwhile\n", encoding="utf-8")
    assert DV.make_plan("p:x", remote, [edit], public=True).actions[0].kind == "conflict"


def test_init_plans_the_manifest_and_registers_nothing_on_a_dry_run(remote, repo, monkeypatch, capsys):
    from src.workbench import registry, steps
    target = {"project_id": "p:x", "repo_path": str(repo), "distro": "local", "publication_dir": "pub",
              "manifest_path": "pub/ghai.project.yaml", "public": True, "paper_label": "P", "repo": "r",
              "seed_paths": {"bib": "docs/r.bib"}}
    monkeypatch.setattr(registry, "upsert", lambda *a, **k: (_ for _ in ()).throw(AssertionError("registered")))
    ctx = steps.Context("p:x", target, remote, local_target=False)
    assert I.run(ctx, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "new      pub/ghai.project.yaml" in out and "kept as is" not in out
