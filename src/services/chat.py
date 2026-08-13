from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
from threading import Lock
from time import perf_counter
from typing import Any

import financial_agent as fa


_ACTIVE_REQUESTS: set[str] = set()
_ACTIVE_LOCK = Lock()


def is_chat_request_active(request_id: str) -> bool:
    with _ACTIVE_LOCK:
        return request_id in _ACTIVE_REQUESTS


def chat_request_can_resume(item: dict[str, Any]) -> bool:
    return item.get("status") == "pending" and not is_chat_request_active(str(item["request_id"]))


def _claim(request_id: str) -> bool:
    with _ACTIVE_LOCK:
        if request_id in _ACTIVE_REQUESTS:
            return False
        _ACTIVE_REQUESTS.add(request_id)
        return True


def _release(request_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_REQUESTS.discard(request_id)


@contextmanager
def _connect():
    conn = fa.connect()
    fa.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def process_chat_request(
    *,
    request_id: str,
    session_id: str,
    text: str,
    resume: bool = False,
    image_data_urls: tuple[str, ...] = (),
) -> dict[str, Any]:
    started = perf_counter()
    provider = fa.llm_provider()
    model = fa.llm_model(provider)
    action_name = ""
    draft_count = 0
    request_created = False
    if not _claim(request_id):
        return {"kind": "pending", "request_id": request_id, "status": "pending"}
    try:
        with _connect() as conn:
            existing = fa.get_chat_request(conn, request_id)
            if resume:
                if existing is None:
                    raise ValueError("找不到聊天请求")
                if existing["status"] in {"awaiting_confirmation", "completed"}:
                    return existing["result"]
                if existing["status"] != "pending":
                    raise ValueError(existing["error_message"] or "该聊天请求已经结束")
                if existing["provider"] != provider or existing["model"] != model:
                    raise RuntimeError("恢复执行前模型设置发生变化，请切回原模型后重试")
                session_id = existing["session_id"]
                text = fa.get_chat_request_user_text(conn, request_id)
                if existing.get("has_images"):
                    raise ValueError("图片请求不能在服务重启后恢复，请重新上传图片")
                request_created = True
            elif existing is not None:
                if existing["status"] in {"awaiting_confirmation", "completed"}:
                    return existing["result"]
                if existing["status"] == "pending":
                    return {"kind": "pending", "request_id": request_id, "status": "pending"}
                raise ValueError(existing["error_message"] or "该聊天请求已经结束")
            else:
                fa.create_chat_request(
                    conn,
                    request_id,
                    session_id,
                    text,
                    provider,
                    model,
                    image_data_urls=image_data_urls,
                )
                request_created = True
            fa.update_chat_progress(conn, request_id, "context", "正在准备本地上下文")
            context = fa.load_agent_context(conn, session_id)

        with _connect() as conn:
            fa.update_chat_progress(conn, request_id, "model", "正在调用模型并选择工具")
            action, tool_result = fa.execute_agent_request(
                conn,
                text,
                context,
                run_id=request_id,
                session_id=session_id,
                preview_writes=True,
                allow_interactive_approval=False,
                image_data_urls=image_data_urls,
            )
            fa.update_chat_progress(conn, request_id, "tools", "正在校验并执行本地工具")
            action_name = action.action
            if action.action == "record":
                drafts = tool_result.get("drafts")
                if drafts is None and tool_result.get("draft") is not None:
                    drafts = [tool_result["draft"]]
                if not drafts:
                    raise ValueError("模型没有返回账单草稿")
                draft_count = len(drafts)
                result = {
                    "kind": "drafts",
                    "agent_action": asdict(action),
                    "drafts": drafts,
                    "draft_count": draft_count,
                    "requires_confirmation": True,
                }
                request_status = "awaiting_confirmation"
            elif action.action == "clarify":
                result = {
                    "kind": "clarification",
                    "agent_action": asdict(action),
                    "question": tool_result["question"],
                    "requires_confirmation": False,
                }
                request_status = "completed"
            elif action.action == "management":
                proposals = tool_result.get("proposals") or []
                if not proposals:
                    raise ValueError("模型没有返回订阅或待还草稿")
                draft_count = len(proposals)
                result = {
                    "kind": "management_drafts",
                    "agent_action": asdict(action),
                    "proposals": proposals,
                    "draft_count": draft_count,
                    "requires_confirmation": True,
                }
                request_status = "awaiting_confirmation"
            else:
                result = {"kind": "result", **tool_result}
                request_status = "completed"
            result["request_id"] = request_id
            fa.add_message(
                conn,
                session_id,
                "assistant",
                json.dumps(result, ensure_ascii=False),
                request_id=request_id,
            )
            fa.save_agent_state(conn, session_id, action, result)
            fa.update_chat_progress(conn, request_id, "finalizing", "正在保存结果")
            fa.update_chat_request(
                conn,
                request_id,
                request_status,
                action=action_name,
                result=result,
            )
            fa.log_agent_run(
                conn,
                run_id=request_id,
                tool_mode="native",
                session_id=session_id,
                provider=provider,
                model=model,
                action=action_name,
                status="success",
                duration_ms=round((perf_counter() - started) * 1000),
                input_chars=len(text),
                output_count=draft_count,
            )
            return result
    except Exception as exc:
        try:
            with _connect() as conn:
                if request_created:
                    error_result = {
                        "kind": "error",
                        "request_id": request_id,
                        "message": fa.present_model_error(exc, provider=provider),
                    }
                    fa.add_message(
                        conn,
                        session_id,
                        "assistant",
                        json.dumps(error_result, ensure_ascii=False),
                        request_id=request_id,
                    )
                    fa.update_chat_request(
                        conn,
                        request_id,
                        "error",
                        action=action_name,
                        result=error_result,
                        error_message=str(exc),
                    )
                fa.log_agent_run(
                    conn,
                    run_id=request_id,
                    tool_mode="native",
                    session_id=session_id,
                    provider=provider,
                    model=model,
                    action=action_name,
                    status="error",
                    duration_ms=round((perf_counter() - started) * 1000),
                    input_chars=len(text),
                    error=exc,
                )
        except Exception:
            pass
        raise
    finally:
        _release(request_id)
