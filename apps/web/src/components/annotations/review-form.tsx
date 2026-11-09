"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useReducer, useState } from "react";
import { buildCompleteRequest, buildScores, scoreReducer, type ScoreRow } from "@/lib/annotation-form";
import { proxy } from "@/lib/proxy-client";
import type { Annotation, AnnotationListItem, Dataset, ListResponse } from "@/lib/types";
import { scoreTone } from "@/components/ui";
import { UnifiedDiff } from "@/components/prompts/diff-view";
import { area, field, label, primaryBtn, secondaryBtn } from "@/components/evals/ui";

type Created = { scores: { name: string; value: number }[]; dpo: boolean; dataset: Dataset | null };

const SUGGESTIONS = ["helpfulness", "correctness", "faithfulness", "tone"];

function Thumb({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" className={up ? "" : "rotate-180"}>
      <path d="M5 7v6H2.5V7H5Zm0 0 2.2-4.5c.9 0 1.6.7 1.6 1.6V6h3.4c.8 0 1.4.8 1.2 1.6l-.9 4.2c-.2.7-.8 1.2-1.5 1.2H5" />
    </svg>
  );
}

export function ReviewForm({
  annotationId,
  original,
  datasets,
}: {
  annotationId: string;
  original: string;
  datasets: Dataset[];
}) {
  const router = useRouter();
  const [rows, dispatch] = useReducer(scoreReducer, [] as ScoreRow[]);
  const [corrected, setCorrected] = useState(original);
  const [datasetId, setDatasetId] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [next, setNext] = useState<{ state: "idle" | "loading" | "empty" }>({ state: "idle" });

  const edited = corrected !== original;
  const feedback = rows.find((r) => r.name === "user_feedback")?.value;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const { scores, error: scoreError } = buildScores(rows);
    if (scoreError) return setError(scoreError);
    const body = buildCompleteRequest({ scores, original, corrected, datasetId, comment });
    setBusy(true);
    try {
      await proxy<Annotation>("POST", `annotations/${encodeURIComponent(annotationId)}/complete`, body);
      setCreated({ scores, dpo: body.corrected_output !== undefined, dataset: datasets.find((d) => d.id === datasetId) ?? null });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function goNext() {
    setNext({ state: "loading" });
    try {
      const res = await proxy<ListResponse<AnnotationListItem>>("GET", "annotations?status=pending&limit=5");
      const target = res.items.find((a) => a.id !== annotationId);
      if (target) router.push(`/annotations/${encodeURIComponent(target.id)}`);
      else setNext({ state: "empty" });
    } catch (err) {
      setError((err as Error).message);
      setNext({ state: "idle" });
    }
  }

  if (created) {
    return (
      <section aria-label="Annotation completed" className="rounded-md border border-emerald-900/60 bg-emerald-950/20 p-4">
        <h2 className="flex items-center gap-2 font-medium text-emerald-300">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          Annotation completed
        </h2>
        <ul className="mt-3 space-y-2 text-zinc-300">
          <li>
            <span className="text-zinc-500">Scores </span>
            {created.scores.length === 0 ? (
              <span className="text-zinc-500">none recorded</span>
            ) : (
              <span className="inline-flex flex-wrap gap-1 align-middle">
                {created.scores.map((s) => (
                  <span key={s.name} className="inline-flex items-center gap-1 rounded bg-zinc-800/80 px-1.5 text-[11px] leading-5">
                    <span className="text-zinc-400">{s.name}</span>
                    <span className={`font-mono tabular-nums ${scoreTone(s.value)}`}>{s.value}</span>
                  </span>
                ))}
              </span>
            )}
          </li>
          <li>
            <span className="text-zinc-500">DPO pair </span>
            {created.dpo ? "created: your correction is chosen, the original output is rejected" : "not created (no correction)"}
          </li>
          <li>
            <span className="text-zinc-500">Regression case </span>
            {created.dataset ? (
              <>
                added to{" "}
                <Link href={`/datasets/${encodeURIComponent(created.dataset.id)}`} className="font-mono text-emerald-300 hover:underline">
                  {created.dataset.name}
                </Link>
              </>
            ) : (
              "not added"
            )}
          </li>
        </ul>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button type="button" onClick={goNext} disabled={next.state === "loading"} className={primaryBtn}>
            {next.state === "loading" ? "Looking…" : "Next in queue →"}
          </button>
          <Link href="/annotations" className={`${secondaryBtn} inline-flex items-center`}>
            Back to queue
          </Link>
          {next.state === "empty" && <span className="text-zinc-400">Queue is empty. Nothing left to review.</span>}
        </div>
        {error && (
          <p role="alert" className="mt-2 text-red-300">
            {error}
          </p>
        )}
      </section>
    );
  }

  return (
    <form onSubmit={submit} aria-label="Annotation" className="space-y-4 rounded-md border border-zinc-800 bg-zinc-900/30 p-4">
      <fieldset className="space-y-2">
        <legend className="mb-1 flex w-full items-center justify-between text-[11px] font-medium uppercase tracking-wider text-zinc-400">
          Scores <span className="font-normal normal-case tracking-normal text-zinc-600">0 to 1</span>
        </legend>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">Quick feedback</span>
          {([1, 0] as const).map((v) => {
            const on = feedback === String(v);
            return (
              <button
                key={v}
                type="button"
                aria-pressed={on}
                aria-label={v ? "Thumbs up (user_feedback = 1)" : "Thumbs down (user_feedback = 0)"}
                onClick={() => dispatch({ type: "feedback", value: v })}
                className={`inline-flex h-7 w-9 items-center justify-center rounded border ${
                  on
                    ? v
                      ? "border-emerald-600 bg-emerald-900/50 text-emerald-200"
                      : "border-red-700 bg-red-950/60 text-red-200"
                    : "border-zinc-700 bg-zinc-800/60 text-zinc-300 hover:bg-zinc-700"
                }`}
              >
                <Thumb up={!!v} />
              </button>
            );
          })}
        </div>
        {rows.length === 0 && <p className="text-xs text-zinc-600">No scores yet. Use a thumb or add a named score.</p>}
        {rows.map((r) => (
          <div key={r.id} className="flex items-center gap-2">
            <input
              aria-label="Score name"
              list="score-suggestions"
              value={r.name}
              onChange={(e) => dispatch({ type: "set", id: r.id, field: "name", text: e.target.value })}
              placeholder="name"
              className={`${field} w-44 font-mono`}
            />
            <input
              aria-label={`Value for ${r.name || "score"}`}
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={r.value}
              onChange={(e) => dispatch({ type: "set", id: r.id, field: "value", text: e.target.value })}
              placeholder="0..1"
              className={`${field} w-20 font-mono tabular-nums`}
            />
            <button type="button" aria-label={`Remove score ${r.name}`} onClick={() => dispatch({ type: "remove", id: r.id })} className="h-6 w-6 rounded text-zinc-500 hover:bg-red-950/50 hover:text-red-300">
              ×
            </button>
          </div>
        ))}
        <datalist id="score-suggestions">
          {SUGGESTIONS.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button type="button" onClick={() => dispatch({ type: "add" })} className="text-xs text-emerald-300 hover:underline">
          + Add score
        </button>
      </fieldset>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="corrected" className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Corrected output
          </label>
          {edited && (
            <button type="button" onClick={() => setCorrected(original)} className="text-xs text-zinc-400 hover:text-zinc-100">
              Reset to original
            </button>
          )}
        </div>
        <textarea
          id="corrected"
          value={corrected}
          onChange={(e) => setCorrected(e.target.value)}
          rows={7}
          spellCheck={false}
          placeholder={original ? undefined : "This trace has no output; write the ideal answer here."}
          className={`${area} resize-y font-sans text-sm`}
        />
        {edited ? (
          <div className="space-y-1">
            <p className="text-xs text-zinc-400">
              Diff against the original. Saving records a DPO pair: <span className="text-emerald-300">chosen</span> = your text,{" "}
              <span className="text-red-300">rejected</span> = original.
            </p>
            <UnifiedDiff before={original} after={corrected} />
          </div>
        ) : (
          <p className="text-xs text-zinc-500">Prefilled with the root output. Edit it to create a DPO pair; leave it untouched to skip.</p>
        )}
      </div>

      <label className={label}>
        Save as regression case
        <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} className={`${field} w-full normal-case tracking-normal`}>
          <option value="">None</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.item_count})
            </option>
          ))}
        </select>
        <span className="normal-case tracking-normal text-zinc-600">
          {datasets.length === 0 ? "No datasets yet; create one under Datasets." : datasetId && edited ? "Your correction becomes the expected output." : "The trace's own output becomes the expected output."}
        </span>
      </label>

      <label className={label}>
        Comment
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} placeholder="Why was this wrong? Optional." className={`${area} resize-y font-sans text-sm normal-case tracking-normal`} />
      </label>

      {error && (
        <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2.5 py-1.5 text-red-200">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2 border-t border-zinc-800 pt-3">
        <button type="submit" disabled={busy} className={primaryBtn}>
          {busy ? "Saving…" : "Complete annotation"}
        </button>
        <Link href="/annotations" className="px-2 text-zinc-400 hover:text-zinc-100">
          Cancel
        </Link>
      </div>
    </form>
  );
}
