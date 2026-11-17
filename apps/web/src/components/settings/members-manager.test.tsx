// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const refresh = vi.fn();
const proxy = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@/lib/proxy-client", async (orig) => ({ ...(await orig<typeof import("@/lib/proxy-client")>()), proxy: (...a: unknown[]) => proxy(...a) }));

import { ProxyError } from "@/lib/proxy-client";
import type { ProjectMember } from "@/lib/types";
import { ADD_NOTICE, MembersManager } from "./members-manager";

const members: ProjectMember[] = [
  { user: { id: "u1", name: "Demo", email: "demo@example.com", created_at: "2026-01-01T00:00:00Z", last_login_at: null }, role: "owner", added_at: "2026-01-01T00:00:00Z" },
];
const view = () => render(<MembersManager projectId="p1" myRole="owner" myUserId="u1" members={members} />);
const submit = (email: string) => {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: "Add member" }));
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MembersManager add", () => {
  it("after a 202 refreshes the list and shows a neutral notice", async () => {
    proxy.mockResolvedValue({ status: "pending_or_added" });
    view();
    submit("someone@example.com");
    const note = await screen.findByRole("status");
    expect(note.textContent).toBe(ADD_NOTICE);
    expect(note.textContent).not.toMatch(/adicionado com sucesso|não encontrado|not found/i);
    expect(proxy).toHaveBeenCalledWith("POST", "projects/p1/members", { email: "someone@example.com", role: "member" });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("on 409 shows the error and no notice", async () => {
    proxy.mockRejectedValue(new ProxyError(409, "user is already a member"));
    view();
    submit("demo@example.com");
    await waitFor(() => expect(screen.getByText(/already a member/i)).toBeTruthy());
    expect(screen.queryByRole("status")).toBeNull();
    expect(refresh).not.toHaveBeenCalled();
  });
});
