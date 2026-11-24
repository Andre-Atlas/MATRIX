import { unstable_rethrow } from "next/navigation";
import { apiGet } from "@/lib/api";
import type { CanonicalSweep, ListResponse } from "@/lib/types";
import { SweepsListClient } from "@/components/sweeps/sweeps-list-client";

export const dynamic = "force-dynamic";

export default async function SweepsPage() {
  let initialSweeps: CanonicalSweep[] = [];
  let initialError: string | null = null;

  try {
    const resp = await apiGet<ListResponse<CanonicalSweep>>("/api/v1/sweeps");
    initialSweeps = Array.isArray(resp?.items) ? resp.items : [];
  } catch (err) {
    unstable_rethrow(err);
    initialError = err instanceof Error ? err.message : "Failed to load sweeps.";
  }

  return <SweepsListClient initialSweeps={initialSweeps} initialError={initialError} />;
}
