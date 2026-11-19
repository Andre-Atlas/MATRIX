// Source scan (heuristic). ECharts sets tooltip/label `formatter` strings with innerHTML, so:
//  1. dangerouslySetInnerHTML / innerHTML / insertAdjacentHTML are banned in components;
//  2. in components/runs and components/charts, a template literal containing an HTML tag
//     (<b>, <br/>, <span ...) must not interpolate `${...}` unless the expression is wrapped in
//     escapeHtml(...) or is a number/format call. Build tooltips in lib/runs/tooltips.ts instead.
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");
const dirs = ["components/runs", "components/charts"];
const files = dirs.flatMap((d) => readdirSync(path.join(root, d)).filter((f) => /\.tsx?$/.test(f) && !/\.test\./.test(f)).map((f) => path.join(root, d, f)));

const SAFE = /^\s*(escapeHtml\(|formatMetric\(|f3\()/;

describe("no unescaped HTML in chart code", () => {
  it("scans files", () => expect(files.length).toBeGreaterThan(2));
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    it(`${path.relative(root, f)}: no raw HTML sinks`, () => {
      expect(src).not.toMatch(/dangerouslySetInnerHTML|\.innerHTML|insertAdjacentHTML|setHTML\(/);
    });
    it(`${path.relative(root, f)}: no unescaped \${} in HTML templates`, () => {
      const bad: string[] = [];
      for (const m of src.matchAll(/`([^`]*)`/g)) {
        const body = m[1];
        if (!/<\/?[a-z][^>]*>/i.test(body)) continue;
        for (const e of body.matchAll(/\$\{([^}]*)\}/g)) if (!SAFE.test(e[1])) bad.push(e[1]);
      }
      expect(bad).toEqual([]);
    });
  }
});
