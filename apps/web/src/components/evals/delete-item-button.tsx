"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { proxy } from "@/lib/proxy-client";
import { useCan } from "@/components/auth/role-context";

export function DeleteItemButton({ datasetId, itemId }: { datasetId: string; itemId: string }) {
  const router = useRouter();
  const canWrite = useCan("member");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await proxy("DELETE", `datasets/${encodeURIComponent(datasetId)}/items/${encodeURIComponent(itemId)}`);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (!canWrite) return null;

  if (error) {
    return (
      <span role="alert" className="text-xs text-red-300" title={error}>
        Delete failed{" "}
        <button type="button" onClick={() => setError(null)} className="underline">
          ok
        </button>
      </span>
    );
  }
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        aria-label={`Delete item ${itemId}`}
        className="rounded px-1.5 text-xs text-zinc-500 hover:bg-red-950/50 hover:text-red-300"
      >
        Delete
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-zinc-400">Delete?</span>
      <button
        type="button"
        onClick={remove}
        disabled={busy}
        autoFocus
        className="rounded border border-red-800/70 bg-red-950/50 px-1.5 text-red-200 hover:bg-red-900/60 disabled:opacity-50"
      >
        {busy ? "…" : "Yes"}
      </button>
      <button type="button" onClick={() => setConfirming(false)} className="px-1 text-zinc-400 hover:text-zinc-100">
        No
      </button>
    </span>
  );
}
