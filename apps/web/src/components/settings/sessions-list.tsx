"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { explain, proxy } from "@/lib/proxy-client";
import { td, th } from "@/components/ui";
import type { AuthSession } from "@/lib/types";
import { ConfirmButton } from "./confirm-button";
import { ErrorNote, formatWhen } from "./ui";

function device(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

export function SessionsList({ sessions }: { sessions: AuthSession[] }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function revoke(id: string) {
    setError(null);
    try {
      await proxy("DELETE", `auth/sessions/${encodeURIComponent(id)}`);
      router.refresh();
    } catch (e) {
      setError(explain(e));
    }
  }

  return (
    <div className="space-y-2">
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full">
          <thead className="border-b border-zinc-800 bg-zinc-900/60">
            <tr>
              <th className={th}>Device</th>
              <th className={th}>IP</th>
              <th className={th}>Signed in (UTC)</th>
              <th className={th}>Last seen (UTC)</th>
              <th className={th}>Expires (UTC)</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {sessions.map((s) => (
              <tr key={s.id} className="hover:bg-zinc-900/60">
                <td className={td} title={s.user_agent ?? undefined}>
                  {device(s.user_agent)}
                  {s.current && (
                    <span className="ml-2 rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 text-[11px] text-emerald-300">
                      this session
                    </span>
                  )}
                </td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{s.ip ?? "—"}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(s.created_at)}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(s.last_seen_at)}</td>
                <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(s.expires_at)}</td>
                <td className={`${td} text-right`}>
                  {s.current ? (
                    <span className="text-xs text-zinc-600">use Sign out</span>
                  ) : (
                    <ConfirmButton label="Revoke" question="Revoke?" ariaLabel={`Revoke session ${device(s.user_agent)}`} onConfirm={() => revoke(s.id)} />
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
