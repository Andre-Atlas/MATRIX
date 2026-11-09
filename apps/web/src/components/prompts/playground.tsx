"use client";

import { useState } from "react";
import { extractVariables, missingVariables, renderTemplate, sampleValue } from "@/lib/prompt-template";
import type { PromptType, PromptTemplate } from "@/lib/types";
import { area, field } from "@/components/evals/ui";

/** Client-side render of the template with sample values. No model call is made. */
export function Playground({ type, template }: { type: PromptType; template: PromptTemplate }) {
  const variables = extractVariables(template);
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(variables.map((v) => [v, sampleValue(v)])));
  const rendered = renderTemplate(template, values);
  const missing = missingVariables(template, values);

  return (
    <section aria-label="Playground preview" className="rounded-md border border-zinc-800">
      <header className="flex items-baseline justify-between border-b border-zinc-800 bg-zinc-900/60 px-3 py-2">
        <h2 className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">Playground preview</h2>
        <span className="text-xs text-zinc-500">Renders locally; no model is called</span>
      </header>
      <div className="grid gap-4 p-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="space-y-2">
          {variables.length === 0 ? (
            <p className="text-zinc-500">This template has no variables.</p>
          ) : (
            variables.map((v) => (
              <label key={v} className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
                <code className="font-mono normal-case tracking-normal text-violet-300">{`{{${v}}}`}</code>
                {v.toLowerCase().includes("context") || (values[v] ?? "").length > 60 ? (
                  <textarea value={values[v] ?? ""} onChange={(e) => setValues((cur) => ({ ...cur, [v]: e.target.value }))} rows={3} className={area} />
                ) : (
                  <input value={values[v] ?? ""} onChange={(e) => setValues((cur) => ({ ...cur, [v]: e.target.value }))} className={`${field} w-full normal-case tracking-normal`} />
                )}
              </label>
            ))
          )}
        </div>
        <div className="min-w-0">
          {missing.length > 0 && (
            <p role="status" className="mb-1.5 text-xs text-amber-300">
              Empty: {missing.join(", ")}. The server rejects a render with a missing variable.
            </p>
          )}
          {typeof rendered === "string" ? (
            <pre aria-label="Rendered prompt" className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-xs leading-relaxed text-zinc-200">
              {rendered}
            </pre>
          ) : (
            <ol aria-label="Rendered messages" className="max-h-80 space-y-1.5 overflow-auto">
              {rendered.map((m, i) => (
                <li key={i} className="rounded border border-zinc-800 bg-zinc-950">
                  <div className="border-b border-zinc-800/80 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-500">{m.role}</div>
                  <pre className="whitespace-pre-wrap break-words p-2.5 font-mono text-xs leading-relaxed text-zinc-200">{m.content}</pre>
                </li>
              ))}
            </ol>
          )}
          <p className="mt-1 font-mono text-[11px] tabular-nums text-zinc-600">
            {type} · {(typeof rendered === "string" ? rendered : rendered.map((m) => m.content).join("")).length} chars
          </p>
        </div>
      </div>
    </section>
  );
}
