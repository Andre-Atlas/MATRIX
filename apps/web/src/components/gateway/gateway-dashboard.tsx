"use client";

import { useState } from "react";
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
}

export function GatewayDashboard({
  stats,
  providers,
  rules,
  logs,
}: GatewayDashboardProps) {
  const [activeTab, setActiveTab] = useState<"all" | "providers" | "rules" | "logs">("all");

  return (
    <div className="space-y-6">
      {/* KPI Metrics */}
      <GatewayMetrics stats={stats} />

      {/* Navigation Filter Tabs */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveTab("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "all"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Visão Completa
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("providers")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "providers"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Provedores & Circuit Breakers ({providers.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("rules")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "rules"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Cadeias de Fallback ({rules.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("logs")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === "logs"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Logs Recentes ({logs.length})
          </button>
        </div>

        <div className="hidden sm:flex items-center gap-2 text-xs text-zinc-500 font-mono">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
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
