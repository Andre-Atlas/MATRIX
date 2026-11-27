from . import data_sets
from .benchmarks import BenchmarksClient
from .client import MatrixClient, MatrixError, init
from .data_sets import DataSetsClient
from .datasets import DatasetsClient
from .mlops import MatrixTrainerCallback, MLOpsClient, SweepExhausted, TrainingRun
from .prompts import Prompt
from .registry import (
    RegistryClient,
    WebhooksClient,
    lineage,
    log_model,
    promote,
    register,
    set_alias,
)
from .sandboxes import SandboxesClient
from .sweeps import agent, sweep
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
    "BenchmarksClient",
    "DataSetsClient",
    "DatasetsClient",
    "MLOpsClient",
    "MatrixClient",
    "MatrixError",
    "MatrixTrainerCallback",
    "Prompt",
    "RegistryClient",
    "Run",
    "SandboxesClient",
    "SpanHelper",
    "SweepExhausted",
    "TrainingRun",
    "WebhooksClient",
    "agent",
    "current_run",
    "current_trace_id",
    "data_sets",
    "finish",
    "init",
    "lineage",
    "log",
    "log_model",
    "log_params",
    "promote",
    "register",
    "session",
    "set_alias",
    "start_run",
    "sweep",
    "trace_agent",
    "trace_chain",
    "trace_llm",
    "trace_retriever",
    "trace_tool",
]
