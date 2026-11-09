import json

import pytest

from matrix_server.pricing import compute_cost, get_price, reload_prices


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MATRIX_PRICES_FILE", raising=False)
    reload_prices()
    yield
    reload_prices()


def test_exact_case_insensitive():
    assert get_price("GPT-4o") == get_price("gpt-4o")
    assert get_price("gpt-4o").input == 2.5


@pytest.mark.parametrize(
    "name,key",
    [
        ("openai/gpt-4o", "gpt-4o"),
        ("anthropic/claude-sonnet-4", "claude-sonnet-4"),
        ("models/gemini-2.5-pro", "gemini-2.5-pro"),
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("claude-sonnet-4-20250514", "claude-sonnet-4"),
        ("claude-3-5-haiku-latest", "claude-3-5-haiku"),
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("claude-sonnet-4@20250514", "claude-sonnet-4"),
        ("openai/gpt-4.1-mini-2025-04-14", "gpt-4.1-mini"),
    ],
)
def test_normalisation(name, key):
    assert get_price(name) == get_price(key)
    assert get_price(name) is not None


def test_longest_prefix_wins():
    assert get_price("gpt-4o-mini-custom-ft").input == get_price("gpt-4o-mini").input
    assert get_price("gpt-5-mini-foo").input == get_price("gpt-5-mini").input
    assert get_price("o3-mini-high").input == get_price("o3-mini").input


def test_unknown_and_empty():
    assert compute_cost("totally-unknown", 1000, 1000) == 0.0
    assert compute_cost(None, 1000, 1000) == 0.0
    assert compute_cost("", 1000, 1000) == 0.0
    assert get_price(None) is None


def test_basic_cost():
    # gpt-4o: 2.5 in / 10 out per 1M
    assert compute_cost("gpt-4o", 1_000_000, 1_000_000) == 12.5
    assert compute_cost("gpt-4o", 1000, 500) == round(0.0025 + 0.005, 8)


def test_cache_read_subtracted_from_input():
    # 1M input of which 400k cached: 600k*2.5 + 400k*1.25 per 1M = 1.5 + 0.5
    assert compute_cost("gpt-4o", 1_000_000, 0, cache_read_tokens=400_000) == 2.0


def test_cache_write_and_fallback():
    # sonnet-4 cache_write 3.75
    assert compute_cost("claude-sonnet-4", 0, 0, cache_write_tokens=1_000_000) == 3.75
    # gpt-4o has no distinct cache_write -> falls back to input price
    assert compute_cost("gpt-4o", 0, 0, cache_write_tokens=1_000_000) == 2.5


def test_cache_read_exceeding_input_never_negative():
    cost = compute_cost("gpt-4o", 10, 0, cache_read_tokens=1000)
    assert cost >= 0
    assert cost == round(1000 * 1.25 / 1_000_000, 8)


def test_no_negative_and_rounding():
    assert compute_cost("gpt-4o", -5, -5, -5, -5) == 0.0
    c = compute_cost("gpt-4o-mini", 1, 1)
    assert c == round(c, 8)


def test_override_file(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    f.write_text(
        json.dumps(
            {
                "_note": "x",
                "gpt-4o": {"input": 1.0, "output": 2.0},
                "my-model": {"input": 4.0, "output": 8.0, "cache_read": 0.4},
            }
        )
    )
    monkeypatch.setenv("MATRIX_PRICES_FILE", str(f))
    reload_prices()
    assert compute_cost("gpt-4o", 1_000_000, 1_000_000) == 3.0
    # unspecified fields keep the default (cache_read 1.25)
    assert get_price("gpt-4o").cache_read == 1.25
    assert compute_cost("my-model", 1_000_000, 0) == 4.0
    assert get_price("my-model").cache_read == 0.4
    # untouched defaults remain
    assert get_price("gpt-4.1").input == 2


def test_broken_override_ignored(tmp_path, monkeypatch):
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    monkeypatch.setenv("MATRIX_PRICES_FILE", str(f))
    reload_prices()
    assert get_price("gpt-4o").input == 2.5
    monkeypatch.setenv("MATRIX_PRICES_FILE", str(tmp_path / "missing.json"))
    reload_prices()
    assert get_price("gpt-4o").input == 2.5


def test_cached_until_reload(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"gpt-4o": {"input": 9.0, "output": 9.0}}))
    assert get_price("gpt-4o").input == 2.5
    monkeypatch.setenv("MATRIX_PRICES_FILE", str(f))
    assert get_price("gpt-4o").input == 2.5  # cached
    reload_prices()
    assert get_price("gpt-4o").input == 9.0
