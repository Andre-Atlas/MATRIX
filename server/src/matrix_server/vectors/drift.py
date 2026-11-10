"""Statistical and Semantic Drift Monitoring (Module 5).

Calculates Jensen-Shannon Divergence (JSD), Population Stability Index (PSI),
and Centroid Drift between baseline/reference and production windows.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from matrix_server.vectors.projection import cosine_distance

DriftLevel = Literal["healthy", "warning", "critical"]

# Reference thresholds according to blueprint specification
HEALTHY_THRESHOLD = 0.10
WARNING_THRESHOLD = 0.25


class DriftMetrics(BaseModel):
    jsd: float = Field(description="Jensen-Shannon Divergence between cluster distributions [0, 1]")
    psi: float = Field(description="Population Stability Index for cluster distribution shift")
    centroid_drift: float = Field(description="Cosine distance between mean embedding vectors [0, 2]")
    drift_level: DriftLevel = Field(description="'healthy' | 'warning' | 'critical'")


class TemporalDriftPoint(BaseModel):
    timestamp: str
    jsd: float
    psi: float
    centroid_drift: float
    drift_level: DriftLevel
    sample_size: int = 0


class DriftReport(BaseModel):
    status: DriftLevel
    metrics: DriftMetrics
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "healthy_max": HEALTHY_THRESHOLD,
            "warning_max": WARNING_THRESHOLD,
        }
    )
    cluster_distribution: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {"reference": {}, "production": {}}
    )
    history: list[TemporalDriftPoint] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Core Drift Algorithms ---


def compute_jsd(
    p: list[float],
    q: list[float],
    epsilon: float = 1e-9,
) -> float:
    """Computes Jensen-Shannon Divergence (JSD) using base-2 logarithm.

    Result is bounded strictly in [0.0, 1.0], where 0 indicates identical distributions
    and 1 indicates completely disjoint distributions.
    """
    if not p or not q or len(p) != len(q):
        return 0.0

    sum_p = sum(p)
    sum_q = sum(q)

    if sum_p <= 0.0 and sum_q <= 0.0:
        return 0.0

    # Handle one empty distribution
    if sum_p <= 0.0 or sum_q <= 0.0:
        return 1.0

    # Normalize to probabilities
    k = len(p)
    p_prob = [val / sum_p for val in p]
    q_prob = [val / sum_q for val in q]

    # Add epsilon smoothing and re-normalize
    p_smooth = [(val + epsilon) / (1.0 + k * epsilon) for val in p_prob]
    q_smooth = [(val + epsilon) / (1.0 + k * epsilon) for val in q_prob]

    # M = 0.5 * (P + Q)
    m = [0.5 * (p_smooth[i] + q_smooth[i]) for i in range(k)]

    # D_KL(P || M) and D_KL(Q || M) using base 2
    kl_pm = sum(p_smooth[i] * math.log2(p_smooth[i] / m[i]) for i in range(k))
    kl_qm = sum(q_smooth[i] * math.log2(q_smooth[i] / m[i]) for i in range(k))

    jsd = 0.5 * kl_pm + 0.5 * kl_qm
    return round(max(0.0, min(1.0, jsd)), 6)


def compute_psi(
    actual: list[float],
    expected: list[float],
    epsilon: float = 1e-4,
) -> float:
    """Computes Population Stability Index (PSI) between production (actual) and reference (expected).

    PSI = sum((actual_i - expected_i) * ln(actual_i / expected_i))
    """
    if not actual or not expected or len(actual) != len(expected):
        return 0.0

    sum_a = sum(actual)
    sum_e = sum(expected)

    if sum_a <= 0.0 and sum_e <= 0.0:
        return 0.0
    if sum_a <= 0.0 or sum_e <= 0.0:
        return 1.0

    k = len(actual)
    a_prob = [val / sum_a for val in actual]
    e_prob = [val / sum_e for val in expected]

    # Smooth to prevent log(0) or division by 0
    a_smooth = [(val + epsilon) / (1.0 + k * epsilon) for val in a_prob]
    e_smooth = [(val + epsilon) / (1.0 + k * epsilon) for val in e_prob]

    psi = 0.0
    for i in range(k):
        diff = a_smooth[i] - e_smooth[i]
        ratio = a_smooth[i] / e_smooth[i]
        psi += diff * math.log(ratio)

    return round(max(0.0, psi), 6)


def classify_drift(jsd: float) -> DriftLevel:
    """Classifies drift severity based on Jensen-Shannon Divergence.

    - "healthy": JSD < 0.10
    - "warning": 0.10 <= JSD < 0.25
    - "critical": JSD >= 0.25
    """
    if jsd < HEALTHY_THRESHOLD:
        return "healthy"
    elif jsd < WARNING_THRESHOLD:
        return "warning"
    else:
        return "critical"


def compute_centroid_drift(
    ref_vectors: list[list[float]],
    prod_vectors: list[list[float]],
) -> float:
    """Computes centroid semantic drift as cosine distance between mean vectors.

    drift = 1 - cosine_similarity(mean(ref), mean(prod))
    """
    if not ref_vectors or not prod_vectors:
        return 0.0

    dim = len(ref_vectors[0])
    n_ref = len(ref_vectors)
    n_prod = len(prod_vectors)

    ref_mean = [sum(v[d] for v in ref_vectors) / n_ref for d in range(dim)]
    prod_mean = [sum(v[d] for v in prod_vectors) / n_prod for d in range(dim)]

    return round(cosine_distance(ref_mean, prod_mean), 6)


def calculate_distribution(
    cluster_assignments: list[int],
    num_clusters: int,
) -> list[float]:
    """Computes normalized distribution array of length num_clusters."""
    if not cluster_assignments or num_clusters <= 0:
        return [0.0] * num_clusters

    counts = [0] * num_clusters
    for a in cluster_assignments:
        if 0 <= a < num_clusters:
            counts[a] += 1

    total = sum(counts)
    if total == 0:
        return [0.0] * num_clusters
    return [round(c / total, 6) for c in counts]


def compute_drift_report(
    reference_embeddings: list[list[float]],
    production_embeddings: list[list[float]],
    reference_clusters: list[int],
    production_clusters: list[int],
    num_clusters: int,
    history_points: list[TemporalDriftPoint] | None = None,
) -> DriftReport:
    """Computes full drift monitoring report comparing reference and production windows."""
    ref_dist = calculate_distribution(reference_clusters, num_clusters)
    prod_dist = calculate_distribution(production_clusters, num_clusters)

    jsd = compute_jsd(ref_dist, prod_dist)
    psi = compute_psi(actual=prod_dist, expected=ref_dist)
    c_drift = compute_centroid_drift(reference_embeddings, production_embeddings)
    status = classify_drift(jsd)

    metrics = DriftMetrics(
        jsd=jsd,
        psi=psi,
        centroid_drift=c_drift,
        drift_level=status,
    )

    cluster_dist_map = {
        "reference": {str(i): ref_dist[i] for i in range(num_clusters)},
        "production": {str(i): prod_dist[i] for i in range(num_clusters)},
    }

    return DriftReport(
        status=status,
        metrics=metrics,
        cluster_distribution=cluster_dist_map,
        history=history_points or [],
        metadata={
            "reference_sample_count": len(reference_embeddings),
            "production_sample_count": len(production_embeddings),
            "num_clusters": num_clusters,
        },
    )
