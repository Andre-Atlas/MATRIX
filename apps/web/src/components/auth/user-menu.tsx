"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

export function UserMenu({ email, name }: { email: string; name: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);

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

  async function signOut() {
    setBusy(true);
    try {
      // The server clears the cookies even when the API call fails.
      await fetch("/api/auth/logout", { method: "POST", cache: "no-store" });
    } catch {
      /* offline: the cookie stays; the user can retry */
    }
    window.location.assign("/login");
  }

  return (
    <div ref={root} className="relative mt-auto border-t border-zinc-800 pt-2">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`User menu for ${email}`}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left hover:bg-zinc-800/60"
      >
        <span aria-hidden className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-zinc-800 text-[11px] font-medium uppercase text-zinc-200">
          {(name || email).slice(0, 1)}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-zinc-200">{name || email}</span>
          <span className="block truncate text-[11px] text-zinc-500">{email}</span>
        </span>
      </button>
      {open && (
        <div role="menu" aria-label="User" className="absolute bottom-full left-0 z-40 mb-1 w-full rounded-md border border-zinc-700 bg-zinc-900 p-1 shadow-xl">
          <Link
            role="menuitem"
            href="/settings"
            onClick={() => setOpen(false)}
            className="block rounded px-2 py-1.5 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-50"
          >
            Account settings
          </Link>
          <button
            role="menuitem"
            type="button"
            onClick={signOut}
            disabled={busy}
            className="block w-full rounded px-2 py-1.5 text-left text-zinc-300 hover:bg-zinc-800 hover:text-red-300 disabled:opacity-60"
          >
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}
