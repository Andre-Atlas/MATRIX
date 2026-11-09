"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  STANDARD_LABELS,
  checkSchemaShape,
  emptyMessage,
  extractVariables,
  isValidLabel,
  isValidPromptName,
  parseJsonObject,
  promptPath,
  validateDraft,
} from "@/lib/prompt-template";
import { proxy } from "@/lib/proxy-client";
import { PROMPT_ROLES, type ChatTemplateMessage, type CreatePromptRequest, type PromptType, type PromptVersion } from "@/lib/types";
import { area, field, label, primaryBtn, secondaryBtn } from "@/components/evals/ui";
import { VariableChips } from "./ui";

const DEFAULT_CONFIG = `{\n  "model": "gpt-4o-mini",\n  "temperature": 0.2,\n  "max_tokens": 500\n}`;

export function PromptForm({ base }: { base: PromptVersion | null }) {
  const router = useRouter();
  const [name, setName] = useState(base?.name ?? "");
  const [type, setType] = useState<PromptType>(base?.type ?? "text");
  const [text, setText] = useState(typeof base?.template === "string" ? base.template : "");
  const [messages, setMessages] = useState<ChatTemplateMessage[]>(
    Array.isArray(base?.template) ? base.template.map((m) => ({ ...m })) : [emptyMessage("system"), emptyMessage("user")],
  );
  const [config, setConfig] = useState(base ? JSON.stringify(base.config, null, 2) : DEFAULT_CONFIG);
  const [schema, setSchema] = useState(base?.response_schema != null ? JSON.stringify(base.response_schema, null, 2) : "");
  const [commit, setCommit] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const template = type === "text" ? text : messages;
  const variables = useMemo(() => extractVariables(template), [template]);
  const configParsed = useMemo(() => parseJsonObject(config), [config]);
  const schemaParsed = useMemo(() => parseJsonObject(schema, { allowBoolean: true }), [schema]);
  const schemaShape = schemaParsed.error === null && schemaParsed.value !== undefined ? checkSchemaShape(schemaParsed.value) : null;
  const schemaError = schemaParsed.error ?? schemaShape;
  const nameError = name && !isValidPromptName(name) ? "Letters, digits, . _ - and / only; must start with a letter or digit." : null;
  const customLabels = custom
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
  const badCustom = customLabels.find((l) => !isValidLabel(l));

  const toggleLabel = (l: string) => setLabels((cur) => (cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l]));
  const setMessage = (i: number, patch: Partial<ChatTemplateMessage>) =>
    setMessages((cur) => cur.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  const moveMessage = (i: number, d: -1 | 1) =>
    setMessages((cur) => {
      const j = i + d;
      if (j < 0 || j >= cur.length) return cur;
      const next = [...cur];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim() || nameError) return setError(nameError ?? "Name is required.");
    const tErr = validateDraft(type, template);
    if (tErr) return setError(tErr);
    if (configParsed.error) return setError(`Config: ${configParsed.error}`);
    if (schemaError) return setError(`Response schema: ${schemaError}`);
    if (badCustom) return setError(`Invalid label "${badCustom}": lowercase letters, digits, _ and - only.`);
    const body: CreatePromptRequest = {
      name: name.trim(),
      type,
      template,
      config: (configParsed.value as Record<string, unknown> | undefined) ?? {},
      ...(schemaParsed.value !== undefined ? { response_schema: schemaParsed.value } : {}),
      labels: [...new Set([...labels, ...customLabels])],
      ...(commit.trim() ? { commit_message: commit.trim() } : {}),
    };
    setBusy(true);
    try {
      const v = await proxy<PromptVersion>("POST", "prompts", body);
      router.push(`/prompts/${promptPath(v.name)}?v=${v.version}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label={base ? "New prompt version" : "New prompt"} className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <label className={label}>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            readOnly={!!base}
            required
            placeholder="rag/answer"
            aria-invalid={!!nameError}
            className={`${field} w-72 font-mono ${base ? "cursor-not-allowed text-zinc-400" : ""}`}
          />
          {nameError && <span className="normal-case tracking-normal text-red-300">{nameError}</span>}
        </label>
        <fieldset className={label}>
          <legend className="mb-1">Type</legend>
          <div className="inline-flex rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
            {(["text", "chat"] as PromptType[]).map((t) => (
              <label key={t} className="cursor-pointer">
                <input type="radio" name="type" value={t} checked={type === t} onChange={() => setType(t)} className="peer sr-only" />
                <span className="block rounded px-2.5 py-0.5 font-mono text-xs normal-case tracking-normal text-zinc-400 peer-checked:bg-zinc-700 peer-checked:text-zinc-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-emerald-400">
                  {t}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <section aria-label="Template" className="space-y-1.5">
        <div className="flex items-center justify-between">
          <h2 className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Template</h2>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            Variables <span aria-live="polite"><VariableChips variables={variables} /></span>
          </div>
        </div>
        {type === "text" ? (
          <textarea
            aria-label="Template text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            spellCheck={false}
            placeholder={"Answer using only the context.\n\nContext: {{context}}\nQuestion: {{question}}"}
            className={`${area} resize-y`}
          />
        ) : (
          <div className="space-y-2">
            {messages.map((m, i) => (
              <div key={i} className="rounded-md border border-zinc-800 bg-zinc-900/40 p-2">
                <div className="mb-1.5 flex items-center gap-2">
                  <select
                    aria-label={`Message ${i + 1} role`}
                    value={m.role}
                    onChange={(e) => setMessage(i, { role: e.target.value })}
                    className={`${field} font-mono`}
                  >
                    {PROMPT_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <span className="ml-auto flex gap-1">
                    <button type="button" aria-label={`Move message ${i + 1} up`} disabled={i === 0} onClick={() => moveMessage(i, -1)} className="h-6 w-6 rounded text-zinc-400 hover:bg-zinc-800 disabled:opacity-30">
                      ↑
                    </button>
                    <button type="button" aria-label={`Move message ${i + 1} down`} disabled={i === messages.length - 1} onClick={() => moveMessage(i, 1)} className="h-6 w-6 rounded text-zinc-400 hover:bg-zinc-800 disabled:opacity-30">
                      ↓
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove message ${i + 1}`}
                      disabled={messages.length === 1}
                      onClick={() => setMessages((cur) => cur.filter((_, k) => k !== i))}
                      className="h-6 w-6 rounded text-zinc-400 hover:bg-red-950/50 hover:text-red-300 disabled:opacity-30"
                    >
                      ×
                    </button>
                  </span>
                </div>
                <textarea
                  aria-label={`Message ${i + 1} content`}
                  value={m.content}
                  onChange={(e) => setMessage(i, { content: e.target.value })}
                  rows={4}
                  spellCheck={false}
                  placeholder="Message content; use {{variable}} placeholders"
                  className={`${area} resize-y`}
                />
              </div>
            ))}
            <button type="button" onClick={() => setMessages((cur) => [...cur, emptyMessage(cur.at(-1)?.role === "user" ? "assistant" : "user")])} className={secondaryBtn}>
              + Add message
            </button>
          </div>
        )}
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <label className={label}>
          Config (JSON)
          <textarea
            value={config}
            onChange={(e) => setConfig(e.target.value)}
            rows={6}
            spellCheck={false}
            aria-invalid={!!configParsed.error}
            className={`${area} resize-y normal-case tracking-normal`}
          />
          {configParsed.error && <span className="normal-case tracking-normal text-red-300">{configParsed.error}</span>}
        </label>
        <label className={label}>
          Response schema (JSON Schema, optional)
          <textarea
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder={'{ "type": "object", "properties": { "answer": { "type": "string" } }, "required": ["answer"] }'}
            aria-invalid={!!schemaError}
            className={`${area} resize-y normal-case tracking-normal`}
          />
          {schemaError ? (
            <span className="normal-case tracking-normal text-red-300">{schemaError}</span>
          ) : schema.trim() ? (
            <span className="normal-case tracking-normal text-emerald-300">Valid schema shape</span>
          ) : null}
        </label>
      </div>

      <label className={`${label} max-w-xl`}>
        Commit message
        <input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="What changed and why" className={`${field} w-full normal-case tracking-normal`} />
      </label>

      <fieldset className="space-y-1.5">
        <legend className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Apply labels to the new version</legend>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {STANDARD_LABELS.map((l) => (
            <label key={l} className="flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={labels.includes(l)} onChange={() => toggleLabel(l)} className="accent-emerald-500" />
              <span className="font-mono text-xs">{l}</span>
            </label>
          ))}
          <label className="flex items-center gap-1.5 text-xs text-zinc-500">
            custom
            <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="canary, eu" aria-invalid={!!badCustom} className={`${field} w-36 font-mono`} />
          </label>
        </div>
        <p className="text-xs text-zinc-500">A label can sit on one version at a time; applying it here moves it from wherever it is now.</p>
      </fieldset>

      {error && (
        <p role="alert" className="rounded border border-red-900/70 bg-red-950/40 px-2.5 py-1.5 text-red-200">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2 border-t border-zinc-800 pt-3">
        <button type="submit" disabled={busy} className={primaryBtn}>
          {busy ? "Saving…" : base ? "Save new version" : "Create prompt"}
        </button>
        <button type="button" onClick={() => router.back()} className={secondaryBtn}>
          Cancel
        </button>
      </div>
    </form>
  );
}
