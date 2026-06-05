"""可选 OpenAI SDK client 构造工具。

Optional OpenAI SDK client construction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def load_dotenv_if_present(path: str | Path | None = None) -> None:
    """加载简单 .env 文件，但不覆盖已存在的环境变量。

    Load a simple .env file without overriding existing environment variables.
    """
    candidates = [Path(path)] if path else [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def create_openai_client(**kwargs: Any) -> Any:
    """构造可选 OpenAI client，同时避免把它变成核心依赖。

    Construct the optional OpenAI client without making it a core dependency.
    """
    load_dotenv_if_present()
    try:
        from openai import OpenAI
    except ImportError as error:
        # 中文：LLM 能力是可选功能，未安装 SDK 时给出明确安装提示。
        # English: LLM support is optional, so missing SDKs produce a clear install hint.
        raise RuntimeError(
            "OpenAI SDK is not installed. Install the optional 'llm' dependency."
        ) from error
    if "api_key" not in kwargs and os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
    if "base_url" not in kwargs and os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    try:
        return OpenAI(**kwargs)
    except Exception as error:
        # 中文：屏蔽底层 SDK 配置细节，向 CLI 用户返回更稳定的错误消息。
        # English: Hide low-level SDK setup details behind a stable CLI-facing error.
        raise RuntimeError("Failed to configure the optional OpenAI client.") from error
