"""Optional OpenAI SDK client construction."""

from __future__ import annotations

from typing import Any


def create_openai_client(**kwargs: Any) -> Any:
    """Construct the optional OpenAI client without making it a core dependency."""
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "OpenAI SDK is not installed. Install the optional 'llm' dependency."
        ) from error
    try:
        return OpenAI(**kwargs)
    except Exception as error:
        raise RuntimeError("Failed to configure the optional OpenAI client.") from error
