from .benchmarks import BenchmarksClient
from .client import MatrixClient, MatrixError, init
from .datasets import DatasetsClient
from .mlops import MatrixTrainerCallback, MLOpsClient, SweepExhausted, TrainingRun
from .prompts import Prompt
from .sandboxes import SandboxesClient
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
    "BenchmarksClient",
    "DatasetsClient",
    "MLOpsClient",
    "MatrixClient",
    "MatrixError",
    "MatrixTrainerCallback",
    "Prompt",
    "SandboxesClient",
    "SpanHelper",
    "SweepExhausted",
    "TrainingRun",
    "current_trace_id",
    "init",
    "session",
    "trace_agent",
    "trace_chain",
    "trace_llm",
    "trace_retriever",
    "trace_tool",
]
