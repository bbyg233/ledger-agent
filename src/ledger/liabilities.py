from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

import financial_agent as core
from ledger.queries import month_range, normalize_month


LIABILITY_KINDS = {"credit_card", "consumer_credit", "installment", "other"}


def _normalize_liability_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("待还项目名称不能为空")
    kind = str(payload.get("kind") or "other")
    if kind not in LIABILITY_KINDS:
        raise ValueError("待还类型无效")
    statement_day = int(payload.get("statement_day") or 0)
    if not 0 <= statement_day <= 31:
        raise ValueError("出账日必须在每月 1 到 31 日之间")
    statement_month_offset = int(payload.get("statement_month_offset", 1) or 0)
    if statement_month_offset not in {0, 1}:
        raise ValueError("账单归属规则无效")
    due_amount = round(float(payload.get("due_amount") or 0), 2)
    minimum_payment = round(float(payload.get("minimum_payment") or 0), 2)
    if min(due_amount, minimum_payment) < 0:
        raise ValueError("待还金额不能小于 0")
    if minimum_payment > due_amount:
        raise ValueError("最低还款不能超过本期应还")
    due_date_value = str(payload.get("due_date") or "").strip()
    due_date = date.fromisoformat(due_date_value).isoformat() if due_date_value else ""
    credit_limit_value = payload.get("credit_limit")
    credit_limit = None if credit_limit_value in (None, "") else round(float(credit_limit_value), 2)
    if credit_limit is not None and credit_limit < 0:
        raise ValueError("额度不能小于 0")
    return {
        "name": name,
        "provider": str(payload.get("provider") or "").strip()[:80],
        "kind": kind,
        "statement_day": statement_day,
        "statement_month_offset": statement_month_offset,
        # Legacy compatibility cache. Available capital is stored separately.
        "outstanding_balance": due_amount,
        "due_amount": due_amount,
        "due_date": due_date,
        "minimum_payment": minimum_payment,
        "repayment_account": core.ensure_asset_account(conn, str(payload.get("repayment_account") or "未指定")),
        "credit_limit": credit_limit,
        "note": str(payload.get("note") or "").strip()[:300],
        "is_active": int(bool(payload.get("is_active", True))),
    }


def get_liability(conn: sqlite3.Connection, liability_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM liabilities WHERE id = ?", (liability_id[:80],)).fetchone()
    if row is None:
        raise ValueError("找不到待还项目")
    return dict(row)


def inherited_liability_due_date(
    conn: sqlite3.Connection, liability_id: str, statement_month: str
) -> str:
    """Reuse a known due-day for a new statement month without copying its old month."""
    rows = conn.execute(
        """
        SELECT month, due_date FROM liability_statements
        WHERE liability_id = ? AND due_date <> ''
        """,
        (liability_id[:80],),
    ).fetchall()
    if not rows:
        return ""
    target_year, target_number = map(int, statement_month.split("-"))
    target_index = target_year * 12 + target_number
    nearest = min(
        rows,
        key=lambda row: abs(
            int(str(row["month"])[:4]) * 12 + int(str(row["month"])[5:7]) - target_index
        ),
    )
    due_day = int(str(nearest["due_date"])[8:10])
    due_day = min(due_day, calendar.monthrange(target_year, target_number)[1])
    return f"{statement_month}-{due_day:02d}"


def resolve_credit_charge_statement_month(
    conn: sqlite3.Connection,
    liability_id: str,
    charged_at: str,
    requested_month: str,
) -> str:
    """Resolve a credit purchase to its billing statement month.

    The next cycle-switch day determines the statement event. Accounts then
    choose whether the ledger statement is labelled with that event month or
    the following month. This supports both a July 6 to August 5 cycle that
    belongs to August, and a 25th statement that belongs to the month after
    it. Accounts without a configured statement day keep the legacy
    next-due-date behaviour so existing ledgers are not reclassified.
    """
    charged = date.fromisoformat(charged_at)
    charged_on = charged.isoformat()
    requested_month = normalize_month(requested_month)
    liability = get_liability(conn, liability_id)
    statement_day = int(liability.get("statement_day") or 0)
    if statement_day:
        statement_month_offset = int(liability.get("statement_month_offset", 1) or 0)
        last_day = calendar.monthrange(charged.year, charged.month)[1]
        effective_day = min(statement_day, last_day)
        purchase_month = f"{charged.year:04d}-{charged.month:02d}"
        statement_event_month = (
            _next_month(purchase_month) if charged.day >= effective_day else purchase_month
        )
        return _next_month(statement_event_month) if statement_month_offset else statement_event_month

    next_due = conn.execute(
        """
        SELECT month, due_date FROM liability_statements
        WHERE liability_id = ? AND due_date <> '' AND due_date >= ?
        ORDER BY
            CASE WHEN substr(due_date, 1, 7) = month THEN 0 ELSE 1 END,
            due_date,
            month
        LIMIT 1
        """,
        (liability_id[:80], charged_on),
    ).fetchone()
    if next_due is None:
        return requested_month
    return normalize_month(str(next_due["due_date"])[:7])


def liability_statement_balance(conn: sqlite3.Connection, liability_id: str) -> float:
    value = conn.execute(
        "SELECT COALESCE(SUM(remaining_amount), 0) FROM liability_statements WHERE liability_id = ?",
        (liability_id[:80],),
    ).fetchone()[0]
    return round(float(value or 0), 2)


def _upsert_liability_statement(
    conn: sqlite3.Connection,
    liability_id: str,
    item: dict[str, Any],
    statement_month: str = "",
) -> dict[str, Any]:
    month = normalize_month(statement_month or str(item["due_date"])[:7] or core.current_month())
    existing = conn.execute(
        "SELECT * FROM liability_statements WHERE liability_id = ? AND month = ?",
        (liability_id[:80], month),
    ).fetchone()
    now = core.now_iso()
    statement_amount = float(item["due_amount"])
    paid_amount = 0.0
    if existing is not None:
        paid_amount = max(
            0.0,
            float(existing["statement_amount"]) - float(existing["remaining_amount"]),
        )
        if statement_amount < paid_amount:
            raise ValueError(f"本月应还不能低于已还金额 {paid_amount:.2f}")
    remaining_amount = round(max(0.0, statement_amount - paid_amount), 2)
    if existing is None:
        conn.execute(
            """
            INSERT INTO liability_statements
                (id, liability_id, month, statement_amount, remaining_amount,
                 due_date, minimum_payment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                liability_id[:80],
                month,
                statement_amount,
                remaining_amount,
                item["due_date"],
                item["minimum_payment"],
                now,
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE liability_statements
            SET statement_amount = ?, remaining_amount = ?, due_date = ?,
                minimum_payment = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                statement_amount,
                remaining_amount,
                item["due_date"],
                item["minimum_payment"],
                now,
                existing["id"],
            ),
        )
    return {
        "month": month,
        "statement_amount": statement_amount,
        "remaining_amount": remaining_amount,
        "due_date": item["due_date"],
        "minimum_payment": item["minimum_payment"],
    }


def get_liability_for_month(
    conn: sqlite3.Connection, liability_id: str, month: str
) -> dict[str, Any]:
    month = normalize_month(month)
    liability = get_liability(conn, liability_id)
    statement = conn.execute(
        "SELECT * FROM liability_statements WHERE liability_id = ? AND month = ?",
        (liability_id[:80], month),
    ).fetchone()
    if statement is None:
        return {
            **liability,
            "has_statement": False,
            "statement_month": month,
            "due_amount": 0.0,
            "remaining_amount": 0.0,
            "due_date": "",
            "minimum_payment": 0.0,
        }
    return {
        **liability,
        "has_statement": True,
        "statement_month": month,
        "due_amount": float(statement["statement_amount"]),
        "remaining_amount": float(statement["remaining_amount"]),
        "due_date": statement["due_date"],
        "minimum_payment": float(statement["minimum_payment"]),
    }


def create_liability(
    conn: sqlite3.Connection, payload: dict[str, Any], actor: str = "web", *, commit: bool = True
) -> dict[str, Any]:
    item = _normalize_liability_payload(conn, payload)
    duplicate = conn.execute(
        "SELECT id FROM liabilities WHERE is_active = 1 AND lower(name) = lower(?) LIMIT 1",
        (item["name"],),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("已存在同名待还账户，请选择已有账户录入本月账单")
    statement_month = str(payload.get("statement_month") or "")
    liability_id = uuid4().hex
    now = core.now_iso()
    conn.execute(
        """
        INSERT INTO liabilities
            (id, name, provider, kind, statement_day, statement_month_offset, outstanding_balance, due_amount, due_date, minimum_payment,
             repayment_account, credit_limit, note, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (liability_id, *item.values(), now, now),
    )
    statement = _upsert_liability_statement(conn, liability_id, item, statement_month)
    core.audit(conn, "liability.create", {"id": liability_id, **item}, source=actor)
    if commit:
        conn.commit()
    return get_liability_for_month(conn, liability_id, statement["month"])


def update_liability(
    conn: sqlite3.Connection,
    liability_id: str,
    changes: dict[str, Any],
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    before = get_liability(conn, liability_id)
    allowed = {
        "name", "provider", "kind", "statement_day", "statement_month_offset", "due_amount", "due_date", "minimum_payment",
        "repayment_account", "credit_limit", "note", "is_active",
    }
    merged = {
        **before,
        **{
            key: value
            for key, value in changes.items()
            if key in allowed and (value is not None or key == "credit_limit")
        },
    }
    item = _normalize_liability_payload(conn, merged)
    statement_fields = {
        "statement_month", "source_statement_month", "due_amount", "due_date", "minimum_payment",
    }
    account_only_update = not any(field in changes for field in statement_fields)
    if account_only_update:
        try:
            conn.execute(
                """
                UPDATE liabilities
                SET name = ?, provider = ?, kind = ?, statement_day = ?, statement_month_offset = ?, repayment_account = ?,
                    credit_limit = ?, note = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    item["name"], item["provider"], item["kind"], item["statement_day"], item["statement_month_offset"],
                    item["repayment_account"], item["credit_limit"], item["note"], item["is_active"],
                    core.now_iso(), liability_id[:80],
                ),
            )
            after = get_liability(conn, liability_id)
            core.audit(
                conn,
                "liability.update",
                {"id": liability_id[:80], "before": before, "after": after, "account_only": True},
                source=actor,
            )
            if commit:
                conn.commit()
            return after
        except Exception:
            if commit:
                conn.rollback()
            raise

    statement_month = str(changes.get("statement_month") or "")
    target_month = normalize_month(statement_month or str(item["due_date"])[:7] or core.current_month())
    source_value = str(changes.get("source_statement_month") or "")
    source_month = normalize_month(source_value) if source_value else ""
    move_statement = bool(source_month and source_month != target_month)

    try:
        if move_statement:
            source_statement = conn.execute(
                "SELECT id FROM liability_statements WHERE liability_id = ? AND month = ?",
                (liability_id[:80], source_month),
            ).fetchone()
            if source_statement is None:
                raise ValueError("原账单月份不存在，无法迁移")
            target_statement = conn.execute(
                "SELECT id FROM liability_statements WHERE liability_id = ? AND month = ?",
                (liability_id[:80], target_month),
            ).fetchone()
            if target_statement is not None:
                raise ValueError("目标账单月份已存在，请分别编辑两份账单")
            conn.execute(
                "UPDATE liability_statements SET month = ?, updated_at = ? WHERE id = ?",
                (target_month, core.now_iso(), source_statement["id"]),
            )
            conn.execute(
                "UPDATE liability_charges SET statement_month = ? WHERE liability_id = ? AND statement_month = ?",
                (target_month, liability_id[:80], source_month),
            )
            conn.execute(
                "UPDATE liability_payments SET statement_month = ? WHERE liability_id = ? AND statement_month = ?",
                (target_month, liability_id[:80], source_month),
            )

        existing_statement = conn.execute(
            "SELECT statement_amount, remaining_amount FROM liability_statements WHERE liability_id = ? AND month = ?",
            (liability_id[:80], target_month),
        ).fetchone()
        if existing_statement is not None:
            paid_amount = max(
                0.0,
                float(existing_statement["statement_amount"])
                - float(existing_statement["remaining_amount"]),
            )
            if float(item["due_amount"]) < paid_amount:
                raise ValueError(f"账单应还不能低于已还金额 {paid_amount:.2f}")
        statement = _upsert_liability_statement(conn, liability_id, item, target_month)
        outstanding_balance = liability_statement_balance(conn, liability_id)
        conn.execute(
            """
            UPDATE liabilities
            SET name = ?, provider = ?, kind = ?, statement_day = ?, statement_month_offset = ?, outstanding_balance = ?, due_amount = ?, due_date = ?,
                minimum_payment = ?, repayment_account = ?, credit_limit = ?, note = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                item["name"], item["provider"], item["kind"], item["statement_day"], item["statement_month_offset"], outstanding_balance,
                item["due_amount"], item["due_date"], item["minimum_payment"],
                item["repayment_account"], item["credit_limit"], item["note"],
                item["is_active"], core.now_iso(), liability_id[:80],
            ),
        )
        after = get_liability_for_month(conn, liability_id, statement["month"])
        core.audit(
            conn,
            "liability.update",
            {
                "id": liability_id[:80],
                "before": before,
                "after": after,
                "source_statement_month": source_month,
                "statement_month": target_month,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return after


def list_liabilities(
    conn: sqlite3.Connection,
    month: str = "",
    include_inactive: bool = False,
    *,
    include_without_statement: bool = False,
) -> dict[str, Any]:
    month = normalize_month(month)
    clauses = [] if include_inactive else ["l.is_active = 1"]
    if not include_without_statement:
        clauses.append("s.month IS NOT NULL")
    sql = """
        SELECT
            l.id, l.name, l.provider, l.kind, l.statement_day, l.statement_month_offset, l.repayment_account, l.credit_limit,
            l.note, l.is_active, l.created_at, l.updated_at,
            s.month AS statement_month, s.statement_amount, s.remaining_amount,
            s.due_date AS statement_due_date, s.minimum_payment AS statement_minimum_payment,
            (SELECT COUNT(*) FROM liability_payments p
             WHERE p.liability_id = l.id AND p.statement_month = s.month) AS payment_count,
            (SELECT p.paid_at FROM liability_payments p
             WHERE p.liability_id = l.id AND p.statement_month = s.month
             ORDER BY p.paid_at DESC, p.created_at DESC, p.id DESC LIMIT 1) AS latest_payment_date,
            (SELECT p.amount FROM liability_payments p
             WHERE p.liability_id = l.id AND p.statement_month = s.month
             ORDER BY p.paid_at DESC, p.created_at DESC, p.id DESC LIMIT 1) AS latest_payment_amount
        FROM liabilities l
        LEFT JOIN liability_statements s ON s.liability_id = l.id AND s.month = ?
    """
    params: list[Any] = [month]
    if clauses:
        sql += f" WHERE {' AND '.join(clauses)}"
    sql += " ORDER BY l.is_active DESC, COALESCE(s.due_date, '9999-12-31'), l.name COLLATE NOCASE"
    today = date.today().isoformat()
    raw_rows = [(dict(row), False) for row in conn.execute(sql, params).fetchall()]
    if not include_without_statement:
        carried_clauses = ["s.month < ?", "s.due_date = ''", "s.remaining_amount > 0"]
        carried_params: list[Any] = [month]
        if not include_inactive:
            carried_clauses.append("l.is_active = 1")
        carried_sql = f"""
            SELECT
                l.id, l.name, l.provider, l.kind, l.statement_day, l.statement_month_offset, l.repayment_account, l.credit_limit,
                l.note, l.is_active, l.created_at, l.updated_at,
                s.month AS statement_month, s.statement_amount, s.remaining_amount,
                s.due_date AS statement_due_date, s.minimum_payment AS statement_minimum_payment,
                (SELECT COUNT(*) FROM liability_payments p
                 WHERE p.liability_id = l.id AND p.statement_month = s.month) AS payment_count,
                (SELECT p.paid_at FROM liability_payments p
                 WHERE p.liability_id = l.id AND p.statement_month = s.month
                 ORDER BY p.paid_at DESC, p.created_at DESC, p.id DESC LIMIT 1) AS latest_payment_date,
                (SELECT p.amount FROM liability_payments p
                 WHERE p.liability_id = l.id AND p.statement_month = s.month
                 ORDER BY p.paid_at DESC, p.created_at DESC, p.id DESC LIMIT 1) AS latest_payment_amount
            FROM liabilities l
            JOIN liability_statements s ON s.liability_id = l.id
            WHERE {' AND '.join(carried_clauses)}
            ORDER BY s.month DESC, l.name COLLATE NOCASE
        """
        raw_rows.extend(
            (dict(row), True)
            for row in conn.execute(carried_sql, carried_params).fetchall()
        )

    items: list[dict[str, Any]] = []
    for raw, is_carried_forward in raw_rows:
        has_statement = bool(raw["statement_month"])
        statement_month = str(raw["statement_month"] or month)
        item = {
            "id": raw["id"],
            "name": raw["name"],
            "provider": raw["provider"],
            "kind": raw["kind"],
            "statement_day": int(raw["statement_day"] or 0),
            "statement_month_offset": int(raw["statement_month_offset"] or 0),
            "repayment_account": raw["repayment_account"],
            "credit_limit": raw["credit_limit"],
            "note": raw["note"],
            "is_active": raw["is_active"],
            "created_at": raw["created_at"],
            "updated_at": raw["updated_at"],
            "statement_month": statement_month,
            "is_carried_forward": is_carried_forward,
            "carried_from_month": statement_month if is_carried_forward else "",
            "due_amount": float(raw["statement_amount"] or 0),
            "remaining_amount": float(raw["remaining_amount"] or 0),
            "due_date": raw["statement_due_date"] or "",
            "minimum_payment": float(raw["statement_minimum_payment"] or 0),
            "paid_amount": round(
                float(raw["statement_amount"] or 0) - float(raw["remaining_amount"] or 0), 2
            ),
            "payment_count": int(raw["payment_count"] or 0),
            "latest_payment_date": raw["latest_payment_date"] or "",
            "latest_payment_amount": float(raw["latest_payment_amount"] or 0),
        }
        item["payment_status"] = (
            "carried_forward" if is_carried_forward else
            "no_statement" if not has_statement else
            "settled" if float(item["remaining_amount"]) <= 0 else
            "no_due_date" if not item["due_date"] else
            "overdue" if item["due_date"] < today else
            "due" if item["due_date"][:7] <= core.current_month() else "upcoming"
        )
        if has_statement:
            charges = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, statement_month, charged_at, amount, category, merchant, note, created_at
                    FROM liability_charges
                    WHERE liability_id = ? AND statement_month = ?
                    ORDER BY charged_at DESC, rowid DESC
                    """,
                    (item["id"], statement_month),
                ).fetchall()
            ]
            charge_total = round(sum(float(charge["amount"]) for charge in charges), 2)
            item["charges"] = charges
            item["charge_total"] = charge_total
            item["unitemized_amount"] = round(
                max(0.0, float(item["due_amount"]) - charge_total), 2
            )
        else:
            item["charges"] = []
            item["charge_total"] = 0.0
            item["unitemized_amount"] = 0.0
        items.append(item)
    due_items = [
        item for item in items
        if not item["is_carried_forward"] and item["payment_status"] != "no_statement"
    ]
    carried_items = [item for item in items if item["is_carried_forward"]]
    overdue_items = [item for item in items if item["payment_status"] == "overdue"]
    available_sql = """
        SELECT DISTINCT s.month
        FROM liability_statements s
        JOIN liabilities l ON l.id = s.liability_id
    """
    if not include_inactive:
        available_sql += " WHERE l.is_active = 1"
    available_sql += " ORDER BY s.month"
    available_months = [str(row[0]) for row in conn.execute(available_sql).fetchall()]
    suggested_month = next((value for value in available_months if value > month), "")
    if not suggested_month and available_months:
        suggested_month = available_months[-1]
    accounts = list_liability_accounts(conn, include_inactive=True)
    return {
        "month": month,
        "items": items,
        "accounts": accounts,
        "available_months": available_months,
        "suggested_month": suggested_month if suggested_month != month else "",
        "summary": {
            "due_amount": round(sum(float(item["due_amount"]) for item in due_items), 2),
            "remaining_amount": round(sum(float(item["remaining_amount"]) for item in due_items), 2),
            "paid_amount": round(sum(float(item["paid_amount"]) for item in due_items), 2),
            "overdue_amount": round(sum(float(item["remaining_amount"]) for item in overdue_items), 2),
            "due_count": len([item for item in due_items if float(item["due_amount"]) > 0]),
            "carried_remaining_amount": round(
                sum(float(item["remaining_amount"]) for item in carried_items), 2
            ),
            "carried_count": len(carried_items),
        },
    }


def list_liability_accounts(
    conn: sqlite3.Connection, include_inactive: bool = False
) -> list[dict[str, Any]]:
    sql = """
        SELECT l.id, l.name, l.provider, l.kind, l.statement_day, l.statement_month_offset, l.repayment_account, l.credit_limit,
               l.note, l.is_active, COUNT(s.id) AS statement_count, MAX(s.month) AS latest_month
        FROM liabilities l
        LEFT JOIN liability_statements s ON s.liability_id = l.id
    """
    if not include_inactive:
        sql += " WHERE l.is_active = 1"
    sql += " GROUP BY l.id ORDER BY l.is_active DESC, l.name COLLATE NOCASE"
    return [dict(row) for row in conn.execute(sql).fetchall()]


def get_liability_payment(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.*, l.name AS liability_name
        FROM liability_payments p
        JOIN liabilities l ON l.id = p.liability_id
        WHERE p.id = ?
        """,
        (payment_id[:80],),
    ).fetchone()
    if row is None:
        raise ValueError("找不到还款记录")
    return dict(row)


def _refresh_liability_statement_after_payments(
    conn: sqlite3.Connection,
    liability_id: str,
    statement_month: str,
) -> dict[str, Any]:
    statement = conn.execute(
        """
        SELECT statement_amount
        FROM liability_statements
        WHERE liability_id = ? AND month = ?
        """,
        (liability_id[:80], statement_month),
    ).fetchone()
    if statement is None:
        raise ValueError("还款对应的月度账单不存在")
    paid_amount = float(
        conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM liability_payments
            WHERE liability_id = ? AND statement_month = ?
            """,
            (liability_id[:80], statement_month),
        ).fetchone()[0]
    )
    statement_amount = float(statement["statement_amount"])
    if paid_amount > statement_amount + 0.001:
        raise ValueError(f"累计还款不能超过本月应还 {statement_amount:.2f}")
    remaining_amount = round(max(0.0, statement_amount - paid_amount), 2)
    now = core.now_iso()
    conn.execute(
        """
        UPDATE liability_statements
        SET remaining_amount = ?, updated_at = ?
        WHERE liability_id = ? AND month = ?
        """,
        (remaining_amount, now, liability_id[:80], statement_month),
    )
    outstanding_balance = float(
        conn.execute(
            """
            SELECT COALESCE(SUM(remaining_amount), 0)
            FROM liability_statements
            WHERE liability_id = ?
            """,
            (liability_id[:80],),
        ).fetchone()[0]
    )
    conn.execute(
        "UPDATE liabilities SET outstanding_balance = ?, updated_at = ? WHERE id = ?",
        (round(outstanding_balance, 2), now, liability_id[:80]),
    )
    return get_liability_for_month(conn, liability_id, statement_month)


def _refresh_liability_outstanding_balance(conn: sqlite3.Connection, liability_id: str) -> None:
    outstanding_balance = liability_statement_balance(conn, liability_id)
    conn.execute(
        "UPDATE liabilities SET outstanding_balance = ?, updated_at = ? WHERE id = ?",
        (outstanding_balance, core.now_iso(), liability_id[:80]),
    )


def get_liability_charge(conn: sqlite3.Connection, charge_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT c.*, l.name AS liability_name
        FROM liability_charges c
        JOIN liabilities l ON l.id = c.liability_id
        WHERE c.id = ?
        """,
        (charge_id[:80],),
    ).fetchone()
    if row is None:
        raise ValueError("找不到信用消费记录")
    return dict(row)


def record_liability_charge(
    conn: sqlite3.Connection,
    liability_id: str,
    amount: float,
    charged_at: str,
    statement_month: str,
    category: str = "待分类",
    merchant: str = "",
    note: str = "",
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Record a credit-funded purchase without moving a real asset account."""
    liability = get_liability(conn, liability_id)
    if not liability["is_active"]:
        raise ValueError("已停用的待还项目不能登记信用消费")
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("信用消费金额必须大于 0")
    charged_at = date.fromisoformat(charged_at).isoformat()
    requested_statement_month = normalize_month(statement_month)
    # Legacy accounts have no billing-cycle setting. Preserve their historical
    # direct-write behaviour; accounts with a statement day are always
    # revalidated here so a stale client cannot bypass the billing rule.
    statement_month = (
        resolve_credit_charge_statement_month(
            conn, liability_id, charged_at, requested_statement_month
        )
        if int(liability.get("statement_day") or 0)
        else requested_statement_month
    )
    category = core.canonical_reference(conn, "category", category or "待分类")
    merchant = str(merchant).strip()[:80]
    if not merchant:
        raise ValueError("信用消费需要填写商户或用途")

    before = get_liability_for_month(conn, liability_id, statement_month)
    if not before["has_statement"]:
        due_date = inherited_liability_due_date(conn, liability_id, statement_month)
        _upsert_liability_statement(
            conn,
            liability_id,
            {
                "due_amount": 0,
                "due_date": due_date,
                "minimum_payment": 0,
            },
            statement_month,
        )
        before = get_liability_for_month(conn, liability_id, statement_month)

    duplicate_cutoff = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
    duplicate = conn.execute(
        """
        SELECT id FROM liability_charges
        WHERE liability_id = ? AND statement_month = ? AND amount = ? AND charged_at = ?
          AND merchant = ? AND created_at >= ?
        LIMIT 1
        """,
        (liability_id[:80], statement_month, amount, charged_at, merchant, duplicate_cutoff),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("检测到刚刚提交的相同信用消费，请勿重复登记")

    charge_id = uuid4().hex
    now = core.now_iso()
    try:
        conn.execute(
            """
            INSERT INTO liability_charges
                (id, liability_id, statement_month, charged_at, amount, category, merchant, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                charge_id,
                liability_id[:80],
                statement_month,
                charged_at,
                amount,
                category,
                merchant,
                str(note).strip()[:300],
                now,
            ),
        )
        conn.execute(
            """
            UPDATE liability_statements
            SET statement_amount = statement_amount + ?, remaining_amount = remaining_amount + ?, updated_at = ?
            WHERE liability_id = ? AND month = ?
            """,
            (amount, amount, now, liability_id[:80], statement_month),
        )
        _refresh_liability_outstanding_balance(conn, liability_id)
        after = get_liability_for_month(conn, liability_id, statement_month)
        core.audit(
            conn,
            "liability.charge",
            {
                "id": liability_id[:80], "charge_id": charge_id, "amount": amount,
                "charged_at": charged_at, "statement_month": statement_month,
                "requested_statement_month": requested_statement_month,
                "category": category, "merchant": merchant, "before": before, "after": after,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"charge": get_liability_charge(conn, charge_id), "liability": after}


def update_liability_charge(
    conn: sqlite3.Connection,
    charge_id: str,
    changes: dict[str, Any],
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Correct one credit purchase and keep every affected statement balanced."""
    before = get_liability_charge(conn, charge_id)
    liability_id = str(before["liability_id"])
    old_month = normalize_month(str(before["statement_month"]))
    clean = {
        "amount": round(float(changes.get("amount", before["amount"])), 2),
        "charged_at": date.fromisoformat(str(changes.get("charged_at", before["charged_at"]))).isoformat(),
        "statement_month": normalize_month(str(changes.get("statement_month", old_month))),
        "category": core.canonical_reference(
            conn, "category", str(changes.get("category", before["category"]) or "待分类")
        ),
        "merchant": str(changes.get("merchant", before["merchant"])).strip()[:80],
        "note": str(changes.get("note", before["note"])).strip()[:300],
    }
    if clean["amount"] <= 0:
        raise ValueError("信用消费金额必须大于 0")
    if not clean["merchant"]:
        raise ValueError("信用消费需要填写商户或用途")

    target_month = clean["statement_month"]
    if target_month != old_month:
        target_statement = get_liability_for_month(conn, liability_id, target_month)
        if not target_statement["has_statement"]:
            _upsert_liability_statement(
                conn,
                liability_id,
                {"due_amount": 0, "due_date": inherited_liability_due_date(conn, liability_id, target_month), "minimum_payment": 0},
                target_month,
            )

    affected_months = (old_month,) if target_month == old_month else (old_month, target_month)
    statement_updates: dict[str, float] = {}
    for month in affected_months:
        statement = conn.execute(
            """
            SELECT statement_amount FROM liability_statements
            WHERE liability_id = ? AND month = ?
            """,
            (liability_id, month),
        ).fetchone()
        if statement is None:
            raise ValueError("信用消费对应的月度账单不存在")
        amount = float(statement["statement_amount"])
        if month == old_month:
            amount -= float(before["amount"])
        if month == target_month:
            amount += clean["amount"]
        paid = float(
            conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM liability_payments
                WHERE liability_id = ? AND statement_month = ?
                """,
                (liability_id, month),
            ).fetchone()[0]
        )
        if amount < paid - 0.001:
            raise ValueError(f"{month} 账单已有还款 {paid:.2f}，不能把应还改低于已还金额")
        statement_updates[month] = round(max(0.0, amount), 2)

    now = core.now_iso()
    try:
        conn.execute(
            """
            UPDATE liability_charges
            SET statement_month = ?, charged_at = ?, amount = ?, category = ?, merchant = ?, note = ?
            WHERE id = ?
            """,
            (
                clean["statement_month"], clean["charged_at"], clean["amount"], clean["category"],
                clean["merchant"], clean["note"], charge_id[:80],
            ),
        )
        for month, amount in statement_updates.items():
            conn.execute(
                """
                UPDATE liability_statements
                SET statement_amount = ?, remaining_amount = ?, updated_at = ?
                WHERE liability_id = ? AND month = ?
                """,
                (
                    amount,
                    round(amount - float(conn.execute(
                        """
                        SELECT COALESCE(SUM(amount), 0) FROM liability_payments
                        WHERE liability_id = ? AND statement_month = ?
                        """,
                        (liability_id, month),
                    ).fetchone()[0]), 2),
                    now,
                    liability_id,
                    month,
                ),
            )
        _refresh_liability_outstanding_balance(conn, liability_id)
        after = get_liability_charge(conn, charge_id)
        liability = get_liability_for_month(conn, liability_id, target_month)
        core.audit(
            conn,
            "liability.charge.update",
            {"id": liability_id, "charge_id": charge_id[:80], "before": before, "after": after},
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"charge": after, "liability": liability}


def record_liability_payment(
    conn: sqlite3.Connection,
    liability_id: str,
    amount: float,
    paid_at: str = "",
    note: str = "",
    statement_month: str = "",
    payment_account: str = "",
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    account = get_liability(conn, liability_id)
    if not account["is_active"]:
        raise ValueError("已停用的待还项目不能登记还款")
    statement_month = normalize_month(statement_month or str(account["due_date"])[:7])
    before = get_liability_for_month(conn, liability_id, statement_month)
    if not before["has_statement"]:
        raise ValueError("该月份没有待还账单，请先录入本月应还")
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("还款金额必须大于 0")
    if amount > float(before["remaining_amount"]):
        raise ValueError("还款金额不能超过本月未还金额")
    paid_at = date.fromisoformat(paid_at).isoformat() if paid_at else date.today().isoformat()
    payment_account = core.ensure_asset_account(
        conn, payment_account or str(account["repayment_account"] or "未指定")
    )
    duplicate_cutoff = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
    duplicate = conn.execute(
        """
        SELECT id FROM liability_payments
        WHERE liability_id = ? AND statement_month = ? AND amount = ? AND paid_at = ?
          AND created_at >= ?
        LIMIT 1
        """,
        (liability_id[:80], statement_month, amount, paid_at, duplicate_cutoff),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("检测到刚刚提交的相同还款，请勿重复登记")
    payment_id = uuid4().hex
    now = core.now_iso()
    try:
        conn.execute(
            """INSERT INTO liability_payments
                (id, liability_id, amount, paid_at, account, note, created_at, statement_month)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payment_id,
                liability_id[:80],
                amount,
                paid_at,
                payment_account,
                str(note).strip()[:300],
                now,
                statement_month,
            ),
        )
        after = _refresh_liability_statement_after_payments(
            conn, liability_id, statement_month
        )
        core.audit(
            conn,
            "liability.payment",
            {
                "id": liability_id[:80],
                "payment_id": payment_id,
                "amount": amount,
                "paid_at": paid_at,
                "account": payment_account,
                "before": before,
                "after": after,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {
        "payment_id": payment_id,
        "payment": get_liability_payment(conn, payment_id),
        "liability": after,
    }


def update_liability_payment(
    conn: sqlite3.Connection,
    payment_id: str,
    changes: dict[str, Any],
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    before = get_liability_payment(conn, payment_id)
    clean_changes: dict[str, Any] = {}
    if "amount" in changes and changes["amount"] is not None:
        amount = round(float(changes["amount"]), 2)
        if amount <= 0:
            raise ValueError("还款金额必须大于 0")
        clean_changes["amount"] = amount
    if "paid_at" in changes and changes["paid_at"] is not None:
        clean_changes["paid_at"] = date.fromisoformat(str(changes["paid_at"])).isoformat()
    if "account" in changes and changes["account"] is not None:
        clean_changes["account"] = core.ensure_asset_account(conn, str(changes["account"] or "未指定"))
    if "note" in changes and changes["note"] is not None:
        clean_changes["note"] = str(changes["note"]).strip()[:300]
    if not clean_changes:
        raise ValueError("没有可更新的还款字段")
    try:
        assignments = ", ".join(f"{field} = ?" for field in clean_changes)
        conn.execute(
            f"UPDATE liability_payments SET {assignments} WHERE id = ?",
            (*clean_changes.values(), payment_id[:80]),
        )
        liability = _refresh_liability_statement_after_payments(
            conn, before["liability_id"], before["statement_month"]
        )
        after = get_liability_payment(conn, payment_id)
        core.audit(
            conn,
            "liability.payment.update",
            {
                "id": before["liability_id"],
                "payment_id": payment_id[:80],
                "before": before,
                "after": after,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"payment": after, "liability": liability}


def delete_liability_payment(
    conn: sqlite3.Connection,
    payment_id: str,
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    before = get_liability_payment(conn, payment_id)
    try:
        conn.execute("DELETE FROM liability_payments WHERE id = ?", (payment_id[:80],))
        liability = _refresh_liability_statement_after_payments(
            conn, before["liability_id"], before["statement_month"]
        )
        core.audit(
            conn,
            "liability.payment.delete",
            {
                "id": before["liability_id"],
                "payment_id": payment_id[:80],
                "before": before,
                "after": liability,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"deleted": before, "liability": liability}


def liability_payment_total(conn: sqlite3.Connection, month: str) -> float:
    start, end = month_range(normalize_month(month))
    value = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM liability_payments WHERE paid_at BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]
    return round(float(value or 0), 2)


def liability_outstanding_total(conn: sqlite3.Connection) -> float:
    """Return all unpaid statement balances, including future statement months."""
    value = conn.execute(
        "SELECT COALESCE(SUM(remaining_amount), 0) FROM liability_statements"
    ).fetchone()[0]
    return round(float(value or 0), 2)


def _next_month(month: str) -> str:
    year, month_number = map(int, normalize_month(month).split("-"))
    if month_number == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month_number + 1:02d}"
