"use client";

import { useEffect } from "react";
import { formatScore, formatTimestamp } from "@/lib/format";
import { computeVersionMetricsDelta } from "@/lib/model-lineage";
import { formatBytes, shortSha } from "@/lib/mlops";
import { getAliasStyle, getModelStageStyle } from "@/lib/mlops-tokens";
import type { ArtifactRecord, ModelVersion, RegisteredModelDetail } from "@/lib/types";

export interface VersionDiffDrawerProps {
  open: boolean;
  model: RegisteredModelDetail;
  versionA: ModelVersion | null;
  versionB: ModelVersion | null;
  artifacts: ArtifactRecord[];
  onClose: () => void;
}

export function VersionDiffDrawer({
  open,
  model,
  versionA,
  versionB,
  artifacts,
  onClose,
}: VersionDiffDrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open || !versionA || !versionB) return null;

  // Order chronologically: older on left (A), newer on right (B)
  const [left, right] =
    versionA.version <= versionB.version ? [versionA, versionB] : [versionB, versionA];

  const aliasesFor = (ver: number) =>
    Object.entries(model.aliases ?? {})
      .filter(([, v]) => v === ver)
      .map(([a]) => a);

  const findArtifact = (artifactId: string | null) =>
    artifactId ? artifacts.find((a) => a.id === artifactId) ?? null : null;

  const artLeft = findArtifact(left.artifact_id);
  const artRight = findArtifact(right.artifact_id);

  const metricDeltas = computeVersionMetricsDelta(left, right);

  const loraKeys = Array.from(
    new Set([
      ...Object.keys(left.lora_config ?? {}),
      ...Object.keys(right.lora_config ?? {}),
    ]),
  ).sort();

  const stageLeft = getModelStageStyle(left.stage);
  const stageRight = getModelStageStyle(right.stage);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/75"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="version-diff-drawer-title"
        className="flex h-full w-full max-w-2xl flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-4">
          <div>
            <h2 id="version-diff-drawer-title" className="text-sm font-semibold text-zinc-50">
              Compare Versions:{" "}
              <span className="font-mono text-sky-300">v{left.version}</span> vs{" "}
              <span className="font-mono text-emerald-300">v{right.version}</span>
            </h2>
            <p className="mt-0.5 text-xs text-zinc-400">
              Model <span className="font-mono text-zinc-200">{model.name}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close comparison drawer"
            className="inline-flex h-8 w-8 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-5 text-xs">
          {/* Stage & Aliases */}
          <section className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              Stage & Aliases
            </h3>
            <div className="grid grid-cols-2 gap-3">
              {[
                { ver: left, style: stageLeft, label: "Baseline" },
                { ver: right, style: stageRight, label: "Candidate" },
              ].map(({ ver, style, label }) => {
                const verAliases = aliasesFor(ver.version);
                return (
                  <div
                    key={ver.id}
                    className="rounded-md border border-zinc-800 bg-zinc-900/60 p-3 space-y-2"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-semibold text-zinc-100">
                        v{ver.version}
                      </span>
                      <span className="text-[11px] text-zinc-400">{label}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span
                        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-medium ${style.badge}`}
                      >
                        <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                        {style.label}
                      </span>
                      {verAliases.map((a) => (
                        <span
                          key={a}
                          className={`inline-flex items-center px-2 py-0.5 font-mono text-[11px] ${getAliasStyle(a)}`}
                        >
                          @{a}
                        </span>
                      ))}
                    </div>
                    <p className="font-mono text-[11px] tabular-nums text-zinc-400">
                      {formatTimestamp(ver.created_at)}
                    </p>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Metrics Snapshot Delta */}
          <section className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              Metrics Snapshot Delta
            </h3>
            {metricDeltas.length === 0 ? (
              <p className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-zinc-400">
                No metrics snapshot recorded on either version.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-zinc-800">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-zinc-800 bg-zinc-900/60">
                      <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        Metric
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        v{left.version}
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        v{right.version}
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        Delta (v{right.version} − v{left.version})
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {metricDeltas.map((row) => (
                      <tr key={row.metric} className="hover:bg-zinc-900/30">
                        <td className="px-3 py-2 font-mono text-zinc-200">{row.metric}</td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums text-zinc-300">
                          {row.valueA !== null ? formatScore(row.valueA) : "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums text-zinc-100">
                          {row.valueB !== null ? formatScore(row.valueB) : "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums">
                          {row.delta === null ? (
                            <span className="text-zinc-400">—</span>
                          ) : (
                            <span
                              className={
                                row.delta === 0
                                  ? "text-zinc-400"
                                  : row.delta > 0
                                    ? "text-sky-300"
                                    : "text-emerald-300"
                              }
                            >
                              {row.delta > 0 ? `+${row.delta}` : row.delta}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* LoRA Config Diff */}
          <section className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              LoRA Configuration
            </h3>
            {loraKeys.length === 0 ? (
              <p className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-zinc-400">
                No LoRA adapter configuration attached.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-zinc-800">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-zinc-800 bg-zinc-900/60">
                      <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        Parameter
                      </th>
                      <th scope="col" className="px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        v{left.version}
                      </th>
                      <th scope="col" className="px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                        v{right.version}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {loraKeys.map((key) => {
                      const valA = left.lora_config?.[key];
                      const valB = right.lora_config?.[key];
                      const strA = valA === undefined ? "—" : JSON.stringify(valA);
                      const strB = valB === undefined ? "—" : JSON.stringify(valB);
                      const differs = strA !== strB;
                      return (
                        <tr key={key} className={differs ? "bg-amber-950/20" : undefined}>
                          <td className="px-3 py-2 font-mono text-zinc-300">{key}</td>
                          <td className="px-3 py-2 font-mono text-zinc-300">{strA}</td>
                          <td className="px-3 py-2 font-mono text-zinc-100">
                            {strB}
                            {differs && (
                              <span className="ml-2 rounded border border-amber-800/70 bg-amber-950/50 px-1 py-0.5 text-[10px] text-amber-300">
                                changed
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Artifact SHA-256 */}
          <section className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              Artifact SHA-256 & Size
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {[
                { ver: left, art: artLeft },
                { ver: right, art: artRight },
              ].map(({ ver, art }) => (
                <div
                  key={ver.id}
                  className="rounded-md border border-zinc-800 bg-zinc-900/60 p-3 space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-semibold text-zinc-100">v{ver.version}</span>
                    <span className="font-mono text-[11px] tabular-nums text-zinc-400">
                      {art ? formatBytes(art.size_bytes) : "—"}
                    </span>
                  </div>
                  <div className="text-[11px] text-zinc-400">
                    Artifact ID:{" "}
                    <span className="font-mono text-zinc-200">{ver.artifact_id ?? "—"}</span>
                  </div>
                  <div className="text-[11px] text-zinc-400">
                    SHA-256:{" "}
                    <span
                      title={art?.sha256 ?? undefined}
                      className="font-mono text-zinc-200 break-all"
                    >
                      {art ? shortSha(art.sha256, 16) : "—"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
