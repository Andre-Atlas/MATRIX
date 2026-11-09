"""Judges: the reasoning steps behind the LLM-as-judge metrics.

`LLMJudge` prompts a real model and demands strict JSON. `HeuristicJudge` is an offline,
deterministic lexical approximation: its numbers are for tests and demos only and must not
be read as real faithfulness/relevancy scores.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol, runtime_checkable

from matrix_server.evals.providers import LLMProvider, OpenAICompatibleProvider

__all__ = [
    "HeuristicJudge",
    "Judge",
    "JudgeParseError",
    "LLMJudge",
    "content_words",
    "extract_json",
    "logprob_weighted_score",
]


class JudgeParseError(ValueError):
    """The judge's output could not be parsed into the expected JSON."""


@runtime_checkable
class Judge(Protocol):
    def extract_statements(self, question: str, answer: str) -> list[str]: ...

    def verify(self, statement: str, contexts: list[str]) -> tuple[bool, str]: ...

    def generate_questions(self, answer: str, n: int) -> list[str]: ...

    def chunk_relevant(self, question: str, chunk: str, reference: str | None = None) -> bool: ...

    def rate(self, criteria: str, input: str, output: str, expected: str | None = None) -> tuple[float, str]: ...


# --------------------------------------------------------------------------- JSON extraction

_FENCE_RX = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from model output: bare, inside ``` fences, or embedded in prose."""
    if not isinstance(text, str) or not text.strip():
        raise JudgeParseError("empty judge output")
    s = text.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    for m in _FENCE_RX.finditer(s):
        try:
            return json.loads(m.group(1).strip())
        except ValueError:
            continue
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(s, i)
                return obj
            except ValueError:
                continue
    raise JudgeParseError(f"no JSON found in judge output: {s[:120]!r}")


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v >= 1
    if isinstance(v, str):
        return v.strip().lower() in {"1", "yes", "true", "supported", "relevant", "sim"}
    raise JudgeParseError(f"invalid verdict: {v!r}")


def _require(obj: Any, key: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise JudgeParseError(f"judge JSON missing {key!r}")
    return obj[key]


def _str_list(v: Any, key: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise JudgeParseError(f"{key!r} must be a list of strings")
    return [x.strip() for x in v if x.strip()]


# --------------------------------------------------------------------------- G-Eval logprobs

_SCORE_TOKEN_RX = re.compile(r"^[1-5]$")


def logprob_weighted_score(raw: dict | None) -> float | None:
    """Σ s·P(s) over score tokens 1..5, from an OpenAI chat response with top_logprobs.

    Looks for the first digit token after the last `"score"` key in the output. Returns None
    when logprobs are unavailable.
    """
    try:
        content = raw["choices"][0]["logprobs"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, list) or not content:
        return None
    offsets, pos = [], 0
    for tok in content:
        offsets.append(pos)
        pos += len(str(tok.get("token", "")) if isinstance(tok, dict) else "")
    full = "".join(str(t.get("token", "")) for t in content if isinstance(t, dict))
    anchor = full.rfind('"score"')
    if anchor < 0:
        anchor = full.lower().rfind("score")
    for tok, off in zip(content, offsets, strict=False):
        if not isinstance(tok, dict) or off < anchor:
            continue
        if not _SCORE_TOKEN_RX.match(str(tok.get("token", "")).strip()):
            continue
        probs: dict[int, float] = {}
        for alt in tok.get("top_logprobs") or []:
            t = str(alt.get("token", "")).strip()
            lp = alt.get("logprob")
            if _SCORE_TOKEN_RX.match(t) and isinstance(lp, (int, float)):
                probs[int(t)] = probs.get(int(t), 0.0) + math.exp(lp)
        if not probs:
            return float(str(tok.get("token", "")).strip())
        total = sum(probs.values())
        return sum(s * p for s, p in probs.items()) / total
    return None


# --------------------------------------------------------------------------- LLM judge

_SYSTEM = "You are a meticulous evaluation assistant. Reply with strict JSON only, no prose, no markdown."

_STATEMENTS_PROMPT = """Break the ANSWER into atomic, self-contained factual statements.
Each statement must be understandable without the others (replace pronouns with the entities they refer to).
Ignore greetings and filler. Do not add facts that are not in the answer.

QUESTION: {question}
ANSWER: {answer}

Return JSON: {{"statements": ["...", "..."]}}"""

_VERIFY_PROMPT = """Decide whether the STATEMENT can be directly inferred from the CONTEXT (natural language inference).
Use only the context; outside knowledge does not count. Verdict 1 if supported, 0 otherwise.

CONTEXT:
{context}

STATEMENT: {statement}

Return JSON: {{"reason": "<one sentence>", "verdict": 0 or 1}}"""

_QUESTIONS_PROMPT = """Write {n} distinct questions that the ANSWER below would directly answer.
If the answer is evasive or noncommittal ("I don't know"), still write the questions.

ANSWER: {answer}

Return JSON: {{"questions": ["...", "..."]}}"""

_CHUNK_PROMPT = """Decide whether the CONTEXT CHUNK was useful to answer the QUESTION{ref_hint}.
Verdict 1 if useful, 0 otherwise.

QUESTION: {question}
{reference_block}CONTEXT CHUNK:
{chunk}

Return JSON: {{"reason": "<one sentence>", "verdict": 0 or 1}}"""

_RATE_PROMPT = """You are grading an LLM output with a rubric (G-Eval).

CRITERIA: {criteria}

First write 3-5 concise evaluation steps derived from the criteria, then apply them to the output
step by step, and finally give an integer score from 1 (worst) to 5 (best).

INPUT: {input}
OUTPUT: {output}
{expected_block}
Return JSON with keys in this exact order:
{{"steps": ["..."], "reasoning": "<step-by-step assessment>", "score": <integer 1-5>}}"""


class LLMJudge:
    """LLM-as-judge on any `LLMProvider`.

    `logprobs`: request top logprobs for G-Eval weighting. Default: on for OpenAI-compatible
    providers only.
    """

    def __init__(self, provider: LLMProvider, model: str, logprobs: bool | None = None,
                 params: dict | None = None) -> None:
        self.provider = provider
        self.model = model
        self.logprobs = isinstance(provider, OpenAICompatibleProvider) if logprobs is None else logprobs
        self.params = {"temperature": 0, **(params or {})}

    def __repr__(self) -> str:
        return f"LLMJudge(provider={self.provider!r}, model={self.model!r})"

    def _ask(self, prompt: str, extra: dict | None = None):
        msgs = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]
        comp = self.provider.complete(msgs, self.model, {**self.params, **(extra or {})})
        return extract_json(comp.text), comp

    def extract_statements(self, question: str, answer: str) -> list[str]:
        obj, _ = self._ask(_STATEMENTS_PROMPT.format(question=question or "", answer=answer))
        if isinstance(obj, list):
            return _str_list(obj, "statements")
        return _str_list(_require(obj, "statements"), "statements")

    def verify(self, statement: str, contexts: list[str]) -> tuple[bool, str]:
        ctx = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
        obj, _ = self._ask(_VERIFY_PROMPT.format(context=ctx, statement=statement))
        return _as_bool(_require(obj, "verdict")), str(obj.get("reason", ""))

    def generate_questions(self, answer: str, n: int) -> list[str]:
        obj, _ = self._ask(_QUESTIONS_PROMPT.format(n=n, answer=answer))
        if isinstance(obj, list):
            return _str_list(obj, "questions")[:n]
        return _str_list(_require(obj, "questions"), "questions")[:n]

    def chunk_relevant(self, question: str, chunk: str, reference: str | None = None) -> bool:
        prompt = _CHUNK_PROMPT.format(
            question=question or "",
            chunk=chunk,
            ref_hint=" and arrive at the REFERENCE answer" if reference else "",
            reference_block=f"REFERENCE: {reference}\n" if reference else "",
        )
        obj, _ = self._ask(prompt)
        return _as_bool(_require(obj, "verdict"))

    def rate(self, criteria: str, input: str, output: str, expected: str | None = None) -> tuple[float, str]:
        prompt = _RATE_PROMPT.format(
            criteria=criteria,
            input=input,
            output=output,
            expected_block=f"EXPECTED OUTPUT: {expected}\n" if expected else "",
        )
        extra = {"logprobs": True, "top_logprobs": 5} if self.logprobs else None
        obj, comp = self._ask(prompt, extra)
        raw_score = _require(obj, "score")
        try:
            parsed = float(raw_score)
        except (TypeError, ValueError):
            raise JudgeParseError(f"invalid score: {raw_score!r}") from None
        if not 1 <= parsed <= 5:
            raise JudgeParseError(f"score out of range 1..5: {parsed}")
        weighted = logprob_weighted_score(comp.raw) if self.logprobs else None
        score = weighted if weighted is not None else parsed
        return min(max(score, 1.0), 5.0), str(obj.get("reasoning", ""))


# --------------------------------------------------------------------------- heuristic judge

_STOPWORDS = frozenset(
    """a an the and or but if then else of to in on at by for with from as is are was were be been being
    it its this that these those there here what which who whom whose when where why how do does did
    not no yes i you he she we they me him her us them my your our their has have had will would can
    could should may might must shall so than too very just also into about over under up down out
    o os as um uma uns umas e ou mas se de do da dos das em no na nos nas por para com sem que qual
    quais quem quando onde como é são foi ser está estão ao aos à às seu sua seus suas este esta isso
    isto esse essa ele ela eles elas eu nós você vocês não sim mais menos muito também já""".split()  # noqa: SIM905
)
_WORD_RX = re.compile(r"\w+", re.UNICODE)
_SENT_RX = re.compile(r"(?<=[.!?])\s+|\n+")


def content_words(text: str) -> list[str]:
    """Lowercased tokens without stopwords (order preserved, duplicates kept)."""
    return [w for w in _WORD_RX.findall((text or "").lower()) if w not in _STOPWORDS]


class HeuristicJudge:
    """Offline deterministic judge based on lexical overlap. FOR TESTS/DEMO ONLY.

    - statements: sentence split of the answer;
    - verify: supported if >= `support_threshold` (60%) of the statement's content words
      appear in a single context;
    - questions: keyword questions built from the answer's most frequent content words;
    - chunk relevance: >= `relevance_threshold` of the question/reference words in the chunk;
    - rate: 1 + 4 x overlap with the expected output (or the input), rounded.
    """

    def __init__(self, support_threshold: float = 0.6, relevance_threshold: float = 0.3) -> None:
        self.support_threshold = support_threshold
        self.relevance_threshold = relevance_threshold

    def __repr__(self) -> str:
        return "HeuristicJudge()"

    @staticmethod
    def _overlap(words: list[str], text: str) -> tuple[float, list[str]]:
        uniq = list(dict.fromkeys(words))
        if not uniq:
            return 0.0, []
        bag = set(content_words(text))
        missing = [w for w in uniq if w not in bag]
        return (len(uniq) - len(missing)) / len(uniq), missing

    def extract_statements(self, question: str, answer: str) -> list[str]:
        parts = (p.strip() for p in _SENT_RX.split(answer or ""))
        return [p for p in parts if p and content_words(p)]

    def verify(self, statement: str, contexts: list[str]) -> tuple[bool, str]:
        words = content_words(statement)
        if not words:
            return True, "no content words to verify"
        best, best_missing, best_i = -1.0, [], -1
        for i, ctx in enumerate(contexts):
            frac, missing = self._overlap(words, ctx)
            if frac > best:
                best, best_missing, best_i = frac, missing, i
        if best_i < 0:
            return False, "no context available"
        ok = best >= self.support_threshold
        reason = f"{best:.0%} of content words found in context {best_i + 1}"
        if best_missing:
            reason += f"; missing: {', '.join(best_missing[:8])}"
        return ok, reason

    def generate_questions(self, answer: str, n: int) -> list[str]:
        words = content_words(answer)
        if not words or n <= 0:
            return []
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        ranked = sorted(freq, key=lambda w: (-freq[w], words.index(w)))[:6]
        out = []
        for i in range(n):
            window = (ranked[i:] + ranked[:i])[:3]
            out.append(" ".join(window) + "?")
        return out

    def chunk_relevant(self, question: str, chunk: str, reference: str | None = None) -> bool:
        target = content_words(reference or "") or content_words(question or "")
        frac, _ = self._overlap(target, chunk)
        return frac >= self.relevance_threshold

    def rate(self, criteria: str, input: str, output: str, expected: str | None = None) -> tuple[float, str]:
        if not (output or "").strip():
            return 1.0, "empty output"
        ref = content_words(expected or "") or content_words(input or "")
        frac, _ = self._overlap(ref, output)
        score = 1.0 + round(4 * frac)
        return score, f"heuristic: {frac:.0%} of reference words present in output"
