import type {
  LineageNodeType,
  ModelStage,
  ModelType,
  SandboxStepKind,
  TrainingRunStatus,
  TrainingRunType,
} from "./types";

export const VARIANT_COLORS = [
  "#38bdf8",
  "#a78bfa",
  "#fb923c",
  "#f472b6",
  "#22d3ee",
  "#facc15",
] as const;

export const PRIMARY_BTN_CLASS =
  "border border-emerald-700/70 bg-emerald-900/40 text-emerald-200 hover:bg-emerald-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400";

export type RunStatusStyle = {
  label: string;
  badge: string;
  dot: string;
};

export const RUN_STATUS_STYLE: Record<TrainingRunStatus, RunStatusStyle> = {
  running: {
    label: "Running",
    badge: "border-sky-800/70 bg-sky-950/40 text-sky-300",
    dot: "bg-sky-400 animate-pulse motion-reduce:animate-none",
  },
  completed: {
    label: "Completed",
    badge: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
    dot: "bg-emerald-400",
  },
  failed: {
    label: "Failed",
    badge: "border-red-800/70 bg-red-950/50 text-red-300",
    dot: "bg-red-400",
  },
  stopped_early: {
    label: "Stopped Early",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
  },
  killed: {
    label: "Killed",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
  },
  cancelled: {
    label: "Cancelled",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
  },
  stopped: {
    label: "Stopped",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
  },
  queued: {
    label: "Queued",
    badge: "border-zinc-700/80 bg-zinc-900 text-zinc-300",
    dot: "bg-zinc-400",
  },
};

export function getRunStatusStyle(status: string | null | undefined): RunStatusStyle {
  if (status && status in RUN_STATUS_STYLE) {
    return RUN_STATUS_STYLE[status as TrainingRunStatus];
  }
  return {
    label: status || "Unknown",
    badge: "border-zinc-700/80 bg-zinc-900 text-zinc-400",
    dot: "bg-zinc-400",
  };
}

export type ModelStageStyle = {
  label: string;
  badge: string;
  dot: string;
};

export const MODEL_STAGE_STYLE: Record<ModelStage, ModelStageStyle> = {
  production: {
    label: "Production",
    badge: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
    dot: "bg-emerald-400",
  },
  staging: {
    label: "Staging",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
  },
  archived: {
    label: "Archived",
    badge: "border-zinc-700/80 bg-zinc-900 text-zinc-400",
    dot: "bg-zinc-400",
  },
  none: {
    label: "None",
    badge: "border-zinc-700/80 bg-zinc-900/60 text-zinc-300",
    dot: "bg-zinc-400",
  },
};

export function getModelStageStyle(stage: string | null | undefined): ModelStageStyle {
  if (stage && stage in MODEL_STAGE_STYLE) {
    return MODEL_STAGE_STYLE[stage as ModelStage];
  }
  return MODEL_STAGE_STYLE.none;
}

export const ALIAS_STYLE: Record<string, string> = {
  champion: "rounded-full border border-emerald-700/70 bg-emerald-950/60 text-emerald-300",
  "@champion": "rounded-full border border-emerald-700/70 bg-emerald-950/60 text-emerald-300",
  challenger: "rounded-full border border-violet-700/70 bg-violet-950/60 text-violet-300",
  "@challenger": "rounded-full border border-violet-700/70 bg-violet-950/60 text-violet-300",
  default: "rounded-full border border-sky-800/70 bg-sky-950/50 text-sky-300",
};

export function getAliasStyle(alias: string): string {
  const normalized = alias.trim().toLowerCase().replace(/^@/, "");
  return ALIAS_STYLE[normalized] ?? ALIAS_STYLE.default;
}

export type DagNodeStyle = {
  label: string;
  accent: "cyan" | "sky" | "emerald" | "violet";
  border: string;
  bg: string;
  text: string;
  dot: string;
};

export const DAG_NODE_STYLE: Record<LineageNodeType, DagNodeStyle> = {
  dataset: {
    label: "Dataset",
    accent: "cyan",
    border: "border-cyan-800/70",
    bg: "bg-cyan-950/40",
    text: "text-cyan-300",
    dot: "bg-cyan-400",
  },
  run: {
    label: "Training Run",
    accent: "sky",
    border: "border-sky-800/70",
    bg: "bg-sky-950/40",
    text: "text-sky-300",
    dot: "bg-sky-400",
  },
  artifact: {
    label: "Artifact",
    accent: "emerald",
    border: "border-emerald-800/60",
    bg: "bg-emerald-950/40",
    text: "text-emerald-300",
    dot: "bg-emerald-400",
  },
  model_version: {
    label: "Model Version",
    accent: "emerald",
    border: "border-emerald-800/60",
    bg: "bg-emerald-950/40",
    text: "text-emerald-300",
    dot: "bg-emerald-400",
  },
  experiment: {
    label: "Experiment",
    accent: "violet",
    border: "border-violet-800/70",
    bg: "bg-violet-950/40",
    text: "text-violet-300",
    dot: "bg-violet-400",
  },
  safety_scan: {
    label: "Safety Scan",
    accent: "violet",
    border: "border-violet-800/70",
    bg: "bg-violet-950/40",
    text: "text-violet-300",
    dot: "bg-violet-400",
  },
};

export function getDagNodeStyle(nodeType: string | null | undefined): DagNodeStyle {
  if (nodeType && nodeType in DAG_NODE_STYLE) {
    return DAG_NODE_STYLE[nodeType as LineageNodeType];
  }
  return {
    label: nodeType || "Node",
    accent: "sky",
    border: "border-zinc-700/80",
    bg: "bg-zinc-900/60",
    text: "text-zinc-300",
    dot: "bg-zinc-400",
  };
}

export type TaskTypeStyle = {
  label: string;
  badge: string;
};

export const TASK_TYPE_STYLE: Record<
  Exclude<TrainingRunType | ModelType, string & {}> | string,
  TaskTypeStyle
> = {
  sft: { label: "SFT", badge: "border-sky-800/70 bg-sky-950/40 text-sky-300" },
  lora: { label: "LoRA", badge: "border-violet-800/70 bg-violet-950/40 text-violet-300" },
  qlora: { label: "QLoRA", badge: "border-fuchsia-800/70 bg-fuchsia-950/40 text-fuchsia-300" },
  dpo: { label: "DPO", badge: "border-cyan-800/70 bg-cyan-950/40 text-cyan-300" },
  grpo: { label: "GRPO", badge: "border-indigo-800/70 bg-indigo-950/40 text-indigo-300" },
  ppo: { label: "PPO", badge: "border-teal-800/70 bg-teal-950/40 text-teal-300" },
  classic_ml: { label: "Classic ML", badge: "border-amber-800/70 bg-amber-950/40 text-amber-300" },
  llm: { label: "LLM", badge: "border-sky-800/70 bg-sky-950/40 text-sky-300" },
  classifier: { label: "Classifier", badge: "border-amber-800/70 bg-amber-950/40 text-amber-300" },
  regressor: { label: "Regressor", badge: "border-orange-800/70 bg-orange-950/40 text-orange-300" },
  embedding: { label: "Embedding", badge: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300" },
};

export function getTaskTypeStyle(taskType: string | null | undefined): TaskTypeStyle {
  if (!taskType) {
    return { label: "Unknown", badge: "border-zinc-700/80 bg-zinc-900 text-zinc-400" };
  }
  const key = taskType.toLowerCase();
  return (
    TASK_TYPE_STYLE[key] ?? {
      label: taskType.toUpperCase(),
      badge: "border-zinc-700/80 bg-zinc-900 text-zinc-300",
    }
  );
}

// ── SOTA Sandbox Step Styles ──────────────────────────────────────────────

export type SandboxStepStyle = {
  label: string;
  badge: string;
};

export const SANDBOX_STEP_STYLE: Record<SandboxStepKind, SandboxStepStyle> = {
  bash: {
    label: "Bash",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
  },
  python: {
    label: "Python",
    badge: "border-sky-800/70 bg-sky-950/40 text-sky-300",
  },
  file_edit: {
    label: "File Edit",
    badge: "border-cyan-800/70 bg-cyan-950/40 text-cyan-300",
  },
  tool_call: {
    label: "Tool Call",
    badge: "border-orange-800/70 bg-orange-950/40 text-orange-300",
  },
  scorer: {
    label: "Scorer",
    badge: "border-violet-800/70 bg-violet-950/40 text-violet-300",
  },
};

export function getSandboxStepStyle(kind: string | null | undefined): SandboxStepStyle {
  if (kind && kind in SANDBOX_STEP_STYLE) {
    return SANDBOX_STEP_STYLE[kind as SandboxStepKind];
  }
  return {
    label: kind || "Step",
    badge: "border-zinc-700/80 bg-zinc-900 text-zinc-300",
  };
}

// ── SOTA Branch Diff Styles ───────────────────────────────────────────────

export type BranchDiffChangeType = "added" | "removed" | "modified";

export type BranchDiffStyle = {
  label: string;
  prefix: string;
  badge: string;
  rowClass: string;
};

export const BRANCH_DIFF_STYLE: Record<BranchDiffChangeType, BranchDiffStyle> = {
  added: {
    label: "Added",
    prefix: "+",
    badge: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300",
    rowClass: "bg-emerald-950/30 text-emerald-200",
  },
  removed: {
    label: "Removed",
    prefix: "-",
    badge: "border-red-800/70 bg-red-950/50 text-red-300",
    rowClass: "bg-red-950/30 text-red-200",
  },
  modified: {
    label: "Modified",
    prefix: "~",
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    rowClass: "bg-amber-950/30 text-amber-200",
  },
};

export function getBranchDiffStyle(changeType: string | null | undefined): BranchDiffStyle {
  if (changeType && changeType in BRANCH_DIFF_STYLE) {
    return BRANCH_DIFF_STYLE[changeType as BranchDiffChangeType];
  }
  return {
    label: changeType || "Unknown",
    prefix: "",
    badge: "border-zinc-700/80 bg-zinc-900 text-zinc-300",
    rowClass: "bg-zinc-900/30 text-zinc-300",
  };
}

export function scoreTone(v: number): string {
  if (v < 0.5) return "text-red-300";
  if (v < 0.8) return "text-amber-300";
  return "text-emerald-300";
}


