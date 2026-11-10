import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { getActive } from "@/lib/me";
import type { AuthSession } from "@/lib/types";
import { TableSkeleton } from "@/components/ui";
import { PasswordForm } from "@/components/settings/password-form";
import { SessionsList } from "@/components/settings/sessions-list";
import { Panel, RoleBadge, formatWhen } from "@/components/settings/ui";

export const dynamic = "force-dynamic";

async function Sessions() {
  const { items } = await apiGet<{ items: AuthSession[] }>("/api/v1/auth/sessions");
  return <SessionsList sessions={items} />;
}

export default async function SettingsPage() {
  const { me, membership } = await getActive();
  const rows: [string, React.ReactNode][] = [
    ["Name", me.user.name],
    ["Email", <span key="e" className="font-mono">{me.user.email}</span>],
    ["Account created (UTC)", <span key="c" className="font-mono">{formatWhen(me.user.created_at)}</span>],
    ["Last sign-in (UTC)", <span key="l" className="font-mono">{formatWhen(me.user.last_login_at)}</span>],
    [
      "Active project",
      membership ? (
        <span key="p" className="inline-flex items-center gap-2">
          {membership.project.name} <RoleBadge role={membership.role} />
        </span>
      ) : (
        "—"
      ),
    ],
  ];
  return (
    <>
      <Panel title="Profile" id="profile">
        <dl className="grid max-w-xl grid-cols-[10rem_1fr] gap-y-1.5">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-zinc-500">{k}</dt>
              <dd className="text-zinc-200">{v}</dd>
            </div>
          ))}
        </dl>
      </Panel>
      <Panel title="Password" id="password">
        <PasswordForm />
      </Panel>
      <Panel title="Active sessions" id="sessions" description="Revoke a session to sign that device out.">
        <Suspense fallback={<TableSkeleton rows={3} />}>
          <Sessions />
        </Suspense>
      </Panel>
    </>
  );
}
