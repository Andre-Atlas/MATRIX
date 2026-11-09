"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { STANDARD_LABELS, isValidLabel, labelMoveKind, promptPath } from "@/lib/prompt-template";
import { proxy } from "@/lib/proxy-client";
import type { PromptVersion } from "@/lib/types";
import { field } from "@/components/evals/ui";

const CUSTOM = "__custom__";

/** Moves a label onto this version. Moving it to an older version is the rollback button. */
export function LabelMover({ name, version, labelVersions }: { name: string; version: number; labelVersions: Record<string, number> }) {
  const router = useRouter();
  const [choice, setChoice] = useState<string>("production");
  const [custom, setCustom] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const label = choice === CUSTOM ? custom.trim() : choice;
  const labelOk = isValidLabel(label);
  const from = labelOk ? labelVersions[label] : undefined;
  const kind = labelMoveKind(from, version);

  async function move() {
    setBusy(true);
    setError(null);
    try {
      await proxy<PromptVersion>("POST", `prompts/${promptPath(name)}/labels`, { label, version });
      setDone(`${label} now points to v${version}`);
      setConfirming(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const verb = kind === "rollback" ? "Roll back" : kind === "promote" ? "Promote" : "Apply";
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3" aria-label="Move label">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Label
          <select value={choice} onChange={(e) => { setChoice(e.target.value); setConfirming(false); setDone(null); }} className={`${field} font-mono normal-case tracking-normal`}>
            {STANDARD_LABELS.map((l) => (
              <option key={l} value={l}>
                {l}
                {labelVersions[l] !== undefined ? ` (v${labelVersions[l]})` : ""}
              </option>
            ))}
            <option value={CUSTOM}>custom…</option>
          </select>
        </label>
        {choice === CUSTOM && (
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
            Name
            <input value={custom} onChange={(e) => { setCustom(e.target.value); setConfirming(false); }} placeholder="canary" aria-invalid={!!custom && !labelOk} className={`${field} w-32 font-mono normal-case tracking-normal`} />
          </label>
        )}
        {!confirming ? (
          <button
            type="button"
            disabled={!labelOk || kind === "noop"}
            onClick={() => { setConfirming(true); setDone(null); setError(null); }}
            className={`h-7 rounded border px-3 disabled:cursor-not-allowed disabled:opacity-50 ${
              kind === "rollback"
                ? "border-amber-700/70 bg-amber-900/30 text-amber-200 hover:bg-amber-900/50"
                : "border-zinc-700 bg-zinc-800/60 text-zinc-200 hover:bg-zinc-700"
            }`}
          >
            {kind === "noop" ? `Already on v${version}` : `${verb} to v${version}`}
          </button>
        ) : (
          <span className="flex items-center gap-2" role="group" aria-label="Confirm label move">
            <span className="text-zinc-300">
              Move <span className="font-mono text-zinc-50">{label}</span> {from !== undefined ? <>from <span className="font-mono">v{from}</span> </> : null}to{" "}
              <span className="font-mono text-zinc-50">v{version}</span>? New fetches by label return this version immediately.
            </span>
            <button type="button" autoFocus onClick={move} disabled={busy} className="h-7 rounded border border-amber-700/70 bg-amber-900/40 px-3 text-amber-100 hover:bg-amber-900/70 disabled:opacity-50">
              {busy ? "Moving…" : "Confirm"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="px-1 text-zinc-400 hover:text-zinc-100">
              Cancel
            </button>
          </span>
        )}
      </div>
      {choice === CUSTOM && custom && !labelOk && (
        <p className="mt-1.5 text-xs text-red-300">Lowercase letters, digits, _ and - only (max 50, must start with a letter or digit).</p>
      )}
      {error && (
        <p role="alert" className="mt-2 rounded border border-red-900/70 bg-red-950/40 px-2 py-1 text-red-200">
          {error}
        </p>
      )}
      {done && (
        <p role="status" className="mt-2 text-xs text-emerald-300">
          {done}
        </p>
      )}
    </div>
  );
}
