import type { Role } from "@/lib/types";

export const inputCls =
  "h-7 rounded border border-zinc-700 bg-zinc-900 px-2 text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600 disabled:opacity-50";
export const primaryBtn =
  "h-7 rounded border border-emerald-700/70 bg-emerald-900/40 px-3 text-emerald-200 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-50";
export const ghostBtn =
  "h-7 rounded border border-zinc-700 bg-zinc-800 px-3 text-zinc-100 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50";
export const dangerBtn =
  "rounded border border-red-800/70 bg-red-950/50 px-1.5 text-xs text-red-200 hover:bg-red-900/60 disabled:opacity-50";
export const labelCls = "flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500";

export function Panel({
  title,
  description,
  children,
  id,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section aria-labelledby={id} className="mb-6">
      <h2 id={id} className="text-xs font-medium uppercase tracking-wider text-zinc-400">
        {title}
      </h2>
      {description && <p className="mb-2 mt-0.5 text-zinc-500">{description}</p>}
      <div className={description ? "" : "mt-2"}>{children}</div>
    </section>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="rounded border border-red-900/60 bg-red-950/30 px-2.5 py-1.5 text-xs text-red-300">
      {children}
    </p>
  );
}

export function OkNote({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="rounded border border-emerald-900/60 bg-emerald-950/30 px-2.5 py-1.5 text-xs text-emerald-300">
      {children}
    </p>
  );
}

const ROLE_TONE: Record<Role, string> = {
  owner: "border-amber-800/60 bg-amber-950/40 text-amber-300",
  admin: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
  member: "border-sky-800/60 bg-sky-950/40 text-sky-300",
  viewer: "border-zinc-700 bg-zinc-900 text-zinc-400",
};

export function RoleBadge({ role }: { role: Role }) {
  return (
    <span className={`inline-flex items-center rounded border px-1.5 text-[11px] leading-4 ${ROLE_TONE[role]}`}>{role}</span>
  );
}

/** Shown instead of an admin page when the role is too low (the API would answer 403 anyway). */
export function Forbidden({ min, what }: { min: Role; what: string }) {
  return (
    <div role="alert" className="max-w-xl rounded-md border border-amber-900/60 bg-amber-950/20 p-4">
      <h2 className="font-medium text-amber-300">403 · Not allowed</h2>
      <p className="mt-1 text-zinc-400">
        Managing {what} requires the <span className="font-mono text-zinc-200">{min}</span> role in this project. Ask an
        admin of the project if you need access.
      </p>
    </div>
  );
}

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().slice(0, 16).replace("T", " ");
}
