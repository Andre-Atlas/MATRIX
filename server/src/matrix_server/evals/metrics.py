"""Evaluation metrics (Ragas/DeepEval formulas + Promptfoo-style assertions).

Build from a spec with `build_metric({"type", "name"?, "params"?, "threshold"?})` and call
`metric.evaluate(case, deps)`. Scored metrics produce a value in 0..1 and pass when
value >= threshold; boolean assertions produce 1.0/0.0. A value of None means "not
applicable" (e.g. no contexts) and `passed` is then None.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

import jsonschema

from matrix_server.evals.judge import Judge
from matrix_server.evals.providers import Embedder

__all__ = [
    "METRIC_TYPES",
    "EvalCase",
    "EvalDeps",
    "Metric",
    "MetricResult",
    "build_metric",
    "case_question",
    "context_precision_score",
    "evaluate_case",
    "levenshtein_distance",
    "register_metric",
    "rouge_l_f1",
]

MAX_REGEX_INPUT = 100_000
MAX_REGEX_PATTERN = 1_000
MAX_EDIT_CHARS = 5_000
MAX_LCS_TOKENS = 5_000


@dataclass
class MetricResult:
    value: float | None
    passed: bool | None
    reason: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"value": self.value, "passed": self.passed, "reason": self.reason, "details": self.details}


@dataclass
class EvalCase:
    input: dict
    output: str
    expected: str | None = None
    contexts: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    cost_usd: float | None = None


@dataclass
class EvalDeps:
    judge: Judge | None = None
    embedder: Embedder | None = None

    @classmethod
    def offline(cls) -> EvalDeps:
        """Heuristic judge + hashing embedder: deterministic, for tests/demo only."""
        from matrix_server.evals.judge import HeuristicJudge
        from matrix_server.evals.providers import HashingEmbedder

        return cls(judge=HeuristicJudge(), embedder=HashingEmbedder())


def case_question(case: EvalCase) -> str | None:
    """Question for RAG metrics: input.question | query | input, else the first string value."""
    inp = case.input
    if isinstance(inp, str):
        return inp
    if not isinstance(inp, dict):
        return None
    for k in ("question", "query", "input"):
        if isinstance(inp.get(k), str):
            return inp[k]
    for v in inp.values():
        if isinstance(v, str):
            return v
    return None


def _input_text(case: EvalCase) -> str:
    q = case_question(case)
    if q is not None:
        return q
    try:
        return json.dumps(case.input, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(case.input)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# --------------------------------------------------------------------------- base


class Metric:
    """Base metric. Subclasses set `type`, `ALLOWED_PARAMS`, optionally `default_threshold`."""

    type: ClassVar[str] = ""
    default_threshold: ClassVar[float | None] = None
    ALLOWED_PARAMS: ClassVar[frozenset[str]] = frozenset()
    boolean: ClassVar[bool] = False

    def __init__(self, name: str | None = None, params: dict | None = None,
                 threshold: float | None = None) -> None:
        params = dict(params or {})
        unknown = set(params) - set(self.ALLOWED_PARAMS)
        if unknown:
            raise ValueError(f"{self.type}: unknown params {sorted(unknown)}")
        if threshold is not None and not _is_number(threshold):
            raise ValueError(f"{self.type}: threshold must be a number")
        self.name = name or self.type
        self.params = params
        self.threshold = float(threshold) if threshold is not None else self.default_threshold
        self.validate()

    def validate(self) -> None:  # override to check params
        if not self.boolean and self.threshold is not None and not 0 <= self.threshold <= 1:
            raise ValueError(f"{self.type}: threshold must be between 0 and 1")

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        raise NotImplementedError

    def to_spec(self) -> dict:
        return {"type": self.type, "name": self.name, "params": self.params, "threshold": self.threshold}

    # helpers
    def _score(self, value: float | None, reason: str, details: dict | None = None) -> MetricResult:
        if value is None:
            return MetricResult(None, None, reason, details or {})
        value = float(value)
        passed = None if self.threshold is None else value >= self.threshold
        return MetricResult(value, passed, reason, details or {})

    @staticmethod
    def _bool(ok: bool, reason: str, details: dict | None = None) -> MetricResult:
        return MetricResult(1.0 if ok else 0.0, bool(ok), reason, details or {})

    @staticmethod
    def _na(reason: str, details: dict | None = None) -> MetricResult:
        return MetricResult(None, None, reason, details or {})

    def _str_param(self, key: str, required: bool = False, max_len: int | None = None) -> None:
        v = self.params.get(key)
        if v is None:
            if required:
                raise ValueError(f"{self.type}: param {key!r} is required")
            return
        if not isinstance(v, str) or (required and not v.strip()):
            raise ValueError(f"{self.type}: param {key!r} must be a non-empty string")
        if max_len is not None and len(v) > max_len:
            raise ValueError(f"{self.type}: param {key!r} longer than {max_len} chars")

    def _bool_param(self, key: str) -> None:
        if key in self.params and not isinstance(self.params[key], bool):
            raise ValueError(f"{self.type}: param {key!r} must be a boolean")


METRIC_TYPES: dict[str, type[Metric]] = {}


def register_metric(cls: type[Metric] | None = None, *, type_name: str | None = None):
    """Register a Metric subclass (usable as a decorator, with or without `type_name`)."""

    def _do(c: type[Metric]) -> type[Metric]:
        key = type_name or c.type
        if not key:
            raise ValueError("metric class needs a `type`")
        if type_name and c.type != type_name:
            c = type(c.__name__, (c,), {"type": type_name})
        METRIC_TYPES[key] = c
        return c

    return _do(cls) if cls is not None else _do


def build_metric(spec: dict) -> Metric:
    if not isinstance(spec, dict):
        raise ValueError("metric spec must be an object")  # noqa: TRY004
    mtype = spec.get("type")
    if not isinstance(mtype, str) or mtype not in METRIC_TYPES:
        raise ValueError(f"unknown metric type {mtype!r}; available: {sorted(METRIC_TYPES)}")
    extra = set(spec) - {"type", "name", "params", "threshold"}
    if extra:
        raise ValueError(f"unknown metric spec keys {sorted(extra)}")
    name = spec.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError("metric name must be a non-empty string")
    params = spec.get("params")
    if params is not None and not isinstance(params, dict):
        raise ValueError("metric params must be an object")
    return METRIC_TYPES[mtype](name=name, params=params, threshold=spec.get("threshold"))


def evaluate_case(metrics: list[Metric], case: EvalCase, deps: EvalDeps | None = None) -> dict[str, MetricResult]:
    """Run all metrics; an exception becomes a failed result (passed=False) instead of aborting."""
    out: dict[str, MetricResult] = {}
    for m in metrics:
        try:
            out[m.name] = m.evaluate(case, deps)
        except Exception as exc:  # noqa: BLE001 - one bad metric must not sink the run
            msg = str(exc)[:200]
            out[m.name] = MetricResult(None, False, f"error: {type(exc).__name__}: {msg}", {"error": True})
    return out


# --------------------------------------------------------------------------- LLM-judged metrics


def _need_judge(deps: EvalDeps | None) -> Judge | None:
    return deps.judge if deps is not None else None


@register_metric
class Faithfulness(Metric):
    """|statements supported by the contexts| / |statements extracted from the output|."""

    type = "faithfulness"
    default_threshold = 0.7

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        judge = _need_judge(deps)
        if judge is None:
            return self._na("judge not configured")
        if not case.contexts:
            return self._na("no contexts to verify against")
        statements = judge.extract_statements(case_question(case) or "", case.output or "")
        if not statements:
            return self._na("no statements extracted from the output", {"statements": []})
        rows = []
        for st in statements:
            ok, why = judge.verify(st, case.contexts)
            rows.append({"statement": st, "supported": bool(ok), "reason": why})
        supported = sum(r["supported"] for r in rows)
        value = supported / len(rows)
        unsupported = len(rows) - supported
        return self._score(value, f"{supported}/{len(rows)} statements supported by context"
                           + (f"; {unsupported} unsupported" if unsupported else ""), {"statements": rows})


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb)


@register_metric
class AnswerRelevancy(Metric):
    """Mean cosine between the original question and N questions generated from the answer."""

    type = "answer_relevancy"
    default_threshold = 0.7
    ALLOWED_PARAMS = frozenset({"n"})

    def validate(self) -> None:
        super().validate()
        n = self.params.get("n", 3)
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 10:
            raise ValueError("answer_relevancy: param 'n' must be an integer between 1 and 10")

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        judge = _need_judge(deps)
        if judge is None or deps is None or deps.embedder is None:
            return self._na("judge/embedder not configured")
        question = case_question(case)
        if not question:
            return self._na("no question in input")
        n = self.params.get("n", 3)
        generated = judge.generate_questions(case.output or "", n)
        if not generated:
            return self._score(0.0, "no questions could be generated from the output", {"questions": []})
        vecs = deps.embedder.embed([question, *generated])
        sims = [_cosine(vecs[0], v) for v in vecs[1:]]
        value = max(sum(sims) / len(sims), 0.0)
        return self._score(value, f"mean cosine {value:.3f} over {len(sims)} generated questions",
                           {"questions": [{"question": q, "similarity": s} for q, s in zip(generated, sims,
                                                                                            strict=False)]})


@register_metric
class ContextPrecision(Metric):
    """Ragas: Σ_k (precision@k · v_k) / total relevant chunks."""

    type = "context_precision"
    default_threshold = 0.5

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        judge = _need_judge(deps)
        if judge is None:
            return self._na("judge not configured")
        if not case.contexts:
            return self._na("no contexts retrieved")
        question = case_question(case) or ""
        verdicts = [bool(judge.chunk_relevant(question, c, case.expected)) for c in case.contexts]
        value = context_precision_score(verdicts)
        rel = sum(verdicts)
        return self._score(value, f"{rel}/{len(verdicts)} chunks relevant",
                           {"relevance": [int(v) for v in verdicts]})


def context_precision_score(verdicts: list[bool]) -> float:
    total, hits = 0.0, 0
    for k, v in enumerate(verdicts, start=1):
        if v:
            hits += 1
            total += hits / k
    return total / hits if hits else 0.0


@register_metric
class GEval(Metric):
    """Rubric score 1..5 (logprob-weighted when available) normalized to (s-1)/4."""

    type = "g_eval"
    default_threshold = 0.6
    ALLOWED_PARAMS = frozenset({"criteria"})

    def validate(self) -> None:
        super().validate()
        self._str_param("criteria", required=True)

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        judge = _need_judge(deps)
        if judge is None:
            return self._na("judge not configured")
        score, reason = judge.rate(self.params["criteria"], _input_text(case), case.output or "", case.expected)
        value = min(max((float(score) - 1) / 4, 0.0), 1.0)
        return self._score(value, reason or f"score {score:.2f}/5", {"score": float(score)})


# --------------------------------------------------------------------------- deterministic assertions


class _TextMatch(Metric):
    ALLOWED_PARAMS = frozenset({"value", "case_sensitive"})
    boolean = True

    def validate(self) -> None:
        if "value" in self.params and not isinstance(self.params["value"], str):
            raise ValueError(f"{self.type}: param 'value' must be a string")
        self._bool_param("case_sensitive")

    def _target(self, case: EvalCase) -> str | None:
        v = self.params.get("value")
        return v if v is not None else case.expected

    def _norm(self, s: str) -> str:
        return s if self.params.get("case_sensitive", True) else s.casefold()


@register_metric
class Equals(_TextMatch):
    type = "equals"

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        target = self._target(case)
        if target is None:
            return self._na("no expected value")
        ok = self._norm((case.output or "").strip()) == self._norm(target.strip())
        return self._bool(ok, "output equals expected" if ok else "output differs from expected")


@register_metric
class Contains(_TextMatch):
    type = "contains"

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        target = self._target(case)
        if target is None:
            return self._na("no value to look for")
        ok = self._norm(target) in self._norm(case.output or "")
        return self._bool(ok, f"output {'contains' if ok else 'does not contain'} {target[:80]!r}")


@register_metric
class Regex(Metric):
    type = "regex"
    ALLOWED_PARAMS = frozenset({"pattern"})
    boolean = True

    def validate(self) -> None:
        self._str_param("pattern", required=True, max_len=MAX_REGEX_PATTERN)
        try:
            self._rx = re.compile(self.params["pattern"])
        except re.error as exc:
            raise ValueError(f"regex: invalid pattern: {exc}") from None

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        text = case.output or ""
        truncated = len(text) > MAX_REGEX_INPUT
        ok = self._rx.search(text[:MAX_REGEX_INPUT]) is not None
        return self._bool(ok, f"pattern {'matched' if ok else 'not found'}",
                          {"truncated": True} if truncated else None)


@register_metric
class IsJson(Metric):
    type = "is_json"
    ALLOWED_PARAMS = frozenset({"schema"})
    boolean = True

    def validate(self) -> None:
        schema = self.params.get("schema")
        if schema is None:
            return
        if not isinstance(schema, (dict, bool)):
            raise ValueError("is_json: param 'schema' must be a JSON Schema object")  # noqa: TRY004
        try:
            jsonschema.validators.validator_for(schema).check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"is_json: invalid schema: {exc.message}") from None

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        try:
            data = json.loads(case.output or "")
        except ValueError as exc:
            return self._bool(False, f"invalid JSON: {str(exc)[:160]}")
        schema = self.params.get("schema")
        if schema is None:
            return self._bool(True, "valid JSON")
        validator = jsonschema.validators.validator_for(schema)(schema)
        err = jsonschema.exceptions.best_match(validator.iter_errors(data))
        if err is not None:
            path = "/".join(str(p) for p in err.absolute_path)
            return self._bool(False, f"schema violation at '{path}': {err.message[:160]}")
        return self._bool(True, "valid JSON matching schema")


def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@register_metric
class Levenshtein(Metric):
    """1 - distance / max(len) against the expected output (or param `value`)."""

    type = "levenshtein"
    default_threshold = 0.8
    ALLOWED_PARAMS = frozenset({"value"})

    def validate(self) -> None:
        super().validate()
        self._str_param("value")

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        target = self.params.get("value", case.expected)
        if target is None:
            return self._na("no expected value")
        a, b = (case.output or "")[:MAX_EDIT_CHARS], target[:MAX_EDIT_CHARS]
        longest = max(len(a), len(b))
        dist = levenshtein_distance(a, b)
        value = 1.0 if longest == 0 else 1 - dist / longest
        return self._score(value, f"edit distance {dist}", {"distance": dist})


_TOK_RX = re.compile(r"\w+", re.UNICODE)


def rouge_l_f1(candidate: str, reference: str) -> float:
    c = _TOK_RX.findall((candidate or "").lower())[:MAX_LCS_TOKENS]
    r = _TOK_RX.findall((reference or "").lower())[:MAX_LCS_TOKENS]
    if not c and not r:
        return 1.0
    if not c or not r:
        return 0.0
    prev = [0] * (len(r) + 1)
    for tc in c:
        cur = [0]
        for j, tr in enumerate(r, start=1):
            cur.append(prev[j - 1] + 1 if tc == tr else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    if lcs == 0:
        return 0.0
    p, rec = lcs / len(c), lcs / len(r)
    return 2 * p * rec / (p + rec)


@register_metric
class RougeL(Metric):
    type = "rouge_l"
    default_threshold = 0.5
    ALLOWED_PARAMS = frozenset({"value"})

    def validate(self) -> None:
        super().validate()
        self._str_param("value")

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        target = self.params.get("value", case.expected)
        if target is None:
            return self._na("no expected value")
        value = rouge_l_f1(case.output or "", target)
        return self._score(value, f"ROUGE-L F1 {value:.3f}")


class _Budget(Metric):
    boolean = True
    param_key: ClassVar[str] = ""
    case_attr: ClassVar[str] = ""
    unit: ClassVar[str] = ""

    def validate(self) -> None:
        limit = self.params.get(self.param_key, self.threshold)
        if not _is_number(limit) or limit < 0:
            raise ValueError(f"{self.type}: param {self.param_key!r} must be a non-negative number")
        self.limit = float(limit)

    def evaluate(self, case: EvalCase, deps: EvalDeps | None = None) -> MetricResult:
        actual = getattr(case, self.case_attr)
        if actual is None:
            return self._na(f"no {self.case_attr} recorded")
        ok = float(actual) <= self.limit
        return self._bool(ok, f"{actual:g} {self.unit} {'<=' if ok else '>'} {self.limit:g} {self.unit}",
                          {"actual": float(actual), "limit": self.limit})


@register_metric
class Cost(_Budget):
    type = "cost"
    ALLOWED_PARAMS = frozenset({"max_usd"})
    param_key = "max_usd"
    case_attr = "cost_usd"
    unit = "USD"


@register_metric
class Latency(_Budget):
    type = "latency"
    ALLOWED_PARAMS = frozenset({"max_ms"})
    param_key = "max_ms"
    case_attr = "latency_ms"
    unit = "ms"
