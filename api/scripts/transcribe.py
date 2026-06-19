"""
Speech-to-text helper for the "audio" source (e.g. a WhatsApp voice note).

Uses an OpenAI-compatible Whisper transcription endpoint
(`POST {base_url}/audio/transcriptions`). Works with OpenAI (default) or any
compatible provider such as Groq by overriding `base_url` and the model.

No SDK dependency — the multipart request is built by hand with urllib, matching
the rest of the API clients in this project.
"""

import json
import mimetypes
import urllib.error
import urllib.request
import uuid

# WhatsApp voice notes are Opus audio. The OpenAI/Groq endpoints validate by
# file extension; `.opus` lives in an Ogg container, so we present it as `.ogg`
# (which both providers accept) without touching the bytes.
_EXT_REMAP = {".opus": ".ogg"}

# OpenAI Whisper rejects files over 25 MB (Groq's limit is similar). WhatsApp
# voice notes are tiny (~1 MB/min Opus), so this only trips on large uploads.
MAX_FILE_BYTES = 25 * 1024 * 1024


def _build_multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes, content_type: str):
    """Encode a multipart/form-data body. Returns (body_bytes, boundary)."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{key}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode())
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode()
    )
    parts.append(f"Content-Type: {content_type}".encode())
    parts.append(b"")
    parts.append(file_bytes)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    return b"\r\n".join(parts), boundary


def transcribe_audio(
    file_bytes: bytes,
    filename: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "whisper-1",
    language: str | None = None,
) -> tuple[str, float]:
    """Transcribe audio bytes to text. Returns `(transcript, duration_seconds)`.

    `duration_seconds` (de la respuesta `verbose_json`) alimenta el tracking de
    costos de Whisper (cobro por minuto); es 0.0 si el proveedor no la reporta.
    `language` is an optional ISO hint (e.g. "es"/"en"); omit to auto-detect.
    Raises RuntimeError on an API error.
    """
    if not api_key:
        raise RuntimeError(
            "Transcripción no configurada: define OPENAI_API_KEY (o GROQ_API_KEY) en .env"
        )
    if len(file_bytes) > MAX_FILE_BYTES:
        mb = len(file_bytes) / (1024 * 1024)
        raise RuntimeError(
            f"El audio pesa {mb:.1f} MB y supera el límite de 25 MB de Whisper. "
            "Usa una nota de voz más corta o comprímela."
        )

    send_name = filename or "audio.ogg"
    lower = send_name.lower()
    for ext, remap in _EXT_REMAP.items():
        if lower.endswith(ext):
            send_name = send_name[: -len(ext)] + remap
            break
    content_type = mimetypes.guess_type(send_name)[0] or "application/octet-stream"

    # verbose_json incluye `duration` (segundos de audio) además del texto — lo
    # necesitamos para el costo por minuto de Whisper. OpenAI y Groq lo soportan.
    fields: dict = {"model": model, "response_format": "verbose_json"}
    if language:
        fields["language"] = language

    body, boundary = _build_multipart(fields, "file", send_name, file_bytes, content_type)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Transcription API error {e.code}: {body_text}") from e

    try:
        duration = float(data.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return (data.get("text") or "").strip(), duration
