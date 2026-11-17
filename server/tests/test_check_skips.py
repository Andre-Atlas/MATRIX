import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_skips", Path(__file__).resolve().parents[2] / "scripts" / "check_skips.py")
check_skips = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_skips)

S3 = "SKIPPED [84] tests/test_artifacts.py:52: MATRIX_TEST_S3_ENDPOINT not set"
PERF = "SKIPPED [4] tests/test_runs_api.py:581: perf check runs once, on SQLite + ClickHouse"


def test_only_allowed_skips_pass():
    assert check_skips.unexpected(f"short test summary info\n{S3}\n{PERF}\n1 passed, 88 skipped") == []


def test_other_skip_fails_even_next_to_s3():
    out = f"{S3}\nSKIPPED [1] tests/test_x.py:5: needs postgres\nSKIPPED tests/test_y.py:9: unconditional skip"
    assert check_skips.unexpected(out) == [
        "SKIPPED [1] tests/test_x.py:5: needs postgres",
        "SKIPPED tests/test_y.py:9: unconditional skip",
    ]


def test_allowed_reason_must_match_exactly_and_in_its_file():
    """LOW (review 7): a reason merely containing an allowed token used to pass anywhere."""
    lines = [
        "SKIPPED [1] tests/test_rbac.py:10: MATRIX_TEST_S3_ENDPOINT not set",  # right reason, wrong file
        "SKIPPED [1] tests/test_artifacts.py:99: oops MATRIX_TEST_S3_ENDPOINT not set",  # only contains it
        "SKIPPED [1] tests/test_runs_api.py:12: perf check runs once",  # truncated reason
        "SKIPPED [1] tests/test_artifacts.py: no line number",
    ]
    out = "\n".join(lines)
    assert len(check_skips.unexpected(out)) == 4


def test_xfail_and_xpass_fail():
    lines = [
        S3,
        "XFAIL tests/test_a.py::test_x - not ready",
        "XPASS tests/test_a.py::test_y flaky",
        "1 passed, 84 skipped, 1 xfailed, 1 xpassed in 1.0s",
    ]
    out = "\n".join(lines)
    assert check_skips.unexpected(out) == [
        "XFAIL tests/test_a.py::test_x - not ready",
        "XPASS tests/test_a.py::test_y flaky",
        "1 passed, 84 skipped, 1 xfailed, 1 xpassed in 1.0s",
    ]


def test_cli_exit_codes(tmp_path):
    ok, bad, xf = tmp_path / "ok.txt", tmp_path / "bad.txt", tmp_path / "xf.txt"
    ok.write_text(S3 + "\n" + PERF + "\n")
    bad.write_text("SKIPPED [1] tests/a.py:1: nope\n")
    xf.write_text(S3 + "\n1 passed, 1 xfailed\n")
    assert check_skips.main(["x", str(ok)]) == 0
    assert check_skips.main(["x", str(bad)]) == 1
    assert check_skips.main(["x", str(xf)]) == 1
