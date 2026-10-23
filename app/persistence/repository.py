
"""Repository methods for persisting and retrieving domain objects."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.transaction import Transaction
from app.persistence.database import Database
from app.rules.config import RuleConfig
from app.rules.models import RiskAssessment


class DuplicateTransactionError(ValueError):
    """Raised when an ID is reused for a different transaction payload."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transaction_payload(transaction: Transaction) -> dict[str, Any]:
    data = transaction.model_dump(mode="json")
    # Pydantic's JSON mode serializes Decimal and datetimes consistently.
    return data


class PersistenceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.initialize()

    def save_transaction(self, transaction: Transaction) -> bool:
        """Insert a transaction; return False for an identical retry.

        Reusing a transaction ID with different content is rejected
        instead of silently overwriting the original transaction.
        """
        payload = _transaction_payload(transaction)
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        values = (
            transaction.transaction_id,
            transaction.customer_id,
            str(transaction.amount),
            transaction.currency,
            transaction.timestamp.isoformat(),
            transaction.merchant_category,
            transaction.channel,
            transaction.device_id,
            transaction.country_code,
            transaction.created_at.isoformat(),
            payload_json,
        )

        with self.database.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO transactions (
                    transaction_id, customer_id, amount, currency,
                    timestamp, merchant_category, channel, device_id,
                    country_code, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO NOTHING""",
                values,
            )

            if cursor.rowcount == 1:
                return True

            existing = connection.execute(
                "SELECT payload_json FROM transactions "
                "WHERE transaction_id = ?",
                (transaction.transaction_id,),
            ).fetchone()

            if existing is None:
                raise RuntimeError(
                    "Transaction insert was skipped, but no existing "
                    f"transaction was found for '{transaction.transaction_id}'."
                )

            if existing["payload_json"] == payload_json:
                return False

            raise DuplicateTransactionError(
                f"Transaction ID '{transaction.transaction_id}' "
                "already exists with different data."
            )

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return Transaction.model_validate_json(row["payload_json"]) if row else None

    def list_transactions(
        self,
        customer_id: str | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        with self.database.connect() as connection:
            if customer_id is None:
                rows = connection.execute(
                    "SELECT payload_json FROM transactions "
                    "ORDER BY timestamp DESC, transaction_id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM transactions "
                    "WHERE customer_id = ? "
                    "ORDER BY timestamp DESC, transaction_id LIMIT ?",
                    (customer_id, limit),
                ).fetchall()

        return [
            Transaction.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_customer_history(
        self,
        customer_id: str,
        before: datetime | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        """Return prior transactions oldest-first, suitable for rule evaluation."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        with self.database.connect() as connection:
            if before is None:
                rows = connection.execute(
                    "SELECT payload_json FROM transactions "
                    "WHERE customer_id = ? "
                    "ORDER BY timestamp DESC, transaction_id DESC LIMIT ?",
                    (customer_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM transactions "
                    "WHERE customer_id = ? AND timestamp < ? "
                    "ORDER BY timestamp DESC, transaction_id DESC LIMIT ?",
                    (customer_id, before.isoformat(), limit),
                ).fetchall()

        transactions = [
            Transaction.model_validate_json(row["payload_json"])
            for row in rows
        ]
        return sorted(
            transactions,
            key=lambda item: (item.timestamp, item.transaction_id),
        )

    def save_rules(self, config: RuleConfig | None = None) -> int:
        """Persist the five supported rule definitions and their current settings."""
        config = config or RuleConfig()
        now = _now()
        definitions = {
            "high_amount": (
                "High transaction amount",
                {
                    "threshold": str(config.high_amount_threshold),
                    "score": config.high_amount_score,
                },
            ),
            "unusual_amount": (
                "Amount unusual for customer",
                {
                    "multiplier": str(config.unusual_amount_multiplier),
                    "min_history": config.unusual_amount_min_history,
                    "score": config.unusual_amount_score,
                },
            ),
            "velocity": (
                "Rapid successive transactions",
                {
                    "window_seconds": config.velocity_window_seconds,
                    "max_transactions": config.velocity_max_transactions,
                    "score": config.velocity_score,
                },
            ),
            "unfamiliar_device": (
                "Unfamiliar device",
                {"score": config.unfamiliar_device_score},
            ),
            "unusual_geography": (
                "Unusual geographic change",
                {
                    "window_minutes": config.geography_window_minutes,
                    "score": config.unusual_geography_score,
                },
            ),
        }

        with self.database.connect() as connection:
            for rule_id, (name, settings) in definitions.items():
                connection.execute(
                    """INSERT INTO rules(
                        rule_id, name, enabled, configuration_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(rule_id) DO UPDATE SET
                        name = excluded.name,
                        enabled = excluded.enabled,
                        configuration_json = excluded.configuration_json,
                        updated_at = excluded.updated_at""",
                    (
                        rule_id,
                        name,
                        int(rule_id in config.enabled_rules),
                        json.dumps(settings, sort_keys=True),
                        now,
                    ),
                )

        return len(definitions)

    def list_rules(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rules ORDER BY rule_id"
            ).fetchall()

        return [
            {
                **dict(row),
                "enabled": bool(row["enabled"]),
                "configuration": json.loads(row["configuration_json"]),
            }
            for row in rows
        ]

    def save_assessment(self, assessment: RiskAssessment) -> int:
        """Persist an assessment and its per-rule explanations; return its ID."""
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT assessment_id, risk_score, risk_band "
                "FROM assessments WHERE transaction_id = ?",
                (assessment.transaction_id,),
            ).fetchone()

            if existing:
                if (
                    existing["risk_score"] == assessment.risk_score
                    and existing["risk_band"] == assessment.risk_band.value
                ):
                    return int(existing["assessment_id"])

                raise ValueError(
                    f"Transaction '{assessment.transaction_id}' "
                    "already has a different assessment."
                )

            cursor = connection.execute(
                "INSERT INTO assessments"
                "(transaction_id, risk_score, risk_band, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    assessment.transaction_id,
                    assessment.risk_score,
                    assessment.risk_band.value,
                    _now(),
                ),
            )
            assessment_id = int(cursor.lastrowid)

            connection.executemany(
                """INSERT INTO assessment_rule_results
                (assessment_id, rule_id, name, status, score, explanation)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        assessment_id,
                        result.rule_id,
                        result.name,
                        result.status.value,
                        result.score,
                        result.explanation,
                    )
                    for result in assessment.rule_results
                ],
            )

        return assessment_id

    def get_assessment(self, transaction_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()

            if row is None:
                return None

            results = connection.execute(
                "SELECT rule_id, name, status, score, explanation "
                "FROM assessment_rule_results "
                "WHERE assessment_id = ? ORDER BY rule_id",
                (row["assessment_id"],),
            ).fetchall()

        return {
            **dict(row),
            "rule_results": [dict(item) for item in results],
        }

    def create_case(
        self,
        transaction_id: str,
        assessment_id: int | None = None,
        notes: str = "",
    ) -> int:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO cases"
                "(transaction_id, assessment_id, status, notes, created_at, updated_at) "
                "VALUES (?, ?, 'open', ?, ?, ?)",
                (transaction_id, assessment_id, notes, now, now),
            )

        return int(cursor.lastrowid)

    def get_case(self, case_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_cases(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        with self.database.connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM cases ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                if status not in {"open", "in_review", "closed"}:
                    raise ValueError("status must be open, in_review, or closed")

                rows = connection.execute(
                    "SELECT * FROM cases WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()

        return [dict(row) for row in rows]