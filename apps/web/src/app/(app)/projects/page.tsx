import { getMe } from "@/lib/me";
import { currentSession } from "@/lib/api";
import { EmptyState, PageHeader } from "@/components/ui";
import { ProjectPicker } from "@/components/auth/project-picker";

export const dynamic = "force-dynamic";

export default async function ProjectsPage({ searchParams }: { searchParams: Promise<{ stale?: string }> }) {
  const sp = await searchParams;
  const [me, session] = await Promise.all([getMe(), currentSession()]);
  const hasActive = me.memberships.some((m) => m.project.id === session?.projectId);
  return (
    <div className="max-w-xl">
      <PageHeader
        title="Choose a project"
        subtitle={
          sp.stale && !hasActive
            ? "Your previous project is no longer available to you."
            : "Everything in MATRIX is scoped to one project."
        }
      />
      {me.memberships.length === 0 && (
        <EmptyState title="You are not in any project yet">
          Create one below, or ask a project admin to add <span className="font-mono text-zinc-300">{me.user.email}</span>.
        </EmptyState>
      )}
      <ProjectPicker memberships={me.memberships} activeId={hasActive ? (session?.projectId ?? null) : null} />
    </div>
  );
}
