"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { proxy } from "@/lib/proxy-client";
import { useCan } from "@/components/auth/role-context";
import type { Annotation } from "@/lib/types";

/** Opens (or reuses) a pending annotation for the trace and jumps to its review screen. */
export function AnnotateButton({ traceId }: { traceId: string }) {
  const router = useRouter();
  const canWrite = useCan("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function annotate() {
    setBusy(true);
    setError(null);
    try {
      const a = await proxy<Annotation>("POST", "annotations", { trace_id: traceId, reason: "manual" });
      router.push(`/annotations/${encodeURIComponent(a.id)}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (!canWrite) return null;

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={annotate}
        disabled={busy}
        className="inline-flex h-7 items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800/60 px-2.5 text-xs text-zinc-200 hover:bg-zinc-700 disabled:opacity-50"
      >
        <span aria-hidden className="text-emerald-300">✎</span>
        {busy ? "Opening…" : "Annotate"}
      </button>
      {error && (
        <span role="alert" className="max-w-xs truncate text-xs text-red-300" title={error}>
          {error}
        </span>
      )}
    </span>
  );
}
