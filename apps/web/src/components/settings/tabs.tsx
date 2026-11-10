"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRole } from "@/components/auth/role-context";
import { roleAtLeast } from "@/lib/types";

const TABS = [
  { href: "/settings", label: "Profile", admin: false },
  { href: "/settings/members", label: "Members", admin: true },
  { href: "/settings/keys", label: "API keys", admin: true },
  { href: "/settings/egress", label: "Egress hosts", admin: true },
  { href: "/settings/audit", label: "Audit log", admin: true },
];

/** Admin tabs are hidden below the admin role; the pages and the API still enforce it. */
export function SettingsTabs() {
  const pathname = usePathname();
  const role = useRole();
  return (
    <nav aria-label="Settings" className="mb-4 flex gap-1 border-b border-zinc-800">
      {TABS.filter((t) => !t.admin || roleAtLeast(role, "admin")).map((t) => {
        const active = pathname === t.href;
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-1.5 ${
              active ? "border-emerald-400 text-zinc-50" : "border-transparent text-zinc-400 hover:text-zinc-100"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
