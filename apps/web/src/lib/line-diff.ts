// Small LCS line diff used by prompt version comparison and the annotation correction preview.

export type DiffOp = { type: "equal" | "del" | "add"; text: string };

/** One row of a side-by-side view. `left`/`right` carry 1-based line numbers; null means a gap. */
export type SideRow = {
  kind: "equal" | "change" | "del" | "add";
  left: { n: number; text: string } | null;
  right: { n: number; text: string } | null;
};

/** Above this many cells the quadratic table is skipped and the middle is shown as replaced. */
const MAX_CELLS = 4_000_000;

export function splitLines(text: string): string[] {
  if (text === "") return [];
  return text.replace(/\r\n/g, "\n").split("\n");
}

/** Line diff of `a` → `b`. Common prefix/suffix are trimmed first, the middle uses an LCS table. */
export function diffLines(a: string[], b: string[]): DiffOp[] {
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const out: DiffOp[] = a.slice(0, start).map((text) => ({ type: "equal", text }));
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  out.push(...diffMiddle(midA, midB));
  for (let i = endA; i < a.length; i++) out.push({ type: "equal", text: a[i] });
  return out;
}

function diffMiddle(a: string[], b: string[]): DiffOp[] {
  const n = a.length;
  const m = b.length;
  if (n === 0) return b.map((text) => ({ type: "add", text }));
  if (m === 0) return a.map((text) => ({ type: "del", text }));
  if ((n + 1) * (m + 1) > MAX_CELLS) {
    return [...a.map((text): DiffOp => ({ type: "del", text })), ...b.map((text): DiffOp => ({ type: "add", text }))];
  }
  // lcs[i][j] = LCS length of a[i..] and b[j..]
  const w = m + 1;
  const lcs = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * w + j] = a[i] === b[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
    }
  }
  const out: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "equal", text: a[i] });
      i++;
      j++;
    } else if (lcs[(i + 1) * w + j] >= lcs[i * w + j + 1]) {
      out.push({ type: "del", text: a[i++] });
    } else {
      out.push({ type: "add", text: b[j++] });
    }
  }
  while (i < n) out.push({ type: "del", text: a[i++] });
  while (j < m) out.push({ type: "add", text: b[j++] });
  return out;
}

export function diffText(a: string, b: string): DiffOp[] {
  return diffLines(splitLines(a), splitLines(b));
}

/** Pairs each run of deletions with the following run of additions so edits line up as "change" rows. */
export function sideBySide(ops: DiffOp[]): SideRow[] {
  const rows: SideRow[] = [];
  let l = 0;
  let r = 0;
  let i = 0;
  while (i < ops.length) {
    if (ops[i].type === "equal") {
      rows.push({ kind: "equal", left: { n: ++l, text: ops[i].text }, right: { n: ++r, text: ops[i].text } });
      i++;
      continue;
    }
    const dels: string[] = [];
    const adds: string[] = [];
    while (i < ops.length && ops[i].type === "del") dels.push(ops[i++].text);
    while (i < ops.length && ops[i].type === "add") adds.push(ops[i++].text);
    const len = Math.max(dels.length, adds.length);
    for (let k = 0; k < len; k++) {
      const left = k < dels.length ? { n: ++l, text: dels[k] } : null;
      const right = k < adds.length ? { n: ++r, text: adds[k] } : null;
      rows.push({ kind: left && right ? "change" : left ? "del" : "add", left, right });
    }
  }
  return rows;
}

export function diffStats(ops: DiffOp[]): { added: number; removed: number; changed: boolean } {
  const added = ops.filter((o) => o.type === "add").length;
  const removed = ops.filter((o) => o.type === "del").length;
  return { added, removed, changed: added + removed > 0 };
}
