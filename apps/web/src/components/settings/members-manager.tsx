"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { explain, proxy } from "@/lib/proxy-client";
import { ROLES, roleRank, type ProjectMember, type Role } from "@/lib/types";
import { validEmail } from "@/lib/validation";
import { td, th } from "@/components/ui";
import { ConfirmButton } from "./confirm-button";
import { ErrorNote, RoleBadge, formatWhen, inputCls, labelCls, primaryBtn } from "./ui";

/** Roles the caller may grant or assign: admins manage viewer/member, only owners hand out admin/owner. */
const grantable = (me: Role): Role[] => (me === "owner" ? [...ROLES] : ROLES.filter((r) => roleRank(r) < roleRank("admin")));
/** A row is editable unless it is admin/owner and the caller is not an owner. */
const editable = (me: Role, target: Role) => me === "owner" || roleRank(target) < roleRank("admin");

export function MembersManager({
  projectId,
  myRole,
  myUserId,
  members,
}: {
  projectId: string;
  myRole: Role;
  myUserId: string;
  members: ProjectMember[];
}) {
  const router = useRouter();
  const base = `projects/${encodeURIComponent(projectId)}/members`;
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const roles = grantable(myRole);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.refresh();
      return true;
    } catch (e) {
      setError(explain(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!validEmail(email)) return setError("Enter a valid email address.");
    if (await run(() => proxy("POST", base, { email: email.trim(), role }))) setEmail("");
  }

  return (
    <div className="space-y-3">
      <form onSubmit={add} aria-label="Add member" className="flex flex-wrap items-end gap-2">
        <label className={labelCls}>
          Email of an existing user
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="dario@example.com" className={`${inputCls} w-64`} />
        </label>
        <label className={labelCls}>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} className={inputCls}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={busy || !email.trim()} className={primaryBtn}>
          Add member
        </button>
      </form>
      {myRole !== "owner" && (
        <p className="text-xs text-zinc-500">Only an owner can grant or change the admin and owner roles.</p>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full">
          <thead className="border-b border-zinc-800 bg-zinc-900/60">
            <tr>
              <th className={th}>User</th>
              <th className={th}>Role</th>
              <th className={th}>Added (UTC)</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {members.map((m) => {
              const canEdit = editable(myRole, m.role);
              const self = m.user.id === myUserId;
              return (
                <tr key={m.user.id} className="hover:bg-zinc-900/60">
                  <td className={td}>
                    <span className="text-zinc-100">{m.user.name}</span>
                    {self && <span className="ml-2 text-xs text-zinc-500">(you)</span>}
                    <div className="font-mono text-xs text-zinc-500">{m.user.email}</div>
                  </td>
                  <td className={td}>
                    {canEdit ? (
                      <select
                        aria-label={`Role of ${m.user.email}`}
                        value={m.role}
                        disabled={busy}
                        onChange={(e) => run(() => proxy("PATCH", `${base}/${encodeURIComponent(m.user.id)}`, { role: e.target.value }))}
                        className={inputCls}
                      >
                        {ROLES.filter((r) => roles.includes(r) || r === m.role).map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <RoleBadge role={m.role} />
                    )}
                  </td>
                  <td className={`${td} font-mono text-xs text-zinc-400`}>{formatWhen(m.added_at)}</td>
                  <td className={`${td} text-right`}>
                    <ConfirmButton
                      label="Remove"
                      question="Remove?"
                      ariaLabel={`Remove ${m.user.email}`}
                      disabled={!canEdit}
                      onConfirm={async () => void (await run(() => proxy("DELETE", `${base}/${encodeURIComponent(m.user.id)}`)))}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
