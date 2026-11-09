"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const active = href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`rounded px-2 py-1 ${
        active ? "bg-zinc-800 text-zinc-50" : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
      }`}
    >
      {children}
    </Link>
  );
}
