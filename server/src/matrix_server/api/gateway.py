"""API endpoints for AI Gateway (MATRIX Layer 2 Blueprint SOTA)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from matrix_server.auth import AuthContext, require_role
from matrix_server.gateway.router import GatewayRouter, get_default_gateway_router

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])

ViewerAuth = Annotated[AuthContext, Depends(require_role("viewer"))]
MemberAuth = Annotated[AuthContext, Depends(require_role("member"))]


def get_gateway(request: Request) -> GatewayRouter:
    """Dependency provider for GatewayRouter instance."""
    if hasattr(request.app.state, "gateway_router") and request.app.state.gateway_router is not None:
        return request.app.state.gateway_router
    return get_default_gateway_router()


class ChatMessage(BaseModel):
    role: str
    content: str
    name: str | None = None

    model_config = ConfigDict(extra="allow")


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="gpt-4o", description="LLM model identifier")
    messages: list[ChatMessage] = Field(..., min_length=1, description="List of messages")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stream: bool = Field(default=False)

    model_config = ConfigDict(extra="allow")


class GatewayStatsResponse(BaseModel):
    total_requests: int
    cache_hits: int
    cache_hit_rate: float
    tokens_saved: int
    cost_saved_usd: float
    saved_cost_usd: float
    total_cost_usd: float
    avg_latency_ms: float
    latency_p50_ms: float
    latency_p99_ms: float
    active_providers: int
    healthy_providers: int
    fallback_events_24h: int


class ProviderStatusItem(BaseModel):
    name: str
    circuit_breaker: str
    failure_count: int
    failure_threshold: int
    avg_latency_ms: float
    total_requests: int
    models: list[str]


class GatewayProvidersResponse(BaseModel):
    providers: list[ProviderStatusItem]


class GatewayRoutingRuleItem(BaseModel):
    id: str
    name: str
    description: str
    match_pattern: str
    primary_provider: str
    primary_model: str
    fallback_chain: list[str]
    semantic_cache_enabled: bool
    cache_similarity_threshold: float
    rate_limit_rpm: int
    retry_count: int
    timeout_ms: int
    is_active: bool


class GatewayRulesResponse(BaseModel):
    rules: list[GatewayRoutingRuleItem]
    total: int


class RequestLogItem(BaseModel):
    id: str
    request_id: str
    timestamp: str
    project_id: str
    model: str
    provider: str
    status: str  # CACHE_HIT, DIRECT, FALLBACK
    status_code: int
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens: dict[str, int]
    cost_usd: float
    saved_cost_usd: float
    cache_hit: bool
    fallback_triggered: bool


class GatewayLogsResponse(BaseModel):
    logs: list[RequestLogItem]
    total: int


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    response: Response,
    ctx: MemberAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """OpenAI-compatible chat completions endpoint with caching, rate limiting, and fallback."""
    serialized_messages = [m.model_dump() for m in payload.messages]

    result, cache_status, provider_name = await gateway.route_chat_completion(
        project_id=ctx.project_id,
        model=payload.model,
        messages=serialized_messages,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )

    # Expose Gateway routing status in response headers
    response.headers["x-matrix-cache"] = cache_status
    response.headers["x-matrix-provider"] = provider_name

    return result


@router.get("/stats", response_model=GatewayStatsResponse)
def get_gateway_stats(
    ctx: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns gateway aggregate statistics: requests, cache hit rate, saved tokens, and saved cost."""
    return gateway.get_stats()


@router.get("/providers", response_model=GatewayProvidersResponse)
def get_gateway_providers(
    ctx: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns status of providers (OpenAI, Anthropic, Mock), circuit breakers, and latencies."""
    return {"providers": gateway.get_providers_status()}


@router.get("/rules", response_model=GatewayRulesResponse)
def get_gateway_rules(
    ctx: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns active gateway routing and cascading fallback rules."""
    rules = gateway.get_rules()
    return {"rules": rules, "total": len(rules)}


@router.get("/logs", response_model=GatewayLogsResponse)
def get_gateway_logs(
    ctx: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
    limit: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """Returns recent routed request logs with latency, tokens, provider, and cache/fallback status."""
    logs = gateway.get_logs(limit=limit, project_id=ctx.project_id)
    return {"logs": logs, "total": len(logs)}

