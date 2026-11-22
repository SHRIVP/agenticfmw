"""Optional Langfuse tracing: callbacks() is empty unless keys are configured."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def callbacks() -> list:
    """Called per request. A broken Langfuse setup turns tracing off; it never fails the run."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return []
    try:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]
    except Exception as exc:  # log the type only: messages may echo keys
        log.warning("Langfuse tracing disabled: %s", exc.__class__.__name__)
        return []
