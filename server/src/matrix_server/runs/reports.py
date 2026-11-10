"""Typed evaluation reports attached to runs (contract G1, "Relatórios tipados").

`validate_report(kind, data)` returns the normalized `data` to store or raises `ReportError` with
pydantic-style error entries (no input echoed). Numbers must be finite; list lengths are capped so
a report stays chart-sized (big tables go as artifacts). `custom` is free JSON within the size cap.
Normalization: importance features are stored sorted by importance (descending); table cells
longer than 2000 characters are cut and `truncated` is set.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_LABELS = 1000
MAX_METRICS = 100
MAX_CURVE_POINTS = 200
MAX_REGRESSION_SAMPLE = 1000
MAX_HIST_BINS = 1000
MAX_FEATURES = 100
MAX_TABLE_ROWS = 1000
MAX_TABLE_COLUMNS = 200
MAX_CELL_CHARS = 2000
MAX_NAME_CHARS = 200

Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Label = Annotated[str, Field(min_length=1, max_length=MAX_NAME_CHARS)]
MetricName = Annotated[str, Field(min_length=1, max_length=MAX_NAME_CHARS)]


class ReportError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("invalid report")
        self.errors = errors


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- classification ----------------------------------------------------------


class PerClass(_Strict):
    label: Label
    precision: Probability
    recall: Probability
    f1: Probability
    support: NonNegative


class RocCurve(_Strict):
    label: Label
    fpr: list[Probability] = Field(min_length=1, max_length=MAX_CURVE_POINTS)
    tpr: list[Probability] = Field(min_length=1, max_length=MAX_CURVE_POINTS)
    auc: Probability

    @model_validator(mode="after")
    def _same_length(self) -> RocCurve:
        if len(self.fpr) != len(self.tpr):
            raise ValueError("fpr and tpr must have the same length")
        return self


class PrCurve(_Strict):
    label: Label
    precision: list[Probability] = Field(min_length=1, max_length=MAX_CURVE_POINTS)
    recall: list[Probability] = Field(min_length=1, max_length=MAX_CURVE_POINTS)
    ap: Probability

    @model_validator(mode="after")
    def _same_length(self) -> PrCurve:
        if len(self.precision) != len(self.recall):
            raise ValueError("precision and recall must have the same length")
        return self


class ClassificationReport(_Strict):
    labels: list[Label] = Field(min_length=1, max_length=MAX_LABELS)
    metrics: dict[MetricName, Finite] = Field(max_length=MAX_METRICS)
    per_class: list[PerClass] = Field(max_length=MAX_LABELS)
    confusion_matrix: list[list[NonNegative]] = Field(max_length=MAX_LABELS)
    roc: list[RocCurve] = Field(default_factory=list, max_length=MAX_LABELS)
    pr: list[PrCurve] = Field(default_factory=list, max_length=MAX_LABELS)

    @model_validator(mode="after")
    def _consistent(self) -> ClassificationReport:
        n = len(self.labels)
        if len(set(self.labels)) != n:
            raise ValueError("labels must be unique")
        known = set(self.labels)
        if len(self.confusion_matrix) != n or any(len(row) != n for row in self.confusion_matrix):
            raise ValueError("confusion_matrix must be len(labels) x len(labels)")
        for name, items in (("per_class", self.per_class), ("roc", self.roc), ("pr", self.pr)):
            if any(item.label not in known for item in items):
                raise ValueError(f"{name} refers to a label not in labels")
        return self


# --- regression --------------------------------------------------------------


class RegressionMetrics(_Strict):
    rmse: NonNegative
    mae: NonNegative
    r2: Finite
    mape: NonNegative | None = None


class ResidualHist(_Strict):
    edges: list[Finite] = Field(min_length=2, max_length=MAX_HIST_BINS + 1)
    counts: list[NonNegative] = Field(min_length=1, max_length=MAX_HIST_BINS)

    @model_validator(mode="after")
    def _shape(self) -> ResidualHist:
        if len(self.edges) != len(self.counts) + 1:
            raise ValueError("edges must have len(counts) + 1 values")
        if any(b < a for a, b in zip(self.edges, self.edges[1:], strict=False)):
            raise ValueError("edges must be non-decreasing")
        return self


class RegressionSample(_Strict):
    y_true: list[Finite] = Field(max_length=MAX_REGRESSION_SAMPLE)
    y_pred: list[Finite] = Field(max_length=MAX_REGRESSION_SAMPLE)

    @model_validator(mode="after")
    def _same_length(self) -> RegressionSample:
        if len(self.y_true) != len(self.y_pred):
            raise ValueError("y_true and y_pred must have the same length")
        return self


class RegressionReport(_Strict):
    metrics: RegressionMetrics
    residual_hist: ResidualHist
    sample: RegressionSample


# --- importance --------------------------------------------------------------


class Feature(_Strict):
    name: Label
    importance: Finite
    std: NonNegative | None = None


class ImportanceReport(_Strict):
    method: Literal["shap", "permutation", "model"]
    features: list[Feature] = Field(min_length=1, max_length=MAX_FEATURES)

    @model_validator(mode="after")
    def _sorted(self) -> ImportanceReport:
        if len({f.name for f in self.features}) != len(self.features):
            raise ValueError("feature names must be unique")
        self.features = sorted(self.features, key=lambda f: -f.importance)
        return self


# --- table -------------------------------------------------------------------

Cell = StrictBool | int | Finite | str | None


class TableReport(_Strict):
    columns: list[Annotated[str, Field(max_length=MAX_NAME_CHARS)]] = Field(
        min_length=1, max_length=MAX_TABLE_COLUMNS
    )
    rows: list[list[Cell]] = Field(max_length=MAX_TABLE_ROWS)
    truncated: StrictBool = False

    @model_validator(mode="after")
    def _shape(self) -> TableReport:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("every row must have len(columns) cells")
        cut = False
        rows = []
        for row in self.rows:
            out = []
            for cell in row:
                if isinstance(cell, str) and len(cell) > MAX_CELL_CHARS:
                    cell, cut = cell[:MAX_CELL_CHARS], True
                elif isinstance(cell, int) and not isinstance(cell, bool) and abs(cell) > 2**63:
                    raise ValueError("integer cell out of range")
                out.append(cell)
            rows.append(out)
        self.rows = rows
        self.truncated = self.truncated or cut
        return self


_MODELS: dict[str, type[BaseModel]] = {
    "classification": ClassificationReport,
    "regression": RegressionReport,
    "importance": ImportanceReport,
    "table": TableReport,
}


def _check_json(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ReportError([{"type": "too_deep", "loc": ["data"], "msg": "data is nested too deeply"}])
    if isinstance(value, float) and not math.isfinite(value):
        raise ReportError([{"type": "finite_number", "loc": ["data"], "msg": "NaN and Infinity are not allowed"}])
    if isinstance(value, dict):
        for item in value.values():
            _check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)


def validate_report(kind: str, data: Any) -> Any:
    """Validated, normalized `data` for `kind`; raises `ReportError` (422) otherwise."""
    model = _MODELS.get(kind)
    if model is None:
        if kind != "custom":
            raise ReportError([{"type": "enum", "loc": ["kind"], "msg": "unknown report kind"}])
        _check_json(data)
        normalized = data
    else:
        try:
            normalized = model.model_validate(data).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            raise ReportError(
                [{**e, "loc": ["data", *e.get("loc", ())]} for e in errors[:50]]
            ) from None
    try:
        size = len(json.dumps(normalized, allow_nan=False, separators=(",", ":")).encode())
    except (TypeError, ValueError) as exc:
        raise ReportError([{"type": "json", "loc": ["data"], "msg": "data is not valid JSON"}]) from exc
    if size > MAX_REPORT_BYTES:
        raise ReportError([{"type": "too_large", "loc": ["data"], "msg": "report data exceeds 2 MiB"}])
    return normalized
