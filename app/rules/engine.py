"""Run enabled rules and create a bounded, explainable risk assessment."""
from collections.abc import Iterable, Sequence
from app.models.transaction import Transaction
from app.rules.config import RuleConfig
from app.rules.models import RiskAssessment, RiskBand, RuleResult, RuleStatus
from app.rules.rules import FraudRule, RuleContext, build_default_rules

class RuleEngine:
    def __init__(self, config: RuleConfig | None = None, rules: Iterable[FraudRule] | None = None):
        self.config = config or RuleConfig()
        self.rules = tuple(rules) if rules is not None else build_default_rules(self.config)
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule IDs must be unique")

    def evaluate(self, transaction: Transaction, history: Sequence[Transaction] = ()) -> RiskAssessment:
        # Only earlier transactions for the same customer are valid history.
        eligible = tuple(x for x in history if
            x.customer_id == transaction.customer_id
            and x.transaction_id != transaction.transaction_id
            and x.timestamp < transaction.timestamp)
        context = RuleContext(transaction, eligible)
        results = []
        for rule in self.rules:
            if rule.rule_id not in self.config.enabled_rules:
                results.append(RuleResult(rule.rule_id, rule.name, RuleStatus.DISABLED, 0,
                    f"Rule '{rule.rule_id}' is disabled by configuration."))
            else:
                results.append(rule.evaluate(context))
        score = min(100, sum(r.score for r in results if r.triggered))
        if score <= self.config.low_risk_max:
            band = RiskBand.LOW
        elif score <= self.config.medium_risk_max:
            band = RiskBand.MEDIUM
        else:
            band = RiskBand.HIGH
        return RiskAssessment(transaction.transaction_id, score, band, tuple(results))