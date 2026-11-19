"use client";

import { useState } from "react";
import { dangerBtn } from "./ui";

/** Inline "Revoke / Yes / No" confirmation. `onConfirm` may throw; its message is shown. */
export function ConfirmButton({
  label,
  question = "Sure?",
  onConfirm,
  ariaLabel,
  disabled,
}: {
  label: string;
  question?: string;
  onConfirm: () => Promise<void>;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (!confirming) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => setConfirming(true)}
        aria-label={ariaLabel ?? label}
        className="rounded px-1.5 text-xs text-zinc-500 hover:bg-red-950/50 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-zinc-400">{question}</span>
      <button type="button" onClick={go} disabled={busy} autoFocus className={dangerBtn}>
        {busy ? "…" : "Yes"}
      </button>
      <button type="button" onClick={() => setConfirming(false)} className="px-1 text-zinc-400 hover:text-zinc-100">
        No
      </button>
    </span>
  );
}
