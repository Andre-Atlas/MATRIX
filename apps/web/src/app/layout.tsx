import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "MATRIX", description: "LLMOps observability" };

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/traces", label: "Traces" },
  { href: "/sessions", label: "Sessions" },
  { href: "/prompts", label: "Prompts" },
  { href: "/datasets", label: "Datasets" },
  { href: "/experiments", label: "Experiments" },
  { href: "/annotations", label: "Annotations" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-zinc-950 text-sm text-zinc-200 antialiased">
        <div className="flex min-h-screen">
          <aside className="w-48 shrink-0 border-r border-zinc-800 bg-zinc-900 p-3">
            <div className="mb-4 px-2 text-xs font-semibold tracking-widest text-zinc-400">MATRIX</div>
            <nav className="flex flex-col gap-0.5">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="rounded px-2 py-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          </aside>
          <main className="min-w-0 flex-1 p-4">{children}</main>
        </div>
      </body>
    </html>
  );
}
