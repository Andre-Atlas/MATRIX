"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useCan } from "@/components/auth/role-context";
import { ConfirmButton } from "@/components/settings/confirm-button";
import { explain, proxy } from "@/lib/proxy-client";
import { field } from "@/components/evals/ui";

/** Installed packages with a filter box (up to 500 entries). */
export function PackageList({ packages }: { packages: string[] }) {
  const [q, setQ] = useState("");
  const shown = q.trim() ? packages.filter((p) => p.toLowerCase().includes(q.trim().toLowerCase())) : packages;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${packages.length} packages`} aria-label="Search packages" className={`${field} w-full text-xs`} />
      </div>
      <p className="sr-only" aria-live="polite">{shown.length} packages shown</p>
      {shown.length ? (
        <ul className="max-h-64 overflow-auto font-mono text-xs leading-5 text-zinc-300">
          {shown.map((p) => {
            const [name, ver] = p.split("==");
            return (
              <li key={p} className="flex justify-between gap-3 px-1 hover:bg-zinc-800/50">
                <span className="truncate">{name}</span>
                <span className="text-zinc-500">{ver ?? ""}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-xs text-zinc-500">No package matches “{q}”.</p>
      )}
    </div>
  );
}

/** Write actions: mark a running run killed (member), delete a run (admin). Hidden below the role. */
export function RunActions({ runId, running }: { runId: string; running: boolean }) {
  const router = useRouter();
  const canWrite = useCan("member");
  const canDelete = useCan("admin");
  const [error, setError] = useState<string | null>(null);
  if (!canWrite && !canDelete) return null;
  return (
    <div className="flex items-center gap-2">
      {error && <span role="alert" className="text-xs text-red-300">{error}</span>}
      {canWrite && running && (
        <ConfirmButton
          label="Mark killed"
          question="Mark as killed?"
          onConfirm={async () => {
            try {
              await proxy("PATCH", `runs/${encodeURIComponent(runId)}`, { status: "killed" });
              router.refresh();
            } catch (e) {
              setError(explain(e));
            }
          }}
        />
      )}
      {canDelete && (
        <ConfirmButton
          label="Delete run"
          question="Delete run and its metrics?"
          onConfirm={async () => {
            try {
              await proxy("DELETE", `runs/${encodeURIComponent(runId)}`);
              router.push("/runs");
              router.refresh();
            } catch (e) {
              setError(explain(e));
            }
          }}
        />
      )}
    </div>
  );
}
