import type { GatewayRoutingRule } from "@/lib/types";

export interface RoutingRulesPanelProps {
  rules: GatewayRoutingRule[];
}

export function RoutingRulesPanel({ rules }: RoutingRulesPanelProps) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <div className="flex flex-col gap-1 border-b border-zinc-800/80 pb-4">
        <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
          <svg
            className="h-4 w-4 text-sky-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"
            />
          </svg>
          Regras de Roteamento Dinâmico & Cadeias de Fallback
        </h2>
        <p className="text-xs text-zinc-400">
          Quando o provedor primário sofre timeout ou aciona o circuit breaker, o gateway desvia a chamada automaticamente pela cadeia declarada sem impacto para a aplicação cliente.
        </p>
      </div>

      <div className="mt-4 space-y-4">
        {rules.map((rule) => (
          <div
            key={rule.id}
            className="rounded-xl border border-zinc-800/80 bg-zinc-900/90 p-4 transition-all hover:border-zinc-700"
          >
            {/* Rule Header */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800/60 pb-3">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="font-semibold text-sm text-zinc-100">
                  {rule.name}
                </span>
                <span className="rounded bg-sky-950/60 border border-sky-800/60 px-2 py-0.5 text-xs font-mono text-sky-300">
                  {rule.match_pattern}
                </span>
                {rule.semantic_cache_enabled ? (
                  <span className="inline-flex items-center gap-1 rounded bg-emerald-950/50 border border-emerald-800/60 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    Cache Semântico (&ge; {Math.round(rule.cache_similarity_threshold * 100)}%)
                  </span>
                ) : (
                  <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
                    Cache Desativado
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="rounded bg-zinc-800/80 px-2 py-0.5 font-mono">
                  {rule.rate_limit_rpm} RPM
                </span>
                <span className="rounded bg-zinc-800/80 px-2 py-0.5 font-mono">
                  {rule.timeout_ms}ms timeout
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                    rule.is_active
                      ? "bg-emerald-950/60 text-emerald-300 border border-emerald-800/60"
                      : "bg-zinc-800 text-zinc-500"
                  }`}
                >
                  {rule.is_active ? "Ativo" : "Inativo"}
                </span>
              </div>
            </div>

            {rule.description && (
              <p className="mt-2.5 text-xs text-zinc-400">{rule.description}</p>
            )}

            {/* Visual Fallback Pipeline Flow */}
            <div className="mt-3.5">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 mb-2">
                Fluxo de Execução & Fallback Sequencial:
              </div>

              <div className="flex flex-col md:flex-row md:items-center gap-2 overflow-x-auto pb-1">
                {/* Primary Provider Box */}
                <div className="flex-1 min-w-[200px] rounded-lg border-2 border-emerald-700/60 bg-emerald-950/20 p-3">
                  <div className="flex items-center justify-between text-[11px] font-semibold text-emerald-300">
                    <span className="flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-emerald-400" />
                      Primário (P0)
                    </span>
                    <span className="uppercase text-[10px] rounded bg-emerald-900/60 px-1.5 py-0.5">
                      {rule.primary_provider}
                    </span>
                  </div>
                  <div className="mt-1.5 font-mono text-xs font-bold text-zinc-100 truncate" title={rule.primary_model}>
                    {rule.primary_model}
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-400">
                    <span>Retries: {rule.retry_count}x</span>
                    <span>Timeout: {rule.timeout_ms}ms</span>
                  </div>
                </div>

                {/* Arrow and Fallback Nodes */}
                {rule.fallback_chain.map((fallback, idx) => (
                  <div key={`${fallback.provider}-${fallback.model}-${idx}`} className="flex flex-col md:flex-row md:items-center gap-2 flex-1 min-w-[200px]">
                    {/* Arrow */}
                    <div className="flex items-center justify-center text-zinc-500 shrink-0 self-center">
                      <div className="hidden md:flex items-center text-zinc-600 gap-1">
                        <span className="text-[10px] uppercase font-mono text-amber-500/80">on fail</span>
                        <svg className="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                        </svg>
                      </div>
                      <div className="md:hidden flex items-center text-zinc-600 gap-1 my-1">
                        <span className="text-[10px] uppercase font-mono text-amber-500/80">&darr; fallback se erro/timeout</span>
                      </div>
                    </div>

                    {/* Fallback Node Box */}
                    <div className="flex-1 rounded-lg border border-amber-800/50 bg-amber-950/20 p-3">
                      <div className="flex items-center justify-between text-[11px] font-semibold text-amber-300">
                        <span>Fallback {idx + 1} (P{fallback.priority})</span>
                        <span className="uppercase text-[10px] rounded bg-amber-900/60 px-1.5 py-0.5">
                          {fallback.provider}
                        </span>
                      </div>
                      <div className="mt-1.5 font-mono text-xs font-bold text-zinc-100 truncate" title={fallback.model}>
                        {fallback.model}
                      </div>
                      <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-400">
                        <span>Retries: {fallback.max_retries}x</span>
                        <span>Timeout: {fallback.timeout_ms}ms</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
