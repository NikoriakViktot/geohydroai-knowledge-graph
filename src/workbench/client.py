"""The Knowledge API client of the workbench: every corpus operation goes through the API (R-ACC-1).

URL and key come from GHAI_API_URL / GHAI_API_KEY, else from ~/.config/ghai/env (mode 0600).
The key is never printed or written into a delivery.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from src.workbench.decommission import ROOT

sys.path.insert(0, str(ROOT / "clients" / "python"))

from ghai_client import GHAI, GHAIError  # noqa: E402

__all__ = ["GHAI", "GHAIError", "api", "settings"]


def settings() -> tuple[str, str | None]:
    url, key = os.environ.get("GHAI_API_URL"), os.environ.get("GHAI_API_KEY")
    conf = Path.home() / ".config" / "ghai" / "env"
    if (not url or not key) and conf.is_file():
        for line in conf.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?(GHAI_API_URL|GHAI_API_KEY)=(.*)", line)
            if m:
                value = m.group(2).strip().strip("'\"")
                if m.group(1) == "GHAI_API_URL":
                    url = url or value
                else:
                    key = key or value
    return (url or "http://127.0.0.1:8090/v1").rstrip("/"), key


def api(timeout: float = 600.0) -> GHAI:
    url, key = settings()
    if not key:
        raise SystemExit("no GHAI_API_KEY (environment or ~/.config/ghai/env); create one with python -m src.api.keys")
    return GHAI(url, key, timeout=timeout, retries=2)
