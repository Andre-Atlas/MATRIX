"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { proxy } from "@/lib/proxy-client";
import type { Dataset, DatasetItem, ListResponse } from "@/lib/types";
import { field, primaryBtn, secondaryBtn } from "./ui";

type Load = { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "ready"; datasets: Dataset[] };
type Toast = { dataset: Dataset } | null;

const NEW = "__new__";

export function AddToDataset({ traceId, spanId }: { traceId: string; spanId: string }) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load>({ state: "idle" });
  const [selected, setSelected] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const popId = useId();

  const fetchDatasets = useCallback(async () => {
    setLoad({ state: "loading" });
    try {
      const res = await proxy<ListResponse<Dataset>>("GET", "datasets");
      setLoad({ state: "ready", datasets: res.items });
      setSelected((cur) => cur || (res.items[0]?.id ?? NEW));
    } catch (e) {
      setLoad({ state: "error", message: (e as Error).message });
    }
  }, []);

  useEffect(() => {
    if (open && load.state === "idle") void fetchDatasets();
  }, [open, load.state, fetchDatasets]);

  // Close on outside click and Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast]);

  const creating = selected === NEW;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (load.state !== "ready") return;
    setBusy(true);
    setError(null);
    try {
      let dataset = load.datasets.find((d) => d.id === selected);
      if (creating) {
        const name = newName.trim();
        if (!name) throw new Error("Give the new dataset a name.");
        dataset = await proxy<Dataset>("POST", "datasets", { name });
        setLoad({ state: "ready", datasets: [dataset, ...load.datasets] });
        setSelected(dataset.id);
        setNewName("");
      }
      if (!dataset) throw new Error("Pick a dataset.");
      await proxy<DatasetItem>("POST", `datasets/${encodeURIComponent(dataset.id)}/items/from-span`, {
        trace_id: traceId,
        span_id: spanId,
      });
      setOpen(false);
      setToast({ dataset });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={popId}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-7 items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800/60 px-2.5 text-xs text-zinc-200 hover:bg-zinc-700"
      >
        <span aria-hidden className="text-emerald-300">+</span>
        Add to dataset
      </button>

      {open && (
        <form
          id={popId}
          role="dialog"
          aria-label="Add span to dataset"
          onSubmit={submit}
          className="absolute right-0 top-full z-30 mt-1.5 w-72 rounded-md border border-zinc-700 bg-zinc-900 p-3 text-xs shadow-xl shadow-black/50"
        >
          <p className="mb-2 text-zinc-400">
            Save this span as a golden case: input, output as expected answer, retrieved docs as context.
          </p>
          {load.state === "loading" || load.state === "idle" ? (
            <div role="status" className="h-7 animate-pulse rounded bg-zinc-800/70">
              <span className="sr-only">Loading datasets…</span>
            </div>
          ) : load.state === "error" ? (
            <div role="alert" className="rounded border border-red-900/70 bg-red-950/40 p-2 text-red-200">
              Could not load datasets: {load.message}
              <button type="button" onClick={fetchDatasets} className="ml-1 text-red-100 underline">
                Retry
              </button>
            </div>
          ) : (
            <>
              <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
                Dataset
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  className={`${field} w-full normal-case tracking-normal`}
                  autoFocus
                >
                  {load.datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({d.item_count})
                    </option>
                  ))}
                  <option value={NEW}>+ New dataset…</option>
                </select>
              </label>
              {creating && (
                <label className="mt-2 flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
                  New dataset name
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="rag-regressions"
                    className={`${field} w-full font-mono normal-case tracking-normal`}
                    autoFocus
                    required
                  />
                </label>
              )}
              {error && (
                <p role="alert" className="mt-2 rounded border border-red-900/70 bg-red-950/40 px-2 py-1 text-red-200">
                  {error}
                </p>
              )}
              <div className="mt-3 flex justify-end gap-2">
                <button type="button" onClick={() => setOpen(false)} className={secondaryBtn}>
                  Cancel
                </button>
                <button type="submit" disabled={busy} className={primaryBtn}>
                  {busy ? "Adding…" : creating ? "Create & add" : "Add"}
                </button>
              </div>
            </>
          )}
        </form>
      )}

      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50">
        {toast && (
          <div className="pointer-events-auto flex items-center gap-3 rounded-md border border-emerald-800/70 bg-zinc-900 px-3 py-2 text-xs shadow-xl shadow-black/50">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="text-zinc-200">
              Added to <span className="font-mono text-zinc-50">{toast.dataset.name}</span>
            </span>
            <Link href={`/datasets/${encodeURIComponent(toast.dataset.id)}`} className="text-emerald-300 hover:underline">
              View dataset →
            </Link>
            <button type="button" aria-label="Dismiss" onClick={() => setToast(null)} className="text-zinc-500 hover:text-zinc-200">
              ×
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
