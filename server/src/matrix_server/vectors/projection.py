"""Pure, deterministic dimensional projection, clustering, and knowledge gap detection (Module 5)."""

from __future__ import annotations

import importlib.util
import math
import random
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

DEFAULT_PROJECTION_ENGINE = "pca-lite"
DEFAULT_EMBEDDING_LABEL = "hash-3gram (demo)"


class EngineUnavailable(RuntimeError):
    """Raised when an optional analytics projection/clustering engine is not installed (ADR-009)."""


class ProjectionEngine(Protocol):
    """Interface for dimensional projection and clustering engines (ADR-009)."""

    name: str
    simulated: bool

    def project(
        self,
        embeddings: list[list[float]],
        n_components: int = 3,
        seed: int = 42,
    ) -> list[list[float]]: ...

    def cluster(
        self,
        embeddings: list[list[float]],
        k: int = 4,
        seed: int = 42,
    ) -> tuple[list[int], list[list[float]]]: ...


class VectorPoint(BaseModel):
    id: str
    x: float
    y: float
    z: float = 0.0
    kind: Literal["query", "document", "gap"]
    cluster_id: int
    text_preview: str
    score: float | None = None
    trace_id: str | None = None
    timestamp: str | None = None


class ClusterSummary(BaseModel):
    cluster_id: int
    label: str
    count: int
    centroid: list[float] = Field(default_factory=list)


class QueryItem(BaseModel):
    id: str
    text: str
    embedding: list[float] | None = None
    score: float | None = None  # e.g. faithfulness or retrieval confidence
    trace_id: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentItem(BaseModel):
    id: str
    text: str
    embedding: list[float] | None = None
    score: float | None = None
    trace_id: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Pure Vector Math & Embedding Utilities ---


def deterministic_text_embedding(text: str, dim: int = 64) -> list[float]:
    """Pure-Python deterministic feature embedding using character 3-grams and Murmur/FNV-style hashing.

    Generates dense, unit-normalized vectors for arbitrary text when precomputed embeddings are absent.
    """
    clean_text = text.lower().strip()
    if not clean_text:
        return [0.0] * dim

    vec = [0.0] * dim
    # Extract word tokens and character trigrams
    words = re.findall(r"\w+", clean_text)
    for word in words:
        h = 2166136261
        for char in word:
            h = (h ^ ord(char)) * 16777619 & 0xFFFFFFFF
        idx = h % dim
        vec[idx] += 1.0

    # Character trigrams for morphological similarity
    padded = f"_{clean_text}_"
    for i in range(len(padded) - 2):
        trigram = padded[i : i + 3]
        h = 2166136261
        for char in trigram:
            h = (h ^ ord(char)) * 16777619 & 0xFFFFFFFF
        idx = h % dim
        vec[idx] += 0.5

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-12:
        return [round(v / norm, 6) for v in vec]
    return [0.0] * dim


def dot_product(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def vector_norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    norm_a = vector_norm(a)
    norm_b = vector_norm(b)
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    val = dot_product(a, b) / (norm_a * norm_b)
    # Clamp to avoid floating point precision escape
    return max(-1.0, min(1.0, val))


def cosine_distance(a: list[float], b: list[float]) -> float:
    return max(0.0, 1.0 - cosine_similarity(a, b))


def euclidean_distance_sq(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))


# --- Principal Component Analysis (PCA) Dimensional Projection ---


def project_embeddings(
    embeddings: list[list[float]],
    n_components: int = 3,
    max_iter: int = 30,
    seed: int = 42,
) -> list[list[float]]:
    """Projects high-dimensional embeddings to 2D or 3D using fast, deterministic PCA via power iteration.

    Returns coordinates of length `n_components` for each point.
    """
    if not embeddings:
        return []

    n = len(embeddings)
    dim = len(embeddings[0])

    if n == 1:
        return [[0.0] * n_components]

    # Handle small original dimension by zero padding
    if dim < n_components:
        padded = [vec + [0.0] * (n_components - dim) for vec in embeddings]
        return [[round(x, 6) for x in row[:n_components]] for row in padded]

    # 1. Center the data matrix
    means = [sum(embeddings[i][d] for i in range(n)) / n for d in range(dim)]
    # A is (n x dim) centered
    matrix = [[embeddings[i][d] - means[d] for d in range(dim)] for i in range(n)]

    components: list[list[float]] = []

    # 2. Power iteration for top n_components eigenvectors
    for c in range(n_components):
        # Deterministic initialization
        rng = random.Random(seed + c * 101)
        v = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        v_norm = vector_norm(v)
        if v_norm < 1e-12:
            v[c % dim] = 1.0
            v_norm = 1.0
        v = [x / v_norm for x in v]

        for _ in range(max_iter):
            # u = A * v (length n)
            u = [dot_product(matrix[i], v) for i in range(n)]
            # w = A^T * u (length dim)
            w = [sum(matrix[i][d] * u[i] for i in range(n)) for d in range(dim)]
            w_norm = vector_norm(w)
            if w_norm < 1e-12:
                break
            v = [x / w_norm for x in w]

        # Enforce deterministic sign convention (max absolute coefficient is positive)
        max_idx = max(range(dim), key=lambda d: abs(v[d]))
        if v[max_idx] < 0:
            v = [-x for x in v]

        components.append(v)

        # Deflate matrix: A <- A - (A * v) v^T
        u = [dot_product(matrix[i], v) for i in range(n)]
        for i in range(n):
            for d in range(dim):
                matrix[i][d] -= u[i] * v[d]

    # 3. Project original centered data onto principal components
    # Re-center original data
    orig_centered = [[embeddings[i][d] - means[d] for d in range(dim)] for i in range(n)]
    projections: list[list[float]] = []
    for i in range(n):
        coords = [round(dot_product(orig_centered[i], comp), 6) for comp in components]
        # Ensure exact length n_components
        while len(coords) < n_components:
            coords.append(0.0)
        projections.append(coords[:n_components])

    return projections


# --- Deterministic K-Means Clustering ---


def cluster_embeddings(
    embeddings: list[list[float]],
    k: int = 4,
    max_iter: int = 40,
    seed: int = 42,
) -> tuple[list[int], list[list[float]]]:
    """Performs deterministic k-means clustering.

    Uses greedy farthest-point initialization to guarantee deterministic and well-dispersed centroids.
    Returns (cluster_assignments, centroids).
    """
    n = len(embeddings)
    if n == 0:
        return [], []
    dim = len(embeddings[0])

    if k >= n:
        # Each point is its own cluster
        return list(range(n)), [list(vec) for vec in embeddings]
    if k <= 1:
        mean_vec = [sum(embeddings[i][d] for i in range(n)) / n for d in range(dim)]
        return [0] * n, [mean_vec]

    # Deterministic greedy farthest-point initialization
    # Centroid 0: point closest to the overall mean
    overall_mean = [sum(embeddings[i][d] for i in range(n)) / n for d in range(dim)]
    first_c_idx = min(range(n), key=lambda i: euclidean_distance_sq(embeddings[i], overall_mean))
    centroids: list[list[float]] = [list(embeddings[first_c_idx])]

    for _ in range(1, k):
        # Pick point furthest from nearest chosen centroid
        best_point_idx = -1
        max_min_dist = -1.0
        for i in range(n):
            min_dist = min(euclidean_distance_sq(embeddings[i], c) for c in centroids)
            if min_dist > max_min_dist:
                max_min_dist = min_dist
                best_point_idx = i
        centroids.append(list(embeddings[best_point_idx]))

    assignments = [0] * n

    for _ in range(max_iter):
        changed = False
        # Assignment step
        for i in range(n):
            best_cluster = min(
                range(k),
                key=lambda c: euclidean_distance_sq(embeddings[i], centroids[c]),
            )
            if assignments[i] != best_cluster:
                assignments[i] = best_cluster
                changed = True

        if not changed:
            break

        # Update step
        for c in range(k):
            members = [embeddings[i] for i in range(n) if assignments[i] == c]
            if members:
                m_len = len(members)
                centroids[c] = [sum(p[d] for p in members) / m_len for d in range(dim)]
            else:
                # Handle empty cluster by picking point furthest from its assigned centroid
                furthest_idx = max(
                    range(n),
                    key=lambda i: euclidean_distance_sq(embeddings[i], centroids[assignments[i]]),
                )
                centroids[c] = list(embeddings[furthest_idx])

    return assignments, centroids


def assign_cluster_labels(
    assignments: list[int],
    texts: list[str],
    k: int,
) -> dict[int, str]:
    """Generates informative topic labels for each cluster by analyzing member texts."""
    labels: dict[int, str] = {}
    for c in range(k):
        members = [texts[i] for i in range(len(texts)) if assignments[i] == c]
        if not members:
            labels[c] = f"Cluster {c + 1}: General"
            continue

        # Extract top frequent terms (length > 3, alpha only)
        word_counts: dict[str, int] = {}
        for text in members:
            words = re.findall(r"[a-zA-Z]{3,}", text.lower())
            for w in words:
                if w not in {"what", "that", "this", "from", "with", "have", "will", "your", "they"}:
                    word_counts[w] = word_counts.get(w, 0) + 1

        if word_counts:
            sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
            top_words = [w.capitalize() for w, _ in sorted_words[:2]]
            labels[c] = f"Cluster {c + 1}: {' / '.join(top_words)}"
        else:
            first_preview = members[0][:30].strip()
            labels[c] = f"Cluster {c + 1}: {first_preview or 'General'}"

    return labels


# --- Knowledge Gap Detection ---


def identify_knowledge_gaps(
    queries: list[QueryItem],
    documents: list[DocumentItem],
    distance_threshold: float = 0.45,
    faithfulness_threshold: float = 0.60,
) -> set[str]:
    """Identifies knowledge gaps where queries lack close document context or exhibit poor faithfulness.

    A query is flagged as a knowledge gap if:
    1. Distance to nearest document embedding > distance_threshold (or no documents exist), OR
    2. Faithfulness/retrieval confidence score < faithfulness_threshold.
    """
    gap_ids: set[str] = set()
    doc_embeddings = [d.embedding or deterministic_text_embedding(d.text) for d in documents]

    for q in queries:
        q_emb = q.embedding or deterministic_text_embedding(q.text)
        is_gap = False

        # Check retrieval semantic distance
        if not doc_embeddings:
            is_gap = True
        else:
            min_dist = min(cosine_distance(q_emb, d_emb) for d_emb in doc_embeddings)
            if min_dist > distance_threshold:
                is_gap = True

        # Check faithfulness/quality score threshold if provided
        if q.score is not None and q.score < faithfulness_threshold:
            is_gap = True

        if is_gap:
            gap_ids.add(q.id)

    return gap_ids


# --- End-to-End Vector Projection Builder ---


def build_vector_projection(
    queries: list[QueryItem],
    documents: list[DocumentItem],
    n_components: int = 3,
    k_clusters: int | None = None,
    distance_threshold: float = 0.45,
    faithfulness_threshold: float = 0.60,
) -> tuple[list[VectorPoint], list[ClusterSummary]]:
    """Builds the complete 2D/3D projection with clustering, knowledge gaps, and metadata."""
    q_embs = [q.embedding or deterministic_text_embedding(q.text) for q in queries]
    d_embs = [d.embedding or deterministic_text_embedding(d.text) for d in documents]
    q_items = [
        q if q.embedding else q.model_copy(update={"embedding": emb})
        for q, emb in zip(queries, q_embs, strict=True)
    ]
    d_items = [
        d if d.embedding else d.model_copy(update={"embedding": emb})
        for d, emb in zip(documents, d_embs, strict=True)
    ]

    gap_ids = identify_knowledge_gaps(
        queries=q_items,
        documents=d_items,
        distance_threshold=distance_threshold,
        faithfulness_threshold=faithfulness_threshold,
    )

    all_items: list[tuple[str, str, Literal["query", "document", "gap"], list[float], float | None, str | None, str | None]] = []

    for q, emb in zip(queries, q_embs, strict=True):
        kind: Literal["query", "document", "gap"] = "gap" if q.id in gap_ids else "query"
        all_items.append((q.id, q.text, kind, emb, q.score, q.trace_id, q.timestamp))

    for d, emb in zip(documents, d_embs, strict=True):
        all_items.append((d.id, d.text, "document", emb, d.score, d.trace_id, d.timestamp))

    if not all_items:
        return [], []

    embeddings = [item[3] for item in all_items]
    texts = [item[1] for item in all_items]
    n = len(all_items)

    # 1. 2D/3D Dimensional Projection
    coords = project_embeddings(embeddings, n_components=n_components)

    # 2. Clustering
    if k_clusters is None:
        k_clusters = min(max(2, int(math.sqrt(n / 2))), 8)
    k_clusters = min(k_clusters, n)

    cluster_assignments, centroids = cluster_embeddings(embeddings, k=k_clusters)
    cluster_labels = assign_cluster_labels(cluster_assignments, texts, k=k_clusters)

    # 3. Project centroids to target dimensions
    centroid_coords = project_embeddings(centroids, n_components=n_components)

    # 4. Construct VectorPoint items
    points: list[VectorPoint] = []
    for i, (item_id, text, kind, _, score, trace_id, ts) in enumerate(all_items):
        coord = coords[i]
        x = coord[0] if len(coord) > 0 else 0.0
        y = coord[1] if len(coord) > 1 else 0.0
        z = coord[2] if len(coord) > 2 else 0.0

        preview = text[:200] + "..." if len(text) > 200 else text

        points.append(
            VectorPoint(
                id=item_id,
                x=x,
                y=y,
                z=z,
                kind=kind,
                cluster_id=cluster_assignments[i],
                text_preview=preview,
                score=score,
                trace_id=trace_id,
                timestamp=ts,
            )
        )

    # 5. Construct ClusterSummary items
    clusters: list[ClusterSummary] = []
    for c in range(k_clusters):
        count = sum(1 for a in cluster_assignments if a == c)
        centroid = centroid_coords[c] if c < len(centroid_coords) else [0.0] * n_components
        clusters.append(
            ClusterSummary(
                cluster_id=c,
                label=cluster_labels.get(c, f"Cluster {c + 1}"),
                count=count,
                centroid=[round(x, 4) for x in centroid],
            )
        )

    return points, clusters


# --- Projection Engine Implementations & Optional Analytics Stubs (ADR-009) ---


class PCALiteEngine:
    """Pure-Python deterministic PCA-lite projection & k-means clustering engine (demo/simulated)."""

    name: str = DEFAULT_PROJECTION_ENGINE
    simulated: bool = True

    def project(
        self,
        embeddings: list[list[float]],
        n_components: int = 3,
        seed: int = 42,
    ) -> list[list[float]]:
        return project_embeddings(embeddings, n_components=n_components, seed=seed)

    def cluster(
        self,
        embeddings: list[list[float]],
        k: int = 4,
        seed: int = 42,
    ) -> tuple[list[int], list[list[float]]]:
        return cluster_embeddings(embeddings, k=k, seed=seed)


class UMAPProjectionEngine:
    """Stub for UMAP projection engine (requires optional worker 'analytics' extra, ADR-009)."""

    name: str = "umap"
    simulated: bool = False

    def _ensure_installed(self) -> None:
        if importlib.util.find_spec("umap") is None:
            raise EngineUnavailable(
                "UMAP projection engine is unavailable: install optional extra 'analytics' (umap-learn)."
            )
        raise EngineUnavailable(
            "UMAP projection runs asynchronously in the analytics worker (ADR-009), not inline in the API."
        )

    def project(
        self,
        embeddings: list[list[float]],
        n_components: int = 3,
        seed: int = 42,
    ) -> list[list[float]]:
        self._ensure_installed()
        return []  # pragma: no cover

    def cluster(
        self,
        embeddings: list[list[float]],
        k: int = 4,
        seed: int = 42,
    ) -> tuple[list[int], list[list[float]]]:
        self._ensure_installed()
        return [], []  # pragma: no cover


class HDBSCANClusterEngine:
    """Stub for HDBSCAN clustering engine (requires optional worker 'analytics' extra, ADR-009)."""

    name: str = "hdbscan"
    simulated: bool = False

    def _ensure_installed(self) -> None:
        if importlib.util.find_spec("hdbscan") is None:
            raise EngineUnavailable(
                "HDBSCAN clustering engine is unavailable: install optional extra 'analytics' (hdbscan)."
            )
        raise EngineUnavailable(
            "HDBSCAN clustering runs asynchronously in the analytics worker (ADR-009), not inline in the API."
        )

    def project(
        self,
        embeddings: list[list[float]],
        n_components: int = 3,
        seed: int = 42,
    ) -> list[list[float]]:
        self._ensure_installed()
        return []  # pragma: no cover

    def cluster(
        self,
        embeddings: list[list[float]],
        k: int = 4,
        seed: int = 42,
    ) -> tuple[list[int], list[list[float]]]:
        self._ensure_installed()
        return [], []  # pragma: no cover


def get_projection_engine(name: str = DEFAULT_PROJECTION_ENGINE) -> ProjectionEngine:
    """Returns a ProjectionEngine instance by name ('pca-lite', 'umap', 'hdbscan')."""
    norm = name.strip().lower()
    if norm in ("pca-lite", "pca", "default"):
        return PCALiteEngine()
    if norm == "umap":
        return UMAPProjectionEngine()
    if norm == "hdbscan":
        return HDBSCANClusterEngine()
    raise EngineUnavailable(f"Unknown projection engine '{name}'. Supported: pca-lite, umap, hdbscan.")

