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
from .tracking import Run, current_run, finish, log, log_params, start_run

__version__ = "0.1.0"

__all__ = [
    "MatrixClient", "MatrixError", "Prompt", "Run", "SpanHelper", "current_run", "current_trace_id", "finish",
    "init", "log", "log_params", "session", "start_run", "trace_agent", "trace_chain", "trace_llm",
    "trace_retriever", "trace_tool",
]
