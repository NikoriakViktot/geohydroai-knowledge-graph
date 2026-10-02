"""Deliveries into a paper repository: planned, checked, written in one stream, verified there.

    plan  ->  every file is new | update | same | conflict | blocked, with the reason
    write ->  one tar stream + .ghai/deliveries/<stamp>_<project>.{json,sha256}, then `sha256sum -c` remotely

Never over someone's work: a file that differs and is not a clean tracked file (modified,
untracked or ignored) is a conflict unless ``force``. Never anything secret or private into
git: secret-like content, never_deliver globs and, in a public repository, binary builds outside
an ignored ``private/`` folder are blocked. The human reviews ``git status`` there and commits.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

from src.workbench.decommission import ROOT, glob_regex
from src.workbench.manifest import ALWAYS_NEVER, PUBLIC_BINARY

DELIVERY_DIR = ".ghai/deliveries"
PRIVATE_GITIGNORE = b"# Files for the author only: never committed (GeoHydroAI paper workbench).\n*\n!.gitignore\n"

SECRETS: dict[str, re.Pattern] = {
    "a GHAI API key": re.compile(rb"ghai_[A-Za-z0-9_-]{30,}"),
    "a key, token or password assignment": re.compile(
        rb"\b[A-Z0-9_]*(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD|PASSWD)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
    "a private key block": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "a Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "an AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "a key in a URL": re.compile(rb"(?i)[?&](?:api_key|apikey|access_token|token)=[A-Za-z0-9_.-]{8,}"),
    "an e-mail address in a URL (mailto=)": re.compile(rb"(?i)[?&]mailto=[^&\s\"']+@"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def secrets_in(data: bytes) -> list[str]:
    return [name for name, rx in SECRETS.items() if rx.search(data)]


def _matches(path: str, globs) -> str | None:
    for g in globs:
        if glob_regex(g).match(path):
            return g
    return None


def private_root(dest: str) -> str | None:
    """'a/b/private/c/d.docx' -> 'a/b/private' (the folder whose .gitignore keeps it out of git)."""
    parts = PurePosixPath(dest).parts
    if "private" not in parts[:-1]:
        return None
    return "/".join(parts[: parts.index("private") + 1])


@dataclass
class Item:
    dest: str                       # repository-relative destination
    data: bytes
    source: str                     # where it came from, e.g. "frozen:paper_my/theses_v4.json"
    disposition: str = "deliver"    # deliver | private | code
    inventory: str | None = None    # freeze path of the INVENTORY row this item settles
    mtime: float | None = None      # when the source was last changed (epoch s); None = built just now
    base_sha: str | None = None     # an edit of the repository's own file: the sha256 it was computed from

    @property
    def sha256(self) -> str:
        return sha256(self.data)


@dataclass
class Action:
    item: Item
    kind: str                       # new | update | same | conflict | blocked
    reason: str = ""
    remote_sha: str | None = None
    git: str = ""
    superseded: bool = False        # the repository holds a newer version than the source


@dataclass
class Plan:
    project_id: str
    target: str
    actions: list[Action]
    consumer: dict = field(default_factory=dict)
    extra: dict[str, bytes] = field(default_factory=dict)     # generated files (private/.gitignore)

    def by_kind(self, *kinds: str) -> list[Action]:
        return [a for a in self.actions if a.kind in kinds]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.kind] = out.get(a.kind, 0) + 1
        return out


def make_plan(project_id: str, remote, items: list[Item], *, public: bool, never: tuple[str, ...] = (),
              force: bool = False, last_delivered: dict[str, str] | None = None) -> Plan:
    """``last_delivered``: path -> sha256 of the previous delivery; a file changed since is a conflict."""
    dup = {i.dest for i in items if sum(1 for j in items if j.dest == i.dest) > 1}
    if dup:
        raise ValueError(f"two items for the same destination: {sorted(dup)[:5]}")
    dests = [i.dest for i in items]
    state = remote.files(dests)
    ignored = remote.ignored(dests)
    blocked_globs = tuple(never) + ALWAYS_NEVER
    plan = Plan(project_id, repr(remote), [], consumer=remote.git_state())
    needed_ignores: set[str] = set()
    for it in items:
        st = state.get(it.dest, {"sha256": None, "git": "absent"})
        act = Action(it, "", remote_sha=st["sha256"], git=st["git"])
        reason = _blocked(it, ignored, public, blocked_globs)
        if reason:
            act.kind, act.reason = "blocked", reason
        elif st["sha256"] == it.sha256:
            act.kind = "same"
        elif st["sha256"] is None:
            act.kind = "new"
        elif it.base_sha and st["sha256"] == it.base_sha:
            act.kind, act.reason = "update", f"edit of the {st['git']} file as it is now (unchanged since read)"
        else:
            theirs = st.get("committed") or st.get("mtime")
            act.superseded = bool(it.mtime and theirs and theirs > it.mtime)
            edited = last_delivered is not None and last_delivered.get(it.dest) not in (None, st["sha256"])
            if act.superseded:
                stamp = datetime.fromtimestamp(theirs, timezone.utc).strftime("%Y-%m-%d %H:%M")
                act.kind, act.reason = "conflict", f"the repository's copy is newer ({stamp} UTC)"
            elif edited:
                act.kind, act.reason = "conflict", "changed in the repository since the last delivery"
            elif st["git"] == "tracked":
                act.kind, act.reason = "update", "clean tracked file; the old version stays in git"
            else:
                act.kind, act.reason = "conflict", f"{st['git']} file with other content"
            if act.kind == "conflict" and force:
                act.kind, act.reason = "update", f"--force over: {act.reason}"
            elif act.kind == "conflict":
                act.reason += "; not overwritten without --force"
        if act.kind in ("new", "update") and it.disposition == "private" and it.dest not in ignored:
            needed_ignores.add(private_root(it.dest))
        plan.actions.append(act)
    if needed_ignores:
        gi_paths = [f"{root}/.gitignore" for root in sorted(needed_ignores)]
        have = remote.files(gi_paths)
        for path in gi_paths:
            if have[path]["sha256"] is None:
                plan.extra[path] = PRIVATE_GITIGNORE
    return plan


def _blocked(it: Item, ignored: set[str], public: bool, globs: tuple[str, ...]) -> str:
    parts = PurePosixPath(it.dest).parts
    if ".git" in parts:
        return "inside .git"
    g = _matches(it.dest, globs)
    if g:
        return f"never delivered ({g})"
    found = secrets_in(it.data)
    if found:
        return "secret-like content: " + ", ".join(found)
    if it.disposition == "private":
        if it.dest not in ignored and not private_root(it.dest):
            return "private file would be committable: put it under a private/ folder or an ignored path"
    elif public and _matches(it.dest, PUBLIC_BINARY):
        return "binary build in a public repository: deliver it as private"
    return ""


def knowledge_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def write(plan: Plan, remote, *, stamp: str | None = None) -> dict:
    """Write new/update files + generated .gitignores + the delivery manifest; verify remotely."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = plan.project_id.replace(":", "__")
    base = f"{DELIVERY_DIR}/{stamp}_{slug}"
    writes = {a.item.dest: a.item.data for a in plan.by_kind("new", "update")}
    writes.update(plan.extra)
    summary = summarize(plan, stamp)
    manifest = json.dumps(summary, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
    writes[f"{base}.json"] = manifest
    sums = "".join(f"{sha256(data)}  {path}\n" for path, data in sorted(writes.items())).encode("utf-8")
    writes[f"{base}.sha256"] = sums
    if not plan.by_kind("new", "update") and not plan.extra:
        return {**summary, "written": 0, "manifest": None}
    out = remote.put(writes, f"{base}.sha256")
    return {**summary, "written": len(writes), "manifest": f"{base}.json", "check": out.strip()[-200:]}


def summarize(plan: Plan, stamp: str) -> dict:
    return {
        "schema": "ghai.delivery/v1",
        "project_id": plan.project_id,
        "stamp": stamp,
        "knowledge_commit": knowledge_commit(),
        "consumer_before": plan.consumer,
        "counts": plan.counts(),
        "files": [{"path": a.item.dest, "action": a.kind, "sha256": a.item.sha256, "bytes": len(a.item.data),
                   "source": a.item.source, "disposition": a.item.disposition,
                   **({"reason": a.reason} if a.reason else {})} for a in plan.actions],
        "generated": sorted(plan.extra),
    }


def render(plan: Plan, limit: int = 40) -> str:
    lines = [f"{plan.project_id} -> {plan.target}  (HEAD {str(plan.consumer.get('commit') or '-')[:10]}, "
             f"{plan.consumer.get('dirty_paths')} dirty paths)",
             "  " + ", ".join(f"{k}: {v}" for k, v in sorted(plan.counts().items()))]
    for kind in ("blocked", "conflict", "update", "new"):
        acts = plan.by_kind(kind)
        for a in acts[:limit]:
            lines.append(f"  {kind:<8} {a.item.dest}" + (f"  — {a.reason}" if a.reason else ""))
        if len(acts) > limit:
            lines.append(f"  {kind:<8} … {len(acts) - limit} more")
    for path in sorted(plan.extra):
        lines.append(f"  generate {path}")
    return "\n".join(lines)


def provenance_header(text: bytes, source: str, frozen_sha: str, commit: str, day: str) -> bytes:
    """floodstate-eo convention: '# Provenance:' after the shebang/encoding lines of a script."""
    header = (f"# Provenance: knoweledg_graf:{source} @ {commit[:12] or 'unknown'} (frozen copy sha256 "
              f"{frozen_sha[:16]}…), delivered {day} by the GeoHydroAI paper workbench (P6).\n"
              "# Changed: only this header. Paths and imports may still point into knoweledg_graf: "
              "port them before running.\n").encode("utf-8")
    lines = text.splitlines(keepends=True)
    keep = 0
    while keep < len(lines) and keep < 2 and (lines[keep].startswith(b"#!") or b"coding" in lines[keep][:40]
                                              and lines[keep].startswith(b"#")):
        keep += 1
    return b"".join(lines[:keep]) + header + b"".join(lines[keep:])
