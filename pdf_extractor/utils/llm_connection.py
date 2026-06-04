"""可选 OpenAI SDK client 构造工具。

Optional OpenAI SDK client construction.
"""

from __future__ import annotations

from typing import Any


def create_openai_client(**kwargs: Any) -> Any:
    """构造可选 OpenAI client，同时避免把它变成核心依赖。

    Construct the optional OpenAI client without making it a core dependency.
    """
    try:
        from openai import OpenAI
    except ImportError as error:
        # 中文：LLM 能力是可选功能，未安装 SDK 时给出明确安装提示。
        # English: LLM support is optional, so missing SDKs produce a clear install hint.
        raise RuntimeError(
            "OpenAI SDK is not installed. Install the optional 'llm' dependency."
        ) from error
    try:
        return OpenAI(**kwargs)
    except Exception as error:
        # 中文：屏蔽底层 SDK 配置细节，向 CLI 用户返回更稳定的错误消息。
        # English: Hide low-level SDK setup details behind a stable CLI-facing error.
        raise RuntimeError("Failed to configure the optional OpenAI client.") from error
