import { describe, expect, it } from "vitest";
import {
  checkSchemaShape,
  extractVariables,
  isValidLabel,
  isValidPromptName,
  labelMoveKind,
  promptPath,
  missingVariables,
  parseJsonObject,
  renderTemplate,
  templateToText,
  validateDraft,
} from "./prompt-template";

describe("extractVariables", () => {
  it("finds unique variables in order, tolerating whitespace", () => {
    expect(extractVariables("Hi {{name}}, {{ question }} {{  name }} {{context}}")).toEqual(["name", "question", "context"]);
  });

  it("accepts dots and hyphens, rejects invalid names", () => {
    expect(extractVariables("{{user.name}} {{a-b}} {{1bad}} {{ }} {{a b}} {x}")).toEqual(["user.name", "a-b"]);
  });

  it("scans every chat message", () => {
    expect(
      extractVariables([
        { role: "system", content: "Answer in {{lang}}" },
        { role: "user", content: "{{question}} / {{lang}}" },
      ]),
    ).toEqual(["lang", "question"]);
  });

  it("is stateless across calls (global regex)", () => {
    expect(extractVariables("{{a}}")).toEqual(["a"]);
    expect(extractVariables("{{a}}")).toEqual(["a"]);
  });
});

describe("renderTemplate", () => {
  it("substitutes whitespace-tolerant placeholders", () => {
    expect(renderTemplate("Hi {{ name }}! {{name}}", { name: "Ana" })).toBe("Hi Ana! Ana");
  });

  it("leaves missing variables as written", () => {
    expect(renderTemplate("{{a}} {{b}}", { a: "1" })).toBe("1 {{b}}");
  });

  it("does not re-expand substituted values or treat $ specially", () => {
    expect(renderTemplate("{{a}}", { a: "{{b}} $& $1" })).toBe("{{b}} $& $1");
  });

  it("renders chat messages and keeps roles", () => {
    const out = renderTemplate([{ role: "user", content: "Q: {{q}}" }], { q: "why" });
    expect(out).toEqual([{ role: "user", content: "Q: why" }]);
  });

  it("ignores inherited object keys", () => {
    expect(renderTemplate("{{constructor}}", {})).toBe("{{constructor}}");
  });
});

describe("helpers", () => {
  it("reports missing variables", () => {
    expect(missingVariables("{{a}} {{b}}", { a: "x", b: "" })).toEqual(["b"]);
  });

  it("flattens chat templates for diffing", () => {
    expect(templateToText([{ role: "system", content: "s" }, { role: "user", content: "u" }])).toBe("[system]\ns\n[user]\nu");
  });

  it("validates names and labels like the server", () => {
    expect(isValidPromptName("rag/answer")).toBe(true);
    expect(isValidPromptName("/bad")).toBe(false);
    expect(isValidPromptName("has space")).toBe(false);
    expect(isValidLabel("production")).toBe(true);
    expect(isValidLabel("Prod")).toBe(false);
  });

  it("validates drafts", () => {
    expect(validateDraft("text", "  ")).toMatch(/empty/);
    expect(validateDraft("text", "ok")).toBeNull();
    expect(validateDraft("chat", [])).toMatch(/at least one/);
    expect(validateDraft("chat", [{ role: "user", content: "" }])).toMatch(/Message 1 is empty/);
    expect(validateDraft("chat", [{ role: "robot", content: "x" }])).toMatch(/role/);
    expect(validateDraft("chat", [{ role: "user", content: "x" }])).toBeNull();
  });
});

describe("JSON fields", () => {
  it("parses objects, empty and errors", () => {
    expect(parseJsonObject("")).toEqual({ value: undefined, error: null });
    expect(parseJsonObject('{"a":1}')).toEqual({ value: { a: 1 }, error: null });
    expect(parseJsonObject("[1]").error).toMatch(/object/);
    expect(parseJsonObject("{oops").error).toMatch(/Invalid JSON/);
    expect(parseJsonObject("true", { allowBoolean: true }).value).toBe(true);
    expect(parseJsonObject("true").error).toMatch(/object/);
  });

  it("checks schema shape", () => {
    expect(checkSchemaShape({ type: "object", properties: { a: { type: "string" } }, required: ["a"] })).toBeNull();
    expect(checkSchemaShape({ type: "strng" })).toMatch(/Unknown schema type/);
    expect(checkSchemaShape({ properties: [] })).toMatch(/properties/);
    expect(checkSchemaShape({ required: "a" })).toMatch(/required/);
    expect(checkSchemaShape({ properties: { a: { type: "x" } } })).toMatch(/properties\.a/);
    expect(checkSchemaShape([])).toMatch(/object/);
  });
});

describe("promptPath / labelMoveKind", () => {
  it("encodes segments but keeps slashes", () => {
    expect(promptPath("rag/answer")).toBe("rag/answer");
    expect(promptPath("a b/c#d")).toBe("a%20b/c%23d");
  });

  it("classifies label moves", () => {
    expect(labelMoveKind(undefined, 2)).toBe("apply");
    expect(labelMoveKind(3, 2)).toBe("rollback");
    expect(labelMoveKind(2, 3)).toBe("promote");
    expect(labelMoveKind(2, 2)).toBe("noop");
  });
});
