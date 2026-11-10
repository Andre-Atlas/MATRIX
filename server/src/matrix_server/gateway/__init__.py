"""AI Gateway (Layer 2 Blueprint SOTA) for MATRIX.

Components:
- CircuitBreaker: Provider failure isolation with CLOSED/OPEN/HALF_OPEN transitions
- GatewayCache: Exact and semantic prompt caching with token and USD cost tracking
- GatewayRateLimiter: RPM and TPM quotas per project
- GatewayRouter: OpenAI-compatible router with cascading fallback and telemetry
"""

from matrix_server.gateway.cache import GatewayCache, compute_cache_key
from matrix_server.gateway.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.gateway.router import (
    AnthropicProvider,
    BaseProvider,
    GatewayRequestLog,
    GatewayRouter,
    MockProvider,
    OpenAIProvider,
    get_default_gateway_router,
)

__all__ = [
    "AnthropicProvider",
    "BaseProvider",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "GatewayCache",
    "GatewayRateLimiter",
    "GatewayRequestLog",
    "GatewayRouter",
    "MockProvider",
    "OpenAIProvider",
    "compute_cache_key",
    "get_default_gateway_router",
]
