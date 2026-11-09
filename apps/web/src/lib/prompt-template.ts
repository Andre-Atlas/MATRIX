// Prompt template helpers. Semantics mirror server/src/matrix_server/prompts.py:
// `{{ name }}` placeholders, whitespace tolerant, names `[A-Za-z_][A-Za-z0-9_.-]*`.
import type { ChatTemplateMessage, PromptRole, PromptTemplate, PromptType } from "./types";
import { PROMPT_ROLES } from "./types";

const VARIABLE_SOURCE = String.raw`\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}`;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/;
const LABEL_RE = /^[a-z0-9][a-z0-9_-]{0,49}$/;
export const STANDARD_LABELS = ["production", "staging", "development"] as const;

export const isValidPromptName = (name: string) => NAME_RE.test(name);
export const isValidLabel = (label: string) => LABEL_RE.test(label);

function contents(template: PromptTemplate): string[] {
  return typeof template === "string" ? [template] : template.map((m) => m.content);
}

/** Unique placeholders in order of first appearance (text or chat template). */
export function extractVariables(template: PromptTemplate): string[] {
  const seen = new Set<string>();
  for (const text of contents(template)) {
    for (const m of text.matchAll(new RegExp(VARIABLE_SOURCE, "g"))) seen.add(m[1]);
  }
  return [...seen];
}

function renderText(text: string, values: Record<string, string>): string {
  return text.replace(new RegExp(VARIABLE_SOURCE, "g"), (whole, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? values[key] : whole,
  );
}

/**
 * Substitutes placeholders with `values`. Unlike the server (which raises KeyError), a missing
 * variable is left as written so a half-filled playground still shows the template.
 */
export function renderTemplate<T extends PromptTemplate>(template: T, values: Record<string, string>): T {
  if (typeof template === "string") return renderText(template, values) as T;
  return template.map((m) => ({ ...m, content: renderText(m.content, values) })) as T;
}

/** Variables with no value (or an empty one) in `values`. */
export function missingVariables(template: PromptTemplate, values: Record<string, string>): string[] {
  return extractVariables(template).filter((v) => !values[v]);
}

/** Plain-text form used for line diffs and previews: chat messages become `[role]` blocks. */
export function templateToText(template: PromptTemplate): string {
  if (typeof template === "string") return template;
  return template.map((m) => `[${m.role}]\n${m.content}`).join("\n");
}

/** Deterministic sample value so the playground starts filled in. */
export function sampleValue(name: string): string {
  const key = name.toLowerCase();
  if (/question|query|prompt|input|message/.test(key)) return "How do I rotate an API key?";
  if (/context|docs|document|passage/.test(key)) return "[1] Keys are rotated from the admin API. [2] Old keys stay valid for 24 h.";
  if (/name|user/.test(key)) return "Maria";
  if (/lang/.test(key)) return "English";
  if (/tone|style/.test(key)) return "concise";
  return `<${name}>`;
}

export const emptyMessage = (role: PromptRole = "user"): ChatTemplateMessage => ({ role, content: "" });

export type DraftError = string | null;

/** Client-side mirror of the server's template validation, returning the first problem or null. */
export function validateDraft(type: PromptType, template: PromptTemplate): DraftError {
  if (type === "text") {
    if (typeof template !== "string" || !template.trim()) return "Template is empty.";
    return null;
  }
  if (typeof template === "string" || template.length === 0) return "Add at least one message.";
  for (let i = 0; i < template.length; i++) {
    if (!(PROMPT_ROLES as readonly string[]).includes(template[i].role)) return `Message ${i + 1}: unknown role.`;
    if (!template[i].content.trim()) return `Message ${i + 1} is empty.`;
  }
  return null;
}

/** Parses a JSON object field. Empty input is `{ value: undefined }`. */
export function parseJsonObject(
  text: string,
  opts: { allowBoolean?: boolean } = {},
): { value: Record<string, unknown> | boolean | undefined; error: null } | { value: undefined; error: string } {
  const t = text.trim();
  if (!t) return { value: undefined, error: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(t);
  } catch (e) {
    return { value: undefined, error: `Invalid JSON: ${(e as Error).message}` };
  }
  if (typeof parsed === "boolean" && opts.allowBoolean) return { value: parsed, error: null };
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return { value: undefined, error: "Must be a JSON object." };
  return { value: parsed as Record<string, unknown>, error: null };
}

const SCHEMA_TYPES = ["object", "array", "string", "number", "integer", "boolean", "null"];

/** Shallow structural check of a JSON Schema (the server validates fully with jsonschema). */
export function checkSchemaShape(schema: unknown): string | null {
  if (typeof schema === "boolean") return null;
  if (typeof schema !== "object" || schema === null || Array.isArray(schema)) return "Schema must be an object.";
  const s = schema as Record<string, unknown>;
  const t = s.type;
  const types = Array.isArray(t) ? t : t === undefined ? [] : [t];
  for (const x of types) if (typeof x !== "string" || !SCHEMA_TYPES.includes(x)) return `Unknown schema type: ${JSON.stringify(x)}.`;
  if (s.properties !== undefined && (typeof s.properties !== "object" || s.properties === null || Array.isArray(s.properties)))
    return '"properties" must be an object.';
  if (s.required !== undefined && (!Array.isArray(s.required) || s.required.some((r) => typeof r !== "string")))
    return '"required" must be a list of strings.';
  if (s.properties && typeof s.properties === "object") {
    for (const [k, v] of Object.entries(s.properties as Record<string, unknown>)) {
      const sub = checkSchemaShape(v);
      if (sub) return `properties.${k}: ${sub}`;
    }
  }
  return null;
}

/** URL/proxy path for a prompt name; names may contain `/`, which stays a path separator. */
export function promptPath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

export type LabelMoveKind = "apply" | "promote" | "rollback" | "noop";

/** Describes moving `label` (currently on `from`, or nothing) to version `to`. Moving to an older version is a rollback. */
export function labelMoveKind(from: number | undefined, to: number): LabelMoveKind {
  if (from === undefined) return "apply";
  if (from === to) return "noop";
  return to < from ? "rollback" : "promote";
}
