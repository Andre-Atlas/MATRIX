import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { getActive } from "@/lib/me";
import { roleAtLeast, type EgressHost } from "@/lib/types";
import { TableSkeleton } from "@/components/ui";
import { EgressManager } from "@/components/settings/egress-manager";
import { Forbidden, Panel } from "@/components/settings/ui";

export const dynamic = "force-dynamic";

async function Hosts({ projectId }: { projectId: string }) {
  const { items } = await apiGet<{ items: EgressHost[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/egress-hosts`);
  return <EgressManager projectId={projectId} hosts={items} />;
}

export default async function EgressPage() {
  const { projectId, role } = await getActive();
  if (!projectId || !roleAtLeast(role, "admin")) return <Forbidden min="admin" what="egress hosts" />;
  return (
    <Panel
      title="Egress hosts"
      id="egress"
      description="Hosts this project's server-side features (webhooks, online evals, scanners) may call. Everything else is blocked."
    >
      <Suspense fallback={<TableSkeleton rows={3} />}>
        <Hosts projectId={projectId} />
      </Suspense>
    </Panel>
  );
}
