"""Bounding box helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BBox:
    """A PDF coordinate rectangle."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("BBox coordinates must describe a non-negative rectangle.")

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-serializable representation."""
        return asdict(self)
