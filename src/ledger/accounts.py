from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any
from uuid import uuid4

import financial_agent as core


def _insert_account_reconciliation(
    conn: sqlite3.Connection,
    account_name: str,
    reconciled_on: str,
    actual_balance: float,
    expected_balance: float,
    note: str,
    *,
    created_at: str | None = None,
) -> str:
    reconciliation_id = uuid4().hex
    created_at = created_at or core.now_iso()
    conn.execute(
        """
        INSERT INTO account_reconciliations
            (id, account_name, reconciled_on, actual_balance, expected_balance,
             difference, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reconciliation_id,
            account_name,
            reconciled_on,
            round(actual_balance, 2),
            round(expected_balance, 2),
            round(actual_balance - expected_balance, 2),
            note[:300],
            created_at,
        ),
    )
    return reconciliation_id


def migrate_asset_accounts(conn: sqlite3.Connection) -> None:
    """Create tracked asset accounts without rewriting existing ledger records."""
    if conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None:
        return
    today = date.today().isoformat()
    now = core.now_iso()
    legacy = core._legacy_capital_overview(conn, core.current_month())
    has_legacy_capital = bool(legacy.get("configured") and legacy.get("current_balance") is not None)
    for name, kind in core.DEFAULT_ASSET_ACCOUNTS:
        conn.execute(
            "INSERT INTO accounts (name, kind, is_active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (name, kind, now, now),
        )
        if has_legacy_capital:
            _insert_account_reconciliation(conn, name, today, 0, 0, "账户启用时的初始余额", created_at=now)

    # The old model stored one total. Preserve it as a visible account until the
    # user distributes it to real accounts, rather than silently changing capital.
    if has_legacy_capital:
        amount = float(legacy["current_balance"])
        conn.execute(
            "INSERT INTO accounts (name, kind, is_active, created_at, updated_at) VALUES (?, 'other', 1, ?, ?)",
            ("待分配余额", now, now),
        )
        _insert_account_reconciliation(conn, "待分配余额", today, amount, amount, "从旧版当前本金迁移", created_at=now)
        core.audit(
            conn,
            "account.migrate_legacy_capital",
            {"amount": amount, "account": "待分配余额"},
            source="migration",
        )


def _account_kind_for_name(name: str) -> str:
    return dict(core.DEFAULT_ASSET_ACCOUNTS).get(name, "other")


def ensure_asset_account(conn: sqlite3.Connection, name: str) -> str:
    """Return a tracked asset account, creating a zero-balance account if needed."""
    name = core.canonical_reference(conn, "payment_method", name)
    if name in core.UNTRACKED_PAYMENT_METHODS:
        return name
    row = conn.execute("SELECT name FROM accounts WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return str(row["name"])
    now = core.now_iso()
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO accounts (name, kind, is_active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
        (name, _account_kind_for_name(name), now, now),
    )
    _insert_account_reconciliation(conn, name, today, 0, 0, "自动创建账户", created_at=now)
    return name


def _require_asset_account(conn: sqlite3.Connection, name: str) -> str:
    name = ensure_asset_account(conn, name)
    if name in core.UNTRACKED_PAYMENT_METHODS:
        raise ValueError("请选择真实资金账户，不能使用未指定或信用卡")
    row = conn.execute("SELECT name, is_active FROM accounts WHERE name = ?", (name,)).fetchone()
    if row is None or not row["is_active"]:
        raise ValueError("账户不存在或已停用")
    return str(row["name"])


def _account_baseline(conn: sqlite3.Connection, account_name: str, as_of: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM account_reconciliations
        WHERE account_name = ? AND reconciled_on <= ?
        ORDER BY reconciled_on DESC, created_at DESC
        LIMIT 1
        """,
        (account_name, as_of),
    ).fetchone()
    if row is None:
        account = conn.execute("SELECT created_at FROM accounts WHERE name = ?", (account_name,)).fetchone()
        if account is None:
            raise ValueError("找不到账户")
        return {"reconciled_on": "0000-00-00", "actual_balance": 0.0, "created_at": account["created_at"]}
    return dict(row)


def _account_flows_after_baseline(
    conn: sqlite3.Connection, account_name: str, baseline: dict[str, Any], as_of: str
) -> dict[str, float]:
    start = str(baseline["reconciled_on"])
    created_at = str(baseline["created_at"])
    condition = "(event_date > ? OR (event_date = ? AND created_at >= ?)) AND event_date <= ?"
    transaction = conn.execute(
        f"""
        SELECT COALESCE(SUM(CASE direction WHEN 'income' THEN amount ELSE -amount END), 0)
        FROM (SELECT date AS event_date, created_at, direction, amount FROM transactions
              WHERE deleted_at IS NULL AND account = ?)
        WHERE {condition}
        """,
        (account_name, start, start, created_at, as_of),
    ).fetchone()[0]
    repayments = conn.execute(
        f"""
        SELECT COALESCE(SUM(-amount), 0) FROM
        (SELECT paid_at AS event_date, created_at, amount FROM liability_payments WHERE account = ?)
        WHERE {condition}
        """,
        (account_name, start, start, created_at, as_of),
    ).fetchone()[0]
    transfers_in = conn.execute(
        f"""
        SELECT COALESCE(SUM(amount), 0) FROM
        (SELECT transferred_on AS event_date, created_at, amount FROM transfers WHERE target_account = ?)
        WHERE {condition}
        """,
        (account_name, start, start, created_at, as_of),
    ).fetchone()[0]
    transfers_out = conn.execute(
        f"""
        SELECT COALESCE(SUM(-amount), 0) FROM
        (SELECT transferred_on AS event_date, created_at, amount FROM transfers WHERE source_account = ?)
        WHERE {condition}
        """,
        (account_name, start, start, created_at, as_of),
    ).fetchone()[0]
    return {
        "transactions": round(float(transaction or 0), 2),
        "repayments": round(float(repayments or 0), 2),
        "transfers_in": round(float(transfers_in or 0), 2),
        "transfers_out": round(float(transfers_out or 0), 2),
    }


def account_balance(conn: sqlite3.Connection, account_name: str, as_of: str = "") -> dict[str, Any]:
    as_of = date.fromisoformat(as_of).isoformat() if as_of else date.today().isoformat()
    account_name = _require_asset_account(conn, account_name)
    baseline = _account_baseline(conn, account_name, as_of)
    flows = _account_flows_after_baseline(conn, account_name, baseline, as_of)
    balance = round(float(baseline["actual_balance"]) + sum(flows.values()), 2)
    return {
        "name": account_name,
        "as_of": as_of,
        "balance": balance,
        "baseline_date": baseline["reconciled_on"],
        "baseline_balance": round(float(baseline["actual_balance"]), 2),
        "last_difference": round(float(baseline.get("difference") or 0), 2),
        "flows": flows,
    }


def list_accounts(conn: sqlite3.Connection, include_inactive: bool = False) -> dict[str, Any]:
    clauses = "" if include_inactive else "WHERE is_active = 1"
    rows = conn.execute(
        f"SELECT * FROM accounts {clauses} ORDER BY CASE kind WHEN 'wallet' THEN 1 WHEN 'bank' THEN 2 WHEN 'cash' THEN 3 ELSE 4 END, name"
    ).fetchall()
    items = []
    for row in rows:
        balance = account_balance(conn, str(row["name"]))
        items.append({**dict(row), **balance})
    return {"items": items, "total_balance": round(sum(float(item["balance"]) for item in items), 2)}


def create_asset_account(
    conn: sqlite3.Connection,
    name: str,
    kind: str = "other",
    actual_balance: float = 0,
    reconciled_on: str = "",
    actor: str = "web",
) -> dict[str, Any]:
    name = core.clean_reference_name(name)
    if kind not in {"wallet", "bank", "cash", "other"}:
        raise ValueError("账户类型无效")
    if name in core.UNTRACKED_PAYMENT_METHODS:
        raise ValueError("未指定和信用卡不能作为真实资金账户")
    if conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        raise ValueError("账户已存在")
    now = core.now_iso()
    if conn.execute("SELECT 1 FROM payment_methods WHERE name = ?", (name,)).fetchone() is None:
        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM payment_methods").fetchone()[0]
        conn.execute(
            "INSERT INTO payment_methods (name, aliases, is_favorite, sort_order, created_at, updated_at) VALUES (?, '[]', 0, ?, ?, ?)",
            (name, sort_order, now, now),
        )
    conn.execute(
        "INSERT INTO accounts (name, kind, is_active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
        (name, kind, now, now),
    )
    reconcile_account(conn, name, actual_balance, reconciled_on, "创建账户时的初始余额", actor=actor)
    core.audit(conn, "account.create", {"name": name, "kind": kind}, source=actor)
    conn.commit()
    return next(item for item in list_accounts(conn)["items"] if item["name"] == name)


def update_asset_account(
    conn: sqlite3.Connection,
    name: str,
    *,
    new_name: str | None = None,
    kind: str | None = None,
    actual_balance: float | None = None,
    reconciled_on: str = "",
    actor: str = "web",
) -> dict[str, Any]:
    name = core.clean_reference_name(name)
    account = conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
    if account is None:
        raise ValueError("找不到资金账户")
    target = core.clean_reference_name(new_name) if new_name is not None else name
    if kind is not None and kind not in {"wallet", "bank", "cash", "other"}:
        raise ValueError("账户类型无效")
    if target in core.UNTRACKED_PAYMENT_METHODS:
        raise ValueError("未指定和信用卡不能作为真实资金账户")
    if target != name:
        core.update_reference_value(
            conn, "payment_method", name, new_name=target, actor=actor
        )
    effective_kind = kind or str(account["kind"])
    if effective_kind != account["kind"]:
        conn.execute(
            "UPDATE accounts SET kind = ?, updated_at = ? WHERE name = ?",
            (effective_kind, core.now_iso(), target),
        )
    result: dict[str, Any] = {"name": target, "kind": effective_kind}
    if actual_balance is not None:
        reconciliation = reconcile_account(
            conn,
            target,
            float(actual_balance),
            reconciled_on,
            "编辑账户时校准余额",
            actor=actor,
        )
        result["reconciliation"] = reconciliation
    core.audit(
        conn,
        "account.update",
        {"before": name, "after": target, "kind": effective_kind},
        source=actor,
    )
    conn.commit()
    return next(item for item in list_accounts(conn)["items"] if item["name"] == target)


def delete_asset_account(
    conn: sqlite3.Connection, name: str, actor: str = "web"
) -> dict[str, Any]:
    name = core.clean_reference_name(name)
    if conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone() is None:
        raise ValueError("找不到资金账户")
    balance = account_balance(conn, name)["balance"]
    if abs(float(balance)) >= 0.005:
        raise ValueError("账户余额不为 0，请先转出余额或完成对账")
    references = {
        "账单": "SELECT COUNT(*) FROM transactions WHERE account = ?",
        "订阅": "SELECT COUNT(*) FROM subscriptions WHERE account = ?",
        "待还还款方式": "SELECT COUNT(*) FROM liabilities WHERE repayment_account = ?",
        "还款记录": "SELECT COUNT(*) FROM liability_payments WHERE account = ?",
        "转账": "SELECT COUNT(*) FROM transfers WHERE source_account = ? OR target_account = ?",
    }
    used_by = [
        label for label, query in references.items()
        if int(conn.execute(query, (name,) if query.count("?") == 1 else (name, name)).fetchone()[0])
    ]
    if used_by:
        raise ValueError(f"账户已有{'、'.join(used_by)}记录，不能删除")
    conn.execute("DELETE FROM account_reconciliations WHERE account_name = ?", (name,))
    conn.execute("DELETE FROM accounts WHERE name = ?", (name,))
    core.audit(conn, "account.delete", {"name": name}, source=actor)
    conn.commit()
    return {"deleted": name}


def create_transfer(
    conn: sqlite3.Connection,
    source_account: str,
    target_account: str,
    amount: float,
    transferred_on: str = "",
    note: str = "",
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    source_account = _require_asset_account(conn, source_account)
    target_account = _require_asset_account(conn, target_account)
    if source_account == target_account:
        raise ValueError("转出和转入账户不能相同")
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("转账金额必须大于 0")
    transferred_on = date.fromisoformat(transferred_on).isoformat() if transferred_on else date.today().isoformat()
    transfer_id = uuid4().hex
    now = core.now_iso()
    conn.execute(
        "INSERT INTO transfers (id, transferred_on, amount, source_account, target_account, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (transfer_id, transferred_on, amount, source_account, target_account, str(note).strip()[:300], now),
    )
    result = {
        "id": transfer_id, "transferred_on": transferred_on, "amount": amount,
        "source_account": source_account, "target_account": target_account, "note": str(note).strip()[:300],
    }
    core.audit(conn, "account.transfer", result, source=actor)
    if commit:
        conn.commit()
    return result


def reconcile_account(
    conn: sqlite3.Connection,
    account_name: str,
    actual_balance: float,
    reconciled_on: str = "",
    note: str = "",
    actor: str = "web",
) -> dict[str, Any]:
    reconciled_on = date.fromisoformat(reconciled_on).isoformat() if reconciled_on else date.today().isoformat()
    if reconciled_on > date.today().isoformat():
        raise ValueError("对账日期不能晚于今天")
    account_name = _require_asset_account(conn, account_name)
    actual_balance = round(float(actual_balance), 2)
    expected = account_balance(conn, account_name, reconciled_on)["balance"]
    reconciliation_id = _insert_account_reconciliation(
        conn, account_name, reconciled_on, actual_balance, expected, str(note).strip()
    )
    result = {
        "id": reconciliation_id, "account": account_name, "reconciled_on": reconciled_on,
        "actual_balance": actual_balance, "expected_balance": expected,
        "difference": round(actual_balance - expected, 2), "note": str(note).strip()[:300],
    }
    core.audit(conn, "account.reconcile", result, source=actor)
    conn.commit()
    return result

