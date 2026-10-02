"""Signed, short-lived links to corpus files, so that a browser can open a local PDF.

A browser cannot send the X-API-Key header when it follows a link, so GET /v1/locate
(which needs a key) hands out a link of the form /v1/files/<token>: the token carries the
file's path and an expiry, signed with HMAC-SHA256. Anyone holding the link can read that
one file until it expires; the server listens on 127.0.0.1 only.

The secret comes from GHAI_FILE_SECRET, else from ~/.config/ghai/file_secret, created
once (mode 0600) so that links survive a server restart within their lifetime.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TTL_SECONDS = 12 * 3600
_SERVABLE = ("data/",)
_SECRET: bytes | None = None


class InvalidLink(Exception):
    pass


class ExpiredLink(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def secret() -> bytes:
    global _SECRET
    if _SECRET is None:
        env = os.environ.get("GHAI_FILE_SECRET")
        if env:
            _SECRET = env.encode()
        else:
            path = Path.home() / ".config" / "ghai" / "file_secret"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_hex(32))
            _SECRET = path.read_text().strip().encode()
    return _SECRET


def sign(rel_path: str, ttl: int = TTL_SECONDS, now: float | None = None) -> str:
    payload = _b64(json.dumps({"f": rel_path, "e": int((now or time.time()) + ttl)}, separators=(",", ":")).encode())
    mac = hmac.new(secret(), payload.encode(), hashlib.sha256).digest()[:24]
    return f"{payload}.{_b64(mac)}"


def verify(token: str, now: float | None = None) -> Path:
    """The file a token grants, or InvalidLink / ExpiredLink."""
    try:
        payload, mac = token.split(".", 1)
        expected = hmac.new(secret(), payload.encode(), hashlib.sha256).digest()[:24]
        if not hmac.compare_digest(expected, _unb64(mac)):
            raise InvalidLink("signature does not match")
        data = json.loads(_unb64(payload))
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidLink("not a file link from this server") from exc
    if data.get("e", 0) < (now or time.time()):
        raise ExpiredLink("the link has expired")
    rel = str(data.get("f", ""))
    path = (ROOT / rel).resolve()
    if not any(rel.startswith(p) for p in _SERVABLE) or not path.is_relative_to(ROOT.resolve()):
        raise InvalidLink("the link points outside the corpus files")
    if not path.is_file():
        raise InvalidLink("the file no longer exists")
    return path


def relative(path: str | Path) -> str:
    p = Path(path)
    return str(p.resolve().relative_to(ROOT.resolve())) if p.is_absolute() else str(p)
