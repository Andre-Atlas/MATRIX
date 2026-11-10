import type {
  CircuitBreakerState,
  GatewayLogEntry,
  GatewayLogStatus,
  GatewayProviderHealth,
  GatewayStats,
} from "./types";

/**
 * Normalizes circuit state to lowercase standard values.
 */
export function normalizeCircuitState(state: CircuitBreakerState): "closed" | "half_open" | "open" {
  const s = String(state).toLowerCase().replace("-", "_");
  if (s === "open") return "open";
  if (s === "half_open") return "half_open";
  return "closed";
}

/**
 * Returns style configurations for Circuit Breaker states:
 * - closed: healthy, traffic passing (green)
 * - half_open: probing / recovery trial (yellow/amber)
 * - open: tripped, short-circuiting calls (red)
 */
export function getCircuitStateConfig(state: CircuitBreakerState): {
  key: "closed" | "half_open" | "open";
  label: string;
  badgeClass: string;
  dotClass: string;
  borderClass: string;
  textClass: string;
  description: string;
} {
  const norm = normalizeCircuitState(state);
  switch (norm) {
    case "closed":
      return {
        key: "closed",
        label: "Closed (Normal)",
        badgeClass: "bg-emerald-950/60 border-emerald-800/70 text-emerald-300",
        dotClass: "bg-emerald-400",
        borderClass: "border-emerald-800/40",
        textClass: "text-emerald-400",
        description: "All upstream traffic flowing directly. Failure rate within threshold.",
      };
    case "half_open":
      return {
        key: "half_open",
        label: "Half-Open (Testing)",
        badgeClass: "bg-amber-950/60 border-amber-800/70 text-amber-300",
        dotClass: "bg-amber-400",
        borderClass: "border-amber-800/40",
        textClass: "text-amber-400",
        description: "Canary probing in progress to evaluate if provider has stabilized.",
      };
    case "open":
      return {
        key: "open",
        label: "Open (Tripped)",
        badgeClass: "bg-rose-950/60 border-rose-800/70 text-rose-300",
        dotClass: "bg-rose-400",
        borderClass: "border-rose-800/40",
        textClass: "text-rose-400",
        description: "Circuit tripped due to consecutive errors or timeouts. Diverting to fallback chain.",
      };
  }
}

/**
 * Returns visual styles for provider health.
 */
export function getProviderHealthConfig(status: GatewayProviderHealth): {
  label: string;
  badgeClass: string;
  dotClass: string;
} {
  switch (status) {
    case "healthy":
      return {
        label: "Operational",
        badgeClass: "bg-emerald-950/40 border-emerald-800/60 text-emerald-300",
        dotClass: "bg-emerald-400",
      };
    case "degraded":
      return {
        label: "Degraded",
        badgeClass: "bg-amber-950/40 border-amber-800/60 text-amber-300",
        dotClass: "bg-amber-400",
      };
    case "outage":
      return {
        label: "Outage",
        badgeClass: "bg-rose-950/40 border-rose-800/60 text-rose-300",
        dotClass: "bg-rose-400",
      };
  }
}

/**
 * Returns visual styles for Gateway log status badges.
 */
export function getLogStatusConfig(status: GatewayLogStatus): {
  label: string;
  badgeClass: string;
  dotClass: string;
} {
  switch (status) {
    case "CACHE_HIT":
      return {
        label: "CACHE_HIT",
        badgeClass: "bg-emerald-950/60 border-emerald-800/60 text-emerald-300",
        dotClass: "bg-emerald-400",
      };
    case "DIRECT":
      return {
        label: "DIRECT",
        badgeClass: "bg-sky-950/60 border-sky-800/60 text-sky-300",
        dotClass: "bg-sky-400",
      };
    case "FALLBACK":
      return {
        label: "FALLBACK",
        badgeClass: "bg-amber-950/60 border-amber-800/60 text-amber-300",
        dotClass: "bg-amber-400",
      };
    case "ERROR":
      return {
        label: "ERROR",
        badgeClass: "bg-rose-950/60 border-rose-800/60 text-rose-300",
        dotClass: "bg-rose-400",
      };
  }
}

/**
 * Formats USD dollar amounts.
 */
export function formatUSD(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(amount);
}

/**
 * Formats latency duration in ms or seconds.
 */
export function formatLatencyMs(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(2)}s`;
  }
  return `${Math.round(ms)}ms`;
}

/**
 * Formats token counts in compact or standard form.
 */
export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(2)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(1)}k`;
  }
  return tokens.toLocaleString("en-US");
}

/**
 * Filters gateway logs based on query, status, and provider.
 */
export function filterGatewayLogs(
  logs: GatewayLogEntry[],
  filters: {
    status?: string;
    provider?: string;
    search?: string;
  },
): GatewayLogEntry[] {
  return logs.filter((log) => {
    if (filters.status && filters.status !== "all" && log.status !== filters.status) {
      return false;
    }
    if (filters.provider && filters.provider !== "all" && log.provider !== filters.provider) {
      return false;
    }
    if (filters.search) {
      const q = filters.search.toLowerCase().trim();
      const routeName = (log.route_name ?? "").toLowerCase();
      const reqModel = (log.requested_model ?? log.model ?? "").toLowerCase();
      const srvModel = (log.served_model ?? log.model ?? "").toLowerCase();
      const reqId = (log.request_id ?? log.id).toLowerCase();
      const prov = (log.provider ?? "").toLowerCase();
      const match =
        routeName.includes(q) ||
        reqModel.includes(q) ||
        srvModel.includes(q) ||
        reqId.includes(q) ||
        prov.includes(q);
      if (!match) return false;
    }
    return true;
  });
}

/**
 * Computes live metrics from log slice.
 */
export function computeGatewayStatsFromLogs(logs: GatewayLogEntry[]): Partial<GatewayStats> {
  if (!logs.length) {
    return {
      total_requests: 0,
      cache_hits: 0,
      cache_hit_rate: 0,
      saved_cost_usd: 0,
      total_cost_usd: 0,
      latency_p50_ms: 0,
      latency_p99_ms: 0,
    };
  }

  const total = logs.length;
  const hits = logs.filter((l) => l.status === "CACHE_HIT" || l.cache_hit).length;
  const savedCost = logs.reduce((acc, l) => acc + (l.saved_cost_usd ?? l.cost_saved_usd ?? 0), 0);
  const totalCost = logs.reduce((acc, l) => acc + (l.cost_usd ?? 0), 0);

  const latencies = logs.map((l) => l.latency_ms).sort((a, b) => a - b);
  const p50 = latencies[Math.floor(latencies.length * 0.5)] ?? 0;
  const p99 = latencies[Math.floor(latencies.length * 0.99)] ?? latencies[latencies.length - 1] ?? 0;

  return {
    total_requests: total,
    cache_hits: hits,
    cache_hit_rate: total > 0 ? hits / total : 0,
    saved_cost_usd: savedCost,
    total_cost_usd: totalCost,
    latency_p50_ms: p50,
    latency_p99_ms: p99,
  };
}
