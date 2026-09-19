"""snapshot.py — the SWOT-DNIPRO tables are copied, hashed and resolvable; no wsl.exe here."""
from __future__ import annotations

import base64
import json

import pytest

from src.paper_3 import snapshot


def _fake_runner(files: dict[str, bytes]):
    """Emulate the three remote scripts (ls, base64 cat, git state) over a dict."""
    def runner(repo: str, script: str) -> bytes:
        if script.startswith("ls -1"):
            pattern = script.split("--", 1)[1].split("2>")[0].strip()
            import fnmatch
            names = [n for n in files if n.startswith(repo + ":")]
            rels = [n.split(":", 1)[1] for n in names]
            return "\n".join(sorted(r for r in rels if fnmatch.fnmatch(r, pattern))).encode()
        if script.startswith("base64"):
            rel = script.split("'")[1]
            return base64.b64encode(files[f"{repo}:{rel}"])
        if script.startswith("git rev-parse"):
            return b"abc123\n7\n"
        raise AssertionError(script)
    return runner


@pytest.fixture
def fake_files():
    return {
        "swot:outputs/tables/a.csv": b"x,y\n1,2\n",
        "swot:outputs/tables/p28_surface_summary_ZONE_2.csv": b"zone\nZ2\n",
        "swot:outputs/tables/p28_surface_summary_ZONE_4.csv": b"zone\nZ4\n",
        "icesat:outputs/tables/b.csv": b"k\nv\n",
    }


def test_pull_writes_files_manifest_and_hashes(tmp_path, fake_files):
    files = (("swot", "outputs/tables/a.csv"),
             ("swot", "outputs/tables/p28_surface_summary_ZONE_*.csv"),
             ("icesat", "outputs/tables/b.csv"),
             ("swot", "outputs/tables/does_not_exist.csv"))
    manifest = snapshot.pull(tmp_path, run_id="r1", files=files,
                             runner=_fake_runner(fake_files))
    m = json.loads(manifest.read_text())
    assert m["n_files"] == 4                      # glob expanded to two zones
    assert m["missing_patterns"] == ["swot:outputs/tables/does_not_exist.csv"]
    assert m["repos"]["swot"]["commit"] == "abc123"
    assert m["repos"]["swot"]["dirty_paths"] == 7
    assert (tmp_path / "swot/outputs/tables/a.csv").read_bytes() == b"x,y\n1,2\n"
    assert snapshot.verify(tmp_path) == []


def test_verify_detects_tampering(tmp_path, fake_files):
    snapshot.pull(tmp_path, files=(("swot", "outputs/tables/a.csv"),),
                  runner=_fake_runner(fake_files))
    (tmp_path / "swot/outputs/tables/a.csv").write_bytes(b"changed")
    assert snapshot.verify(tmp_path) == ["sha256 mismatch: swot/outputs/tables/a.csv"]


def test_resolve_by_basename_requires_uniqueness(tmp_path, fake_files):
    snapshot.pull(tmp_path, files=(("swot", "outputs/tables/*.csv"),
                                   ("icesat", "outputs/tables/b.csv")),
                  runner=_fake_runner(fake_files))
    assert snapshot.resolve("b.csv", tmp_path).name == "b.csv"
    assert snapshot.resolve("outputs/tables/a.csv", tmp_path).exists()
    with pytest.raises(FileNotFoundError):
        snapshot.resolve("nope.csv", tmp_path)
    assert snapshot.entry_for("a.csv", tmp_path)["sha256"]


def test_listing_drops_zone_identifier_sidecars():
    def runner(repo, script):
        return b"x.csv\nx.csv:Zone.Identifier\n"
    assert snapshot.remote_listing("swot", "*.csv", runner) == ["x.csv"]


def test_snapshot_file_list_is_well_formed():
    for repo, pattern in snapshot.SNAPSHOT_FILES:
        assert repo in snapshot.REPOS
        assert not pattern.startswith("/")
