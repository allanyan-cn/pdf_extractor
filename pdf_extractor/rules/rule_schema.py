"""提取规则的数据模型和校验逻辑。

Extraction rule data model and validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_EXTRACT_TYPES = frozenset(
    {"text", "value", "percentage", "number", "date", "time", "table"}
)
NORMALIZABLE_EXTRACT_TYPES = frozenset({"percentage", "number", "date", "time"})
VALID_PARENTHESES_NORMALIZATION = frozenset({"negative", "positive", "preserve"})
VALID_TABLE_STRATEGIES = frozenset({"local", "llm", "auto"})
VALID_LLM_INPUTS = frozenset({"page_image", "text"})


@dataclass(frozen=True)
class ExtractionRule:
    """经过校验的用户提取规则。

    A validated user extraction rule.
    """

    id: str
    name: str
    scope: str | None
    keywords: list[str]
    extract_type: str
    target: str
    priority: int = 0
    within_heading: str | None = None
    table_selector: dict[str, Any] | None = None
    normalization: dict[str, Any] | None = None
    table_strategy: str = "auto"
    llm_input: str = "page_image"

    def __post_init__(self) -> None:
        """执行 dataclass 初始化后的字段一致性校验。

        Validate field consistency after dataclass initialization.
        """
        for field_name in ("id", "name", "target"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")
        if self.scope is not None and not isinstance(self.scope, str):
            raise ValueError("scope must be a string or null.")
        if self.within_heading is not None and not isinstance(self.within_heading, str):
            raise ValueError("within_heading must be a string or null.")
        if self.table_selector is not None:
            self._validate_table_selector(self.table_selector)
            # 中文：table_selector 表示“从表格中抽一个简单值”，不能和整表提取混用。
            # English: table_selector extracts one simple value, so it cannot be used with table output.
            if self.extract_type == "table":
                raise ValueError("table_selector cannot be used with extract_type 'table'.")
        if not isinstance(self.keywords, list) or any(
            not isinstance(keyword, str) or not keyword.strip()
            for keyword in self.keywords
        ):
            raise ValueError("keywords must be a list of non-empty strings.")
        if self.extract_type not in VALID_EXTRACT_TYPES:
            raise ValueError(
                f"extract_type must be one of: {', '.join(sorted(VALID_EXTRACT_TYPES))}."
            )
        if self.table_strategy not in VALID_TABLE_STRATEGIES:
            raise ValueError(
                f"table_strategy must be one of: {', '.join(sorted(VALID_TABLE_STRATEGIES))}."
            )
        if self.llm_input not in VALID_LLM_INPUTS:
            raise ValueError(
                f"llm_input must be one of: {', '.join(sorted(VALID_LLM_INPUTS))}."
            )
        if (
            (self.table_strategy != "auto" or self.llm_input != "page_image")
            and self.extract_type != "table"
        ):
            # 中文：表格策略只影响整表提取，避免普通值规则携带无效 LLM 配置。
            # English: Table strategy only applies to table extraction, not simple value rules.
            raise ValueError(
                "table_strategy and llm_input can only be customized for extract_type 'table'."
            )
        if self.table_strategy == "local" and self.llm_input != "page_image":
            raise ValueError("llm_input cannot be customized when table_strategy is 'local'.")
        if self.normalization is not None:
            # 中文：value/text/table 保留原始语义，不做标准化，避免金额和括号语义被误改。
            # English: value/text/table preserve raw semantics, so normalization is only for explicit types.
            if self.extract_type not in NORMALIZABLE_EXTRACT_TYPES:
                raise ValueError(
                    "normalization can only be used with extract_type: "
                    f"{', '.join(sorted(NORMALIZABLE_EXTRACT_TYPES))}."
                )
            self._validate_normalization(self.normalization)
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("priority must be an integer.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionRule:
        """从字典构建规则，并拒绝缺失字段或未知字段。

        Construct a rule while rejecting missing or unexpected fields.
        """
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
            "within_heading",
            "table_selector",
            "normalization",
            "table_strategy",
            "llm_input",
        }
        unexpected_fields = sorted(set(data) - allowed_fields)
        if unexpected_fields:
            raise ValueError(f"Unexpected rule fields: {', '.join(unexpected_fields)}.")
        required_fields = allowed_fields - {
            "scope",
            "keywords",
            "priority",
            "within_heading",
            "table_selector",
            "normalization",
            "table_strategy",
            "llm_input",
        }
        missing_fields = sorted(required_fields - set(data))
        if missing_fields:
            raise ValueError(f"Missing rule fields: {', '.join(missing_fields)}.")
        return cls(
            id=data["id"],
            name=data["name"],
            scope=data.get("scope"),
            keywords=data.get("keywords", []),
            extract_type=data["extract_type"],
            target=data["target"],
            priority=data.get("priority", 0),
            within_heading=data.get("within_heading"),
            table_selector=data.get("table_selector"),
            normalization=data.get("normalization"),
            table_strategy=data.get("table_strategy", "auto"),
            llm_input=data.get("llm_input", "page_image"),
        )

    @staticmethod
    def _validate_table_selector(selector: dict[str, Any]) -> None:
        """校验表格单元格定位配置。

        Validate table cell selector configuration.
        """
        if not isinstance(selector, dict):
            raise ValueError("table_selector must be a JSON object or null.")
        allowed_fields = {
            "table_title",
            "table_index",
            "row_header",
            "row_index",
            "column_header",
            "column_index",
        }
        unexpected_fields = sorted(set(selector) - allowed_fields)
        if unexpected_fields:
            raise ValueError(
                f"Unexpected table_selector fields: {', '.join(unexpected_fields)}."
            )
        if "row_header" not in selector and "row_index" not in selector:
            raise ValueError("table_selector requires row_header or row_index.")
        if "column_header" not in selector and "column_index" not in selector:
            raise ValueError("table_selector requires column_header or column_index.")
        # 中文：文本字段用于模糊匹配，必须是非空字符串；序号字段使用 1-based 正整数。
        # English: Text fields are non-empty match labels; index fields are 1-based positives.
        for field_name in ("table_title", "row_header", "column_header"):
            if field_name in selector and (
                not isinstance(selector[field_name], str) or not selector[field_name].strip()
            ):
                raise ValueError(f"table_selector.{field_name} must be a non-empty string.")
        for field_name in ("table_index", "row_index", "column_index"):
            if field_name in selector and (
                not isinstance(selector[field_name], int)
                or isinstance(selector[field_name], bool)
                or selector[field_name] < 1
            ):
                raise ValueError(f"table_selector.{field_name} must be a positive integer.")

    @staticmethod
    def _validate_normalization(normalization: dict[str, Any]) -> None:
        """校验标准化配置。

        Validate normalization configuration.
        """
        if not isinstance(normalization, dict):
            raise ValueError("normalization must be a JSON object or null.")
        allowed_fields = {"parentheses"}
        unexpected_fields = sorted(set(normalization) - allowed_fields)
        if unexpected_fields:
            raise ValueError(
                f"Unexpected normalization fields: {', '.join(unexpected_fields)}."
            )
        parentheses = normalization.get("parentheses")
        if parentheses is not None and parentheses not in VALID_PARENTHESES_NORMALIZATION:
            raise ValueError(
                "normalization.parentheses must be one of: "
                f"{', '.join(sorted(VALID_PARENTHESES_NORMALIZATION))}."
            )
