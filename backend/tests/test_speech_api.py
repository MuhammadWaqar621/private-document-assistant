"""Integration tests for /api/speech/* (transcribe, synthesize).

Real Groq calls are never made in these tests: groq_configured() and
transcribe_audio()/synthesize_speech() (all imported by name into
app/api/speech.py) are monkeypatched to fake, deterministic results - same
pattern as test_documents_api.py mocking azure_ai_configured()/
ingest_document(). This exercises the *endpoint's* request/response wiring
(503 gate, response shape, error translation) rather than Groq's real
Whisper/PlayAI TTS APIs, which are covered by live/manual verification
against the real running stack (see README)."""

import app.api.speech as speech_module

from .conftest import auth_headers, signup


def _transcribe(client, token_body, content=b"fake-audio-bytes", filename="clip.webm"):
    return client.post(
        "/api/speech/transcribe",
        headers=auth_headers(token_body),
        files={"file": (filename, content, "audio/webm")},
    )


def _synthesize(client, token_body, text="Hello there"):
    return client.post(
        "/api/speech/synthesize",
        headers=auth_headers(token_body),
        json={"text": text},
    )


# --- 503 when Groq isn't configured ----------------------------------------


def test_transcribe_returns_503_when_groq_not_configured(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: False)
    user = signup(client)

    response = _transcribe(client, user)

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "groq_not_configured"


def test_synthesize_returns_503_when_groq_not_configured(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: False)
    user = signup(client)

    response = _synthesize(client, user)

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "groq_not_configured"


# --- auth required -----------------------------------------------------------


def test_transcribe_requires_authentication(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)

    response = client.post(
        "/api/speech/transcribe", files={"file": ("clip.webm", b"bytes", "audio/webm")}
    )

    assert response.status_code == 401


def test_synthesize_requires_authentication(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)

    response = client.post("/api/speech/synthesize", json={"text": "hi"})

    assert response.status_code == 401


# --- happy path: mocked Groq call wired into the right response shape -----


def test_transcribe_happy_path_returns_the_transcribed_text(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)
    captured = {}

    def _fake_transcribe(audio_bytes, filename):
        captured["audio_bytes"] = audio_bytes
        captured["filename"] = filename
        return "this is the transcribed text"

    monkeypatch.setattr(speech_module, "transcribe_audio", _fake_transcribe)

    user = signup(client)
    response = _transcribe(client, user, content=b"real-ish-audio-bytes", filename="voice.wav")

    assert response.status_code == 200
    assert response.json() == {"text": "this is the transcribed text"}
    assert captured["audio_bytes"] == b"real-ish-audio-bytes"
    assert captured["filename"] == "voice.wav"


def test_synthesize_happy_path_returns_audio_bytes_as_mp3(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)
    captured = {}

    def _fake_synthesize(text):
        captured["text"] = text
        return b"\xff\xfb\x90\x00fake-mp3-bytes"

    monkeypatch.setattr(speech_module, "synthesize_speech", _fake_synthesize)

    user = signup(client)
    response = _synthesize(client, user, text="Read this aloud")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"\xff\xfb\x90\x00fake-mp3-bytes"
    assert captured["text"] == "Read this aloud"


def test_synthesize_rejects_empty_text(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)
    user = signup(client)

    response = _synthesize(client, user, text="   ")

    assert response.status_code == 400


# --- Groq failures surface as a clear 502, not a raw 500 -------------------


def test_transcribe_translates_groq_failure_into_a_502(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)

    def _boom(audio_bytes, filename):
        raise RuntimeError("Groq API is down")

    monkeypatch.setattr(speech_module, "transcribe_audio", _boom)

    user = signup(client)
    response = _transcribe(client, user)

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "transcription_failed"


def test_synthesize_translates_groq_failure_into_a_502(client, monkeypatch):
    monkeypatch.setattr(speech_module, "groq_configured", lambda: True)

    def _boom(text):
        raise RuntimeError("Groq API is down")

    monkeypatch.setattr(speech_module, "synthesize_speech", _boom)

    user = signup(client)
    response = _synthesize(client, user)

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "synthesis_failed"
