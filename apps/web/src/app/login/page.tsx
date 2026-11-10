import type { Metadata } from "next";
import { LoginForm } from "@/components/auth/login-form";
import { safeNext } from "@/lib/session-cookie";

export const metadata: Metadata = { title: "Sign in · MATRIX" };
export const dynamic = "force-dynamic";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const sp = await searchParams;
  const next = safeNext(sp.next);
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-xs">
        <div className="mb-5 flex items-center gap-2 text-xs font-semibold tracking-widest text-zinc-300">
          <span aria-hidden className="h-2 w-2 rounded-sm bg-emerald-400" />
          MATRIX
        </div>
        <h1 className="mb-1 text-base font-semibold text-zinc-50">Sign in</h1>
        <p className="mb-4 text-zinc-500">Use your MATRIX account. Accounts are created by a platform admin.</p>
        <div className="rounded-md border border-zinc-800 bg-zinc-900/50 p-4">
          <LoginForm next={next} />
        </div>
        {process.env.MATRIX_MOCK === "1" && (
          <p className="mt-3 text-xs text-zinc-500">
            Mock mode: <span className="font-mono text-zinc-300">demo@example.com</span> /{" "}
            <span className="font-mono text-zinc-300">demo-password-123</span>
          </p>
        )}
      </div>
    </main>
  );
}
