import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_skips", Path(__file__).resolve().parents[2] / "scripts" / "check_skips.py")
check_skips = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_skips)

S3 = "SKIPPED [12] tests/test_artifacts_s3.py:20: set MATRIX_TEST_S3_ENDPOINT to run the S3 tests"


def test_only_s3_skips_pass():
    assert check_skips.unexpected(f"short test summary info\n{S3}\n1 passed, 12 skipped") == []


def test_other_skip_fails_even_next_to_s3():
    out = f"{S3}\nSKIPPED [1] tests/test_x.py:5: needs postgres\nSKIPPED tests/test_y.py:9: unconditional skip"
    assert check_skips.unexpected(out) == [
        "SKIPPED [1] tests/test_x.py:5: needs postgres",
        "SKIPPED tests/test_y.py:9: unconditional skip",
    ]


def test_cli_exit_codes(tmp_path):
    ok, bad = tmp_path / "ok.txt", tmp_path / "bad.txt"
    ok.write_text(S3 + "\n")
    bad.write_text("SKIPPED [1] tests/a.py:1: nope\n")
    assert check_skips.main(["x", str(ok)]) == 0
    assert check_skips.main(["x", str(bad)]) == 1
