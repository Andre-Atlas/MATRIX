"""Benchmarks module for MATRIX: LM-Eval & VLMEvalKit runners, catalog, and adapters."""

from matrix_server.benchmarks.runner import (
    API_KEY_ENV_RE,
    BENCHMARK_API_KEY_ENV_PREFIX,
    BenchmarkCredentialError,
    BenchmarkEngine,
    EngineUnavailable,
    HttpBenchmarkEngine,
    LmEvalBenchmarkEngine,
    LocalSimBenchmarkEngine,
    VlmEvalBenchmarkEngine,
    get_benchmark_engine,
    run_benchmark_job,
    sanitize_runner_config,
    validate_runner_config,
)

__all__ = [
    "API_KEY_ENV_RE",
    "BENCHMARK_API_KEY_ENV_PREFIX",
    "BenchmarkCredentialError",
    "BenchmarkEngine",
    "EngineUnavailable",
    "HttpBenchmarkEngine",
    "LmEvalBenchmarkEngine",
    "LocalSimBenchmarkEngine",
    "VlmEvalBenchmarkEngine",
    "get_benchmark_engine",
    "run_benchmark_job",
    "sanitize_runner_config",
    "validate_runner_config",
]
