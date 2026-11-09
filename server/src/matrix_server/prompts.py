"""Pure helpers for prompt templates: validation, `{{ variable }}` extraction and rendering."""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")
ROLES = ("system", "user", "assistant", "tool")
PROMPT_TYPES = ("text", "chat")
MAX_TEMPLATE_CHARS = 200_000


class PromptValidationError(ValueError):
    pass


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise PromptValidationError(f"invalid prompt name: must match {NAME_RE.pattern}")
    return name


def validate_label(label: str) -> str:
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise PromptValidationError(f"invalid label: must match {LABEL_RE.pattern}")
    return label


def validate_template(prompt_type: str, template: Any) -> None:
    if prompt_type not in PROMPT_TYPES:
        raise PromptValidationError("type must be 'text' or 'chat'")
    if prompt_type == "text":
        if not isinstance(template, str):
            raise PromptValidationError("text template must be a string")
        if len(template) > MAX_TEMPLATE_CHARS:
            raise PromptValidationError(f"template exceeds {MAX_TEMPLATE_CHARS} characters")
        return
    if not isinstance(template, list) or not template:
        raise PromptValidationError("chat template must be a non-empty list of messages")
    total = 0
    for i, message in enumerate(template):
        if not isinstance(message, dict):
            raise PromptValidationError(f"message {i} must be an object")
        if message.get("role") not in ROLES:
            raise PromptValidationError(f"message {i}: role must be one of {', '.join(ROLES)}")
        content = message.get("content")
        if not isinstance(content, str):
            raise PromptValidationError(f"message {i}: content must be a string")
        total += len(content)
    if total > MAX_TEMPLATE_CHARS:
        raise PromptValidationError(f"template exceeds {MAX_TEMPLATE_CHARS} characters")


def validate_response_schema(schema: Any) -> None:
    if schema is None:
        return
    if not isinstance(schema, dict | bool):
        raise PromptValidationError("response_schema must be a JSON Schema object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise PromptValidationError(f"invalid response_schema: {exc.message}") from None


def _strings(template: Any) -> list[str]:
    if isinstance(template, str):
        return [template]
    return [m["content"] for m in template if isinstance(m, dict) and isinstance(m.get("content"), str)]


def extract_variables(template: Any) -> list[str]:
    """Unique `{{ name }}` placeholders, in order of first appearance (text or chat template)."""
    seen: dict[str, None] = {}
    for text in _strings(template):
        for match in VARIABLE_RE.finditer(text):
            seen.setdefault(match.group(1))
    return list(seen)


def _render_text(text: str, variables: dict[str, Any]) -> str:
    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(key)
        return str(variables[key])

    return VARIABLE_RE.sub(sub, text)


def render(template: Any, variables: dict[str, Any]) -> Any:
    """Substitute placeholders; raises KeyError for a variable that was not provided."""
    if isinstance(template, str):
        return _render_text(template, variables)
    return [{**m, "content": _render_text(m["content"], variables)} for m in template]
