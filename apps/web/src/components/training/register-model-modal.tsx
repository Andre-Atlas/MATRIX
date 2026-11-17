"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { proxy } from "@/lib/proxy-client";
import type {
  CreateModelRequest,
  CreateModelVersionRequest,
  ListResponse,
  LoraConfig,
  ModelStage,
  ModelType,
  ModelVersion,
  RegisteredModel,
  TrainingRun,
} from "@/lib/types";

type RegisterModelModalProps = {
  open: boolean;
  run: TrainingRun | null;
  onClose: () => void;
};

function inferModelType(run: TrainingRun | null): ModelType {
  if (!run) return "llm";
  if (run.run_type === "lora" || run.run_type === "qlora") return "lora";
  if (run.run_type === "classic_ml") return "classifier";
  return "llm";
}

function extractLoraConfig(run: TrainingRun | null): LoraConfig | null {
  if (!run || (run.run_type !== "lora" && run.run_type !== "qlora")) return null;
  const hp = run.hyperparams ?? {};
  const cfg: LoraConfig = {};
  if (typeof hp.r === "number") cfg.r = hp.r;
  if (typeof hp.lora_alpha === "number") cfg.lora_alpha = hp.lora_alpha;
  if (typeof hp.lora_dropout === "number") cfg.lora_dropout = hp.lora_dropout;
  return Object.keys(cfg).length > 0 ? cfg : null;
}

export function RegisterModelModal({ open, run, onClose }: RegisterModelModalProps) {
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [mode, setMode] = useState<"existing" | "new">("new");
  const [selectedModelId, setSelectedModelId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [modelType, setModelType] = useState<ModelType>("llm");
  const [stage, setStage] = useState<ModelStage>("staging");
  const [modelCard, setModelCard] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ modelId: string; modelName: string; version: number } | null>(null);

  const firstInputRef = useRef<HTMLInputElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    setError(null);
    setCreated(null);
    setNewName(run ? `${run.name}-model` : "");
    setNewDescription(run?.base_model ? `Registered from ${run.name} (${run.base_model})` : "");
    setModelType(inferModelType(run));
    setStage("staging");
    setModelCard(
      run
        ? `Trained via \`${run.run_type}\` on \`${run.dataset_id ?? "custom"}\` (${run.total_steps ?? 0} steps).`
        : "",
    );

    let cancelled = false;
    setLoadingModels(true);
    proxy<ListResponse<RegisteredModel>>("GET", "/api/v1/mlops/models")
      .then((res) => {
        if (cancelled) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        setModels(items);
        if (items.length > 0) {
          setSelectedModelId(items[0].id);
        }
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingModels(false);
      });

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    setTimeout(() => firstInputRef.current?.focus(), 0);

    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open, run, onClose]);

  if (!open) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      let targetModelId = selectedModelId;
      let targetModelName = models.find((m) => m.id === selectedModelId)?.name ?? "";

      if (mode === "new" || models.length === 0) {
        const trimmedName = newName.trim();
        if (!trimmedName) {
          setError("Model name is required.");
          setSubmitting(false);
          return;
        }
        const createReq: CreateModelRequest = {
          name: trimmedName,
          description: newDescription.trim() || null,
          model_type: modelType,
          base_model: run?.base_model ?? null,
          tags: run?.tags ?? [],
        };
        const createdModel = await proxy<RegisteredModel>("POST", "/api/v1/mlops/models", createReq);
        targetModelId = createdModel.id;
        targetModelName = createdModel.name;
      } else if (!targetModelId) {
        setError("Select an existing model.");
        setSubmitting(false);
        return;
      }

      const versionReq: CreateModelVersionRequest = {
        run_id: run?.id ?? null,
        stage,
        lora_config: extractLoraConfig(run),
        metrics_snapshot: run?.summary_metrics ?? {},
        model_card: modelCard.trim() || null,
      };
      const ver = await proxy<ModelVersion>(
        "POST",
        `/api/v1/mlops/models/${encodeURIComponent(targetModelId)}/versions`,
        versionReq,
      );

      setCreated({
        modelId: targetModelId,
        modelName: targetModelName || targetModelId,
        version: ver.version,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register model version.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="register-model-title"
        className="w-full max-w-lg rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-5 py-3.5">
          <div>
            <h2 id="register-model-title" className="text-base font-semibold text-zinc-50">
              Register Model Version
            </h2>
            {run && (
              <p className="mt-0.5 text-xs text-zinc-400">
                Source run: <span className="font-mono text-zinc-200">{run.name}</span>
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-xs text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            Close
          </button>
        </div>

        {created ? (
          <div className="space-y-4 p-5">
            <div className="rounded-md border border-emerald-800/60 bg-emerald-950/40 p-4 text-sm text-emerald-200">
              Registered <span className="font-mono font-semibold">{created.modelName}</span>{" "}
              version <span className="font-mono font-semibold">v{created.version}</span>.
            </div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={onClose}
                className="h-8 rounded border border-zinc-700 bg-zinc-800/80 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
              >
                Done
              </button>
              <Link
                href={`/models?model=${encodeURIComponent(created.modelId)}`}
                className={`inline-flex h-8 items-center rounded px-3 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
              >
                View in Model Registry
              </Link>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 p-5">
            {error && (
              <div
                role="alert"
                className="rounded border border-red-800/70 bg-red-950/50 px-3 py-2 text-xs text-red-300"
              >
                {error}
              </div>
            )}

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setMode("new")}
                className={`h-8 flex-1 rounded border px-3 text-xs font-medium transition ${
                  mode === "new"
                    ? "border-emerald-700/70 bg-emerald-950/50 text-emerald-200"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                Create New Model
              </button>
              <button
                type="button"
                disabled={loadingModels || models.length === 0}
                onClick={() => setMode("existing")}
                className={`h-8 flex-1 rounded border px-3 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  mode === "existing"
                    ? "border-emerald-700/70 bg-emerald-950/50 text-emerald-200"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                Add Version to Existing ({models.length})
              </button>
            </div>

            {mode === "new" || models.length === 0 ? (
              <>
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-zinc-300">Model Name</span>
                  <input
                    ref={firstInputRef}
                    type="text"
                    required
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="e.g. support-copilot-lora"
                    className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-400 focus:outline-none"
                  />
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block space-y-1">
                    <span className="text-xs font-medium text-zinc-300">Model Type</span>
                    <select
                      value={modelType}
                      onChange={(e) => setModelType(e.target.value as ModelType)}
                      className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
                    >
                      <option value="llm">LLM</option>
                      <option value="lora">LoRA Adapter</option>
                      <option value="classifier">Classifier</option>
                      <option value="regressor">Regressor</option>
                      <option value="embedding">Embedding</option>
                    </select>
                  </label>

                  <label className="block space-y-1">
                    <span className="text-xs font-medium text-zinc-300">Initial Stage</span>
                    <select
                      value={stage}
                      onChange={(e) => setStage(e.target.value as ModelStage)}
                      className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
                    >
                      <option value="none">None</option>
                      <option value="staging">Staging</option>
                      <option value="production">Production (admin)</option>
                    </select>
                  </label>
                </div>

                <label className="block space-y-1">
                  <span className="text-xs font-medium text-zinc-300">Description</span>
                  <input
                    type="text"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                    placeholder="Optional model summary"
                    className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 text-xs text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-400 focus:outline-none"
                  />
                </label>
              </>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-zinc-300">Target Model</span>
                  <select
                    value={selectedModelId}
                    onChange={(e) => setSelectedModelId(e.target.value)}
                    className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name} (v{m.latest_version ?? 0})
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block space-y-1">
                  <span className="text-xs font-medium text-zinc-300">Initial Stage</span>
                  <select
                    value={stage}
                    onChange={(e) => setStage(e.target.value as ModelStage)}
                    className="h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
                  >
                    <option value="none">None</option>
                    <option value="staging">Staging</option>
                    <option value="production">Production (admin)</option>
                  </select>
                </label>
              </div>
            )}

            <label className="block space-y-1">
              <span className="text-xs font-medium text-zinc-300">Model Card Notes</span>
              <textarea
                rows={3}
                value={modelCard}
                onChange={(e) => setModelCard(e.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-900 p-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-400 focus:outline-none"
              />
            </label>

            <div className="flex items-center justify-end gap-3 border-t border-zinc-800 pt-4">
              <button
                type="button"
                onClick={onClose}
                className="h-8 rounded border border-zinc-700 bg-zinc-800/80 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className={`h-8 rounded px-4 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
              >
                {submitting ? "Registering…" : "Register Version"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
