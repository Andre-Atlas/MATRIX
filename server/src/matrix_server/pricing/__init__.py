"""Cost engine: token usage -> USD, based on a per-1M-token price table."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Price", "compute_cost", "get_price", "reload_prices"]

_DEFAULT_FILE = Path(__file__).with_name("prices.json")
_ENV_VAR = "MATRIX_PRICES_FILE"
_FIELDS = ("input", "output", "cache_read", "cache_write")
# Only provider prefixes and date/version suffixes are stripped; anything else must be a table
# key or an explicit `_aliases` entry. There is no prefix matching: `o3-pro` is not `o3`.
_PREFIXES = (re.compile(r"^(?:(?:us|eu|apac|global)\.)?(?:anthropic|meta|mistral|cohere|amazon|openai)\."),)
_SUFFIXES = (
    re.compile(r"@.*$"),
    re.compile(r"-v\d+(?::\d+)?$"),
    re.compile(r"-latest$"),
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),
    re.compile(r"-\d{8}$"),
    re.compile(r"-\d{4}$"),
)


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


_table: dict[str, Price] | None = None
_aliases: dict[str, str] = {}


def _build(entry: dict, base: Price | None) -> Price | None:
    merged = {f: getattr(base, f) for f in _FIELDS} if base else {}
    for f in _FIELDS:
        if isinstance(entry.get(f), (int, float)) and not isinstance(entry.get(f), bool):
            merged[f] = float(entry[f])
    if "input" not in merged or "output" not in merged:
        return None
    merged.setdefault("cache_read", merged["input"])
    merged.setdefault("cache_write", merged["input"])
    return Price(**{f: merged[f] for f in _FIELDS})


def _merge_file(table: dict[str, Price], aliases: dict[str, str], path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    raw_aliases = data.get("_aliases")
    if isinstance(raw_aliases, dict):
        for alias, target in raw_aliases.items():
            if not alias.startswith("_") and isinstance(target, str):
                aliases[alias.strip().lower()] = target.strip().lower()
    for key, entry in data.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        k = key.strip().lower()
        price = _build(entry, table.get(k))
        if price is not None:
            table[k] = price


def _load() -> tuple[dict[str, Price], dict[str, str]]:
    table: dict[str, Price] = {}
    aliases: dict[str, str] = {}
    _merge_file(table, aliases, _DEFAULT_FILE)
    override = os.environ.get(_ENV_VAR)
    if override:
        try:
            _merge_file(table, aliases, Path(override))
        except (OSError, ValueError):
            pass  # a broken override must never break ingestion
    return table, aliases


def reload_prices() -> None:
    """Drop the cache; the next lookup re-reads defaults and MATRIX_PRICES_FILE."""
    global _table
    _table = None


def _prices() -> dict[str, Price]:
    global _table, _aliases
    if _table is None:
        _table, _aliases = _load()
    return _table


def _candidates(name: str) -> list[str]:
    out: list[str] = [name]

    def add(s: str) -> None:
        if s and s not in out:
            out.append(s)

    parts = name.split("/")
    for i in range(1, len(parts)):
        add("/".join(parts[i:]))
    for base in tuple(out):
        for rx in _PREFIXES:
            add(rx.sub("", base))
    for base in tuple(out):
        s = base
        for rx in _SUFFIXES:
            s = rx.sub("", s)
            add(s)
    return out


def get_price(model: str | None) -> Price | None:
    if not model or not isinstance(model, str):
        return None
    name = model.strip().lower()
    if not name:
        return None
    table = _prices()
    for c in _candidates(name):
        if c in table:
            return table[c]
        target = _aliases.get(c)
        if target is not None and target in table:
            return table[target]
    return None


def compute_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost in USD.

    `input_tokens` is the total prompt size and includes both cache reads and cache writes
    (OpenInference / OTel GenAI convention); only the remainder is billed at the input price.
    """
    price = get_price(model)
    if price is None:
        return 0.0
    inp = max(int(input_tokens or 0), 0)
    out = max(int(output_tokens or 0), 0)
    cr = max(int(cache_read_tokens or 0), 0)
    cw = max(int(cache_write_tokens or 0), 0)
    uncached = max(inp - cr - cw, 0)
    total = (
        uncached * price.input + cr * price.cache_read + cw * price.cache_write + out * price.output
    ) / 1_000_000
    return max(round(total, 8), 0.0)
