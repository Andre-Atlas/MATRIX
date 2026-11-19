// URL ⇄ API translation for the runs screens: list filters and the `ids` of the compare page.
import { MAX_COMPARE_RUNS, RUN_SORTS, isRunStatus, type RunSort, type RunStatus } from "./types";

const RUN_ID = /^[A-Za-z0-9_.:-]{1,64}$/;

export type IdsParse = { ids: string[]; invalid: string[]; truncated: boolean };

/**
 * `ids=a,b,c` (also repeated `ids=` params): trimmed, de-duplicated in order, ids that are not
 * valid run ids reported apart, at most `MAX_COMPARE_RUNS` kept (`truncated` says so).
 */
export function parseIds(raw: string | string[] | undefined | null, max = MAX_COMPARE_RUNS): IdsParse {
  const parts = (Array.isArray(raw) ? raw : [raw ?? ""]).flatMap((s) => s.split(","));
  const ids: string[] = [];
  const invalid: string[] = [];
  for (const p of parts) {
    const id = p.trim();
    if (!id) continue;
    if (!RUN_ID.test(id)) {
      invalid.push(id.slice(0, 80));
      continue;
    }
    if (!ids.includes(id)) ids.push(id);
  }
  return { ids: ids.slice(0, max), invalid, truncated: ids.length > max };
}

export type RunFilters = {
  status: RunStatus | null;
  group: string | null;
  tag: string | null;
  q: string | null;
  sort: RunSort;
  cursor: string | null;
};

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => {
  const s = (Array.isArray(v) ? v[0] : v)?.trim();
  return s ? s.slice(0, 200) : null;
};

/** Filters from the page's searchParams; anything invalid falls back to "no filter". */
export function runFilters(sp: SP): RunFilters {
  const status = one(sp.status);
  const sort = one(sp.sort);
  return {
    status: isRunStatus(status) ? status : null,
    group: one(sp.group),
    tag: one(sp.tag),
    q: one(sp.q),
    sort: (RUN_SORTS as readonly string[]).includes(sort ?? "") ? (sort as RunSort) : "-started_at",
    cursor: one(sp.cursor),
  };
}

/** Query params of `GET /runs` for these filters. */
export function runListParams(f: RunFilters, limit = 50): Record<string, string | number | undefined> {
  return {
    status: f.status ?? undefined,
    group: f.group ?? undefined,
    tag: f.tag ?? undefined,
    q: f.q ?? undefined,
    sort: f.sort,
    limit,
    cursor: f.cursor ?? undefined,
  };
}

/** `/runs?…` with some filters changed (null removes one). The cursor is dropped unless given. */
export function runsHref(f: RunFilters, change: Partial<RunFilters>): string {
  const next = { ...f, cursor: null, ...change };
  const sp = new URLSearchParams();
  if (next.status) sp.set("status", next.status);
  if (next.group) sp.set("group", next.group);
  if (next.tag) sp.set("tag", next.tag);
  if (next.q) sp.set("q", next.q);
  if (next.sort !== "-started_at") sp.set("sort", next.sort);
  if (next.cursor) sp.set("cursor", next.cursor);
  const s = sp.toString();
  return s ? `/runs?${s}` : "/runs";
}

export const compareHref = (ids: readonly string[]) => `/runs/compare?ids=${ids.map(encodeURIComponent).join(",")}`;
