
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import pytest

from app.models.transaction import Transaction
from app.persistence import Database, DuplicateTransactionError, PersistenceRepository
from app.rules.config import RuleConfig
from app.rules.engine import RuleEngine


@pytest.fixture
def repo(tmp_path):
    return PersistenceRepository(Database(tmp_path / "test.sqlite3"))


def transaction(
    transaction_id="tx-1",
    customer_id="customer-1",
    *,
    minute=0,
    amount="125.00",
    device="device-a",
    country="NG",
):
    return Transaction(
        transaction_id=transaction_id,
        customer_id=customer_id,
        amount=Decimal(amount),
        currency="NGN",
        timestamp=datetime(2026, 1, 1, 12, minute, tzinfo=timezone.utc),
        merchant_category="groceries",
        channel="online",
        device_id=device,
        country_code=country,
    )


def test_transaction_round_trip(repo):
    item = transaction()
    assert repo.save_transaction(item) is True
    fetched = repo.get_transaction(item.transaction_id)
    assert fetched == item
    assert isinstance(fetched.amount, Decimal)


def test_identical_duplicate_is_idempotent(repo):
    item = transaction()
    assert repo.save_transaction(item) is True
    assert repo.save_transaction(item) is False
    assert len(repo.list_transactions()) == 1


def test_concurrent_identical_transactions_are_idempotent(repo):
    item = transaction()
    barrier = Barrier(2)

    def save_transaction():
        barrier.wait()
        return repo.save_transaction(item)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(save_transaction),
            executor.submit(save_transaction),
        ]
        results = [future.result() for future in futures]

    assert sorted(results) == [False, True]
    assert len(repo.list_transactions()) == 1


def test_duplicate_id_with_different_payload_is_rejected(repo):
    repo.save_transaction(transaction())
    with pytest.raises(DuplicateTransactionError):
        repo.save_transaction(transaction(amount="999.00"))


def test_history_is_customer_scoped_and_oldest_first(repo):
    repo.save_transaction(transaction("tx-2", minute=2))
    repo.save_transaction(transaction("tx-1", minute=0))
    repo.save_transaction(
        transaction("tx-other", customer_id="customer-2", minute=1)
    )
    history = repo.get_customer_history("customer-1")
    assert [item.transaction_id for item in history] == ["tx-1", "tx-2"]


def test_history_excludes_equal_or_later_timestamps(repo):
    repo.save_transaction(transaction("tx-1", minute=0))
    repo.save_transaction(transaction("tx-2", minute=1))
    before = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    assert [
        item.transaction_id
        for item in repo.get_customer_history("customer-1", before=before)
    ] == ["tx-1"]


def test_list_transaction_limit_validation(repo):
    with pytest.raises(ValueError):
        repo.list_transactions(limit=0)


def test_rule_config_is_persisted(repo):
    config = RuleConfig(
        enabled_rules=frozenset({"high_amount", "velocity"}),
        velocity_max_transactions=4,
    )
    assert repo.save_rules(config) == 5
    rules = {rule["rule_id"]: rule for rule in repo.list_rules()}
    assert rules["high_amount"]["enabled"] is True
    assert rules["unusual_amount"]["enabled"] is False
    assert rules["velocity"]["configuration"]["max_transactions"] == 4
    repo.save_rules(config)
    assert len(repo.list_rules()) == 5


def test_assessment_and_rule_explanations_are_persisted(repo):
    item = transaction(amount="1500000.00")
    repo.save_transaction(item)
    assessment = RuleEngine().evaluate(item)
    assessment_id = repo.save_assessment(assessment)
    fetched = repo.get_assessment(item.transaction_id)
    assert fetched["assessment_id"] == assessment_id
    assert fetched["risk_score"] == assessment.risk_score
    assert fetched["risk_band"] == assessment.risk_band.value
    assert len(fetched["rule_results"]) == 5
    assert any(row["explanation"] for row in fetched["rule_results"])


def test_repeated_identical_assessment_returns_existing_id(repo):
    item = transaction(amount="1500000.00")
    repo.save_transaction(item)
    assessment = RuleEngine().evaluate(item)
    first = repo.save_assessment(assessment)
    assert repo.save_assessment(assessment) == first


def test_assessment_requires_persisted_transaction(repo):
    assessment = RuleEngine().evaluate(transaction())
    with pytest.raises(Exception):
        repo.save_assessment(assessment)


def test_cases_can_be_created_and_filtered(repo):
    item = transaction(amount="1500000.00")
    repo.save_transaction(item)
    assessment = RuleEngine().evaluate(item)
    assessment_id = repo.save_assessment(assessment)
    case_id = repo.create_case(
        item.transaction_id,
        assessment_id,
        "Review high-value activity",
    )
    case = repo.get_case(case_id)
    assert case["status"] == "open"
    assert case["assessment_id"] == assessment_id
    assert len(repo.list_cases(status="open")) == 1
    assert repo.list_cases(status="closed") == []


def test_invalid_case_status_is_rejected(repo):
    with pytest.raises(ValueError):
        repo.list_cases(status="pending")
