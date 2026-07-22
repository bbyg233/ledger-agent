from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentAction:
    action: str
    text: str = ""
    transaction: dict[str, Any] | None = None
    transactions: list[dict[str, Any]] = field(default_factory=list)
    month: str = ""
    query: str = ""
    category: str = ""
    account: str = ""
    direction: str = ""
    group_by: str = "category"
    monthly_income: float | None = None
    saving_goal: float = 0
    min_amount: float | None = None
    max_amount: float | None = None
    limit: int = 20
    periods: int = 3
    current_start: str = ""
    current_end: str = ""
    baseline_start: str = ""
    baseline_end: str = ""
    recurring_months: int = 6
    min_occurrences: int = 3
    min_amount: float = 0
    question: str = ""


@dataclass
class AgentContext:
    session_id: str
    today: str
    current_month: str
    recent_messages: list[dict[str, Any]]
    preferences: dict[str, Any]
    state: dict[str, Any]
    category_catalog: list[dict[str, Any]] = field(default_factory=list)
    merchant_rules: list[dict[str, Any]] = field(default_factory=list)
    subscription_catalog: list[dict[str, Any]] = field(default_factory=list)
    liability_catalog: list[dict[str, Any]] = field(default_factory=list)
    account_catalog: list[dict[str, Any]] = field(default_factory=list)
