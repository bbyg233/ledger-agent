from __future__ import annotations

import os
import re
from typing import Any

import httpx


GROQ_TRANSCRIPT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "whisper-large-v3-turbo"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4a",
}


def transcription_status() -> dict[str, Any]:
    """Return non-sensitive status used by the local UI."""
    return {
        "provider": "groq",
        "model": os.environ.get("GROQ_ASR_MODEL", DEFAULT_ASR_MODEL),
        "configured": bool(os.environ.get("GROQ_API_KEY", "").strip()),
        "max_bytes": MAX_AUDIO_BYTES,
    }


def _normalize_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _safe_filename(value: str, media_type: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", value.rsplit("/", 1)[-1])[:100]
    if filename and "." in filename:
        return filename
    extensions = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/x-m4a": "m4a",
    }
    return f"voice-note.{extensions.get(media_type, 'webm')}"


def transcribe_audio(audio: bytes, *, filename: str, media_type: str) -> str:
    """Transcribe a short audio clip without persisting the audio locally."""
    if not audio:
        raise ValueError("没有收到录音内容")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("单段录音不能超过 10 MB")

    normalized_type = _normalize_media_type(media_type)
    if normalized_type not in SUPPORTED_AUDIO_TYPES:
        raise ValueError("录音格式不受支持，请使用浏览器录音生成的 WebM、WAV 或 MP4 文件")

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("尚未配置 GROQ_API_KEY，无法使用语音输入")

    model = os.environ.get("GROQ_ASR_MODEL", DEFAULT_ASR_MODEL).strip() or DEFAULT_ASR_MODEL
    safe_filename = _safe_filename(filename, normalized_type)
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                GROQ_TRANSCRIPT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": model,
                    "language": "zh",
                    "response_format": "json",
                    "temperature": "0",
                    "prompt": "这是个人记账语音。保留金额、日期、支付方式、商户和消费用途。",
                },
                files={"file": (safe_filename, audio, normalized_type)},
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError("语音转写超时，请缩短录音后重试") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"语音转写服务返回错误 ({exc.response.status_code})") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("无法连接语音转写服务") from exc

    try:
        text = str(response.json().get("text") or "").strip()
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("语音转写服务返回了无法识别的结果") from exc
    if not text:
        raise RuntimeError("没有识别到语音内容，请靠近麦克风后重试")
    return text
