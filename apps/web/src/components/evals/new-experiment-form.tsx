"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { proxy } from "@/lib/proxy-client";
import {
  METRIC_TYPES,
  MODEL_PROVIDERS,
  type CreateExperimentRequest,
  type Dataset,
  type ExperimentListItem,
  type Message,
  type MetricSpec,
  type MetricType,
  type ModelProvider,
  type Variant,
} from "@/lib/types";
import { area, field, label, primaryBtn, secondaryBtn, VariantSwatch } from "./ui";

type ParamKind = "text" | "number" | "json" | "regex";
type MetricInfo = {
  group: "RAG" | "LLM judge" | "Deterministic" | "Budget";
  help: string;
  param?: { key: string; label: string; placeholder: string; kind: ParamKind; required: boolean };
};

export const METRIC_INFO: Record<MetricType, MetricInfo> = {
  faithfulness: { group: "RAG", help: "Share of output statements supported by the context" },
  answer_relevancy: { group: "RAG", help: "How directly the output answers the question" },
  context_precision: { group: "RAG", help: "Relevant chunks ranked first (precision@k)" },
  g_eval: {
    group: "LLM judge",
    help: "Judge scores the output against your criteria",
    param: { key: "criteria", label: "Criteria", placeholder: "Answers concisely using only the context", kind: "text", required: true },
  },
  equals: { group: "Deterministic", help: "Exact match with the expected output" },
  contains: {
    group: "Deterministic",
    help: "Output contains a value",
    param: { key: "value", label: "Value", placeholder: "Retry-After", kind: "text", required: true },
  },
  regex: {
    group: "Deterministic",
    help: "Output matches a regular expression",
    param: { key: "pattern", label: "Pattern", placeholder: "\\[\\d+\\]", kind: "regex", required: true },
  },
  is_json: {
    group: "Deterministic",
    help: "Output parses as JSON (optionally against a schema)",
    param: { key: "schema", label: "JSON schema", placeholder: '{"type": "object"}', kind: "json", required: false },
  },
  levenshtein: { group: "Deterministic", help: "1 − edit distance to the expected output" },
  rouge_l: { group: "Deterministic", help: "ROUGE-L F1 against the expected output" },
  cost: {
    group: "Budget",
    help: "Passes when the call costs at most",
    param: { key: "max_usd", label: "Max USD", placeholder: "0.002", kind: "number", required: true },
  },
  latency: {
    group: "Budget",
    help: "Passes when the call takes at most",
    param: { key: "max_ms", label: "Max ms", placeholder: "2500", kind: "number", required: true },
  },
};

type PromptMode = "registry" | "inline";
type VariantRow = {
  key: number;
  label: string;
  mode: PromptMode;
  promptName: string;
  promptVersion: string;
  template: string;
  promptType: "text" | "chat";
  provider: ModelProvider;
  model: string;
  params: string;
};
type MetricRow = { enabled: boolean; param: string; threshold: string };
type Errors = Record<string, string>;

let nextKey = 1;
const row = (r: Partial<VariantRow>): VariantRow => ({
  key: nextKey++,
  label: "",
  mode: "registry",
  promptName: "rag-answer",
  promptVersion: "1",
  template: "",
  promptType: "text",
  provider: "openai",
  model: "gpt-4o-mini",
  params: '{"temperature": 0}',
  ...r,
});

const DEFAULT_MODEL: Record<ModelProvider, string> = { openai: "gpt-4o-mini", anthropic: "claude-haiku-4.5", fake: "echo" };

function promptTag(r: VariantRow): string {
  if (r.mode === "registry") return `${r.promptName || "prompt"} v${r.promptVersion || "?"}`;
  return `inline ${r.promptType}`;
}
const autoLabel = (r: VariantRow) =>
  `${r.mode === "registry" ? `v${r.promptVersion || "?"}` : `inline-${r.promptType}`} · ${r.model || r.provider}`;

function parseObject(text: string): Record<string, unknown> | null | "invalid" {
  if (!text.trim()) return null;
  try {
    const v: unknown = JSON.parse(text);
    return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : "invalid";
  } catch {
    return "invalid";
  }
}

function parseChat(text: string): Message[] | null {
  try {
    const v: unknown = JSON.parse(text);
    if (!Array.isArray(v) || !v.length) return null;
    return v.every((m) => typeof m?.role === "string" && typeof m?.content === "string") ? (v as Message[]) : null;
  } catch {
    return null;
  }
}

function build(
  name: string,
  datasetId: string,
  baseline: string,
  variants: VariantRow[],
  metrics: Record<MetricType, MetricRow>,
): { body: CreateExperimentRequest; errors: Errors } {
  const errors: Errors = {};
  if (!name.trim()) errors.name = "Name is required.";
  if (!datasetId) errors.dataset = "Pick a dataset.";
  if (!variants.length) errors.variants = "Add at least one variant.";
  const labels = new Map<string, number>();
  const outVariants: Variant[] = variants.map((r, i) => {
    const lbl = (r.label.trim() || autoLabel(r)).trim();
    if (labels.has(lbl)) errors[`v${i}.label`] = `Label “${lbl}” is already used by variant ${labels.get(lbl)! + 1}.`;
    labels.set(lbl, i);
    let prompt: Variant["prompt"];
    if (r.mode === "registry") {
      const version = Number(r.promptVersion);
      if (!r.promptName.trim()) errors[`v${i}.prompt`] = "Prompt name is required.";
      else if (!Number.isInteger(version) || version < 1) errors[`v${i}.prompt`] = "Version must be a positive integer.";
      prompt = { name: r.promptName.trim(), version };
    } else if (r.promptType === "chat") {
      const msgs = parseChat(r.template);
      if (!msgs) errors[`v${i}.prompt`] = 'Chat template must be a JSON array of {"role", "content"} messages.';
      prompt = { type: "chat", template: msgs ?? [] };
    } else {
      if (!r.template.trim()) errors[`v${i}.prompt`] = "Template is required.";
      prompt = { type: "text", template: r.template };
    }
    if (!r.model.trim()) errors[`v${i}.model`] = "Model name is required.";
    const params = parseObject(r.params);
    if (params === "invalid") errors[`v${i}.params`] = "Params must be a JSON object.";
    return {
      label: lbl,
      prompt,
      model: { provider: r.provider, name: r.model.trim(), ...(params && params !== "invalid" ? { params } : {}) },
    };
  });

  const outMetrics: MetricSpec[] = [];
  for (const type of METRIC_TYPES) {
    const m = metrics[type];
    if (!m.enabled) continue;
    const spec: MetricSpec = { type };
    const p = METRIC_INFO[type].param;
    if (p) {
      const raw = m.param.trim();
      if (!raw) {
        if (p.required) errors[`m.${type}`] = `${p.label} is required.`;
      } else if (p.kind === "number") {
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0) errors[`m.${type}`] = `${p.label} must be a non-negative number.`;
        spec.params = { [p.key]: n };
      } else if (p.kind === "json") {
        const o = parseObject(raw);
        if (o === "invalid") errors[`m.${type}`] = "Schema must be a JSON object.";
        else if (o) spec.params = { [p.key]: o };
      } else if (p.kind === "regex") {
        try {
          new RegExp(raw);
        } catch (e) {
          errors[`m.${type}`] = `Invalid pattern: ${(e as Error).message}`;
        }
        spec.params = { [p.key]: raw };
      } else spec.params = { [p.key]: raw };
    }
    if (m.threshold.trim()) {
      const t = Number(m.threshold);
      if (!Number.isFinite(t) || t < 0 || t > 1) errors[`m.${type}.threshold`] = "Threshold must be between 0 and 1.";
      spec.threshold = t;
    }
    outMetrics.push(spec);
  }
  if (!outMetrics.length) errors.metrics = "Select at least one metric.";

  return {
    body: {
      name: name.trim(),
      dataset_id: datasetId,
      ...(baseline ? { baseline_experiment_id: baseline } : {}),
      variants: outVariants,
      metrics: outMetrics,
    },
    errors,
  };
}

function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <p className="mt-1 text-[11px] normal-case tracking-normal text-red-300">{msg}</p>;
}

const GROUPS: MetricInfo["group"][] = ["RAG", "LLM judge", "Deterministic", "Budget"];

export function NewExperimentForm({
  datasets,
  experiments,
  initialDataset,
}: {
  datasets: Dataset[];
  experiments: ExperimentListItem[];
  initialDataset: string | null;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState(initialDataset ?? datasets[0]?.id ?? "");
  const [baseline, setBaseline] = useState("");
  const [variants, setVariants] = useState<VariantRow[]>(() => [
    row({ promptVersion: "1", provider: "openai", model: "gpt-4o-mini" }),
    row({ promptVersion: "2", provider: "anthropic", model: "claude-haiku-4.5" }),
  ]);
  const [metrics, setMetrics] = useState<Record<MetricType, MetricRow>>(() => {
    const on = new Set<MetricType>(["faithfulness", "answer_relevancy", "context_precision", "latency"]);
    return Object.fromEntries(
      METRIC_TYPES.map((t) => [t, { enabled: on.has(t), param: t === "latency" ? "2500" : "", threshold: "" }]),
    ) as Record<MetricType, MetricRow>;
  });
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const { body, errors } = useMemo(
    () => build(name, datasetId, baseline, variants, metrics),
    [name, datasetId, baseline, variants, metrics],
  );
  const err = (k: string) => (showErrors ? errors[k] : undefined);
  const baselines = experiments.filter((e) => e.dataset_id === datasetId && e.status === "done");
  const dataset = datasets.find((d) => d.id === datasetId);

  const update = (key: number, patch: Partial<VariantRow>) =>
    setVariants((vs) => vs.map((v) => (v.key === key ? { ...v, ...patch } : v)));
  const setMetric = (t: MetricType, patch: Partial<MetricRow>) => setMetrics((m) => ({ ...m, [t]: { ...m[t], ...patch } }));

  /** Cartesian product of the distinct prompts and models already listed (2 + 2 → 2 × 2). */
  function crossProduct() {
    const prompts = [...new Map(variants.map((v) => [promptTag(v) + v.template, v])).values()];
    const models = [...new Map(variants.map((v) => [`${v.provider}/${v.model}/${v.params}`, v])).values()];
    setVariants(
      prompts.flatMap((p) =>
        models.map((m) => {
          const r = row({ ...p, provider: m.provider, model: m.model, params: m.params, label: "" });
          return { ...r, label: autoLabel(r) };
        }),
      ),
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setShowErrors(true);
    setSubmitError(null);
    if (Object.keys(errors).length) return;
    setBusy(true);
    try {
      const res = await proxy<{ id: string; status: string }>("POST", "experiments", body);
      router.push(`/experiments/${encodeURIComponent(res.id)}`);
    } catch (err) {
      setSubmitError((err as Error).message);
      setBusy(false);
    }
  }

  const errorCount = Object.keys(errors).length;
  const sectionTitle = "mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400";

  return (
    <form onSubmit={submit} noValidate aria-label="New experiment" className="space-y-4">
      <section className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
        <h2 className={sectionTitle}>Run</h2>
        <div className="flex flex-wrap items-start gap-3">
          <label className={label}>
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={dataset ? `${dataset.name} prompt sweep` : "prompt sweep"}
              aria-invalid={!!err("name")}
              className={`${field} w-72 normal-case tracking-normal ${err("name") ? "border-red-800" : ""}`}
            />
            <FieldError msg={err("name")} />
          </label>
          <label className={label}>
            Dataset
            <select
              value={datasetId}
              onChange={(e) => {
                setDatasetId(e.target.value);
                setBaseline("");
              }}
              className={`${field} w-56 font-mono normal-case tracking-normal`}
            >
              {datasets.length === 0 && <option value="">no datasets</option>}
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} · {d.item_count} items
                </option>
              ))}
            </select>
            <FieldError msg={err("dataset")} />
          </label>
          <label className={label}>
            Baseline (optional)
            <select
              value={baseline}
              onChange={(e) => setBaseline(e.target.value)}
              className={`${field} w-72 normal-case tracking-normal`}
            >
              <option value="">none — no regression check</option>
              {baselines.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
            <span className="text-[11px] normal-case tracking-normal text-zinc-500">
              Cells are compared with the same variant label in the baseline.
            </span>
          </label>
        </div>
      </section>

      <section className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className={`${sectionTitle} mb-0`}>
            Variants <span className="font-mono text-zinc-600">{variants.length}</span>
          </h2>
          <div className="flex gap-2">
            <button type="button" onClick={crossProduct} className={secondaryBtn} title="Every listed prompt × every listed model">
              Cross prompts × models
            </button>
            <button
              type="button"
              onClick={() => setVariants((vs) => [...vs, row({ ...(vs.at(-1) ?? {}), key: undefined, label: "" })])}
              className={secondaryBtn}
            >
              + Add variant
            </button>
          </div>
        </div>
        <FieldError msg={err("variants")} />
        <div className="hidden grid-cols-[1.25rem_11rem_minmax(0,1fr)_7rem_10rem_11rem_1.5rem] gap-2 px-1 pb-1 text-[11px] uppercase tracking-wider text-zinc-500 lg:grid">
          <span />
          <span>Label</span>
          <span>Prompt</span>
          <span>Provider</span>
          <span>Model</span>
          <span>Params (JSON)</span>
          <span />
        </div>
        <ol className="space-y-2">
          {variants.map((v, i) => {
            const rowErrors = ["label", "prompt", "model", "params"].map((k) => err(`v${i}.${k}`)).filter(Boolean);
            return (
              <li key={v.key} className="rounded border border-zinc-800 bg-zinc-950/50 p-2">
                <div className="grid gap-2 lg:grid-cols-[1.25rem_11rem_minmax(0,1fr)_7rem_10rem_11rem_1.5rem] lg:items-start">
                  <span className="flex h-7 items-center justify-center">
                    <VariantSwatch index={i} />
                  </span>
                  <input
                    aria-label={`Variant ${i + 1} label`}
                    value={v.label}
                    onChange={(e) => update(v.key, { label: e.target.value })}
                    placeholder={autoLabel(v)}
                    aria-invalid={!!err(`v${i}.label`)}
                    className={`${field} w-full font-mono text-xs ${err(`v${i}.label`) ? "border-red-800" : ""}`}
                  />
                  <div className="min-w-0 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <div role="radiogroup" aria-label={`Variant ${i + 1} prompt source`} className="inline-flex rounded border border-zinc-700 bg-zinc-900 p-0.5">
                        {(["registry", "inline"] as PromptMode[]).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            role="radio"
                            aria-checked={v.mode === mode}
                            onClick={() => update(v.key, { mode })}
                            className={`rounded px-2 text-xs leading-5 ${v.mode === mode ? "bg-zinc-700 text-zinc-50" : "text-zinc-400 hover:text-zinc-100"}`}
                          >
                            {mode === "registry" ? "Prompt CMS" : "Inline"}
                          </button>
                        ))}
                      </div>
                      {v.mode === "registry" ? (
                        <>
                          <input
                            aria-label={`Variant ${i + 1} prompt name`}
                            value={v.promptName}
                            onChange={(e) => update(v.key, { promptName: e.target.value })}
                            placeholder="prompt name"
                            className={`${field} w-40 font-mono text-xs ${err(`v${i}.prompt`) ? "border-red-800" : ""}`}
                          />
                          <span className="text-xs text-zinc-500">version</span>
                          <input
                            aria-label={`Variant ${i + 1} prompt version`}
                            value={v.promptVersion}
                            onChange={(e) => update(v.key, { promptVersion: e.target.value })}
                            inputMode="numeric"
                            className={`${field} w-14 font-mono text-xs ${err(`v${i}.prompt`) ? "border-red-800" : ""}`}
                          />
                        </>
                      ) : (
                        <select
                          aria-label={`Variant ${i + 1} template type`}
                          value={v.promptType}
                          onChange={(e) => update(v.key, { promptType: e.target.value as "text" | "chat" })}
                          className={`${field} text-xs`}
                        >
                          <option value="text">text</option>
                          <option value="chat">chat (JSON messages)</option>
                        </select>
                      )}
                    </div>
                    {v.mode === "inline" && (
                      <textarea
                        aria-label={`Variant ${i + 1} template`}
                        value={v.template}
                        onChange={(e) => update(v.key, { template: e.target.value })}
                        rows={3}
                        spellCheck={false}
                        placeholder={
                          v.promptType === "chat"
                            ? '[{"role": "system", "content": "Answer from context."}, {"role": "user", "content": "{{question}}"}]'
                            : "Answer using only this context:\n{{context}}\n\nQuestion: {{question}}"
                        }
                        className={`${area} ${err(`v${i}.prompt`) ? "border-red-800" : ""}`}
                      />
                    )}
                  </div>
                  <select
                    aria-label={`Variant ${i + 1} provider`}
                    value={v.provider}
                    onChange={(e) => {
                      const provider = e.target.value as ModelProvider;
                      update(v.key, { provider, model: v.model === DEFAULT_MODEL[v.provider] ? DEFAULT_MODEL[provider] : v.model });
                    }}
                    className={`${field} w-full text-xs`}
                  >
                    {MODEL_PROVIDERS.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label={`Variant ${i + 1} model name`}
                    value={v.model}
                    onChange={(e) => update(v.key, { model: e.target.value })}
                    placeholder="model"
                    className={`${field} w-full font-mono text-xs ${err(`v${i}.model`) ? "border-red-800" : ""}`}
                  />
                  <input
                    aria-label={`Variant ${i + 1} params JSON`}
                    value={v.params}
                    onChange={(e) => update(v.key, { params: e.target.value })}
                    placeholder='{"temperature": 0}'
                    spellCheck={false}
                    className={`${field} w-full font-mono text-xs ${err(`v${i}.params`) ? "border-red-800" : ""}`}
                  />
                  <button
                    type="button"
                    aria-label={`Remove variant ${i + 1}`}
                    onClick={() => setVariants((vs) => vs.filter((x) => x.key !== v.key))}
                    disabled={variants.length === 1}
                    className="h-7 rounded text-zinc-500 hover:bg-red-950/50 hover:text-red-300 disabled:opacity-30"
                  >
                    ×
                  </button>
                </div>
                {rowErrors.length > 0 && (
                  <ul className="mt-1 pl-7 text-[11px] text-red-300">
                    {rowErrors.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      <section className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
        <h2 className={sectionTitle}>
          Metrics <span className="font-mono text-zinc-600">{METRIC_TYPES.filter((t) => metrics[t].enabled).length}/12</span>
        </h2>
        <FieldError msg={err("metrics")} />
        <div className="grid gap-x-4 gap-y-3 md:grid-cols-2 xl:grid-cols-4">
          {GROUPS.map((g) => (
            <fieldset key={g} className="min-w-0">
              <legend className="mb-1 text-[11px] text-zinc-500">{g}</legend>
              <ul className="space-y-1">
                {METRIC_TYPES.filter((t) => METRIC_INFO[t].group === g).map((t) => {
                  const info = METRIC_INFO[t];
                  const m = metrics[t];
                  const e = err(`m.${t}`) ?? err(`m.${t}.threshold`);
                  return (
                    <li
                      key={t}
                      className={`rounded border px-2 py-1.5 ${m.enabled ? "border-zinc-700 bg-zinc-950/60" : "border-transparent"}`}
                    >
                      <label className="flex cursor-pointer items-start gap-2">
                        <input
                          type="checkbox"
                          checked={m.enabled}
                          onChange={(ev) => setMetric(t, { enabled: ev.target.checked })}
                          className="mt-0.5 accent-emerald-500"
                        />
                        <span className="min-w-0">
                          <span className="font-mono text-xs text-zinc-100">{t}</span>
                          <span className="block text-[11px] leading-4 text-zinc-500">{info.help}</span>
                        </span>
                      </label>
                      {m.enabled && (
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 pl-5">
                          {info.param &&
                            (info.param.kind === "json" ? (
                              <textarea
                                aria-label={`${t} ${info.param.label}`}
                                value={m.param}
                                onChange={(ev) => setMetric(t, { param: ev.target.value })}
                                placeholder={info.param.placeholder}
                                rows={2}
                                spellCheck={false}
                                className={`${area} ${err(`m.${t}`) ? "border-red-800" : ""}`}
                              />
                            ) : (
                              <input
                                aria-label={`${t} ${info.param.label}`}
                                value={m.param}
                                onChange={(ev) => setMetric(t, { param: ev.target.value })}
                                placeholder={`${info.param.label}${info.param.required ? "" : " (optional)"}: ${info.param.placeholder}`}
                                inputMode={info.param.kind === "number" ? "decimal" : undefined}
                                spellCheck={false}
                                className={`${field} min-w-0 flex-1 font-mono text-xs ${err(`m.${t}`) ? "border-red-800" : ""}`}
                              />
                            ))}
                          {info.group !== "Budget" && (
                            <input
                              aria-label={`${t} threshold`}
                              value={m.threshold}
                              onChange={(ev) => setMetric(t, { threshold: ev.target.value })}
                              placeholder="threshold"
                              inputMode="decimal"
                              className={`${field} w-24 font-mono text-xs ${err(`m.${t}.threshold`) ? "border-red-800" : ""}`}
                            />
                          )}
                        </div>
                      )}
                      {e && <p className="mt-1 pl-5 text-[11px] text-red-300">{e}</p>}
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3 border-t border-zinc-800 pt-3">
        <button type="submit" disabled={busy || (showErrors && errorCount > 0)} className={primaryBtn}>
          {busy ? "Queuing…" : "Run experiment"}
        </button>
        <span className="font-mono text-xs text-zinc-500">
          {variants.length} variant(s) × {dataset?.item_count ?? 0} items = {variants.length * (dataset?.item_count ?? 0)} runs
        </span>
        {showErrors && errorCount > 0 && (
          <p role="alert" className="text-xs text-red-300">
            Fix {errorCount} problem{errorCount === 1 ? "" : "s"} above to continue.
          </p>
        )}
        {submitError && (
          <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2 py-0.5 text-red-200">
            {submitError}
          </p>
        )}
      </div>
    </form>
  );
}
