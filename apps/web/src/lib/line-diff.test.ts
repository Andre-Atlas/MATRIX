import { describe, expect, it } from "vitest";
import { diffLines, diffStats, diffText, sideBySide, splitLines } from "./line-diff";

const types = (ops: { type: string }[]) => ops.map((o) => o.type[0]).join("");

describe("splitLines", () => {
  it("handles empty text and CRLF", () => {
    expect(splitLines("")).toEqual([]);
    expect(splitLines("a\r\nb")).toEqual(["a", "b"]);
  });
});

describe("diffLines", () => {
  it("returns only equal ops for identical input", () => {
    const ops = diffText("a\nb\nc", "a\nb\nc");
    expect(types(ops)).toBe("eee");
    expect(diffStats(ops).changed).toBe(false);
  });

  it("detects an insertion in the middle", () => {
    expect(diffLines(["a", "c"], ["a", "b", "c"])).toEqual([
      { type: "equal", text: "a" },
      { type: "add", text: "b" },
      { type: "equal", text: "c" },
    ]);
  });

  it("detects a deletion and a replacement", () => {
    expect(types(diffText("a\nb\nc", "a\nc"))).toBe("ede");
    const ops = diffText("a\nold\nc", "a\nnew\nc");
    expect(ops.map((o) => `${o.type}:${o.text}`)).toEqual(["equal:a", "del:old", "add:new", "equal:c"]);
  });

  it("handles empty sides", () => {
    expect(types(diffText("", "x\ny"))).toBe("aa");
    expect(types(diffText("x\ny", ""))).toBe("dd");
    expect(diffText("", "")).toEqual([]);
  });

  it("reconstructs both inputs from the ops", () => {
    const a = ["one", "two", "three", "four", "five", "six"];
    const b = ["zero", "one", "three", "four", "FIVE", "six", "seven"];
    const ops = diffLines(a, b);
    expect(ops.filter((o) => o.type !== "add").map((o) => o.text)).toEqual(a);
    expect(ops.filter((o) => o.type !== "del").map((o) => o.text)).toEqual(b);
    expect(diffStats(ops)).toEqual({ added: 3, removed: 2, changed: true });
  });

  it("falls back to replace for huge middles without throwing", () => {
    const a = Array.from({ length: 2500 }, (_, i) => `a${i}`);
    const b = Array.from({ length: 2500 }, (_, i) => `b${i}`);
    const ops = diffLines(a, b);
    expect(ops).toHaveLength(5000);
  });
});

describe("sideBySide", () => {
  it("aligns a replaced line as one change row with line numbers", () => {
    const rows = sideBySide(diffText("a\nold\nc", "a\nnew\nc"));
    expect(rows.map((r) => r.kind)).toEqual(["equal", "change", "equal"]);
    expect(rows[1].left).toEqual({ n: 2, text: "old" });
    expect(rows[1].right).toEqual({ n: 2, text: "new" });
  });

  it("leaves gaps for pure additions and deletions", () => {
    const rows = sideBySide(diffText("a\nb", "a\nb\nc\nd"));
    expect(rows.slice(2).map((r) => [r.kind, r.left, r.right?.n])).toEqual([
      ["add", null, 3],
      ["add", null, 4],
    ]);
    const del = sideBySide(diffText("a\nb\nc", "a"));
    expect(del.map((r) => r.kind)).toEqual(["equal", "del", "del"]);
    expect(del[2].left?.n).toBe(3);
  });

  it("pads the shorter side of an uneven change", () => {
    const rows = sideBySide(diffText("x\ny", "z"));
    expect(rows.map((r) => r.kind)).toEqual(["change", "del"]);
  });
});
