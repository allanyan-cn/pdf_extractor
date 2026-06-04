"""PDF 坐标矩形辅助模型。

Bounding box helpers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BBox:
    """PDF 坐标系中的矩形区域。

    A PDF coordinate rectangle.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        """校验 bbox 坐标能形成非负矩形。

        Validate that bbox coordinates form a non-negative rectangle.
        """
        # 中文：坐标必须形成非负面积矩形，避免后续 bbox 合并和序列化产生无效来源。
        # English: Coordinates must form a non-negative rectangle for safe bbox merging.
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("BBox coordinates must describe a non-negative rectangle.")

    def to_dict(self) -> dict[str, float]:
        """返回可 JSON 序列化的字典。

        Return a JSON-serializable representation.
        """
        return asdict(self)
