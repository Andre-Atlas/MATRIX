"use client";

import { useState } from "react";
import { ProxyError, explain, proxy } from "@/lib/proxy-client";
import type { Membership } from "@/lib/types";
import { ErrorNote, RoleBadge, inputCls, primaryBtn } from "@/components/settings/ui";

async function pick(id: string) {
  const res = await fetch("/api/auth/switch-project", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: id }),
    cache: "no-store",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? "Could not switch project.");
  }
}

export function ProjectPicker({ memberships, activeId }: { memberships: Membership[]; activeId: string | null }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open(id: string) {
    setBusy(true);
    setError(null);
    try {
      await pick(id);
      // Full navigation: the project cookie changed, so every server component must re-render.
      window.location.assign("/");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await proxy<{ project: { id: string } }>("POST", "projects", { name: name.trim() });
      await pick(made.project.id);
      // Full navigation: the project cookie changed, so every server component must re-render.
      window.location.assign("/");
    } catch (err) {
      setError(err instanceof ProxyError ? explain(err) : (err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {memberships.length > 0 && (
        <ul className="divide-y divide-zinc-800/70 rounded-md border border-zinc-800">
          {memberships.map((m) => (
            <li key={m.project.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <span className="flex items-center gap-2">
                <span className="font-medium text-zinc-100">{m.project.name}</span>
                <RoleBadge role={m.role} />
                {m.project.id === activeId && <span className="text-xs text-emerald-300">current</span>}
              </span>
              <button type="button" disabled={busy} onClick={() => open(m.project.id)} className={primaryBtn}>
                Open
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={create} className="flex items-end gap-2" aria-label="New project">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          New project
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="Project name" className={`${inputCls} w-64`} />
        </label>
        <button type="submit" disabled={busy || !name.trim()} className={primaryBtn}>
          Create
        </button>
      </form>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
