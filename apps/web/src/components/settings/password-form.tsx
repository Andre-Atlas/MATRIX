"use client";

import { useState } from "react";
import { PASSWORD_MIN, validateNewPassword } from "@/lib/validation";
import { ProxyError, explain, proxy } from "@/lib/proxy-client";
import { ErrorNote, OkNote, labelCls, inputCls, primaryBtn } from "./ui";

export function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setDone(false);
    const invalid = validateNewPassword(current, next, confirm);
    if (invalid) return setError(invalid);
    setBusy(true);
    setError(null);
    try {
      await proxy("POST", "auth/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ProxyError && err.status === 403
          ? "The current password is incorrect."
          : explain(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label="Change password" className="max-w-sm space-y-3">
      <label className={labelCls}>
        Current password
        <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={inputCls} />
      </label>
      <label className={labelCls}>
        New password
        <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} aria-describedby="pw-hint" className={inputCls} />
        <span id="pw-hint" className="normal-case tracking-normal text-zinc-500">
          At least {PASSWORD_MIN} characters. Your other sessions are signed out.
        </span>
      </label>
      <label className={labelCls}>
        Confirm new password
        <input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputCls} />
      </label>
      {error && <ErrorNote>{error}</ErrorNote>}
      {done && <OkNote>Password changed. Other sessions were signed out.</OkNote>}
      <button type="submit" disabled={busy} className={primaryBtn}>
        {busy ? "Saving…" : "Change password"}
      </button>
    </form>
  );
}
