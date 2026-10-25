from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


TEST_API_KEY = "test-api-key"


@pytest.fixture(autouse=True)
def set_test_api_key(monkeypatch):
    """Configure a dedicated API key for each test."""
    monkeypatch.setenv("FRAUD_API_KEY", TEST_API_KEY)


def make_client(tmp_path):
    app = create_app(tmp_path / "api-test.sqlite3")
    return TestClient(
        app,
        headers={"X-API-Key": TEST_API_KEY},
    )


def transaction_payload(**overrides):
    payload = {
        "transaction_id": "txn-api-001",
        "customer_id": "customer-api-001",
        "amount": "12500.00",
        "currency": "NGN",
        "timestamp": datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc).isoformat(),
        "merchant_category": "groceries",
        "channel": "online",
        "device_id": "device-a",
        "country_code": "NG",
        "created_at": datetime(2026, 10, 1, 10, 1, tzinfo=timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_health_endpoint_is_public(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protected_endpoint_rejects_missing_api_key(tmp_path):
    client = make_client(tmp_path)

    response = client.get(
        "/transactions",
        headers={"X-API-Key": ""},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["www-authenticate"] == "ApiKey"


def test_protected_endpoint_rejects_invalid_api_key(tmp_path):
    client = make_client(tmp_path)

    response = client.get(
        "/transactions",
        headers={"X-API-Key": "wrong-api-key"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}


def test_protected_endpoint_accepts_valid_api_key(tmp_path):
    client = make_client(tmp_path)

    response = client.get(
        "/transactions",
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 200


def test_demo_mode_allows_requests_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAUD_DEMO_MODE", "true")
    client = TestClient(create_app(tmp_path / "demo-test.sqlite3"))

    response = client.get("/transactions")

    assert response.status_code == 200
    assert response.json() == []


def test_oversized_request_body_returns_413(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAUD_DEMO_MODE", "true")
    client = TestClient(create_app(tmp_path / "body-limit-test.sqlite3"))

    response = client.post(
        "/transactions",
        content=b"x" * 65_537,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "Request body too large. Maximum size is 65536 bytes."
    )


def test_request_body_at_limit_is_not_rejected_by_size_middleware(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FRAUD_DEMO_MODE", "true")
    client = TestClient(create_app(tmp_path / "body-limit-boundary.sqlite3"))

    response = client.post(
        "/transactions",
        content=b"x" * 65_536,
        headers={"Content-Type": "application/json"},
    )

    # The payload is invalid JSON for a transaction, but its size alone
    # should not cause a 413 response.
    assert response.status_code != 413


def test_demo_mode_rate_limit_returns_429(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAUD_DEMO_MODE", "true")
    client = TestClient(create_app(tmp_path / "rate-limit-test.sqlite3"))

    limiter = client.app.state.rate_limiter
    limiter.allow = lambda client_id, category, limit: False

    response = client.get("/transactions")

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Rate limit exceeded. Please try again shortly."
    )
    assert response.headers["Retry-After"] == "60"


def test_protected_endpoint_fails_when_api_key_is_not_configured(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("FRAUD_API_KEY")
    client = make_client(tmp_path)

    response = client.get("/transactions")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "API authentication is not configured"
    }


def test_ingest_transaction_and_get_it(tmp_path):
    client = make_client(tmp_path)
    response = client.post("/transactions", json=transaction_payload())
    assert response.status_code == 201
    assert response.json()["created"] is True

    fetched = client.get("/transactions/txn-api-001")
    assert fetched.status_code == 200
    assert fetched.json()["transaction_id"] == "txn-api-001"


def test_identical_ingestion_retry_is_idempotent(tmp_path):
    client = make_client(tmp_path)
    payload = transaction_payload()
    assert client.post("/transactions", json=payload).status_code == 201

    retry = client.post("/transactions", json=payload)
    assert retry.status_code == 200
    assert retry.json()["created"] is False


def test_conflicting_duplicate_returns_409(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())

    conflicting = transaction_payload(amount="99999.00")
    response = client.post("/transactions", json=conflicting)

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_transaction_conflict"


def test_invalid_transaction_payload_returns_422(tmp_path):
    client = make_client(tmp_path)
    payload = transaction_payload(amount="-1")

    response = client.post("/transactions", json=payload)

    assert response.status_code == 422


def test_list_transactions_filters_customer_currency_and_channel(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())
    client.post(
        "/transactions",
        json=transaction_payload(
            transaction_id="txn-api-002",
            customer_id="other-customer",
            currency="USD",
            channel="card",
        ),
    )

    response = client.get(
        "/transactions",
        params={"customer_id": "customer-api-001", "currency": "NGN"},
    )

    assert response.status_code == 200
    assert [item["transaction_id"] for item in response.json()] == [
        "txn-api-001"
    ]


def test_missing_transaction_returns_404(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/transactions/missing")

    assert response.status_code == 404


def test_evaluate_transaction_and_retrieve_assessment(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())

    response = client.post("/transactions/txn-api-001/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == "txn-api-001"
    assert 0 <= body["risk_score"] <= 100
    assert body["risk_band"] in {"low", "medium", "high"}
    assert isinstance(body["rule_results"], list)

    fetched = client.get("/assessments/txn-api-001")
    assert fetched.status_code == 200


def test_generate_demo_batch_creates_ten_assessed_transactions(tmp_path):
    client = make_client(tmp_path)

    response = client.post("/transactions/demo/generate")

    assert response.status_code == 200
    body = response.json()

    assert body["generated_count"] == 10
    assert body["assessed_count"] == 10

    transactions = body["transactions"]
    assessments = body["assessments"]

    assert len(transactions) == 10
    assert len(assessments) == 10

    transaction_ids = [item["transaction_id"] for item in transactions]
    assessment_ids = [item["transaction_id"] for item in assessments]

    assert len(set(transaction_ids)) == 10
    assert set(transaction_ids) == set(assessment_ids)

    # Verify the batch was persisted and is retrievable through the existing API.
    stored_transactions = client.get("/transactions?limit=1000")
    stored_assessments = client.get("/assessments?limit=1000")

    assert stored_transactions.status_code == 200
    assert stored_assessments.status_code == 200
    assert set(
        item["transaction_id"] for item in stored_transactions.json()
    ).issuperset(transaction_ids)
    assert set(
        item["transaction_id"] for item in stored_assessments.json()
    ).issuperset(transaction_ids)

    # Scores must come from the real rule engine, not fabricated values.
    for assessment in assessments:
        assert 0 <= assessment["risk_score"] <= 100
        assert assessment["risk_band"] in {"low", "medium", "high"}
        assert isinstance(assessment["rule_results"], list)
        assert len(assessment["rule_results"]) == 5


def test_demo_batch_triggers_real_fraud_rules(tmp_path):
    client = make_client(tmp_path)

    response = client.post("/transactions/demo/generate")

    assert response.status_code == 200
    assessments = response.json()["assessments"]

    triggered_rules = {
        rule["rule_id"]
        for assessment in assessments
        for rule in assessment["rule_results"]
        if rule["status"] == "triggered"
    }

    assert "high_amount" in triggered_rules
    assert "velocity" in triggered_rules
    assert "unfamiliar_device" in triggered_rules
    assert "unusual_geography" in triggered_rules

def test_evaluation_of_missing_transaction_returns_404(tmp_path):
    client = make_client(tmp_path)

    response = client.post("/transactions/missing/evaluate")

    assert response.status_code == 404


def test_assessment_filters_and_invalid_score_range(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())
    client.post("/transactions/txn-api-001/evaluate")

    response = client.get(
        "/assessments",
        params={"risk_band": "high", "min_score": 60},
    )
    assert response.status_code == 200

    invalid = client.get(
        "/assessments",
        params={"min_score": 80, "max_score": 20},
    )
    assert invalid.status_code == 422


def test_create_and_update_case(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())

    evaluation = client.post("/transactions/txn-api-001/evaluate")
    assessment_id = evaluation.json()["assessment_id"]

    created = client.post(
        "/cases",
        json={
            "transaction_id": "txn-api-001",
            "assessment_id": assessment_id,
            "notes": "Needs review",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    updated = client.patch(
        f"/cases/{case_id}",
        json={"status": "in_review", "notes": "Review started"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_review"
    assert updated.json()["notes"] == "Review started"


def test_case_validation_and_not_found_errors(tmp_path):
    client = make_client(tmp_path)

    missing_transaction = client.post(
        "/cases",
        json={"transaction_id": "missing"},
    )
    assert missing_transaction.status_code == 404

    missing_case = client.patch("/cases/999", json={"status": "closed"})
    assert missing_case.status_code == 404

    invalid_status = client.patch("/cases/1", json={"status": "invalid"})
    assert invalid_status.status_code == 422


def test_case_list_filters_by_status_and_transaction(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())
    client.post(
        "/transactions",
        json=transaction_payload(transaction_id="txn-api-002"),
    )
    client.post("/cases", json={"transaction_id": "txn-api-001"})
    client.post("/cases", json={"transaction_id": "txn-api-002"})

    response = client.get(
        "/cases",
        params={"status": "open", "transaction_id": "txn-api-002"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["transaction_id"] == "txn-api-002"


def test_empty_case_update_is_rejected(tmp_path):
    client = make_client(tmp_path)
    client.post("/transactions", json=transaction_payload())

    case = client.post(
        "/cases",
        json={"transaction_id": "txn-api-001"},
    ).json()

    response = client.patch(f"/cases/{case['case_id']}", json={})

    assert response.status_code == 422