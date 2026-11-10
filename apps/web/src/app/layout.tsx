import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "MATRIX", description: "LLMOps observability" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-zinc-950 text-sm text-zinc-200 antialiased">{children}</body>
    </html>
  );
}
