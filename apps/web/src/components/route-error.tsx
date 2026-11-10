"use client";

import { useEffect } from "react";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const is404 = error.message?.includes("404");
  const is401 = error.message?.includes("401");

  return (
    <div role="alert" className="max-w-2xl rounded-md border border-red-900/60 bg-red-950/30 p-5">
      <h2 className="font-semibold text-red-300 text-base">Could not load this page</h2>
      <p className="mt-2 text-zinc-300 text-sm">
        The MATRIX API did not answer as expected. Check that the backend is running and that{" "}
        <code className="font-mono text-zinc-200 bg-zinc-800/80 px-1 py-0.5 rounded">MATRIX_API_URL</code> and{" "}
        <code className="font-mono text-zinc-200 bg-zinc-800/80 px-1 py-0.5 rounded">MATRIX_API_KEY</code> are set.
      </p>

      {error.message && (
        <div className="mt-3 rounded border border-red-900/40 bg-zinc-950/80 p-2.5 font-mono text-xs text-red-400 overflow-x-auto">
          {error.message}
        </div>
      )}

      {is404 && (
        <div className="mt-3 rounded border border-amber-900/40 bg-amber-950/20 p-3 text-xs text-amber-300/90 leading-relaxed">
          <p className="font-medium text-amber-200 mb-1">💡 Troubleshooting 404 Not Found:</p>
          <ul className="list-disc pl-4 space-y-1">
            <li>Ensure <code className="font-mono text-zinc-200">MATRIX_API_URL</code> has no trailing slash (e.g. <code className="font-mono text-emerald-400">http://localhost:8000</code>).</li>
            <li>Verify another process is not occupying port 8000 (e.g. another local project backend).</li>
            <li>To run the UI completely standalone with mock data, start with <code className="font-mono text-emerald-400">MATRIX_MOCK=1 npm run dev</code>.</li>
          </ul>
        </div>
      )}

      {is401 && (
        <div className="mt-3 rounded border border-amber-900/40 bg-amber-950/20 p-3 text-xs text-amber-300/90 leading-relaxed">
          <p className="font-medium text-amber-200 mb-1">💡 Troubleshooting 401 Unauthorized:</p>
          <p>You need to sign in at <a href="/login" className="underline text-emerald-400">/login</a> or provide a valid <code className="font-mono text-zinc-200">MATRIX_API_KEY</code> in <code className="font-mono text-zinc-200">apps/web/.env.local</code>.</p>
        </div>
      )}

      {error.digest && <p className="mt-3 font-mono text-xs text-zinc-500">digest {error.digest}</p>}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={reset}
          className="rounded border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-100 hover:bg-zinc-700 transition"
        >
          Try again
        </button>
        <a
          href="/login"
          className="rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 transition"
        >
          Go to Sign In
        </a>
      </div>
    </div>
  );
}
