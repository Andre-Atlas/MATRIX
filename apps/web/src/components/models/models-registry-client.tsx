"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatScore, formatTimestamp } from "@/lib/format";
import { filterModels } from "@/lib/mlops";
import {
  getAliasStyle,
  getModelStageStyle,
  getTaskTypeStyle,
  PRIMARY_BTN_CLASS,
} from "@/lib/mlops-tokens";
import type {
  ArtifactRecord,
  LineageGraph,
  ModelStage,
  ModelType,
  ModelVersion,
  RegisteredModel,
  RegisteredModelDetail,
} from "@/lib/types";
import { LineageDagTab } from "./lineage-dag-tab";
import { ModelCardTab } from "./model-card-tab";
import { PromoteModal } from "./promote-modal";
import { VersionDiffDrawer } from "./version-diff-drawer";
import { VersionsTab } from "./versions-tab";

export interface ModelsRegistryClientProps {
  initialModels: RegisteredModel[];
  initialDetail: RegisteredModelDetail | null;
  initialLineage: LineageGraph | null;
  initialArtifacts: ArtifactRecord[];
  initialSelectedId: string | null;
  initialError?: string | null;
  initialTab?: DetailTabId;
}

type DetailTabId = "versions" | "lineage" | "card";

const DETAIL_TABS: { id: DetailTabId; label: string }[] = [
  { id: "versions", label: "Versions & Promotion" },
  { id: "lineage", label: "Lineage DAG" },
  { id: "card", label: "Model Card & Artifacts" },
];

function getPrimaryStage(model: RegisteredModel): ModelStage {
  const stages = model.stages ?? {};
  if ((stages.production ?? []).length > 0) return "production";
  if ((stages.staging ?? []).length > 0) return "staging";
  if ((stages.none ?? []).length > 0) return "none";
  if ((stages.archived ?? []).length > 0) return "archived";
  return "none";
}

export function ModelsRegistryClient({
  initialModels,
  initialDetail,
  initialLineage,
  initialArtifacts,
  initialSelectedId,
  initialError = null,
  initialTab = "versions",
}: ModelsRegistryClientProps) {
  const [models, setModels] = useState<RegisteredModel[]>(initialModels);
  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);
  const [detail, setDetail] = useState<RegisteredModelDetail | null>(initialDetail);
  const [lineage, setLineage] = useState<LineageGraph>(
    initialLineage ?? { nodes: [], edges: [] },
  );
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>(initialArtifacts);

  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  // Filters
  const [search, setSearch] = useState("");
  const [modelTypeFilter, setModelTypeFilter] = useState("all");
  const [stageFilter, setStageFilter] = useState("all");
  const [aliasFilter, setAliasFilter] = useState("all");

  // Detail Tabs (WAI-ARIA)
  const [activeTab, setActiveTab] = useState<DetailTabId>(
    initialTab === "lineage" || initialTab === "card" ? initialTab : "versions",
  );
  const tabRefs = useRef<Record<DetailTabId, HTMLButtonElement | null>>({
    versions: null,
    lineage: null,
    card: null,
  });

  // Modals & Drawers
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [registerVersionOpen, setRegisterVersionOpen] = useState(false);
  const [promoteState, setPromoteState] = useState<{
    open: boolean;
    mode: "stage" | "alias";
    version: ModelVersion | null;
  }>({ open: false, mode: "stage", version: null });
  const [diffState, setDiffState] = useState<{
    open: boolean;
    vA: ModelVersion | null;
    vB: ModelVersion | null;
  }>({ open: false, vA: null, vB: null });

  // Register Model form state
  const [newModelName, setNewModelName] = useState("");
  const [newModelType, setNewModelType] = useState<ModelType>("lora");
  const [newBaseModel, setNewBaseModel] = useState("");
  const [newModelDesc, setNewModelDesc] = useState("");
  const [newModelTags, setNewModelTags] = useState("");
  const [registerSubmitting, setRegisterSubmitting] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  // Register Version form state
  const [verRunId, setVerRunId] = useState("");
  const [verArtifactId, setVerArtifactId] = useState("");
  const [verStage, setVerStage] = useState<ModelStage>("none");
  const [verLoraR, setVerLoraR] = useState("16");
  const [verLoraAlpha, setVerLoraAlpha] = useState("32");
  const [verModelCard, setVerModelCard] = useState("");
  const [verSubmitting, setVerSubmitting] = useState(false);
  const [verError, setVerError] = useState<string | null>(null);

  const loadModelDetail = useCallback(async (modelId: string) => {
    setDetailLoading(true);
    try {
      const [detailRes, lineageRes] = await Promise.all([
        fetch(`/api/proxy/api/v1/mlops/models/${encodeURIComponent(modelId)}`, {
          cache: "no-store",
        }),
        fetch(`/api/proxy/api/v1/mlops/models/${encodeURIComponent(modelId)}/lineage`, {
          cache: "no-store",
        }),
      ]);
      if (!detailRes.ok) {
        throw new Error(`Failed to load model detail (${detailRes.status})`);
      }
      const nextDetail = (await detailRes.json()) as RegisteredModelDetail;
      const nextLineage = lineageRes.ok
        ? ((await lineageRes.json()) as LineageGraph)
        : { model_id: modelId, nodes: [], edges: [] };
      setDetail(nextDetail);
      setLineage(nextLineage);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load model detail");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const refreshAll = useCallback(
    async (preferId?: string | null) => {
      setListLoading(true);
      setError(null);
      try {
        const [modelsRes, artRes] = await Promise.all([
          fetch("/api/proxy/api/v1/mlops/models", { cache: "no-store" }),
          fetch("/api/proxy/api/v1/mlops/artifacts", { cache: "no-store" }),
        ]);
        if (!modelsRes.ok) {
          throw new Error(`Failed to load models (${modelsRes.status})`);
        }
        const modelsJson = (await modelsRes.json()) as { items?: RegisteredModel[] };
        const items = modelsJson.items ?? [];
        setModels(items);

        if (artRes.ok) {
          const artJson = (await artRes.json()) as { items?: ArtifactRecord[] };
          setArtifacts(artJson.items ?? []);
        }

        const targetId =
          preferId && items.some((m) => m.id === preferId)
            ? preferId
            : selectedId && items.some((m) => m.id === selectedId)
              ? selectedId
              : items[0]?.id ?? null;

        setSelectedId(targetId);
        if (targetId) {
          await loadModelDetail(targetId);
        } else {
          setDetail(null);
          setLineage({ nodes: [], edges: [] });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load model registry");
      } finally {
        setListLoading(false);
      }
    },
    [selectedId, loadModelDetail],
  );

  const handleSelectModel = (modelId: string) => {
    setSelectedId(modelId);
    void loadModelDetail(modelId);
  };

  // Escape key for Register Model / Register Version modals
  useEffect(() => {
    if (!registerModalOpen && !registerVersionOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setRegisterModalOpen(false);
        setRegisterVersionOpen(false);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [registerModalOpen, registerVersionOpen]);

  // KPI calculations
  const kpis = useMemo(() => {
    const total = models.length;
    const inProd = models.filter(
      (m) =>
        (m.stages?.production ?? []).length > 0 ||
        Object.keys(m.aliases ?? {}).some((a) => a.toLowerCase() === "champion"),
    ).length;
    const inStaging = models.filter(
      (m) =>
        (m.stages?.staging ?? []).length > 0 ||
        Object.keys(m.aliases ?? {}).some((a) => a.toLowerCase() === "challenger"),
    ).length;
    const loraCount = models.filter((m) => m.model_type === "lora").length;
    return { total, inProd, inStaging, loraCount };
  }, [models]);

  const availableAliases = useMemo(() => {
    const set = new Set<string>(["champion", "challenger"]);
    for (const m of models) {
      for (const a of Object.keys(m.aliases ?? {})) {
        set.add(a);
      }
    }
    return [...set].sort();
  }, [models]);

  const filteredModels = useMemo(
    () =>
      filterModels(models, {
        search,
        model_type: modelTypeFilter,
        stage: stageFilter,
        alias: aliasFilter,
      }),
    [models, search, modelTypeFilter, stageFilter, aliasFilter],
  );

  const hasActiveFilters =
    Boolean(search.trim()) ||
    modelTypeFilter !== "all" ||
    stageFilter !== "all" ||
    aliasFilter !== "all";

  const clearFilters = () => {
    setSearch("");
    setModelTypeFilter("all");
    setStageFilter("all");
    setAliasFilter("all");
  };

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, currentIdx: number) => {
    let nextIdx: number | null = null;
    if (e.key === "ArrowRight") nextIdx = (currentIdx + 1) % DETAIL_TABS.length;
    else if (e.key === "ArrowLeft") nextIdx = (currentIdx - 1 + DETAIL_TABS.length) % DETAIL_TABS.length;
    else if (e.key === "Home") nextIdx = 0;
    else if (e.key === "End") nextIdx = DETAIL_TABS.length - 1;

    if (nextIdx !== null) {
      e.preventDefault();
      const nextTab = DETAIL_TABS[nextIdx];
      setActiveTab(nextTab.id);
      tabRefs.current[nextTab.id]?.focus();
    }
  };

  const handleCreateModel = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegisterError(null);
    const trimmedName = newModelName.trim();
    if (!trimmedName) {
      setRegisterError("Model name is required.");
      return;
    }
    setRegisterSubmitting(true);
    try {
      const tags = newModelTags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const res = await fetch("/api/proxy/api/v1/mlops/models", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: trimmedName,
          model_type: newModelType,
          base_model: newBaseModel.trim() || null,
          description: newModelDesc.trim() || null,
          tags,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Failed to register model (${res.status})`);
      }
      const created = (await res.json()) as RegisteredModel;
      setRegisterModalOpen(false);
      setNewModelName("");
      setNewBaseModel("");
      setNewModelDesc("");
      setNewModelTags("");
      await refreshAll(created.id);
    } catch (err) {
      setRegisterError(err instanceof Error ? err.message : "Failed to register model");
    } finally {
      setRegisterSubmitting(false);
    }
  };

  const handleCreateVersion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    setVerError(null);
    setVerSubmitting(true);
    try {
      const isLora = detail.model_type === "lora";
      const rVal = Number(verLoraR);
      const alphaVal = Number(verLoraAlpha);
      const loraConfig =
        isLora && Number.isFinite(rVal) && Number.isFinite(alphaVal)
          ? { r: rVal, lora_alpha: alphaVal }
          : null;

      const res = await fetch(
        `/api/proxy/api/v1/mlops/models/${encodeURIComponent(detail.id)}/versions`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            run_id: verRunId.trim() || null,
            artifact_id: verArtifactId.trim() || null,
            stage: verStage,
            lora_config: loraConfig,
            model_card: verModelCard.trim() || null,
          }),
        },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Failed to register version (${res.status})`);
      }
      setRegisterVersionOpen(false);
      setVerRunId("");
      setVerArtifactId("");
      setVerStage("none");
      setVerModelCard("");
      await refreshAll(detail.id);
    } catch (err) {
      setVerError(err instanceof Error ? err.message : "Failed to register version");
    } finally {
      setVerSubmitting(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold text-zinc-50">
            Model & LoRA Registry
          </h1>
          <p className="mt-0.5 text-sm text-zinc-400">
            Immutable model versions, stage promotions, @champion/@challenger aliases, and end-to-end lineage DAG.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void refreshAll(selectedId)}
            disabled={listLoading}
            className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 transition hover:bg-zinc-800 disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => {
              setRegisterError(null);
              setRegisterModalOpen(true);
            }}
            className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition ${PRIMARY_BTN_CLASS}`}
          >
            + Register Model
          </button>
        </div>
      </header>

      {/* Error State */}
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-800/70 bg-red-950/50 px-4 py-3 text-xs text-red-200"
        >
          <div>
            <span className="font-semibold text-red-100">Registry error:</span> {error}
          </div>
          <button
            type="button"
            onClick={() => void refreshAll(selectedId)}
            className="inline-flex h-7 items-center rounded border border-red-700/70 bg-red-900/40 px-2.5 text-xs font-medium text-red-100 hover:bg-red-900/60"
          >
            Retry
          </button>
        </div>
      )}

      {/* 4 KPIs */}
      <section aria-label="Registry summary metrics" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Registered Models
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-zinc-50">{kpis.total}</div>
        </div>
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            In Production (@champion)
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-emerald-300">{kpis.inProd}</div>
        </div>
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            In Staging (@challenger)
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-amber-300">{kpis.inStaging}</div>
        </div>
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            LoRA Adapters
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-violet-300">{kpis.loraCount}</div>
        </div>
      </section>

      {/* Filters */}
      <section
        aria-label="Filter registered models"
        className="flex flex-wrap items-center gap-2.5 rounded-md border border-zinc-800 bg-zinc-900/60 p-3"
      >
        <div className="min-w-[200px] flex-1">
          <label htmlFor="models-search" className="sr-only">
            Search models
          </label>
          <input
            id="models-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, base model, tag, or ID…"
            className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
          />
        </div>

        <div className="flex items-center gap-1.5">
          <label htmlFor="filter-model-type" className="text-xs text-zinc-400">
            Type
          </label>
          <select
            id="filter-model-type"
            value={modelTypeFilter}
            onChange={(e) => setModelTypeFilter(e.target.value)}
            className="h-8 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All types</option>
            <option value="llm">LLM</option>
            <option value="lora">LoRA</option>
            <option value="classifier">Classifier</option>
            <option value="regressor">Regressor</option>
            <option value="embedding">Embedding</option>
          </select>
        </div>

        <div className="flex items-center gap-1.5">
          <label htmlFor="filter-stage" className="text-xs text-zinc-400">
            Stage
          </label>
          <select
            id="filter-stage"
            value={stageFilter}
            onChange={(e) => setStageFilter(e.target.value)}
            className="h-8 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All stages</option>
            <option value="production">Production</option>
            <option value="staging">Staging</option>
            <option value="archived">Archived</option>
            <option value="none">None</option>
          </select>
        </div>

        <div className="flex items-center gap-1.5">
          <label htmlFor="filter-alias" className="text-xs text-zinc-400">
            Alias
          </label>
          <select
            id="filter-alias"
            value={aliasFilter}
            onChange={(e) => setAliasFilter(e.target.value)}
            className="h-8 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All aliases</option>
            {availableAliases.map((a) => (
              <option key={a} value={a}>
                @{a}
              </option>
            ))}
          </select>
        </div>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-2.5 text-xs text-zinc-200 hover:bg-zinc-800"
          >
            Clear
          </button>
        )}
      </section>

      {/* Catalog Table */}
      {listLoading ? (
        <div
          role="status"
          aria-label="Loading models"
          className="space-y-2 rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
        >
          <div className="h-8 w-full animate-pulse rounded bg-zinc-800/70 motion-reduce:animate-none" />
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/50 motion-reduce:animate-none" />
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/50 motion-reduce:animate-none" />
          <span className="sr-only">Loading models…</span>
        </div>
      ) : filteredModels.length === 0 ? (
        <div className="rounded-md border border-dashed border-zinc-800 px-6 py-12 text-center">
          <p className="text-sm font-medium text-zinc-200">
            {models.length === 0
              ? "No models registered yet"
              : "No models match the active filters"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-xs text-zinc-400">
            {models.length === 0
              ? "Register your first LLM, LoRA adapter, or classic ML model to track immutable versions, promotions, and lineage."
              : "Adjust your search query or reset the type, stage, and alias filters."}
          </p>
          <div className="mt-4 flex justify-center gap-2">
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
              >
                Clear Filters
              </button>
            )}
            <button
              type="button"
              onClick={() => setRegisterModalOpen(true)}
              className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition ${PRIMARY_BTN_CLASS}`}
            >
              + Register Model
            </button>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-900/60">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/80">
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Model
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Type
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Latest Version
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Aliases
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Stage
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Metrics
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Updated (UTC)
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {filteredModels.map((m) => {
                const isSelected = m.id === selectedId;
                const typeStyle = getTaskTypeStyle(m.model_type);
                const stageStyle = getModelStageStyle(getPrimaryStage(m));
                const aliasEntries = Object.entries(m.aliases ?? {});
                const metricEntries = Object.entries(m.latest_metrics ?? {});

                return (
                  <tr
                    key={m.id}
                    aria-selected={isSelected}
                    onClick={() => handleSelectModel(m.id)}
                    className={`cursor-pointer transition ${
                      isSelected ? "bg-emerald-950/20" : "hover:bg-zinc-900/80"
                    }`}
                  >
                    <td className="px-3 py-2.5 align-middle">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSelectModel(m.id);
                        }}
                        className="text-left focus:outline-none"
                      >
                        <div className="font-mono text-xs font-semibold text-zinc-100">
                          {m.name}
                        </div>
                        {m.base_model && (
                          <div className="mt-0.5 font-mono text-[11px] text-zinc-400">
                            {m.base_model}
                          </div>
                        )}
                      </button>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-middle">
                      <span
                        className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${typeStyle.badge}`}
                      >
                        {typeStyle.label}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-middle font-mono tabular-nums text-zinc-200">
                      {m.latest_version ? `v${m.latest_version}` : "—"}
                    </td>
                    <td className="px-3 py-2.5 align-middle">
                      {aliasEntries.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {aliasEntries.map(([aliasName, ver]) => (
                            <span
                              key={aliasName}
                              className={`inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[11px] ${getAliasStyle(aliasName)}`}
                            >
                              @{aliasName}
                              <span className="opacity-80">v{ver}</span>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-zinc-400">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-middle">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-medium ${stageStyle.badge}`}
                      >
                        <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${stageStyle.dot}`} />
                        {stageStyle.label}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 align-middle">
                      {metricEntries.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {metricEntries.slice(0, 2).map(([k, val]) => (
                            <span
                              key={k}
                              className="inline-flex items-center gap-1 rounded bg-zinc-950/80 px-1.5 py-0.5 font-mono text-[11px] tabular-nums"
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
                    <td className="whitespace-nowrap px-3 py-2.5 align-middle font-mono text-[11px] tabular-nums text-zinc-400">
                      {formatTimestamp(m.updated_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail Panel */}
      {selectedId && (
        <section
          aria-label="Selected model details"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 space-y-4"
        >
          {detailLoading && !detail ? (
            <div role="status" aria-label="Loading model detail" className="space-y-3 py-4">
              <div className="h-5 w-48 animate-pulse rounded bg-zinc-800 motion-reduce:animate-none" />
              <div className="h-32 w-full animate-pulse rounded bg-zinc-800/60 motion-reduce:animate-none" />
            </div>
          ) : detail ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800 pb-3">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="font-mono text-sm font-semibold text-zinc-50">
                      {detail.name}
                    </h2>
                    <span
                      className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${getTaskTypeStyle(detail.model_type).badge}`}
                    >
                      {getTaskTypeStyle(detail.model_type).label}
                    </span>
                    {detailLoading && (
                      <span
                        role="status"
                        className="text-[11px] text-zinc-400"
                      >
                        Updating…
                      </span>
                    )}
                  </div>
                  {detail.description && (
                    <p className="text-xs text-zinc-300">{detail.description}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                    {detail.base_model && (
                      <span>
                        Base: <span className="font-mono text-zinc-200">{detail.base_model}</span>
                      </span>
                    )}
                    {detail.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded border border-zinc-800 bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* WAI-ARIA Tablist */}
              <div
                role="tablist"
                aria-label="Model detail sections"
                className="flex flex-wrap gap-1 border-b border-zinc-800 pb-2"
              >
                {DETAIL_TABS.map((tab, idx) => {
                  const isActive = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      ref={(el) => {
                        tabRefs.current[tab.id] = el;
                      }}
                      id={`tab-${tab.id}`}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      aria-controls={`panel-${tab.id}`}
                      tabIndex={isActive ? 0 : -1}
                      onClick={() => setActiveTab(tab.id)}
                      onKeyDown={(e) => handleTabKeyDown(e, idx)}
                      className={`h-8 rounded-md px-3 text-xs font-medium transition ${
                        isActive
                          ? "border border-emerald-700/70 bg-emerald-950/40 text-emerald-200"
                          : "border border-transparent text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                      }`}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>

              {/* Tabpanels */}
              <div
                id={`panel-${activeTab}`}
                role="tabpanel"
                aria-labelledby={`tab-${activeTab}`}
              >
                {activeTab === "versions" && (
                  <VersionsTab
                    model={detail}
                    onPromoteStage={(ver) =>
                      setPromoteState({ open: true, mode: "stage", version: ver })
                    }
                    onMoveAlias={(ver) =>
                      setPromoteState({ open: true, mode: "alias", version: ver })
                    }
                    onCompareVersions={(vA, vB) =>
                      setDiffState({ open: true, vA, vB })
                    }
                    onRegisterVersion={() => {
                      setVerError(null);
                      setRegisterVersionOpen(true);
                    }}
                  />
                )}
                {activeTab === "lineage" && (
                  <LineageDagTab lineage={lineage} loading={detailLoading} />
                )}
                {activeTab === "card" && (
                  <ModelCardTab model={detail} artifacts={artifacts} lineage={lineage} />
                )}
              </div>
            </>
          ) : null}
        </section>
      )}

      {/* Register Model Modal */}
      {registerModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setRegisterModalOpen(false);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="register-model-title"
            className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="register-model-title" className="text-sm font-semibold text-zinc-50">
                  Register New Model
                </h2>
                <p className="mt-0.5 text-xs text-zinc-400">
                  Create a model entry in the registry to track versions and lineage.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setRegisterModalOpen(false)}
                aria-label="Close modal"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateModel} className="mt-4 space-y-3 text-xs">
              <div>
                <label htmlFor="reg-model-name" className="block font-medium text-zinc-300">
                  Model Name *
                </label>
                <input
                  id="reg-model-name"
                  type="text"
                  required
                  value={newModelName}
                  onChange={(e) => setNewModelName(e.target.value)}
                  placeholder="e.g. support-copilot-lora"
                  className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="reg-model-type" className="block font-medium text-zinc-300">
                    Model Type
                  </label>
                  <select
                    id="reg-model-type"
                    value={newModelType}
                    onChange={(e) => setNewModelType(e.target.value as ModelType)}
                    className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  >
                    <option value="lora">LoRA</option>
                    <option value="llm">LLM</option>
                    <option value="classifier">Classifier</option>
                    <option value="regressor">Regressor</option>
                    <option value="embedding">Embedding</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="reg-base-model" className="block font-medium text-zinc-300">
                    Base Model
                  </label>
                  <input
                    id="reg-base-model"
                    type="text"
                    value={newBaseModel}
                    onChange={(e) => setNewBaseModel(e.target.value)}
                    placeholder="meta-llama/Llama-3.1-8B"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="reg-model-desc" className="block font-medium text-zinc-300">
                  Description
                </label>
                <textarea
                  id="reg-model-desc"
                  rows={2}
                  value={newModelDesc}
                  onChange={(e) => setNewModelDesc(e.target.value)}
                  placeholder="Purpose, training domain, or deployment notes…"
                  className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-900 p-2 text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div>
                <label htmlFor="reg-model-tags" className="block font-medium text-zinc-300">
                  Tags (comma-separated)
                </label>
                <input
                  id="reg-model-tags"
                  type="text"
                  value={newModelTags}
                  onChange={(e) => setNewModelTags(e.target.value)}
                  placeholder="lora, support, llama3"
                  className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              {registerError && (
                <div
                  role="alert"
                  className="rounded-md border border-red-800/70 bg-red-950/50 px-3 py-2 text-xs text-red-200"
                >
                  {registerError}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setRegisterModalOpen(false)}
                  className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={registerSubmitting}
                  className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
                >
                  {registerSubmitting ? "Registering…" : "Register Model"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Register Version Modal */}
      {registerVersionOpen && detail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setRegisterVersionOpen(false);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="register-version-title"
            className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="register-version-title" className="text-sm font-semibold text-zinc-50">
                  Register Version for <span className="font-mono text-emerald-300">{detail.name}</span>
                </h2>
                <p className="mt-0.5 text-xs text-zinc-400">
                  Creates an immutable version snapshot.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setRegisterVersionOpen(false)}
                aria-label="Close modal"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateVersion} className="mt-4 space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="ver-run-id" className="block font-medium text-zinc-300">
                    Training Run ID
                  </label>
                  <input
                    id="ver-run-id"
                    type="text"
                    value={verRunId}
                    onChange={(e) => setVerRunId(e.target.value)}
                    placeholder="run-lora-01"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label htmlFor="ver-artifact-id" className="block font-medium text-zinc-300">
                    Artifact ID
                  </label>
                  <input
                    id="ver-artifact-id"
                    type="text"
                    value={verArtifactId}
                    onChange={(e) => setVerArtifactId(e.target.value)}
                    placeholder="art-lora-v3"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="ver-stage" className="block font-medium text-zinc-300">
                  Initial Stage
                </label>
                <select
                  id="ver-stage"
                  value={verStage}
                  onChange={(e) => setVerStage(e.target.value as ModelStage)}
                  className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
                >
                  <option value="none">None</option>
                  <option value="staging">Staging</option>
                  <option value="production">Production</option>
                </select>
              </div>

              {detail.model_type === "lora" && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="ver-lora-r" className="block font-medium text-zinc-300">
                      LoRA r
                    </label>
                    <input
                      id="ver-lora-r"
                      type="number"
                      min={1}
                      value={verLoraR}
                      onChange={(e) => setVerLoraR(e.target.value)}
                      className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label htmlFor="ver-lora-alpha" className="block font-medium text-zinc-300">
                      LoRA alpha
                    </label>
                    <input
                      id="ver-lora-alpha"
                      type="number"
                      min={1}
                      value={verLoraAlpha}
                      onChange={(e) => setVerLoraAlpha(e.target.value)}
                      className="mt-1 h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 font-mono text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
                    />
                  </div>
                </div>
              )}

              <div>
                <label htmlFor="ver-card" className="block font-medium text-zinc-300">
                  Model Card Notes
                </label>
                <textarea
                  id="ver-card"
                  rows={3}
                  value={verModelCard}
                  onChange={(e) => setVerModelCard(e.target.value)}
                  placeholder="Summary of training data, eval results, and intended use…"
                  className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-900 p-2 text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              {verError && (
                <div
                  role="alert"
                  className="rounded-md border border-red-800/70 bg-red-950/50 px-3 py-2 text-xs text-red-200"
                >
                  {verError}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setRegisterVersionOpen(false)}
                  className="inline-flex h-8 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={verSubmitting}
                  className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
                >
                  {verSubmitting ? "Creating…" : "Create Version"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Promote Stage / Move Alias Modal */}
      {detail && promoteState.version && (
        <PromoteModal
          open={promoteState.open}
          mode={promoteState.mode}
          model={detail}
          version={promoteState.version}
          onClose={() => setPromoteState((prev) => ({ ...prev, open: false }))}
          onSuccess={() => refreshAll(detail.id)}
        />
      )}

      {/* Version Diff Drawer */}
      {detail && (
        <VersionDiffDrawer
          open={diffState.open}
          model={detail}
          versionA={diffState.vA}
          versionB={diffState.vB}
          artifacts={artifacts}
          onClose={() => setDiffState((prev) => ({ ...prev, open: false }))}
        />
      )}
    </div>
  );
}
