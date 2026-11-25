"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { performLogout } from "@/lib/login-notice";

export function UserMenu({ email, name }: { email: string; name: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
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
      // The cookies are gone after this, but with revoked:false the token still works in the API:
      // say so here and again on /login.
      const notice = await performLogout();
      if (notice) setWarning(notice);
    } catch {
      /* offline: the cookie stays; the user can retry */
    }
    window.location.assign("/login");
  }

  return (
    <div ref={root} className="relative mt-auto border-t border-zinc-800 pt-2">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`User menu: ${name && name !== email ? `${name} ${email}` : email}`}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left hover:bg-zinc-800/60"
      >
        <span aria-hidden="true" className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-zinc-800 text-zinc-200">
          <svg viewBox="0 0 16 16" fill="currentColor" className="h-3.5 w-3.5">
            <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm-5 6a5 5 0 0 1 10 0H3Z" />
          </svg>
        </span>
        <span className="min-w-0">
          <span className="block truncate text-zinc-200">{name || email}</span>
          {name && name !== email && (
            <>
              {" "}
              <span className="block truncate text-[11px] text-zinc-400">{email}</span>
            </>
          )}
        </span>
      </button>
      {warning && (
        <p role="alert" className="mb-1 rounded border border-amber-800/60 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200">
          {warning}
        </p>
      )}
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
