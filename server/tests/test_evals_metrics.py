import pytest

from matrix_server.evals.metrics import (
    METRIC_TYPES,
    EvalCase,
    EvalDeps,
    Metric,
    MetricResult,
    build_metric,
    case_question,
    evaluate_case,
    levenshtein_distance,
    register_metric,
    rouge_l_f1,
)
from matrix_server.evals.providers import HashingEmbedder

OFFLINE = EvalDeps.offline()


class FixedJudge:
    """Hand-crafted judge for exact math."""

    def __init__(self, statements=(), verdicts=(), relevance=(), questions=(), score=3.0):
        self.statements = list(statements)
        self.verdicts = list(verdicts)
        self.relevance = list(relevance)
        self.questions = list(questions)
        self.score = score
        self.calls = []

    def extract_statements(self, question, answer):
        return list(self.statements)

    def verify(self, statement, contexts):
        return self.verdicts.pop(0), f"reason for {statement}"

    def generate_questions(self, answer, n):
        return self.questions[:n]

    def chunk_relevant(self, question, chunk, reference=None):
        self.calls.append((question, chunk, reference))
        return self.relevance.pop(0)

    def rate(self, criteria, input, output, expected=None):
        self.calls.append((criteria, input, output, expected))
        return self.score, "because"


def case(**kw):
    base = {"input": {"question": "What is the capital of France?"}, "output": "Paris is the capital of France."}
    base.update(kw)
    return EvalCase(**base)


def test_registry_has_all_spec_metrics():
    assert set(METRIC_TYPES) >= {
        "faithfulness", "answer_relevancy", "context_precision", "g_eval", "equals", "contains", "regex",
        "is_json", "levenshtein", "rouge_l", "cost", "latency",
    }


def test_case_question():
    assert case_question(EvalCase({"query": "q1", "question": "q0"}, "")) == "q0"
    assert case_question(EvalCase({"query": "q1"}, "")) == "q1"
    assert case_question(EvalCase({"input": "q2", "x": 1}, "")) == "q2"
    assert case_question(EvalCase({"n": 1, "topic": "t"}, "")) == "t"
    assert case_question(EvalCase({"n": 1}, "")) is None


# ---------------------------------------------------------------- faithfulness


def test_faithfulness_exact():
    j = FixedJudge(statements=["a", "b", "c"], verdicts=[True, False, True])
    r = build_metric({"type": "faithfulness"}).evaluate(case(contexts=["ctx"]), EvalDeps(judge=j))
    assert r.value == pytest.approx(2 / 3) and r.passed is False  # threshold 0.7
    assert [s["supported"] for s in r.details["statements"]] == [True, False, True]
    assert r.details["statements"][1] == {"statement": "b", "supported": False, "reason": "reason for b"}


def test_faithfulness_no_statements_or_contexts():
    m = build_metric({"type": "faithfulness"})
    r = m.evaluate(case(contexts=["c"]), EvalDeps(judge=FixedJudge()))
    assert r.value is None and r.passed is None and "no statements" in r.reason
    assert m.evaluate(case(contexts=[]), EvalDeps(judge=FixedJudge())).value is None
    assert m.evaluate(case(contexts=["c"]), EvalDeps()).reason == "judge not configured"


def test_faithfulness_heuristic():
    c = case(
        output="Paris is the capital of France. Berlin hosts the Eiffel tower.",
        contexts=["Paris is the capital and largest city of France."],
    )
    r = build_metric({"type": "faithfulness"}).evaluate(c, OFFLINE)
    assert r.value == 0.5
    assert [s["supported"] for s in r.details["statements"]] == [True, False]


# ---------------------------------------------------------------- answer relevancy


def test_answer_relevancy_identical_questions():
    j = FixedJudge(questions=["What is the capital of France?"] * 3)
    r = build_metric({"type": "answer_relevancy"}).evaluate(case(), EvalDeps(judge=j, embedder=HashingEmbedder()))
    assert r.value == pytest.approx(1.0) and r.passed is True
    assert len(r.details["questions"]) == 3


def test_answer_relevancy_heuristic_ranks():
    m = build_metric({"type": "answer_relevancy"})
    good = m.evaluate(case(output="The capital of France is Paris."), OFFLINE).value
    bad = m.evaluate(case(output="Bananas are yellow fruits rich in potassium."), OFFLINE).value
    assert 0 <= bad < good <= 1
    assert m.evaluate(case(output="Bananas are yellow."), EvalDeps(judge=FixedJudge())).value is None
    assert m.evaluate(case(output=""), OFFLINE).value == 0.0


# ---------------------------------------------------------------- context precision


def test_context_precision_exact():
    j = FixedJudge(relevance=[True, False, True])
    c = case(contexts=["a", "b", "c"], expected="Paris")
    r = build_metric({"type": "context_precision"}).evaluate(c, EvalDeps(judge=j))
    assert r.value == pytest.approx((1 / 1 + 2 / 3) / 2)
    assert r.details["relevance"] == [1, 0, 1]
    assert j.calls[0] == ("What is the capital of France?", "a", "Paris")


def test_context_precision_edge_cases():
    m = build_metric({"type": "context_precision"})
    assert m.evaluate(case(contexts=[]), OFFLINE).value is None
    r = m.evaluate(case(contexts=["x", "y"]), EvalDeps(judge=FixedJudge(relevance=[False, False])))
    assert r.value == 0.0 and r.passed is False
    r = m.evaluate(case(contexts=["noise", "France capital Paris"]), OFFLINE)
    assert r.value == pytest.approx(0.5)


# ---------------------------------------------------------------- g_eval


def test_g_eval_normalization():
    j = FixedJudge(score=4.0)
    m = build_metric({"type": "g_eval", "params": {"criteria": "Is it correct?"}})
    r = m.evaluate(case(expected="Paris"), EvalDeps(judge=j))
    assert r.value == pytest.approx(0.75) and r.passed is True and r.details["score"] == 4.0
    assert j.calls[0] == ("Is it correct?", "What is the capital of France?", "Paris is the capital of France.",
                          "Paris")
    assert m.evaluate(case(), EvalDeps(judge=FixedJudge(score=1.0))).value == 0.0
    r = m.evaluate(case(expected="capital France Paris"), OFFLINE)
    assert r.value == 1.0


def test_g_eval_requires_criteria():
    with pytest.raises(ValueError):
        build_metric({"type": "g_eval"})
    with pytest.raises(ValueError):
        build_metric({"type": "g_eval", "params": {"criteria": "  "}})


# ---------------------------------------------------------------- deterministic


def test_equals_contains():
    eq = build_metric({"type": "equals"})
    assert eq.evaluate(EvalCase({}, " Paris ", expected="Paris"), None).passed is True
    assert eq.evaluate(EvalCase({}, "paris", expected="Paris"), None).passed is False
    assert build_metric({"type": "equals", "params": {"case_sensitive": False}}).evaluate(
        EvalCase({}, "paris", expected="Paris")).passed is True
    assert eq.evaluate(EvalCase({}, "x")).value is None
    ct = build_metric({"type": "contains", "params": {"value": "PARIS", "case_sensitive": False}})
    r = ct.evaluate(EvalCase({}, "It is Paris."))
    assert (r.value, r.passed) == (1.0, True)
    assert build_metric({"type": "contains", "params": {"value": "PARIS"}}).evaluate(
        EvalCase({}, "It is Paris.")).passed is False


def test_regex():
    m = build_metric({"type": "regex", "params": {"pattern": r"\d{3}-\d{4}"}})
    assert m.evaluate(EvalCase({}, "call 555-1234")).passed is True
    assert m.evaluate(EvalCase({}, "no phone")).passed is False
    big = build_metric({"type": "regex", "params": {"pattern": "END"}}).evaluate(EvalCase({}, "a" * 100_000 + "END"))
    assert big.passed is False and big.details == {"truncated": True}
    with pytest.raises(ValueError):
        build_metric({"type": "regex", "params": {"pattern": "("}})
    with pytest.raises(ValueError):
        build_metric({"type": "regex", "params": {"pattern": "a" * 1001}})
    with pytest.raises(ValueError):
        build_metric({"type": "regex"})


def test_is_json():
    m = build_metric({"type": "is_json"})
    assert m.evaluate(EvalCase({}, '{"a": 1}')).passed is True
    assert m.evaluate(EvalCase({}, "{a: 1}")).passed is False
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    s = build_metric({"type": "is_json", "params": {"schema": schema}})
    assert s.evaluate(EvalCase({}, '{"name": "x"}')).passed is True
    r = s.evaluate(EvalCase({}, '{"name": 3}'))
    assert r.passed is False and "name" in r.reason
    assert s.evaluate(EvalCase({}, "{}")).passed is False
    with pytest.raises(ValueError):
        build_metric({"type": "is_json", "params": {"schema": {"type": "nonsense"}}})


def test_levenshtein():
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("", "abc") == 3
    m = build_metric({"type": "levenshtein"})
    r = m.evaluate(EvalCase({}, "kitten", expected="sitting"))
    assert r.value == pytest.approx(1 - 3 / 7) and r.passed is False
    assert m.evaluate(EvalCase({}, "", expected="")).value == 1.0
    assert m.evaluate(EvalCase({}, "Paris!", expected="Paris")).passed is True


def test_rouge_l():
    # LCS("the cat sat on the mat", "the cat is on the mat") = the cat on the mat (5)
    assert rouge_l_f1("the cat sat on the mat", "the cat is on the mat") == pytest.approx(5 / 6)
    assert rouge_l_f1("a b c", "x y z") == 0.0
    assert rouge_l_f1("", "") == 1.0
    m = build_metric({"type": "rouge_l", "threshold": 0.9})
    r = m.evaluate(EvalCase({}, "the cat sat on the mat", expected="the cat is on the mat"))
    assert r.passed is False and m.threshold == 0.9


def test_cost_latency():
    c = build_metric({"type": "cost", "params": {"max_usd": 0.005}})
    assert c.evaluate(EvalCase({}, "", cost_usd=0.001)).passed is True
    r = c.evaluate(EvalCase({}, "", cost_usd=0.01))
    assert r.passed is False and r.details == {"actual": 0.01, "limit": 0.005}
    assert c.evaluate(EvalCase({}, "")).value is None
    lat = build_metric({"type": "latency", "params": {"max_ms": 2000}})
    assert lat.evaluate(EvalCase({}, "", latency_ms=2000)).passed is True
    assert lat.evaluate(EvalCase({}, "", latency_ms=2001)).passed is False
    assert build_metric({"type": "latency", "threshold": 100}).evaluate(EvalCase({}, "", latency_ms=50)).passed
    with pytest.raises(ValueError):
        build_metric({"type": "cost"})
    with pytest.raises(ValueError):
        build_metric({"type": "cost", "params": {"max_usd": "cheap"}})


# ---------------------------------------------------------------- build / registry


@pytest.mark.parametrize(
    "spec",
    [
        "faithfulness",
        {"type": "unknown"},
        {},
        {"type": "faithfulness", "params": []},
        {"type": "faithfulness", "params": {"foo": 1}},
        {"type": "faithfulness", "threshold": "high"},
        {"type": "faithfulness", "threshold": 1.5},
        {"type": "faithfulness", "threshold": True},
        {"type": "faithfulness", "name": ""},
        {"type": "faithfulness", "bogus": 1},
        {"type": "answer_relevancy", "params": {"n": 0}},
        {"type": "contains", "params": {"value": 3}},
        {"type": "equals", "params": {"case_sensitive": "no"}},
    ],
)
def test_build_metric_rejects(spec):
    with pytest.raises(ValueError):
        build_metric(spec)


def test_build_metric_defaults_and_names():
    m = build_metric({"type": "faithfulness", "name": "faith-strict", "threshold": 0.9})
    assert (m.name, m.threshold) == ("faith-strict", 0.9)
    assert build_metric({"type": "answer_relevancy"}).threshold == 0.7
    assert build_metric({"type": "context_precision"}).threshold == 0.5
    assert build_metric({"type": "g_eval", "params": {"criteria": "x"}}).threshold == 0.6
    assert build_metric({"type": "levenshtein"}).threshold == 0.8
    assert build_metric({"type": "rouge_l"}).threshold == 0.5
    assert build_metric({"type": "equals"}).to_spec()["type"] == "equals"


def test_register_metric_and_evaluate_case():
    @register_metric(type_name="always_half")
    class Half(Metric):
        default_threshold = 0.4

        def evaluate(self, case, deps=None):
            return self._score(0.5, "half")

    class Boom(Metric):
        type = "boom"

        def evaluate(self, case, deps=None):
            raise RuntimeError("judge exploded")

    register_metric(Boom)
    try:
        metrics = [build_metric({"type": "always_half"}), build_metric({"type": "boom", "name": "b"})]
        out = evaluate_case(metrics, EvalCase({}, "x"))
        assert out["always_half"] == MetricResult(0.5, True, "half", {})
        assert out["b"].passed is False and "judge exploded" in out["b"].reason
        assert out["b"].to_dict()["value"] is None
    finally:
        METRIC_TYPES.pop("always_half", None)
        METRIC_TYPES.pop("boom", None)
