"""scripts/check_compose.py: the real compose passes, and the ollama/local-model rules (ADR-016) bite."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_compose", ROOT / "scripts" / "check_compose.py")
check_compose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_compose)


@pytest.fixture
def doc():
    return yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text())


def test_repository_compose_passes(doc):
    assert check_compose.check(doc) == []


def test_ollama_service_shape(doc):
    ollama = doc["services"]["ollama"]
    assert ollama["profiles"] == ["llm"]
    assert ollama["ports"] == ["127.0.0.1:11434:11434"]
    api_env = doc["services"]["api"]["environment"]
    assert "http://ollama:11434" in api_env["MATRIX_LOCAL_MODEL_ENDPOINTS"]


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["services"]["ollama"].update(image="ollama/ollama:latest"), "tag fixa"),
        (lambda d: d["services"]["ollama"].update(image="ollama/ollama"), "tag fixa"),
        (lambda d: d["services"]["ollama"].pop("profiles"), "profiles"),
        (lambda d: d["services"]["ollama"].update(ports=["11434:11434"]), "127.0.0.1"),
        (lambda d: d["services"]["ollama"].update(volumes=["/tmp/x:/root/.ollama"]), "ollama-data"),
        (lambda d: d["services"]["ollama"].pop("healthcheck"), "healthcheck"),
        (lambda d: d["services"]["ollama"]["environment"].update(OLLAMA_KEEP_ALIVE="5m"), "ambiente"),
        (lambda d: d["services"]["api"]["environment"].pop("MATRIX_LOCAL_MODEL_ENDPOINTS"), "MATRIX_LOCAL_MODEL"),
        (lambda d: d["services"]["worker"].update(depends_on=["ollama"]), "depender de ollama"),
    ],
)
def test_ollama_rules(doc, mutate, needle):
    d = copy.deepcopy(doc)
    mutate(d)
    errors = check_compose.check(d)
    assert any(needle in e for e in errors), errors
