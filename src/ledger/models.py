from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TransactionDraft:
    date: str
    amount: float
    direction: str
    category: str
    account: str
    merchant: str
    note: str
    raw_text: str
    source: str = "manual"
    source_id: str = ""
    import_hash: str = ""
    entry_hash: str = ""
    category_confidence: float = 1.0
    category_reason: str = ""
    classification_source: str = "legacy"
    suggested_category: str = ""
    proposed_category: str = ""
    needs_category_review: bool = False


class DuplicateTransactionError(ValueError):
    def __init__(self, duplicates: list[dict[str, Any]]):
        super().__init__("检测到可能重复的账单，请确认后再写入")
        self.duplicates = duplicates

