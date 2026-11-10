"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { buildOnlineEvalRequest, describeFilter, emptyOnlineEvalForm, type OnlineEvalForm } from "@/lib/annotation-form";
import { formatPercent, formatTimestamp } from "@/lib/format";
import { proxy } from "@/lib/proxy-client";
import { useCan } from "@/components/auth/role-context";
import { METRIC_TYPES, MODEL_PROVIDERS, SPAN_KINDS, type OnlineEval, type SpanKind } from "@/lib/types";
import { Mono, td, th } from "@/components/ui";
import { field, label, primaryBtn } from "@/components/evals/ui";
import { DeleteButton } from "./delete-button";

export function OnlineEvalsSection({ evals }: { evals: OnlineEval[] }) {
  const router = useRouter();
  const isAdmin = useCan("admin");
  const [form, setForm] = useState<OnlineEvalForm>(emptyOnlineEvalForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof OnlineEvalForm>(k: K, v: OnlineEvalForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { body, error: err } = buildOnlineEvalRequest(form);
    if (!body) return setError(err);
    setBusy(true);
    setError(null);
    try {
      await proxy("POST", "online-evals", body);
      setForm(emptyOnlineEvalForm());
      router.refresh();
    } catch (e2) {
      setError((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="oe-h" className="mt-8">
      <h2 id="oe-h" className="text-sm font-semibold text-zinc-100">
        Online evals
      </h2>
      <p className="mb-2 text-zinc-500">Score a sample of live traffic with a metric as spans arrive. Judge API keys come from the server environment.</p>
      {evals.length === 0 ? (
        <p className="mb-3 rounded-md border border-dashed border-zinc-800 px-4 py-5 text-center text-zinc-500">No online evals yet.</p>
      ) : (
        <div className="mb-3 overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Metric</th>
                <th className={`${th} text-right`}>Sampling</th>
                <th className={th}>Filter</th>
                <th className={th}>Judge</th>
                <th className={th}>Created (UTC)</th>
                <th className={th}>
                  <span className="sr-only">Delete</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {evals.map((ev) => (
                <tr key={ev.id}>
                  <td className={`${td} font-medium text-zinc-100`}>{ev.name}</td>
                  <td className={`${td} font-mono text-xs text-zinc-300`}>{ev.metric}</td>
                  <td className={`${td} text-right`}>
                    <Mono>{formatPercent(ev.sampling_rate, 0)}</Mono>
                  </td>
                  <td className={`${td} text-xs text-zinc-400`}>{describeFilter(ev.filter)}</td>
                  <td className={`${td} font-mono text-xs text-zinc-400`}>{ev.model ? `${ev.model.provider}/${ev.model.name}` : <span className="text-zinc-600">server default</span>}</td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(ev.created_at)}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <DeleteButton min="admin" path={`online-evals/${encodeURIComponent(ev.id)}`} what={`online eval ${ev.name}`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin ? (
      <form onSubmit={submit} aria-label="Create online eval" className="space-y-3 rounded-md border border-zinc-800 bg-zinc-900/30 p-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className={label}>
            Name
            <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Faithfulness sample" className={`${field} w-52 normal-case tracking-normal`} />
          </label>
          <label className={label}>
            Metric
            <select value={form.metric} onChange={(e) => set("metric", e.target.value)} className={`${field} font-mono normal-case tracking-normal`}>
              {METRIC_TYPES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Params (JSON, optional)
            <input value={form.params} onChange={(e) => set("params", e.target.value)} placeholder='{"criteria": "concise"}' className={`${field} w-56 font-mono normal-case tracking-normal`} />
          </label>
          <label className={`${label} w-52`}>
            <span className="flex justify-between">
              Sampling rate <Mono className="normal-case tracking-normal text-zinc-200">{form.samplingPct}%</Mono>
            </span>
            <input type="range" min={0} max={100} step={1} value={form.samplingPct} onChange={(e) => set("samplingPct", Number(e.target.value))} className="h-7 accent-emerald-500" />
          </label>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className={label}>
            Filter: span kind
            <select value={form.kind} onChange={(e) => set("kind", e.target.value as SpanKind | "")} className={`${field} normal-case tracking-normal`}>
              <option value="">any</option>
              {SPAN_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Filter: span name
            <input value={form.spanName} onChange={(e) => set("spanName", e.target.value)} placeholder="answer" className={`${field} w-36 font-mono normal-case tracking-normal`} />
          </label>
          <label className={label}>
            Judge provider
            <select value={form.provider} onChange={(e) => set("provider", e.target.value)} className={`${field} font-mono normal-case tracking-normal`}>
              {MODEL_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Judge model
            <input value={form.model} onChange={(e) => set("model", e.target.value)} placeholder="gpt-4o-mini (optional)" className={`${field} w-44 font-mono normal-case tracking-normal`} />
          </label>
          <button type="submit" disabled={busy} className={primaryBtn}>
            {busy ? "Creating…" : "Create online eval"}
          </button>
        </div>
        {error && (
          <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2.5 py-1.5 text-red-200">
            {error}
          </p>
        )}
      </form>
      ) : (
        <p className="text-xs text-zinc-500">Creating and deleting these requires the admin role.</p>
      )}
    </section>
  );
}
