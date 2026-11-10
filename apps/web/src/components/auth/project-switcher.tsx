"use client";

import { useEffect, useRef, useState } from "react";
import { ProxyError, explain, proxy } from "@/lib/proxy-client";
import type { Membership } from "@/lib/types";
import { RoleBadge } from "@/components/settings/ui";

async function switchTo(id: string): Promise<void> {
  const res = await fetch("/api/auth/switch-project", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: id }),
    cache: "no-store",
  });
  if (res.status === 401) {
    window.location.assign("/api/auth/expired?next=/");
    return;
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? "Could not switch project.");
  }
}

export function ProjectSwitcher({ memberships, activeId }: { memberships: Membership[]; activeId: string | null }) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const active = memberships.find((m) => m.project.id === activeId);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function choose(id: string) {
    if (id === activeId) return setOpen(false);
    setBusy(true);
    setError(null);
    try {
      await switchTo(id);
      setOpen(false);
      // Full navigation: the project cookie changed, so every server component must re-render.
      window.location.assign("/");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const made = await proxy<{ project: { id: string } }>("POST", "projects", { name: name.trim() });
      await switchTo(made.project.id);
      setName("");
      setCreating(false);
      setOpen(false);
      // Full navigation: the project cookie changed, so every server component must re-render.
      window.location.assign("/");
    } catch (err) {
      setError(err instanceof ProxyError ? explain(err) : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={root} className="relative mb-3">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Project: ${active?.project.name ?? "none selected"}. Switch project`}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-1 rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-left hover:border-zinc-700"
      >
        <span className="min-w-0">
          <span className="block text-[10px] uppercase tracking-wider text-zinc-500">Project</span>
          <span className="block truncate text-zinc-100">{active?.project.name ?? "Select…"}</span>
        </span>
        <span aria-hidden className="text-zinc-500">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Projects"
          className="absolute left-0 top-full z-40 mt-1 w-64 rounded-md border border-zinc-700 bg-zinc-900 p-1 shadow-xl"
        >
          {memberships.length === 0 && <p className="px-2 py-1.5 text-zinc-500">You are not in any project yet.</p>}
          {memberships.map((m) => (
            <button
              key={m.project.id}
              role="menuitemradio"
              aria-checked={m.project.id === activeId}
              type="button"
              disabled={busy}
              onClick={() => choose(m.project.id)}
              className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-zinc-800 disabled:opacity-60 ${
                m.project.id === activeId ? "bg-zinc-800/70 text-zinc-50" : "text-zinc-300"
              }`}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${m.project.id === activeId ? "bg-emerald-400" : "bg-transparent"}`} />
                <span className="truncate">{m.project.name}</span>
              </span>
              <RoleBadge role={m.role} />
            </button>
          ))}
          <div className="mt-1 border-t border-zinc-800 pt-1">
            {creating ? (
              <form onSubmit={create} className="flex items-center gap-1 px-1 py-1">
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={200}
                  placeholder="Project name"
                  aria-label="New project name"
                  className="h-7 min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 text-zinc-100 placeholder:text-zinc-600"
                />
                <button
                  type="submit"
                  disabled={busy || !name.trim()}
                  className="h-7 rounded border border-emerald-700/70 bg-emerald-900/40 px-2 text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
                >
                  Create
                </button>
              </form>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="w-full rounded px-2 py-1.5 text-left text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              >
                + New project
              </button>
            )}
            {error && (
              <p role="alert" className="px-2 py-1 text-xs text-red-300">
                {error}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
