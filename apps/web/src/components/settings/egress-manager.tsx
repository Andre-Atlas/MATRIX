"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { validateEgressEntry } from "@/lib/egress-host";
import { explain, proxy } from "@/lib/proxy-client";
import type { EgressHost } from "@/lib/types";
import { td, th } from "@/components/ui";
import { ConfirmButton } from "./confirm-button";
import { ErrorNote, formatWhen, inputCls, labelCls, primaryBtn } from "./ui";

export function EgressManager({ projectId, hosts }: { projectId: string; hosts: EgressHost[] }) {
  const router = useRouter();
  const base = `projects/${encodeURIComponent(projectId)}/egress-hosts`;
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hint = host.trim() ? validateEgressEntry(host) : null;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const invalid = validateEgressEntry(host);
    if (invalid) return setError(invalid);
    setBusy(true);
    setError(null);
    try {
      await proxy("POST", base, { host: host.trim() });
      setHost("");
      router.refresh();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
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
      <form onSubmit={add} aria-label="Allow host" className="flex flex-wrap items-end gap-2">
        <label className={labelCls}>
          Host
          <input
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="api.example.com or *.example.com"
            aria-invalid={hint ? true : undefined}
            aria-describedby="egress-hint"
            className={`${inputCls} w-72 font-mono`}
          />
        </label>
        <button type="submit" disabled={busy || !host.trim()} className={primaryBtn}>
          Allow host
        </button>
      </form>
      <p id="egress-hint" className={`text-xs ${hint ? "text-amber-300" : "text-zinc-500"}`}>
        {hint ?? "example.com matches that host only; *.example.com matches its subdomains (not example.com itself). No IP addresses, localhost, ports or paths."}
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full">
          <thead className="border-b border-zinc-800 bg-zinc-900/60">
            <tr>
              <th className={th}>Host</th>
              <th className={th}>Added (UTC)</th>
              <th className={th}>Added by</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {hosts.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-zinc-500">
                  No hosts allowed: outbound requests from this project are blocked.
                </td>
              </tr>
            )}
            {hosts.map((h) => (
              <tr key={h.id} className="hover:bg-zinc-900/60">
                <td className={`${td} font-mono text-zinc-100`}>{h.host}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(h.created_at)}</td>
                <td className={`${td} font-mono text-xs text-zinc-500`}>{h.created_by ?? "—"}</td>
                <td className={`${td} text-right`}>
                  <ConfirmButton label="Remove" question="Remove?" ariaLabel={`Remove ${h.host}`} onConfirm={() => remove(h.id)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
