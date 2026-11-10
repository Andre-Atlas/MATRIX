"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { proxy } from "@/lib/proxy-client";
import { useCan } from "@/components/auth/role-context";
import type { CreateDatasetItemRequest } from "@/lib/types";
import { area, label, primaryBtn, secondaryBtn } from "./ui";

const TEMPLATE = '{\n  "question": ""\n}';

export function parseJsonObject(text: string): { value: Record<string, unknown> } | { error: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v !== "object" || v === null || Array.isArray(v)) return { error: "Input must be a JSON object, e.g. {\"question\": \"…\"}" };
    return { value: v as Record<string, unknown> };
  } catch (e) {
    return { error: (e as Error).message };
  }
}

export function AddItemForm({ datasetId }: { datasetId: string }) {
  const router = useRouter();
  const canWrite = useCan("member");
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState(TEMPLATE);
  const [expected, setExpected] = useState("");
  const [context, setContext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(0);
  const parsed = useMemo(() => parseJsonObject(input), [input]);
  const contexts = context
    .split("\n")
    .map((c) => c.trim())
    .filter(Boolean);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!("value" in parsed)) return;
    setBusy(true);
    setError(null);
    const body: CreateDatasetItemRequest = {
      input: parsed.value,
      expected_output: expected.trim() ? expected : null,
      context: contexts,
    };
    try {
      await proxy("POST", `datasets/${encodeURIComponent(datasetId)}/items`, body);
      setInput(TEMPLATE);
      setExpected("");
      setContext("");
      setAdded((n) => n + 1);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!canWrite) return null;

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={secondaryBtn}>
        + Add item
      </button>
    );
  }

  return (
    <form
      onSubmit={submit}
      aria-label="Add dataset item"
      className="mb-4 w-full rounded-md border border-zinc-800 bg-zinc-900/40 p-3"
    >
      <div className="grid gap-3 lg:grid-cols-3">
        <label className={label}>
          Input (JSON object)
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={6}
            spellCheck={false}
            aria-invalid={"error" in parsed}
            aria-describedby="item-input-status"
            className={`${area} normal-case tracking-normal ${"error" in parsed ? "border-red-800 hover:border-red-700" : ""}`}
          />
          <span
            id="item-input-status"
            className={`font-mono text-[11px] normal-case tracking-normal ${"error" in parsed ? "text-red-300" : "text-emerald-300/80"}`}
          >
            {"error" in parsed ? parsed.error : `valid · ${Object.keys(parsed.value).length} key(s)`}
          </span>
        </label>
        <label className={label}>
          Expected output
          <textarea
            value={expected}
            onChange={(e) => setExpected(e.target.value)}
            rows={6}
            placeholder="optional reference answer"
            className={`${area} font-sans normal-case tracking-normal`}
          />
        </label>
        <label className={label}>
          Context (one chunk per line)
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            rows={6}
            placeholder="optional retrieved passages for RAG metrics"
            className={`${area} font-sans normal-case tracking-normal`}
          />
          <span className="font-mono text-[11px] normal-case tracking-normal text-zinc-500">{contexts.length} chunk(s)</span>
        </label>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button type="submit" disabled={busy || "error" in parsed} className={primaryBtn}>
          {busy ? "Adding…" : "Add item"}
        </button>
        <button type="button" onClick={() => setOpen(false)} className={secondaryBtn}>
          Close
        </button>
        {error && (
          <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2 py-0.5 text-red-200">
            {error}
          </p>
        )}
        {!error && added > 0 && (
          <p role="status" className="text-xs text-emerald-300">
            {added} item(s) added
          </p>
        )}
      </div>
    </form>
  );
}
