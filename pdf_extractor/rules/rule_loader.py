"""JSON 提取规则加载器。

JSON extraction rule loading.
"""

from __future__ import annotations

import json
from pathlib import Path

from pdf_extractor.rules.rule_schema import ExtractionRule


class RuleLoader:
    """从 JSON 文件加载并校验提取规则。

    Load and validate extraction rules from JSON files.
    """

    def load(self, rule_path: str) -> list[ExtractionRule]:
        """加载包含非空 ``rules`` 列表的 JSON 文件。

        Load a JSON object containing a non-empty ``rules`` list.
        """
        path = Path(rule_path)
        if not path.is_file():
            raise FileNotFoundError(f"Rule file does not exist: {rule_path}")
        with path.open(encoding="utf-8") as rule_file:
            payload = json.load(rule_file)
        if not isinstance(payload, dict) or set(payload) != {"rules"}:
            raise ValueError("Rule file must contain exactly one top-level 'rules' field.")
        if not isinstance(payload["rules"], list) or not payload["rules"]:
            raise ValueError("'rules' must be a non-empty list.")
        rules = [ExtractionRule.from_dict(rule) for rule in payload["rules"]]
        rule_ids = [rule.id for rule in rules]
        # 中文：重复 rule id 会让 diagnostics 和输出难以追踪，因此在加载阶段直接拒绝。
        # English: Duplicate rule ids make diagnostics ambiguous, so reject them early.
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Rule ids must be unique.")
        return sorted(rules, key=lambda rule: (-rule.priority, rule.id))
