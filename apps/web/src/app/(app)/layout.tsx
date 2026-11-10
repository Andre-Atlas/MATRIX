import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { NavLink } from "@/components/nav-link";
import { ProjectSwitcher } from "@/components/auth/project-switcher";
import { RoleProvider } from "@/components/auth/role-context";
import { UserMenu } from "@/components/auth/user-menu";
import { getActive } from "@/lib/me";

export const dynamic = "force-dynamic";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/traces", label: "Traces" },
  { href: "/sessions", label: "Sessions" },
  { href: "/prompts", label: "Prompts" },
  { href: "/datasets", label: "Datasets" },
  { href: "/experiments", label: "Experiments" },
  { href: "/annotations", label: "Annotations" },
  { href: "/alerts", label: "Alerts" },
];

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, projectId, membership, role } = await getActive();
  // A project cookie that no longer matches a membership (or none): pick again. /projects itself
  // is exempt so the picker can render.
  const path = (await headers()).get("x-matrix-path") ?? "/";
  if (!membership && !path.startsWith("/projects")) redirect(`/projects${projectId ? "?stale=1" : ""}`);

  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-zinc-800 focus:px-3 focus:py-1.5"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 flex h-screen w-48 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/60 p-3">
        <div className="mb-3 flex items-center gap-2 px-2 text-xs font-semibold tracking-widest text-zinc-300">
          <span aria-hidden className="h-2 w-2 rounded-sm bg-emerald-400" />
          MATRIX
        </div>
        <ProjectSwitcher memberships={me.memberships} activeId={membership?.project.id ?? null} />
        {membership && (
          <nav aria-label="Primary" className="flex flex-col gap-0.5">
            {NAV.map((n) => (
              <NavLink key={n.href} href={n.href}>
                {n.label}
              </NavLink>
            ))}
            <div className="my-2 border-t border-zinc-800" />
            <NavLink href="/settings">Settings</NavLink>
          </nav>
        )}
        <UserMenu email={me.user.email} name={me.user.name} />
      </aside>
      <main id="main" className="min-w-0 flex-1 px-6 py-5">
        <RoleProvider role={role}>{children}</RoleProvider>
      </main>
    </div>
  );
}
