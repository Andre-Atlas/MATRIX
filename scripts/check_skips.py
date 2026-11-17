#!/usr/bin/env python3
"""Falha se a saída do `pytest -rs` tiver testes pulados fora da lista permitida.

Uso: pytest -q -rs | tee out.txt; python scripts/check_skips.py out.txt   (ou lendo do stdin)

Só são aceitos skips cujo motivo contenha `MATRIX_TEST_S3_ENDPOINT` (o backend S3 pula sem
endpoint) ou seja o skip estrutural do teste de performance (`perf check runs once`, que só roda
em 1 das combinações de store parametrizadas). Qualquer outro skip derruba o job (saída 1).
"""

from __future__ import annotations

import re
import sys

ALLOWED = ("MATRIX_TEST_S3_ENDPOINT", "perf check runs once")
# `SKIPPED [3] tests/test_x.py:10: motivo` (pytest -rs); the count is absent for a single skip.
LINE = re.compile(r"^SKIPPED(?: \[(\d+)\])? (.*?): (.*)$")


def unexpected(output: str) -> list[str]:
    bad = []
    for line in output.splitlines():
        m = LINE.match(line.strip())
        if m and not any(token in m.group(3) for token in ALLOWED):
            bad.append(line.strip())
    return bad


def main(argv: list[str]) -> int:
    text = open(argv[1], encoding="utf-8", errors="replace").read() if len(argv) > 1 else sys.stdin.read()
    bad = unexpected(text)
    allowed = sum(int(m.group(1) or 1) for m in map(LINE.match, map(str.strip, text.splitlines())) if m) - sum(
        int(LINE.match(b).group(1) or 1) for b in bad  # type: ignore[union-attr]
    )
    for b in bad:
        print(f"FAIL  skip inesperado: {b}")
    print(f"skips permitidos: {allowed}; inesperados: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
