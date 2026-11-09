import os
import sys


def run_api() -> None:
    import uvicorn

    uvicorn.run(
        "matrix_server.app:create_app",
        factory=True,
        host=os.environ.get("MATRIX_HOST", "0.0.0.0"),
        port=int(os.environ.get("MATRIX_PORT", "8000")),
    )


def _not_implemented() -> None:
    print("not implemented yet")
    sys.exit(1)


def run_worker() -> None:
    _not_implemented()


def admin() -> None:
    _not_implemented()
