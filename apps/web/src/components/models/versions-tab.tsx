"use client";

import Link from "next/link";
import { useState } from "react";
import { formatScore, formatTimestamp } from "@/lib/format";
import { getAliasStyle, getModelStageStyle, PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { ModelVersion, RegisteredModelDetail } from "@/lib/types";

export interface VersionsTabProps {
  model: RegisteredModelDetail;
  onPromoteStage: (version: ModelVersion) => void;
  onMoveAlias: (version: ModelVersion) => void;
  onCompareVersions: (vA: ModelVersion, vB: ModelVersion) => void;
  onRegisterVersion: () => void;
}

export function VersionsTab({
  model,
  onPromoteStage,
  onMoveAlias,
  onCompareVersions,
  onRegisterVersion,
}: VersionsTabProps) {
  const [selectedVersions, setSelectedVersions] = useState<number[]>([]);
  const versions = [...(model.versions ?? [])].sort((a, b) => b.version - a.version);
  const aliases = model.aliases ?? {};

  const aliasesForVersion = (versionNum: number): string[] =>
    Object.entries(aliases)
      .filter(([, ver]) => ver === versionNum)
      .map(([name]) => name);

  const toggleSelect = (versionNum: number) => {
    setSelectedVersions((prev) => {
      if (prev.includes(versionNum)) {
        return prev.filter((v) => v !== versionNum);
      }
      if (prev.length >= 2) {
        return [prev[1], versionNum];
      }
      return [...prev, versionNum];
    });
  };

  const handleCompareClick = () => {
    if (selectedVersions.length !== 2) return;
    const vA = versions.find((v) => v.version === selectedVersions[0]);
    const vB = versions.find((v) => v.version === selectedVersions[1]);
    if (vA && vB) {
      onCompareVersions(vA, vB);
    }
  };

  if (versions.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 px-6 py-10 text-center">
        <p className="text-sm font-medium text-zinc-200">No versions registered yet</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-zinc-400">
          Register an immutable model version with its artifact, metrics snapshot, and stage.
        </p>
        <button
          type="button"
          onClick={onRegisterVersion}
          className={`mt-4 inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition ${PRIMARY_BTN_CLASS}`}
        >
          + Register Version
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-zinc-400">
          Immutable versions for <span className="font-mono text-zinc-200">{model.name}</span>. Select 2
          versions to inspect side-by-side diffs.
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={selectedVersions.length !== 2}
            onClick={handleCompareClick}
            className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Compare Versions ({selectedVersions.length}/2)
          </button>
          <button
            type="button"
            onClick={onRegisterVersion}
            className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition ${PRIMARY_BTN_CLASS}`}
          >
            + Register Version
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-950/60">
        <table className="w-full border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-zinc-800 bg-zinc-900/60">
              <th scope="col" className="w-10 px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                <span className="sr-only">Compare</span>
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Version
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Stage
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                LoRA Config
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Training Run
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Metrics Snapshot
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Created (UTC)
              </th>
              <th scope="col" className="whitespace-nowrap px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {versions.map((v) => {
              const stageStyle = getModelStageStyle(v.stage);
              const vAliases = aliasesForVersion(v.version);
              const isChecked = selectedVersions.includes(v.version);
              const metricEntries = Object.entries(v.metrics_snapshot ?? {});
              const hasLora =
                v.lora_config &&
                (v.lora_config.r !== undefined || v.lora_config.lora_alpha !== undefined);

              return (
                <tr key={v.id} className="hover:bg-zinc-900/40">
                  <td className="px-3 py-2 align-middle">
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggleSelect(v.version)}
                      aria-label={`Select version v${v.version} for comparison`}
                      className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-emerald-400 focus-visible:ring-emerald-400"
                    />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-xs font-semibold tabular-nums text-zinc-100">
                        v{v.version}
                      </span>
                      {vAliases.map((alias) => (
                        <span
                          key={alias}
                          className={`inline-flex items-center px-2 py-0.5 font-mono text-[11px] leading-4 ${getAliasStyle(alias)}`}
                        >
                          @{alias}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle">
                    <span
                      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-medium leading-4 ${stageStyle.badge}`}
                    >
                      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${stageStyle.dot}`} />
                      {stageStyle.label}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle font-mono tabular-nums text-zinc-300">
                    {hasLora ? (
                      <span>
                        r={v.lora_config?.r ?? "—"} · α={v.lora_config?.lora_alpha ?? "—"}
                      </span>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle">
                    {v.run_id ? (
                      <Link
                        href={`/training?run=${encodeURIComponent(v.run_id)}`}
                        className="inline-flex h-7 items-center rounded border border-sky-800/60 bg-sky-950/30 px-2 font-mono text-[11px] text-sky-300 transition hover:bg-sky-950/60"
                      >
                        {v.run_id}
                      </Link>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 align-middle">
                    {metricEntries.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {metricEntries.slice(0, 3).map(([k, val]) => (
                          <span
                            key={k}
                            className="inline-flex items-center gap-1 rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] tabular-nums"
                          >
                            <span className="text-zinc-400">{k}:</span>
                            <span className="text-zinc-100">
                              {typeof val === "number" ? formatScore(val) : String(val)}
                            </span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle font-mono text-[11px] tabular-nums text-zinc-400">
                    {formatTimestamp(v.created_at)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right align-middle">
                    <div className="inline-flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => onPromoteStage(v)}
                        className="inline-flex h-7 items-center rounded border border-zinc-700 bg-zinc-900 px-2.5 text-[11px] font-medium text-zinc-200 transition hover:bg-zinc-800"
                      >
                        Promote Stage
                      </button>
                      <button
                        type="button"
                        onClick={() => onMoveAlias(v)}
                        className="inline-flex h-7 items-center rounded border border-zinc-700 bg-zinc-900 px-2.5 text-[11px] font-medium text-zinc-200 transition hover:bg-zinc-800"
                      >
                        Move Alias
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
