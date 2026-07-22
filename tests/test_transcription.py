import pytest

from services import transcription


def test_transcribe_audio_posts_chinese_audio_without_persisting(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "昨天午饭三十八元微信支付"}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(transcription.httpx, "Client", FakeClient)

    text = transcription.transcribe_audio(
        b"short-recording", filename="我的录音.webm", media_type="audio/webm;codecs=opus"
    )

    assert text == "昨天午饭三十八元微信支付"
    assert captured["url"] == transcription.GROQ_TRANSCRIPT_URL
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["data"]["language"] == "zh"
    assert captured["data"]["model"] == "whisper-large-v3-turbo"
    assert captured["files"]["file"][0].endswith(".webm")
    assert captured["files"]["file"][1] == b"short-recording"


def test_transcribe_audio_rejects_invalid_local_input(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        transcription.transcribe_audio(b"audio", filename="voice.webm", media_type="audio/webm")

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with pytest.raises(ValueError, match="格式"):
        transcription.transcribe_audio(b"audio", filename="voice.txt", media_type="text/plain")
    with pytest.raises(ValueError, match="10 MB"):
        transcription.transcribe_audio(
            b"a" * (transcription.MAX_AUDIO_BYTES + 1),
            filename="voice.webm",
            media_type="audio/webm",
        )


def test_transcription_status_never_exposes_the_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_ASR_MODEL", "whisper-large-v3")

    status = transcription.transcription_status()

    assert status == {
        "provider": "groq",
        "model": "whisper-large-v3",
        "configured": True,
        "max_bytes": transcription.MAX_AUDIO_BYTES,
    }
