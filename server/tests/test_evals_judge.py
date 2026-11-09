import json
import math

import pytest

from matrix_server.evals.judge import (
    HeuristicJudge,
    JudgeParseError,
    LLMJudge,
    extract_json,
    logprob_weighted_score,
)
from matrix_server.evals.providers import Completion


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! ```\n{"a": [1, 2]}\n``` hope it helps', {"a": [1, 2]}),
        ('Here you go: {"verdict": 1, "reason": "ok"} thanks', {"verdict": 1, "reason": "ok"}),
        ('noise {bad} then ["x", "y"]', ["x", "y"]),
        ('```json\n{broken\n```\n{"ok": true}', {"ok": True}),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "no json here", "{unclosed"])
def test_extract_json_fails(text):
    with pytest.raises(JudgeParseError):
        extract_json(text)


class Scripted:
    """Provider that returns queued texts and records calls."""

    def __init__(self, *texts, raw=None):
        self.texts = list(texts)
        self.raw = raw
        self.calls = []

    def complete(self, messages, model, params=None):
        self.calls.append((messages, model, params))
        return Completion(self.texts.pop(0), 1, 1, 1.0, 0.0, model, self.raw)


def test_llm_judge_methods():
    p = Scripted(
        '```json\n{"statements": ["Paris is in France.", "It has 2M people."]}\n```',
        '{"reason": "stated", "verdict": 1}',
        '{"questions": ["Where is Paris?", "What is Paris?", "x?", "extra?"]}',
        '{"reason": "no", "verdict": "0"}',
    )
    j = LLMJudge(p, "judge-model")
    assert j.extract_statements("q", "a") == ["Paris is in France.", "It has 2M people."]
    assert j.verify("s", ["c1", "c2"]) == (True, "stated")
    assert j.generate_questions("a", 3) == ["Where is Paris?", "What is Paris?", "x?"]
    assert j.chunk_relevant("q", "chunk", reference="ref") is False
    msgs, model, params = p.calls[0]
    assert model == "judge-model" and params["temperature"] == 0
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    assert "[1] c1" in p.calls[1][0][1]["content"]
    assert "REFERENCE: ref" in p.calls[3][0][1]["content"]


def test_llm_judge_bad_json():
    j = LLMJudge(Scripted("I think it is supported."), "m")
    with pytest.raises(JudgeParseError):
        j.verify("s", ["c"])
    j = LLMJudge(Scripted('{"items": []}'), "m")
    with pytest.raises(JudgeParseError):
        j.extract_statements("q", "a")


def _token(tok, alts):
    return {"token": tok, "logprob": math.log(max(alts.get(tok.strip(), 1e-9), 1e-9)),
            "top_logprobs": [{"token": t, "logprob": math.log(p)} for t, p in alts.items()]}


def _raw_with_logprobs(alts):
    pre = ['{"', "steps", '":', ' ["a"],', ' "reasoning":', ' "ok 5 stars",', ' "', "score", '":', " "]
    content = [{"token": t, "logprob": -0.01, "top_logprobs": []} for t in pre]
    content.append(_token("4", alts))
    content.append({"token": "}", "logprob": 0.0, "top_logprobs": []})
    return {"choices": [{"message": {"content": ""}, "logprobs": {"content": content}}]}


def test_logprob_weighted_score():
    raw = _raw_with_logprobs({"4": 0.6, "5": 0.3, "3": 0.1})
    assert logprob_weighted_score(raw) == pytest.approx(4 * 0.6 + 5 * 0.3 + 3 * 0.1)
    assert logprob_weighted_score(None) is None
    assert logprob_weighted_score({"choices": [{"message": {}}]}) is None


def test_geval_uses_logprobs_when_available():
    raw = _raw_with_logprobs({"4": 0.5, "5": 0.5})
    text = json.dumps({"steps": ["a"], "reasoning": "ok", "score": 4})
    p = Scripted(text, raw=raw)
    j = LLMJudge(p, "m", logprobs=True)
    score, reason = j.rate("correctness", "in", "out", "exp")
    assert score == pytest.approx(4.5) and reason == "ok"
    assert p.calls[0][2]["logprobs"] is True and p.calls[0][2]["top_logprobs"] == 5
    assert "EXPECTED OUTPUT: exp" in p.calls[0][0][1]["content"]


def test_geval_falls_back_to_parsed_score():
    p = Scripted('{"steps": [], "reasoning": "meh", "score": 3}')
    j = LLMJudge(p, "m")  # non-OpenAI provider -> no logprobs requested
    assert j.rate("c", "i", "o") == (3.0, "meh")
    assert "logprobs" not in p.calls[0][2]
    with pytest.raises(JudgeParseError):
        LLMJudge(Scripted('{"score": 9}'), "m").rate("c", "i", "o")


def test_heuristic_judge():
    j = HeuristicJudge()
    sts = j.extract_statements("q", "Paris is the capital of France. It has the Louvre!\nOk.")
    assert sts == ["Paris is the capital of France.", "It has the Louvre!", "Ok."]
    ok, reason = j.verify("Paris is the capital of France.", ["Paris is lovely", "Paris capital France"])
    assert ok and "context 2" in reason
    bad, reason = j.verify("Berlin hosts the Louvre museum.", ["Paris has the Louvre."])
    assert not bad and "missing" in reason
    qs = j.generate_questions("Paris Paris capital France Louvre", 3)
    assert len(qs) == 3 and qs[0].startswith("paris") and len(set(qs)) == 3
    assert j.chunk_relevant("What is the capital of France?", "France capital: Paris")
    assert not j.chunk_relevant("What is the capital of France?", "Bananas are yellow")
    assert j.rate("c", "x", "Paris capital France", expected="capital France Paris") == (
        5.0, "heuristic: 100% of reference words present in output")
    assert j.rate("c", "x", "", expected="a")[0] == 1.0
    assert j.extract_statements("q", "a") == j.extract_statements("q", "a")
