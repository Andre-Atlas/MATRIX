"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { inferArtifactFormat } from "@/lib/model-lineage";
import { formatBytes, shortSha } from "@/lib/mlops";
import { getModelStageStyle } from "@/lib/mlops-tokens";
import type {
  ArtifactRecord,
  LineageGraph,
  ModelVersion,
  RegisteredModelDetail,
} from "@/lib/types";

export interface ModelCardTabProps {
  model: RegisteredModelDetail;
  artifacts: ArtifactRecord[];
  lineage: LineageGraph;
}

export function ModelCardTab({ model, artifacts, lineage }: ModelCardTabProps) {
  const versions = [...(model.versions ?? [])].sort((a, b) => b.version - a.version);
  const championVer = model.aliases?.champion;
  const defaultVer = championVer ?? versions[0]?.version ?? null;

  const [selectedVerNum, setSelectedVerNum] = useState<number | null>(defaultVer);
  const [copiedSha, setCopiedSha] = useState<string | null>(null);

  useEffect(() => {
    const nextDefault = model.aliases?.champion ?? versions[0]?.version ?? null;
    setSelectedVerNum(nextDefault);
    setCopiedSha(null);
  }, [model.id, model.aliases?.champion, versions.length]);

  if (versions.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 px-6 py-10 text-center">
        <p className="text-sm font-medium text-zinc-200">No model versions available</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-zinc-400">
          Register a model version to inspect its artifact checksums, I/O signature, and model card.
        </p>
      </div>
    );
  }

  const version: ModelVersion =
    versions.find((v) => v.version === selectedVerNum) ?? versions[0];

  const matchedArtifact = version.artifact_id
    ? artifacts.find((a) => a.id === version.artifact_id) ?? null
    : null;

  const lineageArtifactNode = version.artifact_id
    ? (lineage.nodes ?? []).find(
        (n) => n.node_type === "artifact" && n.entity_id === version.artifact_id,
      ) ?? null
    : null;

  const sha256 =
    matchedArtifact?.sha256 ??
    (typeof lineageArtifactNode?.metadata?.sha256 === "string"
      ? lineageArtifactNode.metadata.sha256
      : null);

  const sizeBytes =
    matchedArtifact?.size_bytes ??
    (typeof lineageArtifactNode?.metadata?.size_bytes === "number"
      ? lineageArtifactNode.metadata.size_bytes
      : null);

  const artifactFormat = inferArtifactFormat(
    matchedArtifact ??
      (lineageArtifactNode
        ? {
            name: lineageArtifactNode.label,
            kind:
              typeof lineageArtifactNode.metadata?.kind === "string"
                ? lineageArtifactNode.metadata.kind
                : undefined,
          }
        : null),
  );

  const evalNode = version.eval_experiment_id
    ? (lineage.nodes ?? []).find(
        (n) => n.node_type === "experiment" && n.entity_id === version.eval_experiment_id,
      )
    : null;

  const safetyNode = version.safety_scan_id
    ? (lineage.nodes ?? []).find(
        (n) => n.node_type === "safety_scan" && n.entity_id === version.safety_scan_id,
      )
    : null;

  const handleCopySha = async () => {
    if (!sha256) return;
    try {
      await navigator.clipboard.writeText(sha256);
      setCopiedSha(sha256);
      setTimeout(() => setCopiedSha(null), 2000);
    } catch {
      // Clipboard API unavailable in non-secure context
    }
  };

  const stageStyle = getModelStageStyle(version.stage);
  const inputs = version.signature?.inputs ?? [];
  const outputs = version.signature?.outputs ?? [];

  return (
    <div className="space-y-5 text-xs">
      {/* Version selector bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2">
        <div className="flex items-center gap-2">
          <label htmlFor="model-card-version-select" className="text-xs font-medium text-zinc-300">
            Inspecting Version
          </label>
          <select
            id="model-card-version-select"
            value={version.version}
            onChange={(e) => setSelectedVerNum(Number(e.target.value))}
            className="h-7 rounded border border-zinc-700 bg-zinc-950 px-2 font-mono text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            {versions.map((v) => (
              <option key={v.id} value={v.version}>
                v{v.version} ({v.stage})
              </option>
            ))}
          </select>
          <span
            className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-medium ${stageStyle.badge}`}
          >
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${stageStyle.dot}`} />
            {stageStyle.label}
          </span>
        </div>
        <div className="font-mono text-[11px] text-zinc-400">
          Created by {version.created_by ?? "system"}
        </div>
      </div>

      {/* Artifact & Checksum + Eval/Safety Status */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              Artifact & Integrity
            </h3>
            <span className="rounded border border-sky-800/70 bg-sky-950/40 px-2 py-0.5 font-mono text-[11px] font-medium uppercase text-sky-300">
              {artifactFormat}
            </span>
          </div>

          <dl className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <dt className="text-zinc-400">Artifact ID</dt>
              <dd className="font-mono text-zinc-100">{version.artifact_id ?? "—"}</dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-zinc-400">Size</dt>
              <dd className="font-mono tabular-nums text-zinc-100">
                {sizeBytes !== null ? formatBytes(sizeBytes) : "—"}
              </dd>
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <dt className="text-zinc-400">SHA-256</dt>
                <dd className="flex items-center gap-2">
                  <span className="font-mono text-zinc-100">
                    {sha256 ? shortSha(sha256, 12) : "—"}
                  </span>
                  {sha256 && (
                    <button
                      type="button"
                      onClick={handleCopySha}
                      className="inline-flex h-7 items-center rounded border border-zinc-700 bg-zinc-900 px-2 text-[11px] font-medium text-zinc-200 transition hover:bg-zinc-800"
                    >
                      {copiedSha === sha256 ? "Copied!" : "Copy SHA-256"}
                    </button>
                  )}
                </dd>
              </div>
              {sha256 && (
                <div className="rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 font-mono text-[11px] text-zinc-300 break-all">
                  {sha256}
                </div>
              )}
              <span className="sr-only" aria-live="polite">
                {copiedSha ? "SHA-256 hash copied to clipboard." : ""}
              </span>
            </div>
          </dl>
        </section>

        <section className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 space-y-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
            Verification & Governance
          </h3>

          <div className="space-y-2.5">
            <div className="flex items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-950/70 px-3 py-2">
              <div>
                <div className="text-[11px] text-zinc-400">Eval Experiment</div>
                <div className="mt-0.5 font-mono text-xs text-zinc-100">
                  {version.eval_experiment_id ?? "Not evaluated"}
                </div>
              </div>
              {version.eval_experiment_id ? (
                <Link
                  href={`/experiments/${encodeURIComponent(version.eval_experiment_id)}`}
                  className="inline-flex h-7 items-center gap-1.5 rounded border border-emerald-800/60 bg-emerald-950/40 px-2.5 text-[11px] font-medium text-emerald-300 hover:bg-emerald-950/70"
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  {typeof evalNode?.metadata?.status === "string"
                    ? `View (${evalNode.metadata.status})`
                    : "View Experiment"}
                </Link>
              ) : (
                <span className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-400">
                  Pending
                </span>
              )}
            </div>

            <div className="flex items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-950/70 px-3 py-2">
              <div>
                <div className="text-[11px] text-zinc-400">Safety & OWASP Scan</div>
                <div className="mt-0.5 font-mono text-xs text-zinc-100">
                  {version.safety_scan_id ?? "No scan linked"}
                </div>
              </div>
              {version.safety_scan_id ? (
                <Link
                  href={`/safety?scan=${encodeURIComponent(version.safety_scan_id)}`}
                  className="inline-flex h-7 items-center gap-1.5 rounded border border-violet-800/70 bg-violet-950/40 px-2.5 text-[11px] font-medium text-violet-300 hover:bg-violet-950/70"
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-violet-400" />
                  {typeof safetyNode?.metadata?.global_score === "number"
                    ? `Score ${safetyNode.metadata.global_score}`
                    : "View Scan"}
                </Link>
              ) : (
                <span className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-400">
                  Not scanned
                </span>
              )}
            </div>
          </div>
        </section>
      </div>

      {/* I/O Signature Table */}
      <section className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 space-y-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
          I/O Signature Schema
        </h3>
        {inputs.length === 0 && outputs.length === 0 ? (
          <p className="text-zinc-400">No I/O signature recorded for this version.</p>
        ) : (
          <div className="overflow-x-auto rounded border border-zinc-800">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-zinc-800 bg-zinc-950/70">
                  <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                    Direction
                  </th>
                  <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                    Field Name
                  </th>
                  <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                    Dtype
                  </th>
                  <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                    Shape
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {inputs.map((field) => (
                  <tr key={`in-${field.name}`}>
                    <td className="px-3 py-2">
                      <span className="rounded border border-cyan-800/70 bg-cyan-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-cyan-300">
                        Input
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-100">{field.name}</td>
                    <td className="px-3 py-2 font-mono text-zinc-300">{field.dtype}</td>
                    <td className="px-3 py-2 font-mono tabular-nums text-zinc-400">
                      {field.shape ? `[${field.shape.join(", ")}]` : "scalar"}
                    </td>
                  </tr>
                ))}
                {outputs.map((field) => (
                  <tr key={`out-${field.name}`}>
                    <td className="px-3 py-2">
                      <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-emerald-300">
                        Output
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-100">{field.name}</td>
                    <td className="px-3 py-2 font-mono text-zinc-300">{field.dtype}</td>
                    <td className="px-3 py-2 font-mono tabular-nums text-zinc-400">
                      {field.shape ? `[${field.shape.join(", ")}]` : "scalar"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Model Card */}
      <section className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 space-y-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
          Model Card
        </h3>
        {version.model_card ? (
          <div className="whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-950/70 p-3.5 text-xs leading-relaxed text-zinc-200">
            {version.model_card}
          </div>
        ) : (
          <p className="text-zinc-400">No model card documentation provided for v{version.version}.</p>
        )}
      </section>
    </div>
  );
}
