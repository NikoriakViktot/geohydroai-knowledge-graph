"""One extractor per yearbook table family. Each `run(pc)` returns
`{output_name: Extracted}` so the CLI can write CSVs uniformly."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Extracted:
    name: str
    rows: list[dict] = field(default_factory=list)
    #: Verbatim note blocks found under tables: {"volume","page","post_name","text"}.
    footnotes: list[dict] = field(default_factory=list)
    #: Things the parser saw and could not place. Never silently dropped.
    anomalies: list[str] = field(default_factory=list)
    skipped_other_posts: int = 0
    #: Named checks: {"name": {"passed": bool, "detail": str}}.
    checks: dict[str, dict] = field(default_factory=dict)
    pages: list[str] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks[name] = {"passed": bool(passed), "detail": detail}
