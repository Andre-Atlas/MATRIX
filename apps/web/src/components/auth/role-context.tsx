"use client";

import { createContext, useContext } from "react";
import { roleAtLeast, type Role } from "@/lib/types";

// Only the ROLE goes to the browser (to hide controls); the API stays the barrier.
const RoleContext = createContext<Role | null>(null);

export function RoleProvider({ role, children }: { role: Role | null; children: React.ReactNode }) {
  return <RoleContext.Provider value={role}>{children}</RoleContext.Provider>;
}

export const useRole = () => useContext(RoleContext);
export const useCan = (min: Role) => roleAtLeast(useContext(RoleContext), min);
