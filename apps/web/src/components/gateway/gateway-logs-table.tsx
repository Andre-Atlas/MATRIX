"use client";

import { useMemo, useState } from "react";
import type { GatewayLogEntry, GatewayLogStatus } from "@/lib/types";
import {
  filterGatewayLogs,
  formatLatencyMs,
  formatTokenCount,
  formatUSD,
  getLogStatusConfig,
} from "@/lib/gateway";

export interface GatewayLogsTableProps {
  logs: GatewayLogEntry[];
}

export function GatewayLogsTable({ logs }: GatewayLogsTableProps) {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [providerFilter, setProviderFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

  const providers = useMemo(() => {
    const set = new Set<string>();
    for (const log of logs) {
      if (log.provider) set.add(log.provider);
    }
    return Array.from(set).sort();
  }, [logs]);

  const filteredLogs = useMemo(() => {
    return filterGatewayLogs(logs, {
      status: statusFilter,
      provider: providerFilter,
      search,
    });
  }, [logs, statusFilter, providerFilter, search]);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      {/* Header and Filter Controls */}
      <div className="flex flex-col gap-4 border-b border-zinc-800/80 pb-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
              <svg
                className="h-4 w-4 text-emerald-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              Logs de Requisições Recentes do Gateway
            </h2>
            <p className="text-xs text-zinc-400">
              Rastreamento em tempo real de inferências, decisões de cache semântico e ativação de fallbacks resilientes.
            </p>
          </div>
          <div className="text-xs text-zinc-400 font-mono">
            Exibindo {filteredLogs.length} de {logs.length} requisições
          </div>
        </div>

        {/* Filter Toolbar */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Search box */}
          <div className="relative min-w-[220px] flex-1 sm:flex-initial">
            <input
              type="text"
              placeholder="Buscar por modelo, ID ou rota..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950/80 px-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:border-zinc-700 focus:outline-none focus:ring-1 focus:ring-zinc-700"
            />
          </div>

          {/* Status buttons */}
          <div className="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-950/60 p-0.5">
            {(["all", "CACHE_HIT", "DIRECT", "FALLBACK", "ERROR"] as const).map((st) => (
              <button
                key={st}
                type="button"
                onClick={() => setStatusFilter(st)}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  statusFilter === st
                    ? "bg-zinc-800 text-zinc-100 shadow-sm"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {st === "all" ? "Todos" : st}
              </button>
            ))}
          </div>

          {/* Provider Select */}
          <select
            value={providerFilter}
            onChange={(e) => setProviderFilter(e.target.value)}
            aria-label="Filtrar por Provedor"
            className="rounded-lg border border-zinc-800 bg-zinc-950/80 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-zinc-700 focus:outline-none"
          >
            <option value="all">Todos os Provedores</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          {(statusFilter !== "all" || providerFilter !== "all" || search) && (
            <button
              type="button"
              onClick={() => {
                setStatusFilter("all");
                setProviderFilter("all");
                setSearch("");
              }}
              className="text-xs text-zinc-400 hover:text-zinc-200 underline"
            >
              Limpar filtros
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-zinc-800/80 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              <th className="py-2.5 px-3">Status</th>
              <th className="py-2.5 px-3">Timestamp</th>
              <th className="py-2.5 px-3">Rota / Request ID</th>
              <th className="py-2.5 px-3">Modelo (Solicitado &rarr; Servido)</th>
              <th className="py-2.5 px-3">Provedor</th>
              <th className="py-2.5 px-3 text-right">Tokens</th>
              <th className="py-2.5 px-3 text-right">Latência</th>
              <th className="py-2.5 px-3 text-right">Custo / Economia</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/60 font-mono">
            {filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-sm text-zinc-500 font-sans">
                  Nenhuma requisição corresponde aos filtros aplicados.
                </td>
              </tr>
            ) : (
              filteredLogs.map((log) => {
                const statusStyle = getLogStatusConfig(log.status);
                const reqModel = log.requested_model ?? log.model ?? "gpt-4o";
                const srvModel = log.served_model ?? log.model ?? "gpt-4o";
                const isRerouted = reqModel !== srvModel;
                const isExpanded = expandedLogId === log.id;
                const promptTokens =
                  log.prompt_tokens ??
                  log.tokens?.prompt ??
                  (log.tokens as any)?.prompt_tokens ??
                  0;
                const completionTokens =
                  log.completion_tokens ??
                  log.tokens?.completion ??
                  (log.tokens as any)?.completion_tokens ??
                  0;
                const totalTokens =
                  log.total_tokens ??
                  log.tokens?.total ??
                  (log.tokens as any)?.total_tokens ??
                  promptTokens + completionTokens;
                const savedCost = log.saved_cost_usd ?? log.cost_saved_usd ?? 0;
                const costUsd = log.cost_usd ?? 0;
                const routeName = log.route_name ?? `${log.provider} Route`;
                const reqId = log.request_id ?? log.id;
                const isCacheHit = log.cache_hit ?? (log.status === "CACHE_HIT");

                return (
                  <tr
                    key={log.id}
                    onClick={() => setExpandedLogId(isExpanded ? null : log.id)}
                    className="cursor-pointer transition-colors hover:bg-zinc-800/40"
                  >
                    {/* Status Badge */}
                    <td className="py-2.5 px-3 align-middle">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] font-semibold tracking-wider ${statusStyle.badgeClass}`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${statusStyle.dotClass}`} />
                        {log.status}
                      </span>
                    </td>

                    {/* Timestamp */}
                    <td className="py-2.5 px-3 align-middle text-zinc-400 whitespace-nowrap text-[11px]">
                      {new Date(log.timestamp).toLocaleTimeString("en-US", {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                        hour12: false,
                      })}
                    </td>

                    {/* Route & Request ID */}
                    <td className="py-2.5 px-3 align-middle">
                      <div className="font-sans font-medium text-zinc-200 truncate max-w-[200px]" title={routeName}>
                        {routeName}
                      </div>
                      <div className="text-[10px] text-zinc-500 truncate" title={reqId}>
                        {reqId}
                      </div>
                    </td>

                    {/* Model Info */}
                    <td className="py-2.5 px-3 align-middle">
                      <div className="flex items-center gap-1.5">
                        <span className="text-zinc-200 truncate max-w-[170px]" title={srvModel}>
                          {srvModel}
                        </span>
                        {isRerouted && (
                          <span
                            className="rounded bg-amber-950/80 border border-amber-800/60 px-1 py-0.5 text-[9px] text-amber-300"
                            title={`Solicitado originalmente: ${reqModel}`}
                          >
                            rerouted
                          </span>
                        )}
                      </div>
                      {isRerouted && (
                        <div className="text-[10px] text-zinc-500 line-through truncate max-w-[170px]">
                          {reqModel}
                        </div>
                      )}
                    </td>

                    {/* Provider */}
                    <td className="py-2.5 px-3 align-middle">
                      <span className="rounded bg-zinc-800/90 px-1.5 py-0.5 text-[10px] text-zinc-300 uppercase">
                        {log.provider}
                      </span>
                    </td>

                    {/* Tokens */}
                    <td className="py-2.5 px-3 align-middle text-right">
                      <div className="text-zinc-200 font-semibold">
                        {formatTokenCount(totalTokens)}
                      </div>
                      <div className="text-[10px] text-zinc-500">
                        {promptTokens} in / {completionTokens} out
                      </div>
                    </td>

                    {/* Latency */}
                    <td className="py-2.5 px-3 align-middle text-right">
                      <span
                        className={`font-semibold ${
                          log.latency_ms < 50
                            ? "text-emerald-400"
                            : log.latency_ms < 500
                              ? "text-sky-400"
                              : log.latency_ms < 1500
                                ? "text-amber-400"
                                : "text-rose-400"
                        }`}
                      >
                        {formatLatencyMs(log.latency_ms)}
                      </span>
                    </td>

                    {/* Cost / Savings */}
                    <td className="py-2.5 px-3 align-middle text-right whitespace-nowrap">
                      {isCacheHit ? (
                        <div className="text-emerald-400 font-medium">
                          +{formatUSD(savedCost)} salvo
                        </div>
                      ) : (
                        <div className="text-zinc-300">
                          {formatUSD(costUsd)}
                        </div>
                      )}
                      {log.cache_score !== undefined && log.cache_score !== null && (
                        <div className="text-[10px] text-zinc-500">
                          sim: {(log.cache_score * 100).toFixed(1)}%
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Expanded detail footer if a row was selected */}
      {expandedLogId && (
        <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/90 p-3 text-xs">
          {(() => {
            const item = logs.find((l) => l.id === expandedLogId);
            if (!item) return null;
            const itemReqId = item.request_id ?? item.id;
            const itemStatusCode = item.status_code ?? (item.status === "ERROR" ? 503 : 200);
            const itemCacheHit = item.cache_hit ?? (item.status === "CACHE_HIT");
            const itemFallbackTriggered = item.fallback_triggered ?? (item.status === "FALLBACK");
            return (
              <div className="space-y-1.5 font-mono">
                <div className="flex items-center justify-between text-zinc-400 font-sans">
                  <span className="font-semibold text-zinc-200">Detalhes da Requisição {itemReqId}</span>
                  <button
                    type="button"
                    onClick={() => setExpandedLogId(null)}
                    className="text-zinc-500 hover:text-zinc-300 text-xs"
                  >
                    Fechar &times;
                  </button>
                </div>
                {item.fallback_chain_traversed && item.fallback_chain_traversed.length > 0 && (
                  <div className="rounded bg-amber-950/30 border border-amber-800/50 p-2 text-amber-200">
                    <span className="font-semibold text-amber-300">Trajetória do Fallback:</span>{" "}
                    {item.fallback_chain_traversed.join(" -> ")}
                  </div>
                )}
                {item.error_message && (
                  <div className="rounded bg-rose-950/30 border border-rose-800/50 p-2 text-rose-300">
                    <span className="font-semibold text-rose-400">Mensagem de Erro:</span> {item.error_message}
                  </div>
                )}
                <div className="text-zinc-400 text-[11px] flex flex-wrap gap-4 pt-1">
                  <span>Client IP: {item.client_ip ?? "127.0.0.1"}</span>
                  <span>Status Code: HTTP {itemStatusCode}</span>
                  <span>Cache Hit: {itemCacheHit ? "Sim (Zero Inference Cost)" : "Não"}</span>
                  <span>Fallback Ativado: {itemFallbackTriggered ? "Sim" : "Não"}</span>
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
