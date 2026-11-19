// One-shot notices carried across a full navigation (login -> app, logout -> /login) in
// sessionStorage. Text only, rendered as text; storage failures are ignored.
export const NOTICE_KEY = "matrix.notice";

export const LOGOUT_UNREVOKED =
  "Could not end the session on the server; revoke it in Settings > Sessions.";

export function saveNotice(text: string, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(NOTICE_KEY, text.slice(0, 300));
  } catch {
    /* private mode / blocked storage */
  }
}

export function takeNotice(storage: Pick<Storage, "getItem" | "removeItem"> | null = safeStorage()): string | null {
  try {
    const text = storage?.getItem(NOTICE_KEY) ?? null;
    storage?.removeItem(NOTICE_KEY);
    return text;
  } catch {
    return null;
  }
}

/** Notice to show after logout: only when the server says the session was NOT revoked there. */
export function logoutNotice(body: unknown): string | null {
  return body && typeof body === "object" && (body as { revoked?: unknown }).revoked === false ? LOGOUT_UNREVOKED : null;
}

/**
 * Calls /api/auth/logout (the server always clears the cookies) and returns the warning to show, if
 * the session could not be revoked in the API; the warning is also kept for the /login page.
 * Throws when the request itself failed (offline: the cookie stays and the user can retry).
 */
export async function performLogout(fetchImpl: typeof fetch = fetch): Promise<string | null> {
  const res = await fetchImpl("/api/auth/logout", { method: "POST", cache: "no-store" });
  const notice = logoutNotice(await res.json().catch(() => null));
  if (notice) saveNotice(notice);
  return notice;
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}
