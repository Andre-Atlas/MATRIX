import type { GatewayProviderStatus } from "@/lib/types";
import {
  formatLatencyMs,
  getCircuitStateConfig,
  getProviderHealthConfig,
} from "@/lib/gateway";

export interface ProviderCircuitBoardProps {
  providers: GatewayProviderStatus[];
}

export function ProviderCircuitBoard({ providers }: ProviderCircuitBoardProps) {
  const getCircuit = (p: GatewayProviderStatus) => {
    return p.circuit_state ?? (p.circuit_breaker as any) ?? "closed";
  };

  const closedCount = providers.filter((p) => String(getCircuit(p)).toLowerCase().includes("closed")).length;
  const halfOpenCount = providers.filter((p) => String(getCircuit(p)).toLowerCase().includes("half")).length;
  const openCount = providers.filter((p) => String(getCircuit(p)).toLowerCase() === "open").length;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      {/* Header with summary counts */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800/80 pb-4">
        <div>
          <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            Saúde dos Provedores & Circuit Breakers
          </h2>
          <p className="mt-0.5 text-xs text-zinc-400">
            Monitoramento em tempo real de latência, tolerância a falhas e isolamento automático de endpoints instáveis.
          </p>
        </div>

        {/* Status badges summary */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-800/60 bg-emerald-950/40 px-2.5 py-1 text-xs font-medium text-emerald-300">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            {closedCount} Closed (Normal)
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-800/60 bg-amber-950/40 px-2.5 py-1 text-xs font-medium text-amber-300">
            <span className="h-2 w-2 rounded-full bg-amber-400" />
            {halfOpenCount} Half-Open (Canary)
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-800/60 bg-rose-950/40 px-2.5 py-1 text-xs font-medium text-rose-300">
            <span className="h-2 w-2 rounded-full bg-rose-400" />
            {openCount} Open (Tripped)
          </span>
        </div>
      </div>

      {/* Grid of Providers */}
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {providers.map((p) => {
          const circuitState = getCircuit(p);
          const circuit = getCircuitStateConfig(circuitState);
          const healthStatus =
            p.status ??
            (circuit.key === "open" ? "outage" : circuit.key === "half_open" ? "degraded" : "healthy");
          const health = getProviderHealthConfig(healthStatus);
          const isTripped = circuit.key === "open";
          const isWarning = circuit.key === "half_open";
          const failureThreshold = p.failure_threshold ?? 5;
          const p50Ms = p.latency_p50_ms ?? p.avg_latency_ms ?? 150;
          const p99Ms = p.latency_p99_ms ?? Math.round((p.avg_latency_ms ?? 150) * 2.5);
          const uptime = p.uptime_percentage ?? (circuit.key === "open" ? 94.1 : circuit.key === "half_open" ? 98.5 : 99.98);
          const models = p.models ?? [p.name];
          const modelCount = p.model_count ?? models.length;

          return (
            <div
              key={p.id ?? p.name}
              className={`relative flex flex-col justify-between rounded-xl border bg-zinc-900/90 p-4 transition-all hover:border-zinc-700 ${
                isTripped
                  ? "border-rose-900/50 bg-rose-950/10 shadow-rose-950/20"
                  : isWarning
                    ? "border-amber-900/50 bg-amber-950/10"
                    : "border-zinc-800"
              }`}
            >
              {/* Card Header: Provider & Circuit State */}
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold text-zinc-100">
                        {p.name}
                      </span>
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
                        {p.provider ?? p.name.toLowerCase()}
                      </span>
                    </div>
                    {p.endpoint && (
                      <p className="mt-0.5 truncate text-[11px] font-mono text-zinc-500" title={p.endpoint}>
                        {p.endpoint}
                      </p>
                    )}
                  </div>

                  {/* Circuit Breaker Badge */}
                  <span
                    className={`inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold uppercase tracking-wider ${circuit.badgeClass}`}
                    title={circuit.description}
                  >
                    <span className={`h-2 w-2 rounded-full ${circuit.dotClass} ${isWarning ? "animate-pulse" : ""}`} />
                    {circuit.label}
                  </span>
                </div>

                {/* Error Banner if tripped or degraded */}
                {p.error_message && (
                  <div
                    className={`mt-3 rounded-lg border p-2 text-xs ${
                      isTripped
                        ? "border-rose-900/60 bg-rose-950/40 text-rose-300"
                        : "border-amber-900/60 bg-amber-950/40 text-amber-300"
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-medium">
                      <span>Diagnostic:</span>
                    </div>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-300">
                      {p.error_message}
                    </p>
                  </div>
                )}

                {/* Metrics Grid */}
                <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg bg-zinc-950/60 p-2.5 text-center border border-zinc-800/60">
                  <div>
                    <div className="text-[10px] uppercase font-semibold text-zinc-400">p50 Latência</div>
                    <div className="mt-0.5 font-mono text-xs font-bold text-sky-400">
                      {isTripped ? "—" : formatLatencyMs(p50Ms)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase font-semibold text-zinc-400">p99 Latência</div>
                    <div className="mt-0.5 font-mono text-xs font-bold text-amber-400">
                      {isTripped ? "—" : formatLatencyMs(p99Ms)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase font-semibold text-zinc-400">Disponibilidade</div>
                    <div className="mt-0.5 font-mono text-xs font-bold text-emerald-400">
                      {uptime.toFixed(2)}%
                    </div>
                  </div>
                </div>

                {/* Failure Threshold Progress */}
                <div className="mt-3">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-zinc-400">Falhas consecutivas</span>
                    <span
                      className={`font-mono font-semibold ${
                        p.failure_count >= failureThreshold
                          ? "text-rose-400"
                          : p.failure_count > 0
                            ? "text-amber-400"
                            : "text-emerald-400"
                      }`}
                    >
                      {p.failure_count} / {failureThreshold} máx
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        p.failure_count >= failureThreshold
                          ? "bg-rose-500"
                          : p.failure_count > 0
                            ? "bg-amber-500"
                            : "bg-emerald-500"
                      }`}
                      style={{
                        width: `${Math.min(100, (p.failure_count / failureThreshold) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              </div>

              {/* Models list & Health tag */}
              <div className="mt-3 border-t border-zinc-800/80 pt-2.5">
                <div className="flex items-center justify-between text-[11px] text-zinc-400 mb-1.5">
                  <span>Modelos Registrados ({modelCount}):</span>
                  <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] ${health.badgeClass}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${health.dotClass}`} />
                    {health.label}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1 max-h-16 overflow-y-auto">
                  {models.map((m) => (
                    <span
                      key={m}
                      className="inline-block rounded bg-zinc-800/80 px-1.5 py-0.5 text-[10px] font-mono text-zinc-300 hover:text-zinc-100"
                    >
                      {m}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

