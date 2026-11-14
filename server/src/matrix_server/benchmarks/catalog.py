"""Catalog of foundational and multimodal benchmark tasks (LM-Eval & VLMEvalKit)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BenchmarkTaskDef:
    name: str
    suite: str  # "lm_eval" | "vlmevalkit"
    modality: str  # "text" | "multimodal"
    eval_modes: list[str]  # e.g. ["generate_until", "loglikelihood"] or ["multimodal"]
    primary_metric: str
    metrics: list[str]
    default_num_fewshot: int = 0
    sample_count: int = 100
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "suite": self.suite,
            "modality": self.modality,
            "eval_modes": list(self.eval_modes),
            "primary_metric": self.primary_metric,
            "metrics": list(self.metrics),
            "default_num_fewshot": self.default_num_fewshot,
            "sample_count": self.sample_count,
            "description": self.description,
        }


# --- LM-Eval Tasks (Foundational Text Benchmarks) ---------------------------

LM_EVAL_TASKS: dict[str, BenchmarkTaskDef] = {
    "mmlu_pro": BenchmarkTaskDef(
        name="mmlu_pro",
        suite="lm_eval",
        modality="text",
        eval_modes=["loglikelihood", "generate_until"],
        primary_metric="acc",
        metrics=["acc", "acc_norm"],
        default_num_fewshot=5,
        sample_count=100,
        description="MMLU-Pro advanced multi-discipline reasoning and knowledge benchmark",
    ),
    "gpqa_diamond": BenchmarkTaskDef(
        name="gpqa_diamond",
        suite="lm_eval",
        modality="text",
        eval_modes=["loglikelihood", "generate_until"],
        primary_metric="acc_norm",
        metrics=["acc_norm", "acc"],
        default_num_fewshot=0,
        sample_count=198,
        description="GPQA Diamond graduate-level science and reasoning subset",
    ),
    "ifeval": BenchmarkTaskDef(
        name="ifeval",
        suite="lm_eval",
        modality="text",
        eval_modes=["generate_until"],
        primary_metric="strict_acc",
        metrics=["strict_acc", "loose_acc"],
        default_num_fewshot=0,
        sample_count=541,
        description="Instruction-Following Evaluation with strictly verifiable constraints",
    ),
    "bbh": BenchmarkTaskDef(
        name="bbh",
        suite="lm_eval",
        modality="text",
        eval_modes=["loglikelihood", "generate_until"],
        primary_metric="acc_norm",
        metrics=["acc_norm", "acc"],
        default_num_fewshot=3,
        sample_count=250,
        description="BIG-Bench Hard algorithmic, logical, and symbolic reasoning tasks",
    ),
    "humaneval": BenchmarkTaskDef(
        name="humaneval",
        suite="lm_eval",
        modality="text",
        eval_modes=["generate_until"],
        primary_metric="pass@1",
        metrics=["pass@1"],
        default_num_fewshot=0,
        sample_count=164,
        description="OpenAI HumanEval Python functional programming code benchmark",
    ),
    "gsm8k": BenchmarkTaskDef(
        name="gsm8k",
        suite="lm_eval",
        modality="text",
        eval_modes=["generate_until"],
        primary_metric="exact_match",
        metrics=["exact_match", "acc"],
        default_num_fewshot=8,
        sample_count=1319,
        description="Grade School Math 8K multi-step mathematical reasoning benchmark",
    ),
}

# --- VLMEvalKit Tasks (Multimodal Benchmarks) ------------------------------

VLMEVALKIT_TASKS: dict[str, BenchmarkTaskDef] = {
    "mmmu_pro": BenchmarkTaskDef(
        name="mmmu_pro",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="acc",
        metrics=["acc"],
        default_num_fewshot=0,
        sample_count=1730,
        description="MMMU-Pro massive multi-discipline multimodal understanding benchmark",
    ),
    "mathvista": BenchmarkTaskDef(
        name="mathvista",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="acc",
        metrics=["acc"],
        default_num_fewshot=0,
        sample_count=1000,
        description="MathVista visual mathematical and diagrammatic reasoning benchmark",
    ),
    "docvqa": BenchmarkTaskDef(
        name="docvqa",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="anls",
        metrics=["anls", "acc"],
        default_num_fewshot=0,
        sample_count=500,
        description="Document Visual Question Answering (DocVQA) across scanned pages",
    ),
    "chartqa": BenchmarkTaskDef(
        name="chartqa",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="relaxed_acc",
        metrics=["relaxed_acc", "acc"],
        default_num_fewshot=0,
        sample_count=500,
        description="ChartQA reasoning over visual charts, plots, and figures",
    ),
    "ocrbench": BenchmarkTaskDef(
        name="ocrbench",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="score",
        metrics=["score", "acc"],
        default_num_fewshot=0,
        sample_count=1000,
        description="OCRBench optical character recognition across diverse complex scenes",
    ),
    "pope": BenchmarkTaskDef(
        name="pope",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="f1",
        metrics=["f1", "acc"],
        default_num_fewshot=0,
        sample_count=500,
        description="POPE polling-based object probing evaluation for hallucination assessment",
    ),
    "vidore": BenchmarkTaskDef(
        name="vidore",
        suite="vlmevalkit",
        modality="multimodal",
        eval_modes=["multimodal"],
        primary_metric="ndcg@5",
        metrics=["ndcg@5", "map"],
        default_num_fewshot=0,
        sample_count=400,
        description="ViDoRe visual document retrieval and embedding benchmark",
    ),
}

TASK_CATALOG: dict[str, BenchmarkTaskDef] = {**LM_EVAL_TASKS, **VLMEVALKIT_TASKS}
VALID_SUITES: set[str] = {"lm_eval", "vlmevalkit"}


def get_task(task_name: str) -> BenchmarkTaskDef | None:
    return TASK_CATALOG.get(task_name)


def list_catalog(suite: str | None = None) -> list[dict[str, Any]]:
    """Return task definitions optionally filtered by suite."""
    tasks = list(TASK_CATALOG.values())
    if suite is not None:
        tasks = [t for t in tasks if t.suite == suite]
    return [t.to_dict() for t in tasks]


def validate_tasks(suite: str, tasks: list[str]) -> list[BenchmarkTaskDef]:
    """Validate suite and tasks; raise ValueError if unknown or mismatched."""
    if suite not in VALID_SUITES:
        raise ValueError(f"Unknown benchmark suite: '{suite}'. Must be one of: {sorted(VALID_SUITES)}")
    if not tasks:
        raise ValueError("tasks list must not be empty")

    defs: list[BenchmarkTaskDef] = []
    for task_name in tasks:
        tdef = get_task(task_name)
        if tdef is None:
            raise ValueError(f"Task '{task_name}' is not registered in the benchmark catalog")
        if tdef.suite != suite:
            raise ValueError(f"Task '{task_name}' belongs to suite '{tdef.suite}', not requested suite '{suite}'")
        defs.append(tdef)
    return defs
