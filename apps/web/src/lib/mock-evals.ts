// In-memory datasets and experiments for MATRIX_MOCK=1 (development and screenshots only).
// State lives on globalThis so route handlers and server components share it in one process.
import { ApiError } from "./api-error";
import { cellKey, summarizeMatrix, toSummaryRecord } from "./eval-matrix";
import { MOCK_RAG_TRACE_ID, mockDb } from "./mock-data";
import {
  METRIC_TYPES,
  MODEL_PROVIDERS,
  metricKey,
  type CreateDatasetItemRequest,
  type CreateDatasetRequest,
  type CreateExperimentRequest,
  type Dataset,
  type DatasetDetail,
  type DatasetItem,
  type DatasetItemFromSpanRequest,
  type Experiment,
  type ExperimentDetail,
  type ExperimentListItem,
  type ExperimentResult,
  type FaithfulnessStatement,
  type ListResponse,
  type MetricScore,
  type MetricSpec,
  type Regression,
  type Variant,
} from "./types";

type Sentence = { s: string; ok: boolean; why: string };
type Script = { good: string[]; bad: { s: string; why: string }; precision: number };

type ExperimentRec = {
  exp: Omit<Experiment, "status" | "summary" | "started_at" | "finished_at">;
  all: ExperimentResult[];
  startAt: number;
  msPerCell: number;
  failWith?: string;
};

type Store = {
  datasets: Omit<Dataset, "item_count">[];
  items: DatasetItem[];
  scripts: Map<string, Script>;
  experiments: ExperimentRec[];
};

// ── helpers ──────────────────────────────────────────────────────────────────────────────

function hash01(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h / 4294967295;
}

let counter = 0;
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}${(counter++).toString(36).padStart(3, "0")}`;
const iso = (ms: number) => new Date(ms).toISOString();
const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
const round = (x: number, d = 4) => Math.round(x * 10 ** d) / 10 ** d;

function levenshteinSim(a: string, b: string): number {
  a = a.slice(0, 2000);
  b = b.slice(0, 2000);
  if (!a.length && !b.length) return 1;
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return 1 - prev[b.length] / Math.max(a.length, b.length);
}

function rougeL(a: string, b: string): number {
  const x = a.toLowerCase().split(/\W+/).filter(Boolean);
  const y = b.toLowerCase().split(/\W+/).filter(Boolean);
  if (!x.length || !y.length) return 0;
  const dp = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = 1; i <= x.length; i++)
    for (let j = 1; j <= y.length; j++)
      dp[i][j] = x[i - 1] === y[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  const lcs = dp[x.length][y.length];
  if (!lcs) return 0;
  const p = lcs / x.length;
  const r = lcs / y.length;
  return (2 * p * r) / (p + r);
}

const DEFAULT_THRESHOLD: Partial<Record<string, number>> = {
  faithfulness: 0.7,
  answer_relevancy: 0.7,
  context_precision: 0.7,
  g_eval: 0.7,
  levenshtein: 0.8,
  rouge_l: 0.5,
};

const na = (reason: string): MetricScore => ({ value: null, passed: null, reason });
const bool = (ok: boolean, reason: string): MetricScore => ({ value: ok ? 1 : 0, passed: ok, reason });

// ── simulated runs ───────────────────────────────────────────────────────────────────────

type RunOpts = { hallucinate?: boolean; latency?: number; error?: string };

function questionOf(item: DatasetItem): string {
  const q = item.input.question ?? item.input.query ?? item.input.input ?? Object.values(item.input)[0];
  return typeof q === "string" ? q : JSON.stringify(item.input);
}

function scriptFor(store: Store, item: DatasetItem): Script {
  const seeded = store.scripts.get(item.id);
  if (seeded) return seeded;
  const base = item.expected_output ?? `Answer to: ${questionOf(item)}`;
  const good = base
    .split(/(?<=[.!?])\s+/)
    .map((x) => x.trim())
    .filter(Boolean);
  return {
    good: good.length ? good : [base],
    bad: { s: "This behaviour changed in the 2026.10 release.", why: "No context mentions a 2026.10 release." },
    precision: round(0.6 + hash01(item.id) * 0.38, 2),
  };
}

function simulate(store: Store, item: DatasetItem, v: Variant, metrics: MetricSpec[], opts: RunOpts): ExperimentResult {
  const seed = `${item.id}:${v.label}`;
  const v2 = /v2|v3/.test(v.label) || ("template" in v.prompt && v.prompt.template.length > 0);
  const claude = v.model.provider === "anthropic";
  const script = scriptFor(store, item);
  const sentences: Sentence[] = (v2 ? script.good : script.good.slice(0, Math.max(1, script.good.length - 1))).map(
    (s) => ({ s, ok: true, why: "Stated in the retrieved context." }),
  );
  const hallucinate = opts.hallucinate ?? hash01(`h:${seed}`) < 0.15;
  if (hallucinate) sentences.splice(Math.min(1, sentences.length), 0, { ...script.bad, ok: false });
  const prefix = v.model.provider === "fake" ? `[fake:${v.model.name}] ` : "";
  const output = opts.error ? null : prefix + sentences.map((x) => x.s).join(" ");
  const inTok = 820 + Math.round(hash01(`in:${seed}`) * 260) + (v2 ? 140 : 0);
  const outTok = output ? Math.round(output.length / 4) : 0;
  const latency = opts.latency ?? Math.round((claude ? 1300 : 850) + hash01(`lat:${seed}`) * (claude ? 900 : 700));
  const cost = claude ? inTok * 1e-6 + outTok * 5e-6 : inTok * 0.15e-6 + outTok * 0.6e-6;
  const jitter = (k: string, w: number) => (hash01(`${k}:${seed}`) - 0.5) * 2 * w;

  const scores: Record<string, MetricScore> = {};
  for (const m of metrics) {
    const key = metricKey(m);
    const p = m.params ?? {};
    const th = m.threshold ?? DEFAULT_THRESHOLD[m.type] ?? 0.5;
    const graded = (value: number, reason: string, details?: Record<string, unknown>): MetricScore => ({
      value: round(clamp01(value), 4),
      passed: clamp01(value) >= th,
      reason,
      ...(details ? { details } : {}),
    });
    if (output === null) {
      scores[key] = na("no output (variant error)");
      continue;
    }
    switch (m.type) {
      case "faithfulness": {
        if (!item.context.length) {
          scores[key] = na("no contexts to verify against");
          break;
        }
        const statements: FaithfulnessStatement[] = sentences.map((x) => ({
          statement: x.s,
          supported: x.ok,
          reason: x.why,
        }));
        const sup = statements.filter((x) => x.supported).length;
        const uns = statements.length - sup;
        scores[key] = graded(
          sup / statements.length,
          `${sup}/${statements.length} statements supported by context${uns ? `; ${uns} unsupported` : ""}`,
          { statements },
        );
        break;
      }
      case "answer_relevancy": {
        const v = 0.82 + (v2 ? 0.06 : 0) + (claude ? 0.03 : 0) + jitter("ar", 0.05) - (hallucinate ? 0.05 : 0);
        scores[key] = graded(v, `mean cosine ${clamp01(v).toFixed(3)} over 3 generated questions`);
        break;
      }
      case "context_precision": {
        if (!item.context.length) {
          scores[key] = na("no contexts");
          break;
        }
        scores[key] = graded(
          script.precision,
          `${Math.round(script.precision * item.context.length)}/${item.context.length} chunks relevant, weighted by rank`,
        );
        break;
      }
      case "g_eval": {
        const v = 0.72 + (v2 ? 0.1 : 0) + jitter("ge", 0.06) - (hallucinate ? 0.25 : 0);
        scores[key] = graded(
          v,
          hallucinate
            ? `Partly meets "${String(p.criteria ?? "criteria")}": adds a claim not in the context.`
            : `Meets "${String(p.criteria ?? "criteria")}"; concise and grounded.`,
        );
        break;
      }
      case "equals":
        scores[key] =
          item.expected_output === null
            ? na("no expected value")
            : bool(output.trim() === item.expected_output.trim(), "exact match against expected output");
        break;
      case "contains": {
        const needle = String(p.value ?? "");
        const ok = output.toLowerCase().includes(needle.toLowerCase());
        scores[key] = bool(ok, ok ? `output contains "${needle}"` : `"${needle}" not found in output`);
        break;
      }
      case "regex": {
        try {
          const ok = new RegExp(String(p.pattern ?? "")).test(output);
          scores[key] = bool(ok, ok ? `matches /${String(p.pattern)}/` : `no match for /${String(p.pattern)}/`);
        } catch {
          scores[key] = { value: null, passed: false, reason: "error: invalid regex pattern" };
        }
        break;
      }
      case "is_json": {
        try {
          JSON.parse(output);
          scores[key] = bool(true, "output parses as JSON");
        } catch (e) {
          scores[key] = bool(false, `not JSON: ${(e as Error).message.slice(0, 60)}`);
        }
        break;
      }
      case "levenshtein":
        scores[key] =
          item.expected_output === null
            ? na("no expected value")
            : graded(levenshteinSim(output, item.expected_output), "1 - edit distance / max length");
        break;
      case "rouge_l":
        scores[key] =
          item.expected_output === null
            ? na("no expected value")
            : graded(rougeL(output, item.expected_output), "ROUGE-L F1 against expected output");
        break;
      case "cost": {
        const max = Number(p.max_usd ?? m.threshold ?? 0);
        const ok = cost <= max;
        scores[key] = { ...bool(ok, `${round(cost, 6)} USD ${ok ? "<=" : ">"} ${max} USD`), details: { actual: cost, limit: max } };
        break;
      }
      case "latency": {
        const max = Number(p.max_ms ?? m.threshold ?? 0);
        const ok = latency <= max;
        scores[key] = { ...bool(ok, `${latency} ms ${ok ? "<=" : ">"} ${max} ms`), details: { actual: latency, limit: max } };
        break;
      }
    }
  }

  return {
    item_id: item.id,
    variant: v.label,
    output,
    latency_ms: latency,
    cost_usd: opts.error ? 0 : round(cost, 6),
    input_tokens: inTok,
    output_tokens: outTok,
    error: opts.error ?? null,
    scores,
  };
}

// ── seed ─────────────────────────────────────────────────────────────────────────────────

const SEED_ITEMS: { question: string; context: string[]; expected: string; script: Script; source?: number }[] = [
  {
    question: "Why did p95 latency regress after the gateway deploy?",
    context: [
      "When p95 latency regresses, first compare TTFT against total generation time. A TTFT spike usually means queueing at the provider; a long tail after the first token points to output length or tool loops.",
      "The gateway retries 429 and 5xx responses with exponential backoff (base 200 ms, max 3 attempts). Retries are recorded as span events named `retry`.",
    ],
    expected:
      "Compare TTFT with total generation time: a TTFT spike points to provider queueing, a long tail after the first token to output length or tool loops.",
    script: {
      good: [
        "Start by comparing TTFT against total generation time.",
        "A TTFT spike usually means requests are queueing at the provider.",
        "A long tail after the first token points to output length or tool loops.",
      ],
      bad: {
        s: "The gateway deploy disabled prompt caching, which doubled TTFT.",
        why: "No context links the gateway deploy to prompt caching or to TTFT doubling.",
      },
      precision: 0.92,
    },
    source: 0,
  },
  {
    question: "How does the gateway handle rate limits?",
    context: [
      "The gateway retries 429 and 5xx responses with exponential backoff (base 200 ms, max 3 attempts). Retries are recorded as span events named `retry`.",
      "429 responses include a Retry-After header that the gateway honours when present.",
    ],
    expected:
      "It retries 429 and 5xx responses with exponential backoff (base 200 ms, up to 3 attempts) and honours Retry-After.",
    script: {
      good: [
        "The gateway retries 429 and 5xx responses with exponential backoff.",
        "Backoff starts at 200 ms and stops after 3 attempts, honouring Retry-After when present.",
        "Each retry is recorded as a span event named retry.",
      ],
      bad: {
        s: "After the third attempt the request is moved to a dead-letter queue.",
        why: "Contexts cap retries at 3 attempts but never mention a dead-letter queue.",
      },
      precision: 0.83,
    },
    source: 1,
  },
  {
    question: "Does the SDK cache prompts?",
    context: [
      "get_prompt caches each prompt version in memory with a 60 s TTL and refreshes it in the background.",
      "If the API is unreachable, the SDK serves the last cached version and logs a warning.",
    ],
    expected:
      "Yes. Prompts are cached in memory for 60 s and refreshed in the background; the last version is served if the API is down.",
    script: {
      good: [
        "Yes, get_prompt caches each prompt version in memory for 60 seconds.",
        "The cache is refreshed in the background.",
        "When the API is unreachable the SDK keeps serving the last cached version.",
      ],
      bad: { s: "The cache is persisted to disk between restarts.", why: "Contexts only describe an in-memory cache." },
      precision: 1,
    },
  },
  {
    question: "What does context precision measure?",
    context: [
      "Context precision is the mean precision@k over retrieved chunks, weighting relevant chunks that appear earlier.",
      "Relevance of each chunk is judged by an LLM against the expected answer.",
      "Embeddings are computed with text-embedding-3-small and stored in pgvector.",
    ],
    expected: "How well the retriever ranks relevant chunks first: mean precision@k, judged against the expected answer.",
    script: {
      good: [
        "Context precision measures how well the retriever ranks relevant chunks first.",
        "It is the mean precision@k over the retrieved chunks.",
        "Each chunk's relevance is judged against the expected answer.",
      ],
      bad: {
        s: "It is computed from embedding cosine similarity alone.",
        why: "Contexts say relevance is judged by an LLM against the expected answer, not by cosine similarity.",
      },
      precision: 0.58,
    },
  },
  {
    question: "How are online evaluations sampled?",
    context: [
      "Online evaluators run on a deterministic sample of traces: hash(trace_id) mod 10000 < rate × 10000.",
      "The same trace is always in or out of the sample, so re-runs are stable.",
    ],
    expected: "Deterministically: the trace id is hashed against the sampling rate, so a trace is consistently in or out.",
    script: {
      good: [
        "Sampling is deterministic: the trace id is hashed and compared to the sampling rate.",
        "A given trace is always either in or out of the sample.",
        "That keeps re-runs stable.",
      ],
      bad: {
        s: "Traces with errors are always sampled regardless of the rate.",
        why: "No context says that errored traces bypass the sampling rate.",
      },
      precision: 0.75,
    },
  },
  {
    question: "Where are DPO pairs exported?",
    context: [
      "Completing an annotation with a corrected output stores a DPO pair {prompt, chosen, rejected}.",
      "GET /api/v1/exports/dpo.jsonl streams all pairs for the project as JSONL.",
    ],
    expected: "GET /api/v1/exports/dpo.jsonl streams {prompt, chosen, rejected} pairs created from corrected annotations.",
    script: {
      good: [
        "DPO pairs are exported from GET /api/v1/exports/dpo.jsonl.",
        "Each line is a {prompt, chosen, rejected} pair.",
        "Pairs are created when an annotation is completed with a corrected output.",
      ],
      bad: { s: "Pairs are also pushed to the Hugging Face Hub nightly.", why: "Contexts only mention the JSONL export endpoint." },
      precision: 0.88,
    },
  },
];

const RAG_METRICS: MetricSpec[] = [
  { type: "faithfulness", threshold: 0.8 },
  { type: "answer_relevancy" },
  { type: "context_precision" },
  { type: "g_eval", params: { criteria: "Answers concisely using only the provided context" } },
  { type: "latency", params: { max_ms: 2500 } },
  { type: "cost", params: { max_usd: 0.002 } },
];

const variant = (prompt: 1 | 2 | 3, provider: "openai" | "anthropic", name: string): Variant => ({
  label: `v${prompt} · ${name}`,
  prompt: { name: "rag-answer", version: prompt },
  model: { provider, name, params: { temperature: 0 } },
});
const V1_GPT = variant(1, "openai", "gpt-4o-mini");
const V1_CLAUDE = variant(1, "anthropic", "claude-haiku-4.5");
const V2_GPT = variant(2, "openai", "gpt-4o-mini");
const V2_CLAUDE = variant(2, "anthropic", "claude-haiku-4.5");

export const MOCK_DATASET_ID = "ds_rag_golden";
export const MOCK_EXPERIMENT_ID = "exp_prompt_v2_sweep";
export const MOCK_BASELINE_ID = "exp_nightly_1108";

function seed(now: number): Store {
  const store: Store = { datasets: [], items: [], scripts: new Map(), experiments: [] };
  const day = 86_400_000;
  store.datasets.push(
    {
      id: MOCK_DATASET_ID,
      name: "rag-golden",
      description: "Support RAG questions with retrieved contexts and reference answers",
      created_at: iso(now - 6 * day),
    },
    {
      id: "ds_agent_regressions",
      name: "agent-regressions",
      description: "Failed support-agent runs promoted from the annotation queue",
      created_at: iso(now - 2 * day),
    },
  );

  const db = mockDb(now);
  const rag = db.traces.filter((t) => t.summary.root_kind === "CHAIN" || t.summary.tags.includes("rag"));
  const ragTraces = [db.traces.find((t) => t.summary.trace_id === MOCK_RAG_TRACE_ID), ...rag].filter(
    (t, i, a): t is NonNullable<typeof t> => !!t && a.indexOf(t) === i,
  );

  SEED_ITEMS.forEach((s, i) => {
    const src = s.source !== undefined ? ragTraces[s.source] : undefined;
    const gen = src?.spans.find((x) => x.name === "generate") ?? src?.spans[0];
    const id = `item_${String(i + 1).padStart(2, "0")}`;
    store.items.push({
      id,
      dataset_id: MOCK_DATASET_ID,
      input: { question: s.question },
      expected_output: s.expected,
      context: s.context,
      metadata: { topic: ["latency", "gateway", "sdk", "evals", "evals", "flywheel"][i] },
      source_trace_id: src && gen ? src.summary.trace_id : null,
      source_span_id: src && gen ? gen.span_id : null,
      created_at: iso(now - 6 * day + i * 3_600_000),
    });
    store.scripts.set(id, s.script);
  });

  const items = store.items.filter((x) => x.dataset_id === MOCK_DATASET_ID);
  const run = (variants: Variant[], metrics: MetricSpec[], opts: (idx: number, v: Variant) => RunOpts) =>
    items.flatMap((it, idx) => variants.map((v) => simulate(store, it, v, metrics, opts(idx, v))));
  const set = (pairs: [number, Variant][]) => new Set(pairs.map(([i, v]) => `${i}|${v.label}`));

  // Baseline: yesterday's nightly on prompt v1.
  const baseHall = set([[3, V1_CLAUDE]]);
  const baseVariants = [V1_GPT, V1_CLAUDE];
  const baseStart = now - day - 40 * 60_000;
  store.experiments.push({
    exp: {
      id: MOCK_BASELINE_ID,
      name: "rag-answer nightly 11-08",
      dataset_id: MOCK_DATASET_ID,
      baseline_experiment_id: null,
      variants: baseVariants,
      metrics: RAG_METRICS,
      error: null,
      created_at: iso(baseStart - 4000),
    },
    all: run(baseVariants, RAG_METRICS, (i, v) => ({
      hallucinate: baseHall.has(`${i}|${v.label}`),
      latency: v === V1_CLAUDE && i === 2 ? 1790 : undefined,
    })),
    startAt: baseStart,
    msPerCell: 9000,
  });

  // Today's sweep: prompt v1/v2 × two models, compared against the nightly.
  const hall = set([
    [1, V1_GPT],
    [4, V1_GPT],
    [3, V1_CLAUDE],
    [0, V2_CLAUDE],
    [4, V2_CLAUDE],
  ]);
  const sweepVariants = [V1_GPT, V1_CLAUDE, V2_GPT, V2_CLAUDE];
  const sweepStart = now - 3 * 3_600_000;
  store.experiments.push({
    exp: {
      id: MOCK_EXPERIMENT_ID,
      name: "rag-answer prompt v2 sweep",
      dataset_id: MOCK_DATASET_ID,
      baseline_experiment_id: MOCK_BASELINE_ID,
      variants: sweepVariants,
      metrics: RAG_METRICS,
      error: null,
      created_at: iso(sweepStart - 5000),
    },
    all: run(sweepVariants, RAG_METRICS, (i, v) => ({
      hallucinate: hall.has(`${i}|${v.label}`),
      latency: v === V1_CLAUDE && i === 2 ? 2930 : undefined,
      error: v === V2_CLAUDE && i === 5 ? "anthropic: 529 overloaded_error after 3 attempts" : undefined,
    })),
    startAt: sweepStart,
    msPerCell: 7000,
  });

  // A failed run (bad provider key).
  store.experiments.push({
    exp: {
      id: "exp_o4_trial",
      name: "rag-answer o4-mini trial",
      dataset_id: MOCK_DATASET_ID,
      baseline_experiment_id: null,
      variants: [variant(2, "openai", "o4-mini")],
      metrics: RAG_METRICS.slice(0, 3),
      error: "openai: 401 invalid_api_key — set OPENAI_API_KEY on the worker",
      created_at: iso(now - 50 * 60_000),
    },
    all: [],
    startAt: now - 50 * 60_000 + 2000,
    msPerCell: 1000,
    failWith: "openai: 401 invalid_api_key — set OPENAI_API_KEY on the worker",
  });

  // Running now: prompt v3 on two models, a cell every 40 s.
  const v3 = [variant(3, "openai", "gpt-4o-mini"), variant(3, "anthropic", "claude-haiku-4.5")];
  store.experiments.push({
    exp: {
      id: "exp_prompt_v3",
      name: "rag-answer prompt v3 (citations)",
      dataset_id: MOCK_DATASET_ID,
      baseline_experiment_id: MOCK_EXPERIMENT_ID,
      variants: v3,
      metrics: RAG_METRICS,
      error: null,
      created_at: iso(now - 3 * 40_000 - 6000),
    },
    all: run(v3, RAG_METRICS, () => ({ hallucinate: false })),
    startAt: now - 3 * 40_000 - 2000,
    msPerCell: 40_000,
  });

  return store;
}

function store(): Store {
  const g = globalThis as unknown as { __matrixEvalStore?: Store };
  g.__matrixEvalStore ??= seed(Date.now());
  return g.__matrixEvalStore;
}

// ── views ────────────────────────────────────────────────────────────────────────────────

function datasetView(s: Store, d: Omit<Dataset, "item_count">): Dataset {
  return { ...d, item_count: s.items.filter((i) => i.dataset_id === d.id).length };
}

function experimentView(rec: ExperimentRec, now = Date.now()): { experiment: Experiment; results: ExperimentResult[] } {
  const total = rec.all.length;
  let status: Experiment["status"];
  let done = 0;
  let finished: number | null = null;
  if (now < rec.startAt) status = "queued";
  else if (rec.failWith) {
    status = "failed";
    finished = rec.startAt + 1500;
  } else {
    done = Math.min(total, Math.floor((now - rec.startAt) / rec.msPerCell));
    status = done >= total ? "done" : "running";
    if (status === "done") finished = rec.startAt + total * rec.msPerCell;
  }
  const results = rec.all.slice(0, done);
  const summary =
    status === "queued" || status === "failed"
      ? null
      : toSummaryRecord(summarizeMatrix(results, rec.exp.metrics, rec.exp.variants.map((v) => v.label)));
  return {
    experiment: {
      ...rec.exp,
      error: rec.failWith ?? rec.exp.error,
      status,
      summary,
      started_at: now >= rec.startAt ? iso(rec.startAt) : null,
      finished_at: finished !== null ? iso(finished) : null,
    },
    results,
  };
}

function regressionsFor(s: Store, rec: ExperimentRec, results: ExperimentResult[]): Regression[] {
  const base = s.experiments.find((e) => e.exp.id === rec.exp.baseline_experiment_id);
  if (!base) return [];
  const baseResults = experimentView(base).results;
  const byCell = new Map(baseResults.map((r) => [cellKey(r.item_id, r.variant), r]));
  const out: Regression[] = [];
  for (const r of results) {
    const b = byCell.get(cellKey(r.item_id, r.variant));
    if (!b) continue;
    for (const [metric, cur] of Object.entries(r.scores)) {
      const prev = b.scores[metric];
      if (prev?.passed === true && cur.passed === false) {
        out.push({ item_id: r.item_id, variant: r.variant, metric, baseline: prev.value, current: cur.value });
      }
    }
  }
  return out;
}

// ── handlers ─────────────────────────────────────────────────────────────────────────────

export function evalGet(p: string): unknown {
  const s = store();
  let m: RegExpExecArray | null;
  if (p === "/api/v1/datasets") {
    const items = s.datasets.map((d) => datasetView(s, d)).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return { items, next_cursor: null } satisfies ListResponse<Dataset>;
  }
  if ((m = /^\/api\/v1\/datasets\/([^/]+)$/.exec(p))) {
    const d = s.datasets.find((x) => x.id === m![1]);
    if (!d) throw new ApiError(404, "dataset not found");
    return { dataset: datasetView(s, d), items: s.items.filter((i) => i.dataset_id === d.id) } satisfies DatasetDetail;
  }
  if (p === "/api/v1/experiments") {
    const items: ExperimentListItem[] = s.experiments
      .map((e) => experimentView(e).experiment)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map(({ id, name, dataset_id, status, created_at, finished_at, summary }) => ({
        id,
        name,
        dataset_id,
        status,
        created_at,
        finished_at,
        summary,
      }));
    return { items, next_cursor: null };
  }
  if ((m = /^\/api\/v1\/experiments\/([^/]+)$/.exec(p))) {
    const rec = s.experiments.find((x) => x.exp.id === m![1]);
    if (!rec) throw new ApiError(404, "experiment not found");
    const { experiment, results } = experimentView(rec);
    return {
      experiment,
      results,
      summary: experiment.summary ?? {},
      regressions: regressionsFor(s, rec, results),
    } satisfies ExperimentDetail;
  }
  return undefined;
}

export function evalExportJsonl(p: string): string | undefined {
  const m = /^\/api\/v1\/datasets\/([^/]+)\/export\.jsonl$/.exec(p);
  if (!m) return undefined;
  const s = store();
  if (!s.datasets.some((d) => d.id === m[1])) throw new ApiError(404, "dataset not found");
  return s.items
    .filter((i) => i.dataset_id === m[1])
    .map((i) =>
      JSON.stringify({
        input: i.input,
        expected_output: i.expected_output,
        context: i.context,
        metadata: i.metadata,
        source_trace_id: i.source_trace_id,
        source_span_id: i.source_span_id,
      }),
    )
    .join("\n")
    .concat("\n");
}

function bad(msg: string): never {
  throw new ApiError(422, JSON.stringify({ detail: msg }));
}

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function itemFromSpan(s: Store, datasetId: string, body: DatasetItemFromSpanRequest): DatasetItem {
  const trace = mockDb().traces.find((t) => t.summary.trace_id === body.trace_id);
  if (!trace) throw new ApiError(404, "trace not found");
  const span = body.span_id ? trace.spans.find((x) => x.span_id === body.span_id) : trace.spans[0];
  if (!span) throw new ApiError(404, "span not found");
  let input: Record<string, unknown>;
  const lastUser = [...span.input_messages].reverse().find((x) => x.role === "user");
  if (lastUser) input = { question: lastUser.content };
  else {
    try {
      const parsed = span.input ? JSON.parse(span.input) : null;
      input = isObject(parsed) ? parsed : { input: span.input ?? "" };
    } catch {
      input = { input: span.input ?? "" };
    }
  }
  const lastOut = [...span.output_messages].reverse().find((x) => x.role === "assistant");
  const docs = span.documents.length ? span.documents : trace.spans.filter((x) => x.kind === "RETRIEVER").flatMap((x) => x.documents);
  return {
    id: newId("item"),
    dataset_id: datasetId,
    input,
    expected_output: lastOut?.content ?? span.output ?? null,
    context: docs.map((d) => d.content),
    metadata: { span_name: span.name, span_kind: span.kind, ...(span.model ? { model: span.model } : {}) },
    source_trace_id: trace.summary.trace_id,
    source_span_id: span.span_id,
    created_at: iso(Date.now()),
  };
}

/** Used by the annotation mock: adds the trace's root span as a regression case (optionally with a corrected answer). */
export function mockAddTraceToDataset(datasetId: string, traceId: string, expectedOutput: string | null): DatasetItem {
  const s = store();
  if (!s.datasets.some((d) => d.id === datasetId)) throw new ApiError(404, "dataset not found");
  const item = itemFromSpan(s, datasetId, { trace_id: traceId });
  if (expectedOutput !== null) item.expected_output = expectedOutput;
  s.items.push(item);
  return item;
}

function validateExperiment(s: Store, body: CreateExperimentRequest) {
  if (!body?.name?.trim()) bad("name is required");
  if (!s.datasets.some((d) => d.id === body.dataset_id)) bad("dataset_id: unknown dataset");
  if (body.baseline_experiment_id && !s.experiments.some((e) => e.exp.id === body.baseline_experiment_id))
    bad("baseline_experiment_id: unknown experiment");
  if (!Array.isArray(body.variants) || body.variants.length === 0) bad("at least one variant is required");
  const labels = new Set<string>();
  for (const v of body.variants) {
    if (!v.label?.trim()) bad("variant label is required");
    if (labels.has(v.label)) bad(`duplicate variant label "${v.label}"`);
    labels.add(v.label);
    if (!MODEL_PROVIDERS.includes(v.model?.provider)) bad(`variant ${v.label}: unknown provider`);
    if (!v.model?.name) bad(`variant ${v.label}: model name is required`);
  }
  if (!Array.isArray(body.metrics) || body.metrics.length === 0) bad("at least one metric is required");
  for (const m of body.metrics) if (!METRIC_TYPES.includes(m.type)) bad(`unknown metric type "${m.type}"`);
}

export function evalSend(method: string, p: string, body: unknown): unknown {
  const s = store();
  let m: RegExpExecArray | null;
  if (method === "POST" && p === "/api/v1/datasets") {
    const b = body as CreateDatasetRequest;
    const name = typeof b?.name === "string" ? b.name.trim() : "";
    if (!name) bad("name is required");
    if (s.datasets.some((d) => d.name === name)) throw new ApiError(409, JSON.stringify({ detail: `dataset "${name}" already exists` }));
    const d = { id: newId("ds"), name, description: b.description?.trim() || null, created_at: iso(Date.now()) };
    s.datasets.push(d);
    return datasetView(s, d);
  }
  if (method === "POST" && (m = /^\/api\/v1\/datasets\/([^/]+)\/items(\/from-span)?$/.exec(p))) {
    if (!s.datasets.some((d) => d.id === m![1])) throw new ApiError(404, "dataset not found");
    let item: DatasetItem;
    if (m[2]) {
      const b = body as DatasetItemFromSpanRequest;
      if (!b?.trace_id) bad("trace_id is required");
      item = itemFromSpan(s, m[1], b);
    } else {
      const b = body as CreateDatasetItemRequest;
      if (!isObject(b?.input)) bad("input must be a JSON object");
      if (b.context !== undefined && (!Array.isArray(b.context) || b.context.some((c) => typeof c !== "string")))
        bad("context must be a list of strings");
      item = {
        id: newId("item"),
        dataset_id: m[1],
        input: b.input,
        expected_output: b.expected_output ?? null,
        context: b.context ?? [],
        metadata: b.metadata ?? {},
        source_trace_id: null,
        source_span_id: null,
        created_at: iso(Date.now()),
      };
    }
    s.items.push(item);
    return item;
  }
  if (method === "DELETE" && (m = /^\/api\/v1\/datasets\/([^/]+)\/items\/([^/]+)$/.exec(p))) {
    const idx = s.items.findIndex((i) => i.dataset_id === m![1] && i.id === m![2]);
    if (idx < 0) throw new ApiError(404, "item not found");
    s.items.splice(idx, 1);
    return { ok: true };
  }
  if (method === "POST" && p === "/api/v1/experiments") {
    const b = body as CreateExperimentRequest;
    validateExperiment(s, b);
    const id = newId("exp");
    const items = s.items.filter((i) => i.dataset_id === b.dataset_id);
    const now = Date.now();
    s.experiments.push({
      exp: {
        id,
        name: b.name.trim(),
        dataset_id: b.dataset_id,
        baseline_experiment_id: b.baseline_experiment_id || null,
        variants: b.variants,
        metrics: b.metrics,
        error: null,
        created_at: iso(now),
      },
      all: items.flatMap((it) => b.variants.map((v) => simulate(s, it, v, b.metrics, {}))),
      startAt: now + 3000,
      msPerCell: 600,
    });
    return { id, status: "queued" };
  }
  return undefined;
}

/** For tests and debugging: drop all mutations and reseed. */
export function resetMockEvals() {
  (globalThis as unknown as { __matrixEvalStore?: Store }).__matrixEvalStore = undefined;
}
