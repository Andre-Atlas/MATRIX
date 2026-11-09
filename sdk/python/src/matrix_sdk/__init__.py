from .client import MatrixClient, MatrixError, init
from .prompts import Prompt
from .tracing import (
    SpanHelper,
    current_trace_id,
    session,
    trace_agent,
    trace_chain,
    trace_llm,
    trace_retriever,
    trace_tool,
)

__version__ = "0.1.0"

__all__ = [
    "MatrixClient", "MatrixError", "Prompt", "SpanHelper", "current_trace_id", "init", "session",
    "trace_agent", "trace_chain", "trace_llm", "trace_retriever", "trace_tool",
]
