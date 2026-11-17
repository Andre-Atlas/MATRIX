"""Translation between `main`'s MLOps run/artifact vocabulary and the G1/G0 stack (T4, ADR-013).

`main` had its own runs table, metric store and blob store; they are gone. What `main`'s SDK and
web still send (`/api/v1/mlops/runs*`, `/api/v1/mlops/artifacts*`, deprecated) is translated here
into `runs`, `run_metrics` and `artifacts`, and the stored rows are rendered back in `main`'s
response shape. Pure functions only (no session, no store): the adapters in `api/mlops_compat.py`
and migration 0008 use them.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from matrix_server.runs.reports import MAX_CURVE_POINTS, ReportError, validate_report
from matrix_server.runs.store import SYS_PREFIX

# --------------------------------------------------------------------------- status

STATUS_IN = {
    "running": "running",
    "queued": "running",  # no `queued` in G1: trials are created running, the SDK beats on every batch
    "completed": "finished",
    "finished": "finished",
    "failed": "failed",
    "cancelled": "killed",
    "stopped": "killed",
    "killed": "killed",
}
STATUS_OUT = {"running": "running", "finished": "completed", "failed": "failed", "killed": "cancelled"}


def status_in(value: str) -> str | None:
    """G1 status for a status of `main` (or of G1); None when unknown."""
    return STATUS_IN.get(value)


def status_out(value: str) -> str:
    return STATUS_OUT.get(value, value)


# --------------------------------------------------------------------------- metric points

HARDWARE_PREFIX = f"{SYS_PREFIX}hw."
EPOCH_KEY = "epoch"


def metric_key(category: str | None, metric_name: str) -> str:
    """`category="system"` -> `sys/<name>` (once); any other category is dropped."""
    if category == "system" and not metric_name.startswith(SYS_PREFIX):
        return SYS_PREFIX + metric_name
    return metric_name


def _ts(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def points_in(points: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`main` points `{category, metric_name, step, epoch?, value, recorded_at?}` -> G1 points
    `{key, step, value, ts?}`. An `epoch` becomes a point of the `epoch` key at the same step."""
    out: list[dict[str, Any]] = []
    for p in points:
        base: dict[str, Any] = {"step": p["step"]}
        if p.get("recorded_at") is not None:
            base["ts"] = _ts(p["recorded_at"])
        out.append({"key": metric_key(p.get("category"), p["metric_name"]), "value": p["value"], **base})
        if p.get("epoch") is not None:
            out.append({"key": EPOCH_KEY, "value": p["epoch"], **base})
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def hardware_points(hardware: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Numeric `hardware_info` fields -> `sys/hw.<field>` points at step 0."""
    out = []
    for key, value in (hardware or {}).items():
        name = f"{HARDWARE_PREFIX}{key}"
        if _is_number(value) and re.fullmatch(r"[A-Za-z0-9_./:@ -]{1,200}", name):
            out.append({"key": name, "step": 0, "value": float(value)})
    return out


# --------------------------------------------------------------------------- classic_eval <-> reports

CLASSIC_EVAL_REPORT = "classic_eval"
_SCALARS = ("roc_auc", "pr_auc", "accuracy", "f1", "precision", "recall", "log_loss")


def _thin(values: list[Any], limit: int = MAX_CURVE_POINTS) -> list[Any]:
    if len(values) <= limit:
        return values
    idx = sorted({round(i * (len(values) - 1) / (limit - 1)) for i in range(limit)})
    return [values[i] for i in idx]


def _classification(ce: dict[str, Any]) -> tuple[dict[str, Any] | None, set[str], dict[str, Any]]:
    """(report data, consumed keys, extra keys for the custom remainder)."""
    cm = ce.get("confusion_matrix")
    if not isinstance(cm, Mapping) or set(cm) - {"labels", "matrix"}:
        return None, set(), {}
    labels = [str(label) for label in cm.get("labels") or []]
    metrics = {k: float(ce[k]) for k in _SCALARS if _is_number(ce.get(k))}
    data: dict[str, Any] = {
        "labels": labels,
        "metrics": metrics,
        "per_class": [],
        "confusion_matrix": cm.get("matrix"),
        "roc": [],
        "pr": [],
    }
    consumed = {"confusion_matrix", *metrics}
    extra: dict[str, Any] = {}
    curve = ce.get("roc_curve")
    auc = ce.get("roc_auc")
    if isinstance(curve, list) and curve and labels and _is_number(auc) and all(isinstance(p, Mapping) for p in curve):
        pts = _thin(list(curve))
        data["roc"] = [
            {"label": labels[-1], "fpr": [p.get("fpr") for p in pts], "tpr": [p.get("tpr") for p in pts], "auc": auc}
        ]
        consumed.add("roc_curve")
        if len(pts) != len(curve) or any(set(p) - {"fpr", "tpr"} for p in curve):
            extra["roc_curve"] = curve  # thresholds or a longer curve: kept whole in the remainder
    return data, consumed, extra


def _importance(ce: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    items = ce.get("feature_importance")
    if not isinstance(items, list) or not items or not all(isinstance(i, Mapping) for i in items):
        return None, {}
    data = {
        "method": "model",
        "features": [{"name": str(i.get("feature")), "importance": i.get("importance")} for i in items],
    }
    directions = {str(i.get("feature")): i["direction"] for i in items if i.get("direction") is not None}
    extra = {"feature_importance_direction": directions} if directions else {}
    if any(set(i) - {"feature", "importance", "direction"} for i in items):
        extra = {"feature_importance": items}
    return data, extra


def classic_eval_to_reports(classic_eval: Mapping[str, Any] | None) -> list[tuple[str, str, Any]]:
    """`main`'s free `classic_eval` JSON -> `[(kind, name, data)]` reports, all named
    `classic_eval`: typed `classification` / `importance` where the data fits the G1 forms
    (validated), and one `custom` report with whatever did not fit."""
    if not classic_eval:
        return []
    ce = dict(classic_eval)
    reports: list[tuple[str, str, Any]] = []
    rest = dict(ce)
    cls, consumed, extra_cls = _classification(ce)
    if cls is not None:
        try:
            reports.append(("classification", CLASSIC_EVAL_REPORT, validate_report("classification", cls)))
            for key in consumed:
                rest.pop(key, None)
            rest.update(extra_cls)
        except ReportError:
            pass
    imp, extra_imp = _importance(ce)
    if imp is not None:
        try:
            reports.append(("importance", CLASSIC_EVAL_REPORT, validate_report("importance", imp)))
            rest.pop("feature_importance", None)
            rest.update(extra_imp)
        except ReportError:
            pass
    if rest:
        reports.append(("custom", CLASSIC_EVAL_REPORT, validate_report("custom", rest)))
    return reports


def reports_to_classic_eval(reports: Iterable[tuple[str, Any]]) -> dict[str, Any] | None:
    """Inverse of `classic_eval_to_reports` from `[(kind, data)]` of the `classic_eval` reports."""
    out: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    seen = False
    for kind, data in reports:
        seen = True
        if kind == "classification":
            out.update(data.get("metrics") or {})
            out["confusion_matrix"] = {"labels": data.get("labels"), "matrix": data.get("confusion_matrix")}
            roc = data.get("roc") or []
            if roc:
                out["roc_curve"] = [{"fpr": f, "tpr": t} for f, t in zip(roc[0]["fpr"], roc[0]["tpr"], strict=False)]
        elif kind == "importance":
            out["feature_importance"] = [
                {"feature": f["name"], "importance": f["importance"]} for f in data.get("features") or []
            ]
        elif kind == "custom" and isinstance(data, Mapping):
            custom = dict(data)
    directions = custom.pop("feature_importance_direction", None)
    out.update(custom)
    if isinstance(directions, Mapping):
        for item in out.get("feature_importance") or []:
            if item.get("feature") in directions:
                item["direction"] = directions[item["feature"]]
    return out if seen else None


# --------------------------------------------------------------------------- external URIs

EXTERNAL_SCHEMES = frozenset({"s3", "gs", "hf", "lakefs"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_external_uri(uri: str) -> str:
    """Literal check of a by-reference artifact URI (s3/gs/hf/lakefs). Nothing ever fetches it:
    a request to a user URL goes through `egress.egress_client`, which owns the address checks."""
    if not isinstance(uri, str) or not uri or uri != uri.strip() or any(c.isspace() for c in uri) or _CONTROL.search(uri):
        raise ValueError("external URI must be a non-empty string without whitespace or control characters")
    parsed = urlsplit(uri)
    if parsed.scheme.lower() not in EXTERNAL_SCHEMES:
        raise ValueError(f"unsupported external URI scheme; allowed: {', '.join(sorted(EXTERNAL_SCHEMES))}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("external URI must not embed credentials")
    if not parsed.hostname:
        raise ValueError("external URI must include a host/bucket")
    if any(seg in {".", ".."} for seg in parsed.path.split("/")):
        raise ValueError("external URI path must not contain traversal segments")
    return uri


def validate_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("sha256 must be a 64-character hexadecimal string")
    return value.lower()
