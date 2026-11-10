import { cache } from "react";
import { apiGet, currentSession } from "./api";
import { roleAtLeast, type MeResponse, type Membership, type Role } from "./types";

/** `/auth/me` once per request (layout and pages share it). */
export const getMe = cache(() => apiGet<MeResponse>("/api/v1/auth/me"));

export type Active = {
  me: MeResponse;
  projectId: string | null;
  membership: Membership | null;
  role: Role | null;
};

export async function getActive(): Promise<Active> {
  const [me, session] = await Promise.all([getMe(), currentSession()]);
  const projectId = session?.projectId ?? null;
  const membership = me.memberships.find((m) => m.project.id === projectId) ?? null;
  return { me, projectId, membership, role: membership?.role ?? null };
}

export const can = roleAtLeast;
