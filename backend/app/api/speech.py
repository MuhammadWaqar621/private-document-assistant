"""
Speech endpoints: speech-to-text (transcribe) and text-to-speech
(synthesize), both backed by Groq (app/engine/groq_client.py).

These are authenticated utility endpoints (require get_current_user) but
are not tied to a specific chat's ownership - they don't touch chat or
document data at all, just pass bytes/text through to Groq and back.
Mirrors app/core/email.py's smtp_configured()-gate-then-try/except pattern
used by app/api/auth.py's forgot-password endpoint: a 503 when Groq isn't
configured, a clear 502 (not a raw 500) if the Groq call itself fails.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.engine.groq_client import groq_configured, synthesize_speech, transcribe_audio
from app.models import User

router = APIRouter(prefix="/api/speech", tags=["speech"])


class SynthesizeRequest(BaseModel):
    text: str


class TranscribeResponse(BaseModel):
    text: str


def _groq_not_configured_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "groq_not_configured",
            "message": "Groq is not configured. Set GROQ_API_KEY in .env.",
        },
    )


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
) -> TranscribeResponse:
    if not groq_configured():
        raise _groq_not_configured_error()

    audio_bytes = await file.read()
    filename = file.filename or "audio.webm"

    try:
        text = transcribe_audio(audio_bytes, filename)
    except Exception as exc:  # noqa: BLE001 - surface any Groq failure clearly, don't crash
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "transcription_failed", "message": str(exc)},
        )

    return TranscribeResponse(text=text)


@router.post("/synthesize")
def synthesize(
    body: SynthesizeRequest,
    current_user: User = Depends(get_current_user),
) -> Response:
    if not groq_configured():
        raise _groq_not_configured_error()

    if not body.text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Text cannot be empty")

    try:
        audio_bytes = synthesize_speech(body.text)
    except Exception as exc:  # noqa: BLE001 - surface any Groq failure clearly, don't crash
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "synthesis_failed", "message": str(exc)},
        )

    return Response(content=audio_bytes, media_type="audio/mpeg")
