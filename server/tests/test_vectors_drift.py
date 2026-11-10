"""Comprehensive tests for Vector Projection, Knowledge Gap identification, and Drift Monitoring."""

import math

import pytest
from fastapi.testclient import TestClient
from otlp_payloads import full_request

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.vectors.drift import (
    HEALTHY_THRESHOLD,
    WARNING_THRESHOLD,
    calculate_distribution,
    classify_drift,
    compute_centroid_drift,
    compute_drift_report,
    compute_jsd,
    compute_psi,
)
from matrix_server.vectors.projection import (
    DocumentItem,
    QueryItem,
    assign_cluster_labels,
    build_vector_projection,
    cluster_embeddings,
    cosine_distance,
    cosine_similarity,
    deterministic_text_embedding,
    identify_knowledge_gaps,
    project_embeddings,
)

ADMIN = {"Authorization": "Bearer adm"}


def _create_project(client: TestClient, name: str) -> dict[str, str]:
    resp = client.post("/api/v1/admin/projects", json={"name": name}, headers=ADMIN)
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['api_key']}"}


@pytest.fixture
def client_env(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/vec.db", admin_token="adm"))
    client = TestClient(app)
    auth = _create_project(client, "vector-test-proj")
    return client, auth


# ==============================================================================
# 1. Dimensional Projection (PCA) Tests
# ==============================================================================


def test_project_embeddings_dimensions():
    # 5 vectors of dimension 8
    vecs = [
        [1.0, 0.5, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0],
        [0.9, 0.6, 0.1, 0.2, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0, 1.0, 0.8, 0.2, 0.1],
        [0.0, 0.0, 0.0, 0.0, 0.9, 0.7, 0.3, 0.0],
        [0.5, 0.5, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0],
    ]

    proj_3d = project_embeddings(vecs, n_components=3)
    assert len(proj_3d) == 5
    assert all(len(p) == 3 for p in proj_3d)

    proj_2d = project_embeddings(vecs, n_components=2)
    assert len(proj_2d) == 5
    assert all(len(p) == 2 for p in proj_2d)


def test_project_embeddings_determinism():
    vecs = [[(i + 1) * (j + 1) * 0.1 for j in range(16)] for i in range(10)]
    run1 = project_embeddings(vecs, n_components=3, seed=42)
    run2 = project_embeddings(vecs, n_components=3, seed=42)
    assert run1 == run2


def test_project_embeddings_edge_cases():
    assert project_embeddings([], n_components=3) == []

    one = project_embeddings([[1.0, 2.0, 3.0]], n_components=3)
    assert one == [[0.0, 0.0, 0.0]]

    # Low-dimensional input (dim=1) projected to 3D
    low_dim = [[1.0], [2.0], [3.0]]
    res = project_embeddings(low_dim, n_components=3)
    assert len(res) == 3
    assert all(len(r) == 3 for r in res)


# ==============================================================================
# 2. Clustering & Topic Labelling Tests
# ==============================================================================


def test_cluster_embeddings_partitioning():
    # Two distinct clusters
    c1 = [[10.0 + i * 0.1, 10.0 + i * 0.1] for i in range(5)]
    c2 = [[-10.0 - i * 0.1, -10.0 - i * 0.1] for i in range(5)]
    data = c1 + c2

    assignments, centroids = cluster_embeddings(data, k=2)
    assert len(assignments) == 10
    assert len(centroids) == 2

    # Group 1 points share assignment, Group 2 share different assignment
    assert len(set(assignments[:5])) == 1
    assert len(set(assignments[5:])) == 1
    assert assignments[0] != assignments[5]


def test_cluster_embeddings_determinism():
    data = [[float(i * j) for j in range(8)] for i in range(12)]
    a1, c1 = cluster_embeddings(data, k=3, seed=99)
    a2, c2 = cluster_embeddings(data, k=3, seed=99)
    assert a1 == a2
    assert c1 == c2


def test_cluster_labels():
    assignments = [0, 0, 1, 1]
    texts = [
        "Refund request for order #123",
        "How do I cancel and request refund?",
        "Configure OpenTelemetry Python SDK",
        "Set up OpenTelemetry spans and traces",
    ]
    labels = assign_cluster_labels(assignments, texts, k=2)
    assert len(labels) == 2
    assert "Refund" in labels[0]
    assert "Opentelemetry" in labels[1]


def test_deterministic_text_embedding():
    emb1 = deterministic_text_embedding("matrix llm observability", dim=32)
    emb2 = deterministic_text_embedding("matrix llm observability", dim=32)
    emb3 = deterministic_text_embedding("completely different topic", dim=32)

    assert len(emb1) == 32
    assert emb1 == emb2
    assert emb1 != emb3
    # Check unit norm
    norm = math.sqrt(sum(x * x for x in emb1))
    assert math.isclose(norm, 1.0, rel_tol=1e-3)


# ==============================================================================
# 3. Knowledge Gap Detection Tests
# ==============================================================================


def test_identify_knowledge_gaps():
    doc1 = DocumentItem(
        id="d1",
        text="FastAPI authentication with Bearer tokens and API keys.",
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    doc2 = DocumentItem(
        id="d2",
        text="ClickHouse column store for high-throughput span ingestion.",
        embedding=[0.0, 1.0, 0.0, 0.0],
    )

    # Q1: Semantically identical to doc1 -> not a gap
    q1 = QueryItem(id="q1", text="How to do API key auth?", embedding=[0.99, 0.01, 0.0, 0.0], score=0.95)
    # Q2: Orthogonal topic (no relevant docs) -> gap
    q2 = QueryItem(
        id="q2",
        text="Quantum mechanics simulation on GPUs.",
        embedding=[0.0, 0.0, 1.0, 0.0],
        score=0.90,
    )
    # Q3: Semantically close to doc2, but faithfulness score is below threshold -> gap
    q3 = QueryItem(id="q3", text="ClickHouse span store query.", embedding=[0.0, 0.98, 0.0, 0.0], score=0.45)

    gaps = identify_knowledge_gaps(
        queries=[q1, q2, q3],
        documents=[doc1, doc2],
        distance_threshold=0.45,
        faithfulness_threshold=0.60,
    )

    assert "q1" not in gaps
    assert "q2" in gaps
    assert "q3" in gaps


def test_build_vector_projection_output_schema():
    queries = [
        QueryItem(id="q1", text="What is MATRIX?", score=0.9, trace_id="t1", timestamp="2026-11-10T00:00:00Z"),
        QueryItem(id="q2", text="What is quantum gravity?", score=0.2, trace_id="t2", timestamp="2026-11-10T01:00:00Z"),
    ]
    docs = [
        DocumentItem(id="d1", text="MATRIX is an LLMOps observability platform.", trace_id="t1", timestamp="2026-11-10T00:00:00Z")
    ]

    points, clusters = build_vector_projection(queries, docs, n_components=3, k_clusters=2)

    assert len(points) == 3
    assert len(clusters) == 2

    # Check fields of VectorPoint
    for pt in points:
        assert isinstance(pt.id, str)
        assert isinstance(pt.x, float)
        assert isinstance(pt.y, float)
        assert isinstance(pt.z, float)
        assert pt.kind in ("query", "document", "gap")
        assert isinstance(pt.cluster_id, int)
        assert isinstance(pt.text_preview, str)

    # Check that q2 became a gap (score 0.2 and irrelevant topic)
    pt_q2 = next(p for p in points if p.id == "q2")
    assert pt_q2.kind == "gap"

    pt_d1 = next(p for p in points if p.id == "d1")
    assert pt_d1.kind == "document"


# ==============================================================================
# 4. Statistical & Semantic Drift Tests
# ==============================================================================


def test_compute_jsd():
    p = [0.5, 0.5]
    q = [0.5, 0.5]
    assert compute_jsd(p, q) == 0.0

    # Symmetry
    dist_a = [0.8, 0.1, 0.1]
    dist_b = [0.2, 0.4, 0.4]
    jsd_ab = compute_jsd(dist_a, dist_b)
    jsd_ba = compute_jsd(dist_b, dist_a)
    assert math.isclose(jsd_ab, jsd_ba, abs_tol=1e-5)
    assert 0.0 < jsd_ab <= 1.0


def test_compute_psi():
    ref = [0.25, 0.25, 0.25, 0.25]
    prod = [0.25, 0.25, 0.25, 0.25]
    assert compute_psi(actual=prod, expected=ref) == 0.0

    shifted_prod = [0.60, 0.20, 0.10, 0.10]
    psi = compute_psi(actual=shifted_prod, expected=ref)
    assert psi > 0.10


def test_classify_drift():
    assert classify_drift(0.05) == "healthy"
    assert classify_drift(HEALTHY_THRESHOLD - 0.001) == "healthy"
    assert classify_drift(HEALTHY_THRESHOLD) == "warning"
    assert classify_drift(0.18) == "warning"
    assert classify_drift(WARNING_THRESHOLD) == "critical"
    assert classify_drift(0.35) == "critical"


def test_compute_centroid_drift():
    v1 = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    v2 = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert compute_centroid_drift(v1, v2) == 0.0

    # Orthogonal vectors mean cosine similarity 0 -> cosine distance 1.0
    v3 = [[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
    drift = compute_centroid_drift(v1, v3)
    assert math.isclose(drift, 1.0, abs_tol=1e-4)


def test_compute_drift_report():
    ref_vecs = [[1.0, 0.0], [0.9, 0.1]]
    prod_vecs = [[0.8, 0.2], [0.7, 0.3]]
    ref_clusters = [0, 1]
    prod_clusters = [0, 1]

    report = compute_drift_report(
        reference_embeddings=ref_vecs,
        production_embeddings=prod_vecs,
        reference_clusters=ref_clusters,
        production_clusters=prod_clusters,
        num_clusters=2,
    )
    assert report.status in ("healthy", "warning", "critical")
    assert 0.0 <= report.metrics.jsd <= 1.0
    assert report.thresholds["healthy_max"] == HEALTHY_THRESHOLD
    assert report.thresholds["warning_max"] == WARNING_THRESHOLD


def test_cosine_helpers():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_distance([1, 0], [1, 0]) == 0.0
    assert math.isclose(cosine_distance([1, 0], [0, 1]), 1.0, abs_tol=1e-5)


def test_calculate_distribution():
    assignments = [0, 0, 1, 2]
    dist = calculate_distribution(assignments, num_clusters=3)
    assert dist == [0.5, 0.25, 0.25]


# ==============================================================================
# 5. API Endpoints Tests
# ==============================================================================


def test_vectors_api_requires_auth(client_env):
    client, _ = client_env
    assert client.get("/api/v1/vectors/projection").status_code == 401
    assert client.get("/api/v1/vectors/drift").status_code == 401


def test_get_vector_projection_endpoint(client_env):
    client, auth = client_env
    resp = client.get("/api/v1/vectors/projection", headers=auth)
    assert resp.status_code == 200
    data = resp.json()

    assert "points" in data
    assert "clusters" in data
    assert "metadata" in data

    meta = data["metadata"]
    assert meta["total_points"] > 0
    assert meta["total_queries"] >= 0
    assert meta["total_documents"] >= 0

    points = data["points"]
    assert len(points) <= 100
    pt0 = points[0]
    assert set(pt0.keys()) >= {
        "id",
        "x",
        "y",
        "z",
        "kind",
        "cluster_id",
        "text_preview",
        "score",
        "trace_id",
        "timestamp",
    }


def test_get_vector_projection_filters(client_env):
    client, auth = client_env

    # 1. Test limit
    resp_limit = client.get("/api/v1/vectors/projection?limit=5", headers=auth)
    assert resp_limit.status_code == 200
    assert len(resp_limit.json()["points"]) <= 5

    # 2. Test kind filter
    resp_docs = client.get("/api/v1/vectors/projection?kind=document", headers=auth)
    assert resp_docs.status_code == 200
    pts_docs = resp_docs.json()["points"]
    assert all(p["kind"] == "document" for p in pts_docs)

    resp_queries = client.get("/api/v1/vectors/projection?kind=query", headers=auth)
    assert resp_queries.status_code == 200
    pts_q = resp_queries.json()["points"]
    assert all(p["kind"] == "query" for p in pts_q)

    # 3. Test cluster filter
    resp_c0 = client.get("/api/v1/vectors/projection?cluster=0", headers=auth)
    assert resp_c0.status_code == 200
    pts_c0 = resp_c0.json()["points"]
    assert all(p["cluster_id"] == 0 for p in pts_c0)


def test_get_vector_drift_endpoint(client_env):
    client, auth = client_env
    resp = client.get(
        "/api/v1/vectors/drift?reference_window_days=7&production_window_days=1",
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] in ("healthy", "warning", "critical")
    assert "metrics" in data
    metrics = data["metrics"]
    assert 0.0 <= metrics["jsd"] <= 1.0
    assert metrics["psi"] >= 0.0
    assert metrics["centroid_drift"] >= 0.0
    assert metrics["drift_level"] in ("healthy", "warning", "critical")

    assert "thresholds" in data
    assert data["thresholds"]["healthy_max"] == HEALTHY_THRESHOLD
    assert data["thresholds"]["warning_max"] == WARNING_THRESHOLD

    assert "cluster_distribution" in data
    assert "reference" in data["cluster_distribution"]
    assert "production" in data["cluster_distribution"]

    assert "history" in data
    assert len(data["history"]) > 0
    h0 = data["history"][0]
    assert set(h0.keys()) >= {"timestamp", "jsd", "psi", "centroid_drift", "drift_level", "sample_size"}


def test_projection_with_ingested_trace(client_env):
    client, auth = client_env

    # Ingest a full OTLP trace (contains AGENT, LLM, RETRIEVER spans)
    resp_ingest = client.post(
        "/v1/traces",
        content=full_request().SerializeToString(),
        headers={"Content-Type": "application/x-protobuf", **auth},
    )
    assert resp_ingest.status_code == 200

    # Query projection endpoint
    resp = client.get("/api/v1/vectors/projection", headers=auth)
    assert resp.status_code == 200
    data = resp.json()

    # Verify trace spans are reflected
    trace_ids = {p["trace_id"] for p in data["points"] if p.get("trace_id")}
    assert len(trace_ids) > 0
