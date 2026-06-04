"""项目日志配置。

Project logging configuration.
"""

from __future__ import annotations

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """为 CLI 使用者配置简洁日志格式。

    Configure concise application logging for CLI consumers.
    """
    # 中文：统一日志格式，让解析、规则执行和输出路径信息在终端中容易扫描。
    # English: A shared format keeps parser, rule, and output messages easy to scan.
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
