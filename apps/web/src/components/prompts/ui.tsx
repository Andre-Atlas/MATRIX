import Link from "next/link";
import { promptPath } from "@/lib/prompt-template";

const LABEL_TONE: Record<string, string> = {
  production: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
  staging: "border-amber-800/60 bg-amber-950/40 text-amber-300",
  development: "border-sky-800/60 bg-sky-950/40 text-sky-300",
};

export function LabelChip({ label, version }: { label: string; version?: number }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 text-[11px] leading-4 ${
        LABEL_TONE[label] ?? "border-zinc-700 bg-zinc-800/60 text-zinc-300"
      }`}
    >
      {label}
      {version !== undefined && <span className="font-mono tabular-nums text-zinc-400">v{version}</span>}
    </span>
  );
}

export const promptHref = (name: string, query?: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined) p.set(k, String(v));
  const qs = p.toString();
  return `/prompts/${promptPath(name)}${qs ? `?${qs}` : ""}`;
};

export function VariableChips({ variables }: { variables: string[] }) {
  if (!variables.length) return <span className="text-zinc-600">none</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {variables.map((v) => (
        <code key={v} className="rounded bg-zinc-800/80 px-1.5 font-mono text-[11px] leading-5 text-violet-300">
          {`{{${v}}}`}
        </code>
      ))}
    </span>
  );
}

export function TemplateView({ type, template }: { type: "text" | "chat"; template: string | { role: string; content: string }[] }) {
  if (typeof template === "string" || type === "text") {
    return (
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-xs leading-relaxed text-zinc-200">
        {typeof template === "string" ? template : JSON.stringify(template, null, 2)}
      </pre>
    );
  }
  return (
    <ol className="space-y-1.5" aria-label="Messages">
      {template.map((m, i) => (
        <li key={i} className="rounded border border-zinc-800 bg-zinc-950">
          <div className="border-b border-zinc-800/80 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-500">{m.role}</div>
          <pre className="whitespace-pre-wrap break-words p-2.5 font-mono text-xs leading-relaxed text-zinc-200">{m.content}</pre>
        </li>
      ))}
    </ol>
  );
}

export function NewLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="inline-flex h-7 items-center rounded border border-emerald-700/70 bg-emerald-900/40 px-3 text-emerald-200 hover:bg-emerald-900/70"
    >
      {children}
    </Link>
  );
}
