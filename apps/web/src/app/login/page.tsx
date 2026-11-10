"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const endpoint = isRegister ? "/api/proxy/auth/register" : "/api/proxy/auth/login";
      const payload = isRegister
        ? { email, password, name, project_name: projectName || undefined }
        : { email, password };

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Authentication failed");
      }

      // Store in cookie via client as fallback if proxy did not forward set-cookie
      if (data.token) {
        document.cookie = `matrix_session=${data.token}; path=/; max-age=${72 * 3600}; SameSite=Lax`;
        if (data.project?.id) {
          document.cookie = `matrix_project_id=${data.project.id}; path=/; max-age=${72 * 3600}; SameSite=Lax`;
        } else if (data.projects?.[0]?.id) {
          document.cookie = `matrix_project_id=${data.projects[0].id}; path=/; max-age=${72 * 3600}; SameSite=Lax`;
        }
      }

      router.push("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-[80vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-lg border border-zinc-800 bg-zinc-900/80 p-6 shadow-xl backdrop-blur">
        <div className="mb-6 flex items-center justify-center gap-2 text-center">
          <span aria-hidden className="h-2.5 w-2.5 rounded-sm bg-emerald-400" />
          <h1 className="text-base font-semibold tracking-wider text-zinc-100">MATRIX LLMOps</h1>
        </div>

        <div className="mb-5 flex rounded border border-zinc-800 bg-zinc-950 p-0.5 text-xs">
          <button
            type="button"
            onClick={() => {
              setIsRegister(false);
              setError(null);
            }}
            className={`flex-1 rounded py-1.5 font-medium transition ${
              !isRegister ? "bg-zinc-800 text-zinc-100 shadow" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setIsRegister(true);
              setError(null);
            }}
            className={`flex-1 rounded py-1.5 font-medium transition ${
              isRegister ? "bg-zinc-800 text-zinc-100 shadow" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Register
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded border border-red-800/80 bg-red-950/60 p-2.5 text-xs text-red-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {isRegister && (
            <>
              <div>
                <label className="mb-1 block text-xs font-medium text-zinc-400">Full Name</label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Ada Lovelace"
                  className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-700 focus:outline-none"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-zinc-400">Initial Project Name</label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="My AI App (optional)"
                  className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-700 focus:outline-none"
                />
              </div>
            </>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">Email Address</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@organization.com"
              className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-700 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">Password</label>
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-700 focus:outline-none"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="mt-2 rounded bg-emerald-600 px-3 py-2 text-xs font-medium text-zinc-50 hover:bg-emerald-500 disabled:opacity-50"
          >
            {loading ? "Processing..." : isRegister ? "Create Account & Project" : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
