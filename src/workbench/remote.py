"""A paper repository in another WSL distribution, reached through wsl.exe.

Generalised from src/paper_3/snapshot.py (run_remote, remote_listing, remote_read). Scripts go to
``bash -s`` on stdin, so nothing is re-quoted on the Windows command line. Bytes move as one tar
stream through a temporary file, never one launch per file. Paths travel inside quoted heredocs,
and file contents are compared by sha256 on the remote side.

``LocalRunner`` runs the same argv on this machine with the repository path as cwd: a repository
in this distribution, or a scratch directory standing in for one (``--to DIR``, tests).
"""

from __future__ import annotations

import io
import secrets
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

WSL_EXE = "/mnt/c/Windows/System32/wsl.exe"
HEREDOC = "GHAI_PATHS_EOF"


@dataclass
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes


Runner = Callable[[list[str], bytes | None, int], Result]


def wsl_runner(argv: list[str], stdin: bytes | None, timeout: int) -> Result:
    proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    return Result(proc.returncode, proc.stdout, proc.stderr)


class LocalRunner:
    """Executes the command part of a wsl.exe argv locally, in the repository directory."""

    def __call__(self, argv: list[str], stdin: bytes | None, timeout: int) -> Result:
        cwd = argv[argv.index("--cd") + 1]
        cmd = argv[argv.index("--exec") + 1:]
        proc = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, cwd=cwd)
        return Result(proc.returncode, proc.stdout, proc.stderr)


class RemoteError(RuntimeError):
    pass


def _check_paths(paths: Iterable[str]) -> list[str]:
    out = []
    for p in paths:
        if not p or "\n" in p or p == HEREDOC or p.startswith("/") or ".." in PurePosixPath(p).parts:
            raise ValueError(f"not a safe repository-relative path: {p!r}")
        out.append(p)
    return out


def _heredoc(paths: Iterable[str], tail: str = "") -> str:
    """``<<'EOF'`` + one path per line; ``tail`` continues the command line (e.g. ``|| true``)."""
    return f"<<'{HEREDOC}'{tail}\n" + "".join(f"{p}\n" for p in paths) + f"{HEREDOC}\n"


class RemoteRepo:
    def __init__(self, distro: str, path: str, runner: Runner | None = None, attempts: int = 3):
        if not path.startswith("/"):
            raise ValueError("repository path must be absolute")
        self.distro, self.path = distro, path.rstrip("/")
        self.runner = runner or wsl_runner
        self.attempts = attempts

    def __repr__(self) -> str:
        return f"RemoteRepo({self.distro}:{self.path})"

    def argv(self, *cmd: str) -> list[str]:
        return [WSL_EXE, "-d", self.distro, "--cd", self.path, "--exec", *cmd]

    def _launch(self, argv: list[str], stdin: bytes | None, timeout: int) -> bytes:
        """Retry bare non-zero exits with no output: wsl.exe relay hiccups (snapshot.py, 2026-09-23)."""
        last = None
        for attempt in range(self.attempts):
            res = self.runner(argv, stdin, timeout)
            if res.returncode == 0:
                return res.stdout
            last = res
            if res.stdout or res.stderr.strip():
                break                      # a real failure says something; do not repeat it
            time.sleep(0.5 * (attempt + 1))
        raise RemoteError(f"{self}: exit {last.returncode}: {last.stderr.decode(errors='replace')[-500:]}")

    # ── primitives ─────────────────────────────────────────────────────────────
    def run(self, script: str, timeout: int = 300) -> bytes:
        """A bash script, given on stdin, in the repository root."""
        return self._launch(self.argv("bash", "-s"), ("set -euo pipefail\n" + script).encode(), timeout)

    def _tmp(self) -> str:
        return f"/tmp/ghai-{secrets.token_hex(8)}"

    def put_bytes(self, data: bytes, timeout: int = 600) -> str:
        tmp = self._tmp()
        self._launch(self.argv("bash", "-c", f"umask 077; cat > {tmp}"), data, timeout)
        return tmp

    def get_bytes(self, tmp: str, timeout: int = 600) -> bytes:
        try:
            return self._launch(self.argv("cat", tmp), None, timeout)
        finally:
            self._launch(self.argv("rm", "-f", tmp), None, 60)

    # ── queries ────────────────────────────────────────────────────────────────
    def git_state(self) -> dict:
        out = self.run("if git rev-parse --git-dir >/dev/null 2>&1; then git rev-parse HEAD; "
                       "git branch --show-current; git status --porcelain | wc -l; else echo none; fi\n").decode().split()
        if not out or out[0] == "none":
            return {"git": False, "commit": None, "branch": None, "dirty_paths": None}
        return {"git": True, "commit": out[0], "branch": out[1] if len(out) > 2 else None,
                "dirty_paths": int(out[-1])}

    def files(self, paths: list[str]) -> dict[str, dict]:
        """For each path: sha256 (None when absent), git state, mtime and last commit time (epoch s).

        git state: tracked (clean), modified (staged or not), untracked, ignored (also for a path that
        does not exist yet but would be ignored), absent, or nogit outside a work tree.
        """
        paths = _check_paths(paths)
        if not paths:
            return {}
        script = (
            "in_git=0; git rev-parse --git-dir >/dev/null 2>&1 && in_git=1\n"
            "while IFS= read -r p; do\n"
            "  h=-; if [ -f \"$p\" ] && [ ! -L \"$p\" ]; then h=$(sha256sum -- \"$p\"); h=${h%% *}; fi\n"
            "  if [ $in_git = 0 ]; then s=nogit\n"
            "  elif git ls-files --error-unmatch -- \"$p\" >/dev/null 2>&1; then\n"
            "    if git diff --quiet -- \"$p\" && git diff --cached --quiet -- \"$p\"; then s=tracked; else s=modified; fi\n"
            "  elif git check-ignore -q --no-index -- \"$p\"; then s=ignored\n"
            "  elif [ -e \"$p\" ]; then s=untracked\n"
            "  else s=absent; fi\n"
            "  m=-; [ -e \"$p\" ] && m=$(stat -c %Y -- \"$p\")\n"
            "  c=-; if [ $s = tracked ] || [ $s = modified ]; then c=$(git log -1 --format=%ct -- \"$p\"); c=${c:--}; fi\n"
            "  printf '%s\\t%s\\t%s\\t%s\\t%s\\n' \"$h\" \"$s\" \"$m\" \"$c\" \"$p\"\n"
            f"done {_heredoc(paths)}"
        )
        out = {}
        for line in self.run(script).decode("utf-8").splitlines():
            digest, state, mtime, committed, path = line.split("\t", 4)
            out[path] = {"sha256": None if digest == "-" else digest, "git": state,
                         "mtime": None if mtime == "-" else int(mtime),
                         "committed": None if committed == "-" else int(committed)}
        return out

    def ignored(self, paths: list[str]) -> set[str]:
        paths = _check_paths(paths)
        if not paths:
            return set()
        script = ("git rev-parse --git-dir >/dev/null 2>&1 || exit 0\n"
                  f"git check-ignore --no-index --stdin {_heredoc(paths, tail=' || true')}")
        return {l for l in self.run(script).decode("utf-8").splitlines() if l}

    def listdir(self, rel: str) -> list[str]:
        """Names in a directory of the repository ([] when it does not exist)."""
        _check_paths([rel])
        out = self.run(f"if [ -d {_q(rel)} ]; then ls -1A -- {_q(rel)}; fi\n").decode("utf-8")
        return [l for l in out.splitlines() if l]

    def read(self, rel: str) -> bytes | None:
        _check_paths([rel])
        tmp = self._tmp()
        found = self.run(f"if [ -f {_q(rel)} ]; then cp -- {_q(rel)} {tmp}; echo yes; else echo no; fi\n").strip()
        return self.get_bytes(tmp) if found == b"yes" else None

    # ── trees ──────────────────────────────────────────────────────────────────
    def pull(self, paths: list[str]) -> dict[str, bytes]:
        """Files (or whole directories) by repository-relative path, in one tar stream."""
        paths = _check_paths(paths)
        if not paths:
            return {}
        tmp = self._tmp()
        script = ("files=()\n"
                  f"while IFS= read -r p; do if [ -e \"$p\" ]; then files+=(\"$p\"); fi; done {_heredoc(paths)}"
                  f"if [ ${{#files[@]}} -eq 0 ]; then tar -cf {tmp} --files-from /dev/null; else\n"
                  f"  tar --exclude='*:Zone.Identifier' --exclude='__pycache__' -cf {tmp} -- \"${{files[@]}}\"; fi\n")
        self.run(script)
        data = self.get_bytes(tmp)
        out: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    _check_paths([member.name])
                    out[member.name] = tar.extractfile(member).read()
        return out

    def put(self, files: dict[str, bytes], sums_path: str, timeout: int = 900) -> str:
        """Write files at their repository paths, then `sha256sum -c` them remotely from ``sums_path``.

        ``files`` must contain ``sums_path`` itself (sha256sum format, repository-relative paths).
        Returns the remote check output; raises when any file fails it.
        """
        _check_paths(files)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel in sorted(files):
                info = tarfile.TarInfo(rel)
                info.size, info.mode, info.mtime = len(files[rel]), 0o644, int(time.time())
                tar.addfile(info, io.BytesIO(files[rel]))
        tmp = self.put_bytes(buf.getvalue(), timeout)
        script = (f"tar --no-same-owner --no-same-permissions -xf {tmp}; rm -f {tmp}\n"
                  f"sha256sum -c --quiet -- {_q(sums_path)} && echo SHA256_OK\n")
        out = self.run(script, timeout).decode("utf-8", errors="replace")
        if "SHA256_OK" not in out:
            raise RemoteError(f"{self}: sha256 check failed after writing:\n{out[-1500:]}")
        return out


def _q(path: str) -> str:
    """Single-quote a path for bash."""
    return "'" + path.replace("'", "'\\''") + "'"
