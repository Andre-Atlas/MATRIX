"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { buildRuleRequest, describeCondition, emptyRuleForm, toggleAction, type ConditionKind, type RuleForm } from "@/lib/annotation-form";
import { formatTimestamp } from "@/lib/format";
import { proxy } from "@/lib/proxy-client";
import { useCan } from "@/components/auth/role-context";
import { RULE_ACTIONS, RULE_OPS, type Dataset, type Rule } from "@/lib/types";
import { Mono, td, th } from "@/components/ui";
import { field, label, primaryBtn } from "@/components/evals/ui";
import { DeleteButton } from "./delete-button";

const KINDS: { value: ConditionKind; label: string }[] = [
  { value: "score", label: "Score compares to value" },
  { value: "feedback", label: "Negative user feedback" },
  { value: "latency", label: "Latency at least" },
  { value: "error", label: "Trace status is error" },
];

const ACTION_HELP = { annotate: "Send to queue", add_to_dataset: "Add to dataset", alert: "Raise alert" } as const;

export function RulesSection({ rules, datasets }: { rules: Rule[]; datasets: Dataset[] }) {
  const router = useRouter();
  const isAdmin = useCan("admin");
  const [form, setForm] = useState<RuleForm>(emptyRuleForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof RuleForm>(k: K, v: RuleForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const datasetName = (id: string | null) => datasets.find((d) => d.id === id)?.name ?? id;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { body, error: err } = buildRuleRequest(form);
    if (!body) return setError(err);
    setBusy(true);
    setError(null);
    try {
      await proxy("POST", "rules", body);
      setForm(emptyRuleForm());
      router.refresh();
    } catch (e2) {
      setError((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="rules-h" className="mt-8">
      <h2 id="rules-h" className="text-sm font-semibold text-zinc-100">
        Rules
      </h2>
      <p className="mb-2 text-zinc-500">When a trace or score matches, queue it for review, save it as a regression case, or raise an alert.</p>
      {rules.length === 0 ? (
        <p className="mb-3 rounded-md border border-dashed border-zinc-800 px-4 py-5 text-center text-zinc-500">No rules yet. Create the first one below.</p>
      ) : (
        <div className="mb-3 overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Condition</th>
                <th className={th}>Actions</th>
                <th className={th}>Dataset</th>
                <th className={th}>Created (UTC)</th>
                <th className={th}>
                  <span className="sr-only">Delete</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {rules.map((r) => (
                <tr key={r.id}>
                  <td className={`${td} font-medium text-zinc-100`}>
                    {r.name}
                    {!r.enabled && <span className="ml-2 text-xs text-zinc-500">disabled</span>}
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-300">{describeCondition(r.condition)}</Mono>
                  </td>
                  <td className={td}>
                    <span className="flex gap-1">
                      {r.actions.map((a) => (
                        <span key={a} className="rounded bg-zinc-800/80 px-1.5 font-mono text-[11px] leading-5 text-zinc-300">
                          {a}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td className={`${td} font-mono text-xs text-zinc-400`}>{r.dataset_id ? datasetName(r.dataset_id) : <span className="text-zinc-600">—</span>}</td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(r.created_at)}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <DeleteButton min="admin" path={`rules/${encodeURIComponent(r.id)}`} what={`rule ${r.name}`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin ? (
      <form onSubmit={submit} aria-label="Create rule" className="space-y-3 rounded-md border border-zinc-800 bg-zinc-900/30 p-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className={label}>
            Name
            <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Low faithfulness to queue" className={`${field} w-60 normal-case tracking-normal`} />
          </label>
          <label className={label}>
            When
            <select value={form.kind} onChange={(e) => set("kind", e.target.value as ConditionKind)} className={`${field} normal-case tracking-normal`}>
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          {form.kind === "score" && (
            <>
              <label className={label}>
                Score name
                <input value={form.scoreName} onChange={(e) => set("scoreName", e.target.value)} placeholder="faithfulness" className={`${field} w-36 font-mono normal-case tracking-normal`} />
              </label>
              <label className={label}>
                Op
                <select value={form.op} onChange={(e) => set("op", e.target.value as RuleForm["op"])} className={`${field} font-mono`}>
                  {RULE_OPS.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </label>
              <label className={label}>
                Value
                <input type="number" step="any" value={form.value} onChange={(e) => set("value", e.target.value)} className={`${field} w-20 font-mono tabular-nums`} />
              </label>
            </>
          )}
          {form.kind === "latency" && (
            <label className={label}>
              Min latency (ms)
              <input type="number" min={0} value={form.minLatencyMs} onChange={(e) => set("minLatencyMs", e.target.value)} className={`${field} w-28 font-mono tabular-nums`} />
            </label>
          )}
        </div>
        <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <legend className="sr-only">Actions</legend>
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">Then</span>
          {RULE_ACTIONS.map((a) => (
            <label key={a} className="flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={form.actions.includes(a)} onChange={() => set("actions", toggleAction(form.actions, a))} className="accent-emerald-500" />
              {ACTION_HELP[a]}
            </label>
          ))}
          {form.actions.includes("add_to_dataset") && (
            <label className="flex items-center gap-1.5 text-zinc-500">
              into
              <select value={form.datasetId} onChange={(e) => set("datasetId", e.target.value)} aria-label="Dataset" className={`${field} font-mono`}>
                <option value="">Select dataset…</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </fieldset>
        {error && (
          <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2.5 py-1.5 text-red-200">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} className={primaryBtn}>
          {busy ? "Creating…" : "Create rule"}
        </button>
      </form>
      ) : (
        <p className="text-xs text-zinc-500">Creating and deleting these requires the admin role.</p>
      )}
    </section>
  );
}
