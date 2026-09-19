"""Topic definitions and the source policy each one declares."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.paper_3.briefs.schema import EVIDENCE_ROLES, TOPICS

TOPICS_PATH = Path(__file__).resolve().parent / "topics.yaml"


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    title: str
    question: str
    #: The most a record from an abstract may ever be for this topic. Capped in
    #: code by `cap_role`, so an extractor cannot promote an abstract past it.
    abstract_role: str
    #: Fields that only full text may populate.
    fulltext_required_for: tuple[str, ...]
    theses: tuple[str, ...]
    key_terms: tuple[tuple[str, ...], ...]
    negative_terms: tuple[str, ...]
    extract: tuple[str, ...]
    #: Require two different families inside one passage, not merely somewhere in
    #: the document. "ICESat-2" and "geoid" both appearing in a paper means
    #: little; both in the same two thousand characters means it relates them.
    require_cooccurrence: bool = False

    @property
    def primary_family(self) -> tuple[str, ...]:
        return self.key_terms[0] if self.key_terms else ()

    def families_hit(self, text: str) -> int:
        low = (text or "").lower()
        return sum(1 for fam in self.key_terms
                   if any(term.lower() in low for term in fam))

    def primary_hit(self, text: str) -> bool:
        low = (text or "").lower()
        return any(term.lower() in low for term in self.primary_family)

    def has_negative(self, text: str) -> bool:
        low = (text or "").lower()
        return any(term.lower() in low for term in self.negative_terms)

    def is_on_topic(self, text: str, min_other: int = 1) -> bool:
        """Primary family plus at least `min_other` others.

        Same shape as `Thesis.is_on_topic`. The topic families are deliberately
        wider than the thesis families they draw on — a brief asks what the field
        knows, not whether one specific claim has been made before.
        """
        if not self.primary_hit(text):
            return False
        return (self.families_hit(text) - 1) >= min_other

    def cap_role(self, proposed_role: str, evidence_level: str) -> str:
        """Clamp a proposed role to what this topic allows at this level.

        The enforcement point for the package's one rule. An extractor reading an
        abstract may believe it found a quantitative result; for topics B and C it
        is demoted to `discovery` regardless, because an abstract cannot supply
        the context that makes a number comparable.
        """
        if evidence_level == "full_text":
            return proposed_role if proposed_role in EVIDENCE_ROLES else "discovery"
        ceiling = EVIDENCE_ROLES.index(self.abstract_role)
        proposed = (EVIDENCE_ROLES.index(proposed_role)
                    if proposed_role in EVIDENCE_ROLES else 0)
        return EVIDENCE_ROLES[min(proposed, ceiling)]


def load_topics(path: Path | None = None) -> list[Topic]:
    raw = yaml.safe_load(Path(path or TOPICS_PATH).read_text(encoding="utf-8"))
    out = []
    for entry in raw:
        out.append(Topic(
            id=entry["id"],
            name=entry["name"],
            title=entry["title"],
            question=" ".join((entry.get("question") or "").split()),
            abstract_role=entry.get("abstract_role", "discovery"),
            fulltext_required_for=tuple(entry.get("fulltext_required_for", [])),
            theses=tuple(entry.get("theses", [])),
            key_terms=tuple(tuple(fam) for fam in entry.get("key_terms", [])),
            negative_terms=tuple(entry.get("negative_terms", [])),
            extract=tuple(entry.get("extract", [])),
            require_cooccurrence=bool(entry.get("require_cooccurrence", False)),
        ))
    return out


def get_topic(topic_id: str, topics: list[Topic] | None = None) -> Topic:
    for topic in (topics if topics is not None else load_topics()):
        if topic.id == topic_id:
            return topic
    raise KeyError(f"no such topic: {topic_id}")


def validate_topics(topics: list[Topic]) -> list[str]:
    problems: list[str] = []
    ids = [t.id for t in topics]
    if sorted(ids) != sorted(TOPICS):
        problems.append(f"expected topics {TOPICS}, found {ids}")
    for topic in topics:
        if topic.abstract_role not in EVIDENCE_ROLES:
            problems.append(f"{topic.id}: illegal abstract_role {topic.abstract_role!r}")
        if len(topic.key_terms) < 2:
            problems.append(f"{topic.id}: needs >=2 key-term families")
        if not topic.extract:
            problems.append(f"{topic.id}: extracts nothing")
        if not topic.question:
            problems.append(f"{topic.id}: has no question")
        # A topic that lets abstracts be quantitative would break the package rule.
        if topic.abstract_role == "quantitative_support":
            problems.append(
                f"{topic.id}: abstract_role cannot be quantitative_support — "
                f"an abstract never carries the context a number needs")
    return problems
