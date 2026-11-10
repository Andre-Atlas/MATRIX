"""Framework integrations. Each submodule needs its optional extra, e.g. `pip install 'matrix-sdk[hf]'`."""

from __future__ import annotations

import importlib
from types import ModuleType


def require(module: str, extra: str) -> ModuleType:
    """Imports an optional dependency or fails with the extra that provides it."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"this MATRIX integration needs `{module}`: pip install 'matrix-sdk[{extra}]'"
        ) from exc
