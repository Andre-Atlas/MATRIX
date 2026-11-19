import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { getActive } from "@/lib/me";
import { roleAtLeast, type ProjectMember } from "@/lib/types";
import { TableSkeleton } from "@/components/ui";
import { MembersManager } from "@/components/settings/members-manager";
import { Forbidden, Panel } from "@/components/settings/ui";

export const dynamic = "force-dynamic";

async function Members({ projectId, myUserId, myRole }: { projectId: string; myUserId: string; myRole: "admin" | "owner" }) {
  const { items } = await apiGet<{ items: ProjectMember[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/members`);
  return <MembersManager projectId={projectId} myRole={myRole} myUserId={myUserId} members={items} />;
}

export default async function MembersPage() {
  const { me, projectId, role } = await getActive();
  if (!projectId || !roleAtLeast(role, "admin")) return <Forbidden min="admin" what="members" />;
  return (
    <Panel title="Members" id="members" description="Viewers read, members change data, admins manage the project, owners can do everything.">
      <Suspense fallback={<TableSkeleton rows={4} />}>
        <Members projectId={projectId} myUserId={me.user.id} myRole={role as "admin" | "owner"} />
      </Suspense>
    </Panel>
  );
}
