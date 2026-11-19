import { describe, expect, it, vi } from "vitest";
import { LOGOUT_UNREVOKED, NOTICE_KEY, logoutNotice, performLogout, saveNotice, takeNotice } from "./login-notice";


describe("logout notice", () => {
  it("only warns when the server says the session was not revoked", () => {
    expect(logoutNotice({ ok: true, revoked: false })).toBe(LOGOUT_UNREVOKED);
    expect(logoutNotice({ ok: true, revoked: true })).toBeNull();
    expect(logoutNotice(null)).toBeNull();
    expect(logoutNotice("revoked:false")).toBeNull();
  });

  it("is carried once across the redirect", () => {
    saveNotice("hello");
    expect(window.sessionStorage.getItem(NOTICE_KEY)).toBe("hello");
    expect(takeNotice()).toBe("hello");
    expect(takeNotice()).toBeNull();
  });

  it("performLogout returns and keeps the warning only for revoked:false", async () => {
    takeNotice();
    const f = vi.fn(async () => Response.json({ ok: true, revoked: false }));
    expect(await performLogout(f as unknown as typeof fetch)).toBe(LOGOUT_UNREVOKED);
    expect(f).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({ method: "POST" }));
    expect(takeNotice()).toBe(LOGOUT_UNREVOKED);
    const g = vi.fn(async () => Response.json({ ok: true, revoked: true }));
    expect(await performLogout(g as unknown as typeof fetch)).toBeNull();
    expect(takeNotice()).toBeNull();
  });
});
