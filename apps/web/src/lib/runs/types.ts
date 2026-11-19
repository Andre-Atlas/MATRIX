// Shapes of the training-run API (contract G1; server/src/matrix_server/api/runs.py is the source
// of truth where they differ).

export const RUN_STATUSES = ["running", "finished", "failed", "killed"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];
export const isRunStatus = (s: unknown): s is RunStatus => RUN_STATUSES.includes(s as RunStatus);

export const RUN_SORTS = ["-started_at", "started_at", "-name", "name"] as const;
export type RunSort = (typeof RUN_SORTS)[number];

export type RunGit = { commit: string | null; branch: string | null; dirty: boolean | null; remote: string | null };
export type RunEnv = { python: string | null; platform: string | null; hostname: string | null; packages: string[] };

export type Run = {
  id: string;
  name: string;
  status: RunStatus;
  group: string | null;
  tags: string[];
  params: Record<string, unknown>;
  git: RunGit | null;
  env: RunEnv | null;
  summary: Record<string, number | null>;
  dataset_version_id: string | null;
  sweep_id: string | null;
  parent_run_id: string | null;
  created_by: string | null;
  started_at: string;
  ended_at: string | null;
  heartbeat_at: string | null;
  error: string | null;
};

export type RunPage = { items: Run[]; next_cursor: string | null };

export type MetricKeyStat = {
  key: string;
  count: number;
  first_step: number;
  last_step: number;
  last_value: number | null;
  min: number | null;
  max: number | null;
};

/** One series as the API returns it: non-finite values come back as `null`. */
export type SeriesData = { steps: number[]; values: (number | null)[]; ts: string[] };
export type SeriesResponse = { series: Record<string, SeriesData>; downsampled: boolean };
export type CompareResponse = { runs: Record<string, { name: string; series: Record<string, SeriesData> }> };

export type XAxisMode = "step" | "time";

export type ArtifactInfo = {
  id: string;
  name: string;
  media_type: string;
  size: number;
  sha256: string;
  verified: boolean;
  owner_kind: string | null;
  owner_id: string | null;
  created_at: string;
  created_by: string | null;
};
export type RunArtifactKind = "file" | "model" | "plot" | "table" | "report";
export type RunArtifact = { artifact: ArtifactInfo; name: string; kind: RunArtifactKind };

export type ClassificationReport = {
  labels: string[];
  metrics: Record<string, number>;
  per_class: { label: string; precision: number; recall: number; f1: number; support: number }[];
  confusion_matrix: number[][];
  roc: { label: string; fpr: number[]; tpr: number[]; auc: number }[];
  pr: { label: string; precision: number[]; recall: number[]; ap: number }[];
};
export type RegressionReport = {
  metrics: { rmse: number; mae: number; r2: number; mape?: number };
  residual_hist: { edges: number[]; counts: number[] };
  sample: { y_true: number[]; y_pred: number[] };
};
export type ImportanceReport = {
  method: "shap" | "permutation" | "model";
  features: { name: string; importance: number; std?: number }[];
};
export type TableReport = { columns: string[]; rows: unknown[][]; truncated: boolean };

export type ReportKind = "classification" | "regression" | "importance" | "table" | "custom";
type ReportBase<K extends ReportKind, D> = { id: string; run_id: string; kind: K; name: string; data: D; created_at: string };
export type Report =
  | ReportBase<"classification", ClassificationReport>
  | ReportBase<"regression", RegressionReport>
  | ReportBase<"importance", ImportanceReport>
  | ReportBase<"table", TableReport>
  | ReportBase<"custom", unknown>;

/** The API caps: never ask for or draw more than this many points per series. */
export const MAX_POINTS_PER_SERIES = 5000;
export const MAX_COMPARE_RUNS = 20;
export const MAX_COMPARE_KEYS = 20;
export const MAX_SERIES_KEYS = 100;
