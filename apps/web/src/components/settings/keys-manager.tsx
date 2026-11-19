"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { explain, proxy } from "@/lib/proxy-client";
import { KEY_SCOPES, type ApiKeyItem, type CreatedApiKey, type KeyScope } from "@/lib/types";
import { td, th } from "@/components/ui";
import { ConfirmButton } from "./confirm-button";
import { ErrorNote, formatWhen, ghostBtn, inputCls, labelCls, primaryBtn } from "./ui";

const SCOPE_HELP: Record<KeyScope, string> = {
  ingest: "send traces",
  read: "read data",
  write: "change data",
};

export function KeysManager({ projectId, keys }: { projectId: string; keys: ApiKeyItem[] }) {
  const router = useRouter();
  const base = `projects/${encodeURIComponent(projectId)}/keys`;
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<KeyScope>>(new Set(["ingest"]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The secret exists only in this component's state: shown once, never stored or put in a URL.
  const [secret, setSecret] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const secretRef = useRef<HTMLInputElement>(null);

  function toggle(s: KeyScope) {
    setScopes((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setError("Give the key a name.");
    if (scopes.size === 0) return setError("Pick at least one scope.");
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const made = await proxy<CreatedApiKey>("POST", base, { name: name.trim(), scopes: KEY_SCOPES.filter((s) => scopes.has(s)) });
      setSecret(made);
      setName("");
      router.refresh();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.api_key);
      setCopied(true);
    } catch {
      secretRef.current?.select();
      setError("Could not use the clipboard; the key is selected, copy it with Ctrl/Cmd+C.");
    }
  }

  async function revoke(id: string) {
    setError(null);
    try {
      await proxy("DELETE", `${base}/${encodeURIComponent(id)}`);
      router.refresh();
    } catch (e) {
      setError(explain(e));
    }
  }

  return (
    <div className="space-y-3">
      <form onSubmit={create} aria-label="Create API key" className="flex flex-wrap items-end gap-3">
        <label className={labelCls}>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="prod-ingest" className={`${inputCls} w-56`} />
        </label>
        <fieldset className="flex items-center gap-3">
          <legend className="mb-1 text-[11px] uppercase tracking-wider text-zinc-500">Scopes</legend>
          {KEY_SCOPES.map((s) => (
            <label key={s} className="flex h-7 items-center gap-1.5 text-zinc-300">
              <input type="checkbox" checked={scopes.has(s)} onChange={() => toggle(s)} className="accent-emerald-500" />
              <span className="font-mono">{s}</span>
              <span className="text-xs text-zinc-500">{SCOPE_HELP[s]}</span>
            </label>
          ))}
        </fieldset>
        <button type="submit" disabled={busy} className={primaryBtn}>
          {busy ? "Creating…" : "Create key"}
        </button>
      </form>
      {error && <ErrorNote>{error}</ErrorNote>}

      {secret && (
        <div role="alert" className="rounded-md border border-amber-800/60 bg-amber-950/20 p-3">
          <p className="font-medium text-amber-300">Copy this key now. It will not be shown again.</p>
          <p className="mt-0.5 text-xs text-zinc-400">
            Store it in your secret manager. If you lose it, revoke it and create a new one.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <input
              ref={secretRef}
              readOnly
              aria-label="New API key"
              value={secret.api_key}
              onFocus={(e) => e.currentTarget.select()}
              className="h-8 min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 font-mono text-xs text-zinc-100"
            />
            <button type="button" onClick={copy} className={ghostBtn}>
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={() => {
                setSecret(null);
                setCopied(false);
              }}
              className="h-7 px-2 text-zinc-400 hover:text-zinc-100"
            >
              Done
            </button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full">
          <thead className="border-b border-zinc-800 bg-zinc-900/60">
            <tr>
              <th className={th}>Name</th>
              <th className={th}>Key</th>
              <th className={th}>Scopes</th>
              <th className={th}>Created (UTC)</th>
              <th className={th}>Last used (UTC)</th>
              <th className={th}>Status</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {keys.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-zinc-500">
                  No keys yet.
                </td>
              </tr>
            )}
            {keys.map((k) => (
              <tr key={k.id} className={`hover:bg-zinc-900/60 ${k.revoked_at ? "opacity-60" : ""}`}>
                <td className={td}>{k.name ?? <span className="text-zinc-600">unnamed</span>}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>mx_{k.prefix}_…</td>
                <td className={td}>
                  <span className="flex gap-1">
                    {k.scopes.map((s) => (
                      <span key={s} className="rounded bg-zinc-800/80 px-1.5 font-mono text-[11px] leading-5 text-zinc-300">
                        {s}
                      </span>
                    ))}
                  </span>
                </td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(k.created_at)}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(k.last_used_at)}</td>
                <td className={td}>
                  {k.revoked_at ? (
                    <span className="rounded border border-red-800/70 bg-red-950/50 px-1.5 text-[11px] text-red-300">revoked</span>
                  ) : (
                    <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 text-[11px] text-emerald-300">active</span>
                  )}
                </td>
                <td className={`${td} text-right`}>
                  {!k.revoked_at && (
                    <ConfirmButton label="Revoke" question="Revoke key?" ariaLabel={`Revoke key ${k.name ?? k.prefix}`} onConfirm={() => revoke(k.id)} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
