"""AI Gateway (Layer 2 Blueprint SOTA / T8) for MATRIX.

Components:
- CircuitBreaker: Provider failure isolation with CLOSED/OPEN/HALF_OPEN transitions
- GatewayCache: Project-isolated exact and semantic prompt caching with token and USD cost tracking
- GatewayRateLimiter: Shared DB-backed fixed-60s RPM and TPM quotas per project
- GatewayRouter: OpenAI-compatible router with explicit mock policy, allowlist, caps, and DB audit log
- GatewayRateWindow / GatewayRequest: SQLAlchemy models for shared worker state
"""

from matrix_server.gateway.cache import GatewayCache, compute_cache_key
from matrix_server.gateway.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from matrix_server.gateway.models import GatewayRateWindow, GatewayRequest
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.gateway.router import (
    AnthropicProvider,
    BaseProvider,
    GatewayRequestLog,
    GatewayRouter,
    MockProvider,
    OpenAIProvider,
    ProviderExecutionError,
    ProviderNotConfiguredError,
    filter_provider_params,
    get_default_gateway_router,
    sanitize_provider_error,
)

__all__ = [
    "AnthropicProvider",
    "BaseProvider",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "GatewayCache",
    "GatewayRateLimiter",
    "GatewayRateWindow",
    "GatewayRequest",
    "GatewayRequestLog",
    "GatewayRouter",
    "MockProvider",
    "OpenAIProvider",
    "ProviderExecutionError",
    "ProviderNotConfiguredError",
    "compute_cache_key",
    "filter_provider_params",
    "get_default_gateway_router",
    "sanitize_provider_error",
]
