"""API endpoints for AI Gateway (MATRIX Layer 2 Blueprint SOTA / T8)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from matrix_server.auth import Principal, Role, require
from matrix_server.gateway.router import GatewayRouter, get_default_gateway_router

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])

ViewerAuth = Annotated[Principal, Depends(require(Role.VIEWER))]
MemberAuth = Annotated[Principal, Depends(require(Role.MEMBER))]


def get_gateway(request: Request) -> GatewayRouter:
    """Dependency provider for GatewayRouter instance, binding the app's DB session_factory."""
    if hasattr(request.app.state, "gateway_router") and request.app.state.gateway_router is not None:
        gw: GatewayRouter = request.app.state.gateway_router
    else:
        gw = get_default_gateway_router()

    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is not None and gw.session_factory is None:
        gw.bind_session_factory(session_factory)
    return gw


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
    provider: str | None = Field(default=None, description="Optional explicit provider override (e.g. 'mock')")

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
    route: str = "/api/v1/gateway/chat/completions"
    model: str
    provider: str
    status: str  # CACHE_HIT, DIRECT, FALLBACK, ERROR
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
    simulated: bool = False
    error_sanitized: str | None = None


class GatewayLogsResponse(BaseModel):
    logs: list[RequestLogItem]
    total: int


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    response: Response,
    principal: MemberAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """OpenAI-compatible chat completions endpoint with caching, rate limiting, and fallback."""
    serialized_messages = [m.model_dump() for m in payload.messages]
    extra_params = dict(payload.model_extra or {})

    result, cache_status, provider_name = await gateway.route_chat_completion(
        project_id=principal.project_id,
        model=payload.model,
        messages=serialized_messages,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        provider=payload.provider,
        stream=payload.stream,
        **extra_params,
    )

    x_matrix = result.get("x_matrix") or {}
    is_simulated = bool(x_matrix.get("simulated", False))
    warnings = x_matrix.get("warnings") or []

    # Expose Gateway routing metadata in response headers
    response.headers["x-matrix-cache"] = cache_status
    response.headers["x-matrix-provider"] = provider_name
    response.headers["x-matrix-simulated"] = "true" if is_simulated else "false"
    if warnings:
        response.headers["x-matrix-warnings"] = ", ".join(str(w) for w in warnings)

    return result


@router.get("/stats", response_model=GatewayStatsResponse)
def get_gateway_stats(
    principal: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns project-scoped gateway statistics from persistent logs."""
    return gateway.get_stats(project_id=principal.project_id)


@router.get("/providers", response_model=GatewayProvidersResponse)
def get_gateway_providers(
    principal: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns provider health and project-scoped request metrics."""
    return {"providers": gateway.get_providers_status(project_id=principal.project_id)}


@router.get("/rules", response_model=GatewayRulesResponse)
def get_gateway_rules(
    principal: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
) -> dict[str, Any]:
    """Returns active gateway routing rules scoped to the current project."""
    rules = gateway.get_rules(project_id=principal.project_id)
    return {"rules": rules, "total": len(rules)}


@router.get("/logs", response_model=GatewayLogsResponse)
def get_gateway_logs(
    principal: ViewerAuth,
    gateway: Annotated[GatewayRouter, Depends(get_gateway)],
    limit: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """Returns recent project-scoped request audit logs."""
    logs = gateway.get_logs(limit=limit, project_id=principal.project_id)
    return {"logs": logs, "total": len(logs)}
