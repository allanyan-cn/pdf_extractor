"""Extraction rule data model and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_EXTRACT_TYPES = frozenset({"text", "value", "table"})


@dataclass(frozen=True)
class ExtractionRule:
    """A validated user extraction rule."""

    id: str
    name: str
    scope: str | None
    keywords: list[str]
    extract_type: str
    target: str
    priority: int = 0

    def __post_init__(self) -> None:
        for field_name in ("id", "name", "target"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")
        if self.scope is not None and not isinstance(self.scope, str):
            raise ValueError("scope must be a string or null.")
        if (
            not isinstance(self.keywords, list)
            or not self.keywords
            or any(not isinstance(keyword, str) or not keyword.strip() for keyword in self.keywords)
        ):
            raise ValueError("keywords must be a non-empty list of non-empty strings.")
        if self.extract_type not in VALID_EXTRACT_TYPES:
            raise ValueError(
                f"extract_type must be one of: {', '.join(sorted(VALID_EXTRACT_TYPES))}."
            )
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("priority must be an integer.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionRule:
        """Construct a rule while rejecting missing or unexpected fields."""
        if not isinstance(data, dict):
            raise ValueError("Each rule must be a JSON object.")
        allowed_fields = {
            "id",
            "name",
            "scope",
            "keywords",
            "extract_type",
            "target",
            "priority",
        }
        unexpected_fields = sorted(set(data) - allowed_fields)
        if unexpected_fields:
            raise ValueError(f"Unexpected rule fields: {', '.join(unexpected_fields)}.")
        required_fields = allowed_fields - {"scope", "priority"}
        missing_fields = sorted(required_fields - set(data))
        if missing_fields:
            raise ValueError(f"Missing rule fields: {', '.join(missing_fields)}.")
        return cls(
            id=data["id"],
            name=data["name"],
            scope=data.get("scope"),
            keywords=data["keywords"],
            extract_type=data["extract_type"],
            target=data["target"],
            priority=data.get("priority", 0),
        )
