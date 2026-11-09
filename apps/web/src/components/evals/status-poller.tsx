"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-renders the server page every `ms` while mounted (mount it only while work is in flight). */
export function StatusPoller({ ms = 3000 }: { ms?: number }) {
  const router = useRouter();
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, ms);
    return () => clearInterval(t);
  }, [router, ms]);
  return (
    <span role="status" className="inline-flex items-center gap-1.5 text-xs text-zinc-500">
      <span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
      auto-refresh {ms / 1000}s
    </span>
  );
}
