"use client";

import { useEffect, useState } from "react";
import { takeNotice } from "@/lib/login-notice";

/** Shows (once) a notice left by the login or logout flow. */
export function LoginNotice({ className = "" }: { className?: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => setText(takeNotice()), []);
  if (!text) return null;
  return (
    <div role="status" className={`flex items-start gap-2 rounded border border-amber-800/60 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200 ${className}`}>
      <span className="min-w-0 flex-1">{text}</span>
      <button type="button" aria-label="Dismiss" onClick={() => setText(null)} className="text-amber-300 hover:text-amber-100">
        ×
      </button>
    </div>
  );
}
