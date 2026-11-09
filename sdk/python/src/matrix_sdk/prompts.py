"""Prompt objects returned by the MATRIX prompt registry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_VAR = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def _render(text: str, variables: dict[str, Any]) -> str:
    return _VAR.sub(lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0), text)


@dataclass
class Prompt:
    name: str
    version: int
    type: str
    template: str | list[dict[str, Any]]
    config: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Prompt:
        return cls(
            name=data["name"],
            version=data["version"],
            type=data.get("type", "text"),
            template=data["template"],
            config=data.get("config") or {},
            labels=data.get("labels") or [],
            variables=data.get("variables") or [],
        )

    def compile(self, **variables: Any) -> str | list[dict[str, Any]]:
        if isinstance(self.template, str):
            return _render(self.template, variables)
        return [
            {**m, "content": _render(m["content"], variables)} if isinstance(m.get("content"), str) else dict(m)
            for m in self.template
        ]
