"""
coordinates.py — PDF coordinate types and parsing.

GROBID encodes element positions as a coords attribute:
    coords="page,x,y,width,height[;page,x,y,width,height...]"

Units are PDF points (1/72 inch).  Multi-segment elements (e.g. a sentence
spanning two lines) carry semicolon-separated segments, potentially across
pages (rare but valid for floats near page breaks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    """One rectangular region on a single PDF page."""
    page: int     # 1-indexed
    x:    float   # left edge in PDF points
    y:    float   # top edge in PDF points
    w:    float   # width
    h:    float   # height

    def area(self) -> float:
        return self.w * self.h

    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def distance_to(self, other: BoundingBox) -> float:
        """Euclidean distance between centres; inf when on different pages."""
        if self.page != other.page:
            return math.inf
        cx1, cy1 = self.center()
        cx2, cy2 = other.center()
        return math.hypot(cx1 - cx2, cy1 - cy2)

    def overlaps(self, other: BoundingBox) -> bool:
        if self.page != other.page:
            return False
        return not (
            self.x + self.w < other.x or
            other.x + other.w < self.x or
            self.y + self.h < other.y or
            other.y + other.h < self.y
        )


@dataclass(frozen=True)
class Coordinates:
    """
    All bounding boxes for one TEI element.

    An element can span multiple segments (multi-line sentence) or even
    multiple pages (split float).  Most elements have exactly one box.
    """
    boxes: tuple[BoundingBox, ...]

    @property
    def primary_page(self) -> int:
        return self.boxes[0].page if self.boxes else 0

    @property
    def pages(self) -> frozenset[int]:
        return frozenset(b.page for b in self.boxes)

    def is_near(self, other: Coordinates, threshold: float = 150.0) -> bool:
        """True if the closest pair of boxes is within threshold PDF points."""
        for b1 in self.boxes:
            for b2 in other.boxes:
                if b1.distance_to(b2) <= threshold:
                    return True
        return False

    def min_distance_to(self, other: Coordinates) -> float:
        if not self.boxes or not other.boxes:
            return math.inf
        return min(
            b1.distance_to(b2)
            for b1 in self.boxes
            for b2 in other.boxes
        )

    def primary_bbox(self) -> tuple[float, float, float, float] | None:
        """First box as (x, y, w, h) for chunk serialisation."""
        if not self.boxes:
            return None
        b = self.boxes[0]
        return (b.x, b.y, b.w, b.h)


def parse_coords(coords_str: str | None) -> Coordinates | None:
    """
    Parse a GROBID coords attribute string into a Coordinates object.

    Returns None when the string is absent, empty, or entirely unparseable.
    Individual malformed segments are silently skipped so partial results
    are still returned.
    """
    if not coords_str:
        return None
    boxes: list[BoundingBox] = []
    for segment in coords_str.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split(",")
        if len(parts) != 5:
            continue
        try:
            boxes.append(BoundingBox(
                page=int(parts[0]),
                x=float(parts[1]),
                y=float(parts[2]),
                w=float(parts[3]),
                h=float(parts[4]),
            ))
        except (ValueError, IndexError):
            continue
    return Coordinates(boxes=tuple(boxes)) if boxes else None
