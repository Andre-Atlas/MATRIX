#!/usr/bin/env python3
"""Falha se a saída do `pytest -rxXs` tiver skips fora da lista permitida, ou qualquer xfail/xpass.

Uso: pytest -q -rxXs | tee out.txt; python scripts/check_skips.py out.txt   (ou lendo do stdin)

Só são aceitos skips cujo arquivo E motivo batem exatamente com uma entrada de `ALLOWED`: o fixture
do backend S3 em `tests/test_artifacts.py` (pula sem endpoint) e o skip estrutural do teste de
performance em `tests/test_runs_api.py` (só roda em 1 das combinações de store parametrizadas). Um
motivo que apenas contenha o texto permitido, ou o mesmo motivo noutro arquivo, não passa. Linhas
XFAIL/XPASS (e contagens `xfailed`/`xpassed` no resumo) derrubam o job (saída 1).
"""

from __future__ import annotations

import re
import sys

# (arquivo, motivo exato) aceitos.
ALLOWED = frozenset(
    {
        ("tests/test_artifacts.py", "MATRIX_TEST_S3_ENDPOINT not set"),
        ("tests/test_runs_api.py", "perf check runs once, on SQLite + ClickHouse"),
    }
)
# `SKIPPED [3] tests/test_x.py:10: motivo` (pytest -rs); the count is absent for a single skip.
LINE = re.compile(r"^SKIPPED(?: \[(\d+)\])? (.*?):(\d+): (.*)$")
XLINE = re.compile(r"^(XFAIL|XPASS)\b")
XSUMMARY = re.compile(r"\b\d+ x(failed|passed)\b")


def _allowed(path: str, reason: str) -> bool:
    path = path.replace("\\", "/")
    return any((path == f or path.endswith("/" + f)) and reason.strip() == r for f, r in ALLOWED)


def unexpected(output: str) -> list[str]:
    bad = []
    for raw in output.splitlines():
        line = raw.strip()
        m = LINE.match(line)
        if m and not _allowed(m.group(2), m.group(4)):
            bad.append(line)
        elif line.startswith("SKIPPED") and not m:
            bad.append(line)  # a skip line we cannot parse is not trusted
        elif XLINE.match(line) or XSUMMARY.search(line):
            bad.append(line)
    return bad


def main(argv: list[str]) -> int:
    text = open(argv[1], encoding="utf-8", errors="replace").read() if len(argv) > 1 else sys.stdin.read()
    bad = unexpected(text)
    allowed = sum(
        int(m.group(1) or 1)
        for m in map(LINE.match, map(str.strip, text.splitlines()))
        if m and _allowed(m.group(2), m.group(4))
    )
    for b in bad:
        print(f"FAIL  skip/xfail/xpass inesperado: {b}")
    print(f"skips permitidos: {allowed}; inesperados: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
