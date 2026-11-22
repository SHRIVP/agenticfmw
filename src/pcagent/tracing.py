"""Optional Langfuse tracing: callbacks() is empty unless keys are configured."""

from __future__ import annotations

import os


def callbacks() -> list:
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]
