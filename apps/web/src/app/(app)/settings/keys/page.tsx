import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { getActive } from "@/lib/me";
import { roleAtLeast, type ApiKeyItem } from "@/lib/types";
import { TableSkeleton } from "@/components/ui";
import { KeysManager } from "@/components/settings/keys-manager";
import { Forbidden, Panel } from "@/components/settings/ui";

export const dynamic = "force-dynamic";

async function Keys({ projectId }: { projectId: string }) {
  const { items } = await apiGet<{ items: ApiKeyItem[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/keys`);
  return <KeysManager projectId={projectId} keys={items} />;
}

export default async function KeysPage() {
  const { projectId, role } = await getActive();
  if (!projectId || !roleAtLeast(role, "admin")) return <Forbidden min="admin" what="API keys" />;
  return (
    <Panel
      title="API keys"
      id="keys"
      description="Keys identify this project for the SDK and OTLP exporters. A key never has admin power, and the secret is shown only when it is created."
    >
      <Suspense fallback={<TableSkeleton rows={4} />}>
        <Keys projectId={projectId} />
      </Suspense>
    </Panel>
  );
}
