"use client";

import { useEffect, useState } from "react";
import { getAliasStyle, getModelStageStyle, PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { ModelStage, ModelVersion, RegisteredModelDetail } from "@/lib/types";

export interface PromoteModalProps {
  open: boolean;
  mode: "stage" | "alias";
  model: RegisteredModelDetail;
  version: ModelVersion;
  onClose: () => void;
  onSuccess: () => void | Promise<void>;
}

const STAGES: { value: ModelStage; label: string; description: string }[] = [
  { value: "none", label: "None", description: "Unassigned / experimental candidate" },
  { value: "staging", label: "Staging", description: "Pre-production validation & shadow traffic" },
  { value: "production", label: "Production", description: "Active production deployment stage" },
  { value: "archived", label: "Archived", description: "Retired from active serving" },
];

const PRESET_ALIASES = ["champion", "challenger", "canary"];

export function PromoteModal({
  open,
  mode: initialMode,
  model,
  version,
  onClose,
  onSuccess,
}: PromoteModalProps) {
  const [activeMode, setActiveMode] = useState<"stage" | "alias">(initialMode);
  const [targetStage, setTargetStage] = useState<ModelStage>(version.stage);
  const [aliasInput, setAliasInput] = useState<string>("champion");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setActiveMode(initialMode);
    setTargetStage(version.stage);
    setAliasInput(initialMode === "alias" ? "champion" : "challenger");
    setError(null);
  }, [open, initialMode, version]);

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

  if (!open) return null;

  const currentChampionVersion = model.aliases?.champion;
  const normalizedAlias = aliasInput.trim().replace(/^@/, "").toLowerCase();
  const isReassigningChampion =
    activeMode === "alias" &&
    normalizedAlias === "champion" &&
    currentChampionVersion !== undefined &&
    currentChampionVersion !== version.version;
  const isAssigningChampionFirstTime =
    activeMode === "alias" &&
    normalizedAlias === "champion" &&
    currentChampionVersion === undefined;
  const isPromotingProduction =
    activeMode === "stage" && targetStage === "production" && version.stage !== "production";

  const existingProdVersions = (model.versions ?? [])
    .filter((v) => v.stage === "production" && v.version !== version.version)
    .map((v) => `v${v.version}`);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      if (activeMode === "stage") {
        const res = await fetch(
          `/api/proxy/api/v1/mlops/models/${encodeURIComponent(model.id)}/versions/${version.version}/stage`,
          {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ stage: targetStage }),
          },
        );
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `Failed to update stage (${res.status})`);
        }
      } else {
        if (!normalizedAlias) {
          throw new Error("Alias name is required.");
        }
        const res = await fetch(
          `/api/proxy/api/v1/mlops/models/${encodeURIComponent(model.id)}/aliases`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ alias: normalizedAlias, version: version.version }),
          },
        );
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `Failed to assign alias (${res.status})`);
        }
      }

      await onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="promote-modal-title"
        className="w-full max-w-lg rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="promote-modal-title" className="text-sm font-semibold text-zinc-50">
              Manage Version <span className="font-mono text-emerald-300">v{version.version}</span>
            </h2>
            <p className="mt-0.5 text-xs text-zinc-400">
              Model <span className="font-mono text-zinc-200">{model.name}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close modal"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 inline-flex rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
          <button
            type="button"
            onClick={() => {
              setActiveMode("stage");
              setError(null);
            }}
            className={`h-7 rounded px-3 text-xs font-medium transition ${
              activeMode === "stage"
                ? "bg-zinc-800 text-zinc-50"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Promote Stage
          </button>
          <button
            type="button"
            onClick={() => {
              setActiveMode("alias");
              setError(null);
            }}
            className={`h-7 rounded px-3 text-xs font-medium transition ${
              activeMode === "alias"
                ? "bg-zinc-800 text-zinc-50"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Move Alias
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {activeMode === "stage" ? (
            <fieldset className="space-y-2">
              <legend className="text-xs font-medium text-zinc-300">Target Stage</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {STAGES.map((s) => {
                  const style = getModelStageStyle(s.value);
                  const selected = targetStage === s.value;
                  return (
                    <label
                      key={s.value}
                      className={`flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition ${
                        selected
                          ? "border-emerald-600/80 bg-emerald-950/20"
                          : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] font-medium ${style.badge}`}
                        >
                          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                          {s.label}
                        </span>
                        <input
                          type="radio"
                          name="targetStage"
                          value={s.value}
                          checked={selected}
                          onChange={() => setTargetStage(s.value)}
                          className="h-3.5 w-3.5 text-emerald-400"
                        />
                      </div>
                      <span className="text-[11px] text-zinc-400">{s.description}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ) : (
            <div className="space-y-3">
              <div>
                <label htmlFor="alias-name-input" className="block text-xs font-medium text-zinc-300">
                  Alias Name
                </label>
                <input
                  id="alias-name-input"
                  type="text"
                  value={aliasInput}
                  onChange={(e) => setAliasInput(e.target.value)}
                  placeholder="champion"
                  className="mt-1.5 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-zinc-400">Presets:</span>
                {PRESET_ALIASES.map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setAliasInput(preset)}
                    className={`inline-flex h-6 items-center px-2 font-mono text-[11px] transition ${getAliasStyle(preset)}`}
                  >
                    @{preset}
                  </button>
                ))}
              </div>
            </div>
          )}

          {(isReassigningChampion || isAssigningChampionFirstTime || isPromotingProduction) && (
            <div
              role="note"
              className="rounded-md border border-amber-800/70 bg-amber-950/40 p-3 text-xs text-amber-200"
            >
              <p className="font-semibold text-amber-100">Production Traffic Impact Warning</p>
              {isReassigningChampion && (
                <p className="mt-1 leading-relaxed text-amber-200/90">
                  Reassigning <span className="font-mono font-semibold">@champion</span> from{" "}
                  <span className="font-mono font-semibold">v{currentChampionVersion}</span> to{" "}
                  <span className="font-mono font-semibold">v{version.version}</span> will
                  immediately point production alias resolution to{" "}
                  <span className="font-mono font-semibold">v{version.version}</span>.
                </p>
              )}
              {isAssigningChampionFirstTime && (
                <p className="mt-1 leading-relaxed text-amber-200/90">
                  Assigning <span className="font-mono font-semibold">@champion</span> designates{" "}
                  <span className="font-mono font-semibold">v{version.version}</span> as the
                  primary production alias for this model.
                </p>
              )}
              {isPromotingProduction && (
                <p className="mt-1 leading-relaxed text-amber-200/90">
                  Promoting <span className="font-mono font-semibold">v{version.version}</span> to{" "}
                  <span className="font-semibold">Production</span> marks it eligible for live
                  serving.
                  {existingProdVersions.length > 0 && (
                    <>
                      {" "}
                      Note: {existingProdVersions.join(", ")} currently also marked as Production.
                    </>
                  )}
                </p>
              )}
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="rounded-md border border-red-800/70 bg-red-950/50 px-3 py-2 text-xs text-red-200"
            >
              {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
            >
              {submitting
                ? "Saving…"
                : activeMode === "stage"
                  ? `Set Stage to ${STAGES.find((s) => s.value === targetStage)?.label ?? targetStage}`
                  : `Assign @${normalizedAlias || "alias"}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
