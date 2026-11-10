"""OWASP Top 10 for LLM Applications categories and metadata definitions.

This module models the defensive compliance categories according to OWASP Top 10
for LLMs standards (MATRIX Track C - Defensive AI Security Audit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SeverityLevel = Literal["critical", "high", "medium", "low", "info"]


@dataclass(frozen=True)
class SafetyCategory:
    """Metadata representing an OWASP Top 10 for LLMs security category."""

    id: str
    name: str
    description: str
    weight: float
    default_severity: SeverityLevel


OWASP_CATEGORIES: dict[str, SafetyCategory] = {
    "LLM01": SafetyCategory(
        id="LLM01",
        name="Prompt Injection",
        description=(
            "Audits resistance against prompt injection, jailbreaks, instruction overrides, "
            "and system prompt exfiltration attempts."
        ),
        weight=1.0,
        default_severity="critical",
    ),
    "LLM02": SafetyCategory(
        id="LLM02",
        name="Insecure Output Handling",
        description=(
            "Audits output sanitization and validation to prevent XSS payloads, unsafe code "
            "execution, unescaped markdown exfiltration, and injection attacks in downstream systems."
        ),
        weight=0.9,
        default_severity="high",
    ),
    "LLM06": SafetyCategory(
        id="LLM06",
        name="Sensitive Information Disclosure",
        description=(
            "Audits model outputs for leakage of synthetic PII (fictitious CPFs, synthetic cards), "
            "proprietary credentials, API keys, passwords, and confidential operational data."
        ),
        weight=1.0,
        default_severity="critical",
    ),
    "LLM07": SafetyCategory(
        id="LLM07",
        name="Insecure Plugin Design",
        description=(
            "Audits plugin and tool integration boundaries to verify authorization checks, "
            "parameter tampering resistance, and strict execution guardrails."
        ),
        weight=0.8,
        default_severity="high",
    ),
    "LLM09": SafetyCategory(
        id="LLM09",
        name="Overreliance",
        description=(
            "Audits safeguards against excessive trust in model output, detecting blind confirmation "
            "of false factual claims, unverified legal/medical claims, and lack of disclaimers."
        ),
        weight=0.7,
        default_severity="medium",
    ),
}


def get_category(category_id: str) -> SafetyCategory | None:
    """Retrieve category definition by ID (case-insensitive)."""
    return OWASP_CATEGORIES.get(category_id.upper())


def list_categories() -> list[SafetyCategory]:
    """Return all defined OWASP LLM categories ordered by identifier."""
    return [OWASP_CATEGORIES[key] for key in sorted(OWASP_CATEGORIES.keys())]
