"use client";

import { useState } from "react";
import { retryText } from "@/lib/validation";

type Failure = { text: string; wait: number | null };

export function LoginForm({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password, next }),
        cache: "no-store",
      });
      const body = (await res.json().catch(() => ({}))) as { next?: string; detail?: string; retry_after?: number };
      if (res.ok) {
        // Full navigation: the new cookies apply and every server component re-renders signed in.
        window.location.assign(typeof body.next === "string" && body.next.startsWith("/") ? body.next : "/");
        return;
      }
      const wait = typeof body.retry_after === "number" ? body.retry_after : null;
      const text =
        res.status === 401
          ? "Invalid email or password."
          : res.status === 429
            ? "Too many attempts."
            : res.status === 503
              ? "The server is busy."
              : (body.detail ?? "Sign in failed. Try again shortly.");
      setFailure({ text, wait });
      setPassword("");
    } catch {
      setFailure({ text: "Could not reach the MATRIX web server.", wait: null });
    } finally {
      setBusy(false);
    }
  }

  const field =
    "h-8 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600";
  return (
    <form onSubmit={submit} aria-label="Sign in" className="space-y-3" aria-busy={busy}>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
        Email
        <input
          name="email"
          type="email"
          autoComplete="username"
          required
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className={field}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
        Password
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={field}
        />
      </label>
      {failure && (
        <p role="alert" className="rounded border border-red-900/60 bg-red-950/30 px-2.5 py-1.5 text-xs text-red-300">
          {failure.text}
          {failure.wait !== null && <> Try again in {retryText(failure.wait)}.</>}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="h-8 w-full rounded border border-emerald-700/70 bg-emerald-900/40 text-emerald-200 hover:bg-emerald-900/70 disabled:cursor-wait disabled:opacity-60"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
