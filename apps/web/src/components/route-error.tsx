"use client";

import { useEffect } from "react";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div role="alert" className="max-w-xl rounded-md border border-red-900/60 bg-red-950/30 p-4">
      <h2 className="font-medium text-red-300">Could not load this page</h2>
      <p className="mt-1 text-zinc-400">
        The MATRIX API did not answer as expected. Check that the API is running and that{" "}
        <code className="font-mono text-zinc-300">MATRIX_API_URL</code> is set. If you were just added to or removed
        from this project, pick it again from the project menu.
      </p>
      {error.digest && <p className="mt-2 font-mono text-xs text-zinc-500">digest {error.digest}</p>}
      <button
        type="button"
        onClick={reset}
        className="mt-3 rounded border border-zinc-700 bg-zinc-800 px-3 py-1 text-zinc-100 hover:bg-zinc-700"
      >
        Try again
      </button>
    </div>
  );
}
