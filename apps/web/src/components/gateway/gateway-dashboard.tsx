"use client";

import { useState } from "react";
import { SimulatedBadge } from "@/components/ui";
import type {
  GatewayLogEntry,
  GatewayProviderStatus,
  GatewayRoutingRule,
  GatewayStats,
} from "@/lib/types";
import { GatewayLogsTable } from "./gateway-logs-table";
import { GatewayMetrics } from "./gateway-metrics";
import { ProviderCircuitBoard } from "./provider-circuit-board";
import { RoutingRulesPanel } from "./routing-rules-panel";

export interface GatewayDashboardProps {
  stats: GatewayStats;
  providers: GatewayProviderStatus[];
  rules: GatewayRoutingRule[];
  logs: GatewayLogEntry[];
  isSimulated?: boolean;
}

export function GatewayDashboard({
  stats,
  providers,
  rules,
  logs,
  isSimulated = false,
}: GatewayDashboardProps) {
  const [activeTab, setActiveTab] = useState<"all" | "providers" | "rules" | "logs">("all");
  const showSimulatedBanner =
    isSimulated ||
    logs.some((l) => l.simulated === true || l.provider.toLowerCase() === "mock") ||
    providers.some((p) => p.provider?.toLowerCase() === "mock");

  return (
    <div className="space-y-6">
      {showSimulatedBanner && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-700/60 bg-amber-950/30 px-3.5 py-2.5 text-xs text-amber-200"
        >
          <SimulatedBadge reason="mock gateway" />
          <span>
            AI Gateway telemetry, provider circuit breakers, and routing rules are displaying simulated/mock data.
          </span>
        </div>
      )}

      {/* KPI Metrics */}
      <GatewayMetrics stats={stats} />

      {/* Navigation Filter Tabs */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <div role="tablist" aria-label="Gateway views" className="flex items-center gap-1">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "all"}
            onClick={() => setActiveTab("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "all"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            All Views
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "providers"}
            onClick={() => setActiveTab("providers")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "providers"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Providers &amp; Circuit Breakers ({providers.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "rules"}
            onClick={() => setActiveTab("rules")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "rules"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Fallback Rules ({rules.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "logs"}
            onClick={() => setActiveTab("logs")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "logs"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Recent Logs ({logs.length})
          </button>
        </div>

        <div className="hidden sm:flex items-center gap-2 text-xs text-zinc-400 font-mono">
          <span aria-hidden="true" className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" />
          AI Gateway v2.4 (Active Proxy)
        </div>
      </div>

      {/* Tab Panels */}
      {(activeTab === "all" || activeTab === "providers") && (
        <ProviderCircuitBoard providers={providers} />
      )}

      {(activeTab === "all" || activeTab === "rules") && (
        <RoutingRulesPanel rules={rules} />
      )}

      {(activeTab === "all" || activeTab === "logs") && (
        <GatewayLogsTable logs={logs} />
      )}
    </div>
  );
}
