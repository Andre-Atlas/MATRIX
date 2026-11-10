import type { Metadata } from "next";
import { NavLink } from "@/components/nav-link";
import { UserNav } from "@/components/auth/user-nav";
import "./globals.css";

export const metadata: Metadata = { title: "MATRIX", description: "LLMOps observability" };

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/traces", label: "Traces" },
  { href: "/sessions", label: "Sessions" },
  { href: "/prompts", label: "Prompts" },
  { href: "/datasets", label: "Datasets" },
  { href: "/experiments", label: "Experiments" },
  { href: "/vectors", label: "Vector Space" },
  { href: "/annotations", label: "Annotations" },
  { href: "/alerts", label: "Alerts" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-zinc-950 text-sm text-zinc-200 antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-zinc-800 focus:px-3 focus:py-1.5"
        >
          Skip to content
        </a>
        <div className="flex min-h-screen">
          <aside className="flex w-48 shrink-0 flex-col justify-between border-r border-zinc-800 bg-zinc-900/60 p-3">
            <div>
              <div className="mb-4 flex items-center gap-2 px-2 text-xs font-semibold tracking-widest text-zinc-300">
                <span aria-hidden className="h-2 w-2 rounded-sm bg-emerald-400" />
                MATRIX
              </div>
              <nav aria-label="Primary" className="flex flex-col gap-0.5">
                {NAV.map((n) => (
                  <NavLink key={n.href} href={n.href}>
                    {n.label}
                  </NavLink>
                ))}
              </nav>
            </div>
            <UserNav />
          </aside>
          <main id="main" className="min-w-0 flex-1 px-6 py-5">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
