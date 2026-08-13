from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from financial_agent import (
    DuplicateTransactionError,
    add_transaction,
    add_transactions,
    agent_tool_catalog,
    apply_management_proposals,
    auto_classify_pending_transactions,
    audit,
    backup_path,
    connect,
    create_backup,
    create_asset_account,
    create_transfer,
    create_reference_value,
    current_month,
    db_path,
    delete_budget,
    delete_asset_account,
    delete_liability_payment,
    init_db,
    chat_history,
    get_chat_request,
    get_preferences,
    list_agent_runs,
    list_accounts,
    list_backups,
    list_budgets,
    list_operation_logs,
    list_reference_values,
    list_inbox_items,
    load_environment,
    log_agent_run,
    llm_model,
    llm_uses_responses_api,
    llm_provider,
    llm_provider_catalog,
    present_model_error,
    merge_reference_value,
    preview_last_transaction_action,
    redact_sensitive_text,
    recover_stale_chat_requests,
    recent_financial_records,
    reconcile_account,
    restore_backup,
    search_transactions,
    set_llm_selection,
    set_preference,
    soft_delete_transaction,
    summarize,
    undo_last_transaction_action,
    update_reference_value,
    update_chat_request,
    update_asset_account,
    update_inbox_item,
    update_transaction,
    upsert_budget,
    validate_transaction_payload,
    where_money_went,
    create_inbox_item,
    create_liability,
    record_liability_charge,
    create_subscription,
    clear_chat_history,
    decode_chat_image_data_url,
    get_message_attachment,
    capital_overview,
    list_liabilities,
    liability_outstanding_total,
    liability_payment_total,
    list_subscriptions,
    record_liability_payment,
    record_subscription_charge,
    reverse_subscription_charge,
    set_capital_balance,
    skip_subscription_charge,
    update_liability,
    update_liability_charge,
    update_liability_payment,
    update_subscription,
)
from services.chat import chat_request_can_resume, process_chat_request
from services.reminder import (
    REMINDER_PREFERENCE_KEY,
    reminder_view,
    set_reminder_skip_for_today,
    update_reminder_settings,
)
from services.reminder_scheduler import schedule_windows_reminder_sync
from services.personal_memory import (
    PERSONAL_MEMORY_PREFERENCE_KEY,
    create_personal_memory,
    delete_personal_memory,
    list_personal_memories,
    update_personal_memory,
)
from services.transcription import transcription_status, transcribe_audio


# Explicit process environment variables must win over local .env values.
load_environment(override=False)
app = FastAPI(title="Personal Ledger Agent", docs_url="/api/docs", redoc_url=None)
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(default="web", min_length=1, max_length=80)
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=80)


class ImageChatRequest(BaseModel):
    text: str = Field(default="", max_length=1000)
    images: list[str] = Field(min_length=1, max_length=3)
    session_id: str = Field(default="web", min_length=1, max_length=80)
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=80)


class InboxCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class InboxStatus(BaseModel):
    status: str = Field(pattern=r"^(pending|processing|archived)$")


class SubscriptionPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0)
    cycle_months: int = Field(default=1)
    next_charge_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str = Field(default="其他", max_length=40)
    account: str = Field(default="未指定", max_length=40)
    note: str = Field(default="", max_length=300)
    is_active: bool = True


class SubscriptionChanges(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    amount: float | None = Field(default=None, gt=0)
    cycle_months: int | None = None
    next_charge_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str | None = Field(default=None, max_length=40)
    account: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None


class SubscriptionSkip(BaseModel):
    expected_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class LiabilityPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default="", max_length=80)
    kind: str = Field(default="other", max_length=30)
    statement_day: int = Field(default=0, ge=0, le=31)
    statement_month_offset: int = Field(default=1, ge=0, le=1)
    statement_month: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}$")
    due_amount: float = Field(default=0, ge=0)
    due_date: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    minimum_payment: float = Field(default=0, ge=0)
    repayment_account: str = Field(default="未指定", max_length=40)
    credit_limit: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=300)
    is_active: bool = True


class LiabilityChanges(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, max_length=30)
    statement_day: int | None = Field(default=None, ge=0, le=31)
    statement_month_offset: int | None = Field(default=None, ge=0, le=1)
    statement_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    source_statement_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    due_amount: float | None = Field(default=None, ge=0)
    due_date: str | None = Field(default=None, pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    minimum_payment: float | None = Field(default=None, ge=0)
    repayment_account: str | None = Field(default=None, max_length=40)
    credit_limit: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None


class LiabilityPayment(BaseModel):
    amount: float = Field(gt=0)
    paid_at: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    statement_month: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}$")
    account: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=300)


class LiabilityPaymentChanges(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    paid_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    account: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=300)


class LiabilityCharge(BaseModel):
    amount: float = Field(gt=0)
    charged_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    statement_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    category: str = Field(min_length=1, max_length=40)
    merchant: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=300)


class LiabilityChargeChanges(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    charged_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    statement_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    category: str | None = Field(default=None, min_length=1, max_length=40)
    merchant: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=300)


class CapitalPayload(BaseModel):
    current_balance: float = Field(ge=0)


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: str = Field(default="other", pattern=r"^(wallet|bank|cash|other)$")
    actual_balance: float = Field(default=0)
    reconciled_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")


class AccountReconciliation(BaseModel):
    account: str = Field(min_length=1, max_length=40)
    actual_balance: float
    reconciled_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    note: str = Field(default="", max_length=300)


class AccountChanges(BaseModel):
    new_name: str | None = Field(default=None, min_length=1, max_length=40)
    kind: str | None = Field(default=None, pattern=r"^(wallet|bank|cash|other)$")
    actual_balance: float | None = None
    reconciled_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")


class TransferPayload(BaseModel):
    source_account: str = Field(min_length=1, max_length=40)
    target_account: str = Field(min_length=1, max_length=40)
    amount: float = Field(gt=0)
    transferred_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    note: str = Field(default="", max_length=300)


class ManagementProposalConfirmation(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    proposals: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    remaining_proposals: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    complete_request: bool = True


class ManagementProposalChanges(BaseModel):
    proposals: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class TransactionConfirmation(BaseModel):
    date: str
    amount: float = Field(gt=0)
    direction: str
    category: str
    account: str
    merchant: str = ""
    note: str = ""
    raw_text: str = ""
    allow_duplicate: bool = False
    category_confidence: float = Field(default=1.0, ge=0, le=1)
    category_reason: str = Field(default="", max_length=200)
    classification_source: str = Field(default="llm", max_length=30)
    suggested_category: str = Field(default="", max_length=40)
    proposed_category: str = Field(default="", max_length=40)
    needs_category_review: bool = False


class BatchConfirmation(BaseModel):
    transactions: list[TransactionConfirmation] = Field(min_length=1, max_length=20)
    allow_duplicate: bool = False
    request_id: str = Field(default="", max_length=80)
    complete_request: bool = True


class CreditChargeConfirmation(BaseModel):
    liability_id: str = Field(min_length=1, max_length=80)
    statement_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: float = Field(gt=0)
    charged_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str = Field(min_length=1, max_length=40)
    merchant: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=300)


class MixedBatchConfirmation(BaseModel):
    transactions: list[TransactionConfirmation] = Field(default_factory=list, max_length=20)
    credit_charges: list[CreditChargeConfirmation] = Field(default_factory=list, max_length=20)
    allow_duplicate: bool = False
    request_id: str = Field(default="", max_length=80)
    complete_request: bool = True


class ReferenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    is_favorite: bool = False


class ReferenceUpdate(BaseModel):
    new_name: str | None = Field(default=None, min_length=1, max_length=40)
    aliases: list[str] | None = Field(default=None, max_length=20)
    is_favorite: bool | None = None


class ReferenceMerge(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=40)


class RestorePayload(BaseModel):
    confirmation: str


class ClassifyPayload(BaseModel):
    limit: int = Field(default=50, ge=1, le=50)


class TransactionChanges(BaseModel):
    date: str | None = None
    amount: float | None = Field(default=None, gt=0)
    direction: str | None = None
    category: str | None = None
    account: str | None = None
    merchant: str | None = None
    note: str | None = None


class BudgetPayload(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: float = Field(ge=0)


class ModelPayload(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=128)


class ReminderSettingsPayload(BaseModel):
    enabled: bool | None = None
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ReminderTodayPayload(BaseModel):
    skip: bool


class PersonalMemoryPayload(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=500)


class PersonalMemoryChanges(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=60)
    content: str | None = Field(default=None, min_length=1, max_length=500)
    enabled: bool | None = None


@contextmanager
def database() -> Iterator[Any]:
    conn = connect()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=redact_sensitive_text(str(exc)))


def validate_image_data_urls(images: list[str]) -> tuple[str, ...]:
    validated = []
    for image in images:
        value = str(image).strip()
        decode_chat_image_data_url(value)
        validated.append(value)
    return tuple(validated)


def duplicate_error(exc: DuplicateTransactionError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "duplicate_transaction", "message": str(exc), "duplicates": exc.duplicates},
    )


@app.middleware("http")
async def disable_browser_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    provider = llm_provider()
    providers = llm_provider_catalog()
    active = next(item for item in providers if item["id"] == provider)
    return {
        "status": "ok",
        "provider": provider,
        "provider_label": active["label"],
        "providers": providers,
        "model": llm_model(),
        "api_key_configured": active["configured"],
        "speech_to_text": transcription_status(),
        "database": str(db_path()),
    }


@app.get("/api/agent/tools")
def agent_tools() -> dict[str, Any]:
    return {"tools": agent_tool_catalog()}


@app.put("/api/settings/model")
def change_model(payload: ModelPayload) -> dict[str, str]:
    try:
        selection = set_llm_selection(payload.provider, payload.model)
        with database() as conn:
            audit(conn, "settings.model.change", selection, source="web")
            conn.commit()
        return selection
    except (OSError, ValueError) as exc:
        raise api_error(exc) from exc


def current_reminder_settings(conn: Any) -> dict[str, Any]:
    return get_preferences(conn).get(REMINDER_PREFERENCE_KEY, {})


@app.get("/api/settings/reminder")
def get_reminder_settings() -> dict[str, Any]:
    with database() as conn:
        return reminder_view(current_reminder_settings(conn))


@app.put("/api/settings/reminder")
def change_reminder_settings(payload: ReminderSettingsPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            settings = update_reminder_settings(
                current_reminder_settings(conn),
                enabled=payload.enabled,
                reminder_time=payload.time,
            )
            set_preference(conn, REMINDER_PREFERENCE_KEY, settings)
            result = reminder_view(settings)
            audit(conn, "settings.reminder.change", result, source="web")
            conn.commit()
            result["scheduler_sync_scheduled"] = schedule_windows_reminder_sync(
                result["time"], enabled=result["enabled"]
            )
            return result
    except ValueError as exc:
        raise api_error(exc) from exc


@app.put("/api/settings/reminder/today")
def change_reminder_today(payload: ReminderTodayPayload) -> dict[str, Any]:
    with database() as conn:
        settings = set_reminder_skip_for_today(
            current_reminder_settings(conn), skip=payload.skip
        )
        set_preference(conn, REMINDER_PREFERENCE_KEY, settings)
        result = reminder_view(settings)
        audit(conn, "settings.reminder.today", result, source="web")
        conn.commit()
        return result


def current_personal_memories(conn: Any) -> list[dict[str, Any]]:
    return get_preferences(conn).get(PERSONAL_MEMORY_PREFERENCE_KEY, [])


@app.get("/api/personal-memories")
def get_personal_memories() -> dict[str, Any]:
    with database() as conn:
        return {"items": list_personal_memories(current_personal_memories(conn))}


@app.post("/api/personal-memories")
def add_personal_memory(payload: PersonalMemoryPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            memories, memory = create_personal_memory(
                current_personal_memories(conn), title=payload.title, content=payload.content
            )
            set_preference(conn, PERSONAL_MEMORY_PREFERENCE_KEY, memories)
            audit(
                conn,
                "settings.personal_memory.create",
                {"id": memory["id"], "title": memory["title"]},
                source="web",
            )
            conn.commit()
            return {"memory": memory}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.patch("/api/personal-memories/{memory_id}")
def change_personal_memory(memory_id: str, payload: PersonalMemoryChanges) -> dict[str, Any]:
    try:
        with database() as conn:
            memories, memory = update_personal_memory(
                current_personal_memories(conn),
                memory_id,
                title=payload.title,
                content=payload.content,
                enabled=payload.enabled,
            )
            set_preference(conn, PERSONAL_MEMORY_PREFERENCE_KEY, memories)
            audit(
                conn,
                "settings.personal_memory.update",
                {"id": memory["id"], "title": memory["title"], "enabled": memory["enabled"]},
                source="web",
            )
            conn.commit()
            return {"memory": memory}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.delete("/api/personal-memories/{memory_id}")
def remove_personal_memory(memory_id: str) -> dict[str, Any]:
    try:
        with database() as conn:
            memories = delete_personal_memory(current_personal_memories(conn), memory_id)
            set_preference(conn, PERSONAL_MEMORY_PREFERENCE_KEY, memories)
            audit(conn, "settings.personal_memory.delete", {"id": memory_id}, source="web")
            conn.commit()
            return {"deleted": True, "id": memory_id}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/dashboard")
def dashboard(month: str = Query(default_factory=current_month, pattern=r"^\d{4}-\d{2}$")) -> dict[str, Any]:
    try:
        with database() as conn:
            summary = summarize(conn, month)
            subscriptions = list_subscriptions(conn, month=month)
            liabilities = list_liabilities(conn, month=month)
            capital = capital_overview(conn, month)
            scheduled_subscriptions = float(subscriptions["summary"]["scheduled_amount"])
            liability_paid = float(liabilities["summary"]["paid_amount"])
            liability_remaining = float(liabilities["summary"]["remaining_amount"])
            current_debt = liability_outstanding_total(conn)
            repayment_outflow = liability_payment_total(conn, month)
            cash_change = round(
                float(summary["income"]) - float(summary["cash_expense"]) - repayment_outflow,
                2,
            )
            return {
                "month": month,
                "summary": summary,
                "forecast": {
                    "scheduled_subscriptions": scheduled_subscriptions,
                    "liability_due": float(liabilities["summary"]["due_amount"]),
                    "liability_paid": liability_paid,
                    "liability_remaining": liability_remaining,
                    "current_debt": current_debt,
                    "repayment_outflow": repayment_outflow,
                    "cash_change": cash_change,
                },
                "capital": capital,
                "where": where_money_went(conn, month=month, group_by="category", limit=8),
                "recent": recent_financial_records(conn, month=month, limit=12),
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/accounts")
def accounts(include_inactive: bool = False) -> dict[str, Any]:
    with database() as conn:
        return list_accounts(conn, include_inactive=include_inactive)


@app.post("/api/accounts")
def add_account(payload: AccountCreate) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"account": create_asset_account(conn, **payload.model_dump())}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/accounts/{name}")
def edit_account(name: str, payload: AccountChanges) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"account": update_asset_account(conn, name, **payload.model_dump())}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.delete("/api/accounts/{name}")
def remove_account(name: str) -> dict[str, Any]:
    try:
        with database() as conn:
            return delete_asset_account(conn, name)
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/accounts/reconcile")
def reconcile(payload: AccountReconciliation) -> dict[str, Any]:
    try:
        with database() as conn:
            data = payload.model_dump()
            return {
                "reconciliation": reconcile_account(
                    conn,
                    account_name=data["account"],
                    actual_balance=data["actual_balance"],
                    reconciled_on=data["reconciled_on"],
                    note=data["note"],
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/transfers")
def transfer(payload: TransferPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"transfer": create_transfer(conn, **payload.model_dump())}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    try:
        return process_chat_request(
            request_id=request.request_id,
            session_id=request.session_id,
            text=request.text,
        )
    except Exception as exc:
        if isinstance(exc, (RuntimeError, ValueError, TypeError)):
            raise api_error(exc) from exc
        detail = present_model_error(exc)
        raise HTTPException(status_code=502, detail=f"模型请求失败: {detail}") from exc


@app.post("/api/chat/image")
def chat_with_images(request: ImageChatRequest) -> dict[str, Any]:
    try:
        if not llm_uses_responses_api(llm_model()):
            raise ValueError("当前模型不支持图片输入，请在模型设置中选择支持图片的火山方舟模型")
        return process_chat_request(
            request_id=request.request_id,
            session_id=request.session_id,
            text=request.text or "请识别图片中的账单并生成草稿",
            image_data_urls=validate_image_data_urls(request.images),
        )
    except Exception as exc:
        if isinstance(exc, (RuntimeError, ValueError, TypeError)):
            raise api_error(exc) from exc
        raise HTTPException(status_code=502, detail="图片账单识别失败") from exc


@app.post("/api/audio/transcriptions")
async def transcribe_voice_note(request: Request) -> dict[str, Any]:
    try:
        audio = await request.body()
        text = await asyncio.to_thread(
            transcribe_audio,
            audio,
            filename=request.headers.get("X-Ledger-Audio-Name", "voice-note.webm"),
            media_type=request.headers.get("content-type", ""),
        )
        return {"text": text, "speech_to_text": transcription_status()}
    except ValueError as exc:
        raise api_error(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=redact_sensitive_text(str(exc))) from exc


@app.get("/api/chat/history")
def get_history(
    session_id: str = Query(default="web", min_length=1, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    with database() as conn:
        return chat_history(conn, session_id, limit=limit)


@app.delete("/api/chat/history")
def clear_history(session_id: str = Query(default="web", min_length=1, max_length=80)) -> dict[str, Any]:
    try:
        with database() as conn:
            return clear_chat_history(conn, session_id)
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/chat/attachments/{attachment_id}")
def chat_attachment(attachment_id: str) -> Response:
    try:
        with database() as conn:
            attachment = get_message_attachment(conn, attachment_id)
        return Response(
            content=attachment["data"],
            media_type=attachment["media_type"],
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/chat/requests/{request_id}")
def chat_request_status(request_id: str) -> dict[str, Any]:
    try:
        with database() as conn:
            recover_stale_chat_requests(conn)
            item = get_chat_request(conn, request_id)
            if item is None:
                raise ValueError("找不到聊天请求")
            item["can_resume"] = chat_request_can_resume(item)
            return item
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/chat/requests/{request_id}/events")
async def chat_request_events(request_id: str) -> StreamingResponse:
    async def event_stream():
        previous = ""
        for _ in range(300):
            try:
                with database() as conn:
                    item = get_chat_request(conn, request_id)
            except ValueError:
                item = None
            if item is not None:
                payload = {
                    "request_id": item["request_id"],
                    "status": item["status"],
                    "stage": item.get("progress_stage") or "queued",
                    "message": item.get("progress_message") or "正在排队",
                    "updated_at": item["updated_at"],
                }
                encoded = json.dumps(payload, ensure_ascii=False)
                if encoded != previous:
                    yield f"event: progress\ndata: {encoded}\n\n"
                    previous = encoded
                if item["status"] in {"awaiting_confirmation", "completed", "error", "dismissed"}:
                    yield "event: done\ndata: {}\n\n"
                    return
            else:
                yield ": waiting for request\n\n"
            await asyncio.sleep(0.35)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/chat/requests/{request_id}/resume")
def resume_chat_request(request_id: str) -> dict[str, Any]:
    try:
        return process_chat_request(
            request_id=request_id,
            session_id="",
            text="",
            resume=True,
        )
    except Exception as exc:
        if isinstance(exc, (RuntimeError, ValueError, TypeError)):
            raise api_error(exc) from exc
        detail = present_model_error(exc)
        raise HTTPException(status_code=502, detail=f"模型请求失败: {detail}") from exc


@app.post("/api/chat/requests/{request_id}/dismiss")
def dismiss_chat_request(request_id: str) -> dict[str, Any]:
    try:
        with database() as conn:
            item = get_chat_request(conn, request_id)
            if item is None:
                raise ValueError("找不到聊天请求")
            if item["status"] != "awaiting_confirmation":
                raise ValueError("只有待确认草稿可以取消")
            return update_chat_request(
                conn,
                request_id,
                "dismissed",
                action=item["action"],
                result=item["result"],
            )
    except ValueError as exc:
        raise api_error(exc) from exc


@app.post("/api/management-proposals/confirm")
def confirm_management_proposals(payload: ManagementProposalConfirmation) -> dict[str, Any]:
    try:
        with database() as conn:
            request_item = get_chat_request(conn, payload.request_id)
            if request_item is None or request_item["status"] != "awaiting_confirmation":
                raise ValueError("找不到待确认的订阅或待还草稿")
            if request_item["action"] != "management":
                raise ValueError("该请求不是订阅或待还草稿")
            conn.execute("BEGIN")
            try:
                result = apply_management_proposals(
                    conn, payload.proposals, actor="web", commit=False
                )
                if payload.complete_request:
                    update_chat_request(
                        conn,
                        payload.request_id,
                        "completed",
                        action="management",
                        result=request_item["result"],
                        commit=False,
                    )
                elif payload.remaining_proposals is not None:
                    pending_result = {**request_item["result"], "proposals": payload.remaining_proposals}
                    update_chat_request(
                        conn,
                        payload.request_id,
                        "awaiting_confirmation",
                        action="management",
                        result=pending_result,
                        commit=False,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return result
    except DuplicateTransactionError as exc:
        raise duplicate_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/chat/requests/{request_id}/management-proposals")
def update_management_proposals(
    request_id: str, payload: ManagementProposalChanges
) -> dict[str, Any]:
    try:
        with database() as conn:
            request_item = get_chat_request(conn, request_id)
            if request_item is None or request_item["status"] != "awaiting_confirmation":
                raise ValueError("找不到待确认的订阅或待还草稿")
            if request_item["action"] != "management":
                raise ValueError("该请求不是订阅或待还草稿")
            if not payload.proposals:
                return update_chat_request(
                    conn,
                    request_id,
                    "dismissed",
                    action="management",
                    result=request_item["result"],
                )
            pending_result = {**request_item["result"], "proposals": payload.proposals}
            return update_chat_request(
                conn,
                request_id,
                "awaiting_confirmation",
                action="management",
                result=pending_result,
            )
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/inbox")
def inbox(status: str = Query(default="pending", pattern=r"^(|pending|processing|archived)$")) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"items": list_inbox_items(conn, status=status)}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.post("/api/inbox")
def create_inbox(payload: InboxCreate) -> dict[str, Any]:
    with database() as conn:
        return {"item": create_inbox_item(conn, payload.text)}


@app.patch("/api/inbox/{item_id}")
def update_inbox(item_id: str, payload: InboxStatus) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"item": update_inbox_item(conn, item_id, payload.status)}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/subscriptions")
def subscriptions(
    month: str = Query(default_factory=current_month, pattern=r"^\d{4}-\d{2}$"),
    include_inactive: bool = False,
) -> dict[str, Any]:
    try:
        with database() as conn:
            return list_subscriptions(conn, month=month, include_inactive=include_inactive)
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/subscriptions")
def add_subscription(payload: SubscriptionPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"subscription": create_subscription(conn, payload.model_dump())}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/subscriptions/{subscription_id}")
def edit_subscription(subscription_id: str, payload: SubscriptionChanges) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"subscription": update_subscription(conn, subscription_id, payload.model_dump(exclude_none=True))}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/subscriptions/{subscription_id}/charge")
def charge_subscription(subscription_id: str) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"charged": record_subscription_charge(conn, subscription_id)}
    except DuplicateTransactionError as exc:
        raise duplicate_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.delete("/api/subscription-charges/{transaction_id}")
def undo_subscription_charge(transaction_id: int) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"reversed": reverse_subscription_charge(conn, transaction_id)}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/subscriptions/{subscription_id}/skip")
def skip_subscription(subscription_id: str, payload: SubscriptionSkip) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "skip": skip_subscription_charge(
                    conn, subscription_id, expected_date=payload.expected_date
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/liabilities")
def liabilities(
    month: str = Query(default_factory=current_month, pattern=r"^\d{4}-\d{2}$"),
    include_inactive: bool = False,
) -> dict[str, Any]:
    try:
        with database() as conn:
            return list_liabilities(conn, month=month, include_inactive=include_inactive)
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.put("/api/capital/{month}")
def save_capital(month: str, payload: CapitalPayload) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=422, detail="月份格式必须是 YYYY-MM")
    try:
        with database() as conn:
            return {"capital": set_capital_balance(conn, month, payload.current_balance)}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/liabilities")
def add_liability(payload: LiabilityPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"liability": create_liability(conn, payload.model_dump())}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/liabilities/{liability_id}")
def edit_liability(liability_id: str, payload: LiabilityChanges) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "liability": update_liability(
                    conn, liability_id, payload.model_dump(exclude_unset=True)
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/liabilities/{liability_id}/payments")
def pay_liability(liability_id: str, payload: LiabilityPayment) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "payment": record_liability_payment(
                    conn,
                    liability_id,
                    payload.amount,
                    payload.paid_at,
                    payload.note,
                    payload.statement_month,
                    payload.account,
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/liabilities/{liability_id}/charges")
def charge_liability(liability_id: str, payload: LiabilityCharge) -> dict[str, Any]:
    try:
        with database() as conn:
            return record_liability_charge(
                conn,
                liability_id,
                payload.amount,
                payload.charged_at,
                payload.statement_month,
                payload.category,
                payload.merchant,
                payload.note,
            )
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/liability-charges/{charge_id}")
def edit_liability_charge(
    charge_id: str, payload: LiabilityChargeChanges
) -> dict[str, Any]:
    try:
        with database() as conn:
            return update_liability_charge(
                conn, charge_id, payload.model_dump(exclude_unset=True)
            )
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/liability-payments/{payment_id}")
def edit_liability_payment(
    payment_id: str, payload: LiabilityPaymentChanges
) -> dict[str, Any]:
    try:
        with database() as conn:
            return update_liability_payment(
                conn,
                payment_id,
                payload.model_dump(exclude_unset=True),
            )
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.delete("/api/liability-payments/{payment_id}")
def remove_liability_payment(payment_id: str) -> dict[str, Any]:
    try:
        with database() as conn:
            return delete_liability_payment(conn, payment_id)
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/transactions")
def transactions(
    month: str = Query(default="", pattern=r"^$|^\d{4}-\d{2}$"),
    query: str = "",
    category: str = "",
    account: str = "",
    direction: str = Query(default="", pattern=r"^$|^income$|^expense$"),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "results": search_transactions(
                    conn,
                    month=month,
                    query=query,
                    category=category,
                    account=account,
                    direction=direction,
                    limit=limit,
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/financial-records")
def financial_records(
    month: str = Query(default="", pattern=r"^$|^\d{4}-\d{2}$"),
    query: str = "",
    direction: str = Query(default="", pattern=r"^$|^income$|^expense$|^repayment$|^transfer$|^liability$"),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "results": recent_financial_records(
                    conn,
                    month=month,
                    query=query,
                    direction=direction,
                    limit=limit,
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.patch("/api/transactions/{transaction_id}")
def edit_transaction(transaction_id: int, payload: TransactionChanges) -> dict[str, Any]:
    try:
        with database() as conn:
            updated = update_transaction(
                conn,
                transaction_id,
                payload.model_dump(exclude_none=True),
                actor="web",
            )
        return {"updated": updated}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.delete("/api/transactions/{transaction_id}")
def delete_transaction(transaction_id: int) -> dict[str, Any]:
    try:
        with database() as conn:
            deleted = soft_delete_transaction(conn, transaction_id, actor="web")
        return {"deleted": deleted}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/undo")
def undo_transaction() -> dict[str, Any]:
    try:
        with database() as conn:
            return undo_last_transaction_action(conn, actor="web")
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/undo/preview")
def undo_preview() -> dict[str, Any]:
    try:
        with database() as conn:
            return preview_last_transaction_action(conn)
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/budgets")
def budgets(month: str = Query(default_factory=current_month, pattern=r"^\d{4}-\d{2}$")) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"month": month, "budgets": list_budgets(conn, month)}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.put("/api/budgets/{category}")
def save_budget(category: str, payload: BudgetPayload) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "budget": upsert_budget(
                    conn,
                    payload.month,
                    category,
                    payload.amount,
                    actor="web",
                )
            }
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.delete("/api/budgets/{category}")
def remove_budget(
    category: str, month: str = Query(pattern=r"^\d{4}-\d{2}$")
) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"budget": delete_budget(conn, month, category, actor="web")}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/transactions/confirm")
def confirm_transaction(payload: TransactionConfirmation) -> dict[str, Any]:
    try:
        draft = validate_transaction_payload(
            payload.model_dump(exclude={"raw_text", "allow_duplicate"}), raw_text=payload.raw_text
        )
        with database() as conn:
            transaction_id = add_transaction(
                conn, draft, actor="web", allow_duplicate=payload.allow_duplicate
            )
        return {"written": True, "id": transaction_id, "transaction": asdict(draft)}
    except DuplicateTransactionError as exc:
        raise duplicate_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/transactions/confirm-batch")
def confirm_transaction_batch(payload: BatchConfirmation) -> dict[str, Any]:
    try:
        drafts = [
            validate_transaction_payload(
                item.model_dump(exclude={"raw_text", "allow_duplicate"}),
                raw_text=item.raw_text,
            )
            for item in payload.transactions
        ]
        with database() as conn:
            result = add_transactions(
                conn, drafts, actor="web", allow_duplicate=payload.allow_duplicate
            )
            if payload.request_id and payload.complete_request:
                request_item = get_chat_request(conn, payload.request_id)
                if request_item is not None and request_item["status"] == "awaiting_confirmation":
                    update_chat_request(
                        conn,
                        payload.request_id,
                        "completed",
                        action=request_item["action"],
                        result=request_item["result"],
                    )
        return {**result, "transactions": [asdict(draft) for draft in drafts]}
    except DuplicateTransactionError as exc:
        raise duplicate_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/transactions/confirm-mixed")
def confirm_mixed_transaction_batch(payload: MixedBatchConfirmation) -> dict[str, Any]:
    if not payload.transactions and not payload.credit_charges:
        raise api_error(ValueError("至少需要一笔直接支付或信贷消费"))
    try:
        drafts = [
            validate_transaction_payload(
                item.model_dump(exclude={"raw_text", "allow_duplicate"}), raw_text=item.raw_text
            )
            for item in payload.transactions
        ]
        with database() as conn:
            conn.execute("BEGIN")
            try:
                result = (
                    add_transactions(
                        conn, drafts, actor="web", allow_duplicate=payload.allow_duplicate, commit=False
                    )
                    if drafts
                    else {"ids": [], "batch_id": "", "written": 0}
                )
                charges = [
                    record_liability_charge(
                        conn,
                        item.liability_id,
                        item.amount,
                        item.charged_at,
                        item.statement_month,
                        item.category,
                        item.merchant,
                        item.note,
                        actor="web",
                        commit=False,
                    )
                    for item in payload.credit_charges
                ]
                if payload.request_id and payload.complete_request:
                    request_item = get_chat_request(conn, payload.request_id)
                    if request_item is not None and request_item["status"] == "awaiting_confirmation":
                        update_chat_request(
                            conn,
                            payload.request_id,
                            "completed",
                            action=request_item["action"],
                            result=request_item["result"],
                            commit=False,
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            **result,
            "credit_charge_count": len(charges),
            "transactions": [asdict(draft) for draft in drafts],
            "charges": charges,
        }
    except DuplicateTransactionError as exc:
        raise duplicate_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.post("/api/transactions/classify-pending")
def classify_pending(payload: ClassifyPayload) -> dict[str, Any]:
    started = perf_counter()
    provider = llm_provider()
    model = llm_model()
    try:
        with database() as conn:
            result = auto_classify_pending_transactions(
                conn, limit=payload.limit, actor="web"
            )
            log_agent_run(
                conn,
                tool_mode="structured_output",
                session_id="classification",
                provider=provider,
                model=model,
                action="classify_pending",
                status="success",
                duration_ms=round((perf_counter() - started) * 1000),
                input_chars=0,
                output_count=result["classified"],
            )
            return result
    except Exception as exc:
        try:
            with database() as conn:
                log_agent_run(
                    conn,
                    tool_mode="structured_output",
                    session_id="classification",
                    provider=provider,
                    model=model,
                    action="classify_pending",
                    status="error",
                    duration_ms=round((perf_counter() - started) * 1000),
                    input_chars=0,
                    error=exc,
                )
        except Exception:
            pass
        if isinstance(exc, (RuntimeError, ValueError, TypeError)):
            raise api_error(exc) from exc
        raise HTTPException(status_code=502, detail="自动分类模型请求失败") from exc


@app.get("/api/references/{kind}")
def references(kind: str) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"kind": kind, "items": list_reference_values(conn, kind)}
    except ValueError as exc:
        raise api_error(exc) from exc


@app.post("/api/references/{kind}")
def add_reference(kind: str, payload: ReferenceCreate) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "item": create_reference_value(
                    conn,
                    kind,
                    payload.name,
                    payload.aliases,
                    payload.is_favorite,
                    actor="web",
                )
            }
    except ValueError as exc:
        raise api_error(exc) from exc


@app.patch("/api/references/{kind}/{name}")
def edit_reference(kind: str, name: str, payload: ReferenceUpdate) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "item": update_reference_value(
                    conn,
                    kind,
                    name,
                    new_name=payload.new_name,
                    aliases=payload.aliases,
                    is_favorite=payload.is_favorite,
                    actor="web",
                )
            }
    except ValueError as exc:
        raise api_error(exc) from exc


@app.post("/api/references/{kind}/merge")
def merge_reference(kind: str, payload: ReferenceMerge) -> dict[str, Any]:
    try:
        with database() as conn:
            return {
                "merged": merge_reference_value(
                    conn, kind, payload.source, payload.target, actor="web"
                )
            }
    except ValueError as exc:
        raise api_error(exc) from exc


@app.get("/api/backups")
def backups() -> dict[str, Any]:
    return {"backups": list_backups()}


@app.post("/api/backups")
def make_backup() -> dict[str, Any]:
    try:
        with database() as conn:
            return {"backup": create_backup(conn, actor="web")}
    except (OSError, ValueError) as exc:
        raise api_error(exc) from exc


@app.get("/api/backups/{name}/download")
def download_backup(name: str) -> FileResponse:
    try:
        path = backup_path(name)
        if not path.is_file():
            raise ValueError("备份不存在")
        return FileResponse(path, filename=name, media_type="application/vnd.sqlite3")
    except ValueError as exc:
        raise api_error(exc) from exc


@app.post("/api/backups/{name}/restore")
def restore_ledger(name: str, payload: RestorePayload) -> dict[str, Any]:
    if payload.confirmation != "恢复账本":
        raise HTTPException(status_code=400, detail="请输入“恢复账本”确认")
    try:
        with database() as conn:
            return restore_backup(conn, name, actor="web")
    except (OSError, ValueError) as exc:
        raise api_error(exc) from exc


@app.get("/api/logs/operations")
def operation_logs(
    limit: int = Query(default=100, ge=1, le=200),
    action_prefix: str = Query(default="", max_length=40),
) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"logs": list_operation_logs(conn, limit=limit, action_prefix=action_prefix)}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc


@app.get("/api/logs/agent")
def agent_logs(
    limit: int = Query(default=100, ge=1, le=200),
    status: str = Query(default="", pattern=r"^$|^success$|^error$"),
) -> dict[str, Any]:
    try:
        with database() as conn:
            return {"logs": list_agent_runs(conn, limit=limit, status=status)}
    except (ValueError, TypeError) as exc:
        raise api_error(exc) from exc
