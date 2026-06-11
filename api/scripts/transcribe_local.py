"""
Local speech-to-text using faster-whisper — a free, offline alternative to the
hosted Whisper endpoint in `transcribe.py`. No API key and no network required:
the model is downloaded once on first use, then runs on the CPU (or GPU).

Selected via `TRANSCRIPTION_ENGINE=local` (see config.py). faster-whisper is an
optional dependency, imported lazily so the rest of the API runs without it when
the hosted engine is in use.
"""

import io
import threading

# The model is expensive to construct (loads weights into RAM), so we build it
# once and reuse it across jobs. Guarded by a lock because transcriptions run in
# a thread-pool executor and could otherwise race on first use.
_model = None
_model_key = None
_model_lock = threading.Lock()


def _get_model(model_size: str, device: str, compute_type: str):
    global _model, _model_key
    with _model_lock:
        key = (model_size, device, compute_type)
        if _model is None or _model_key != key:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise RuntimeError(
                    "faster-whisper no está instalado. Ejecuta: "
                    "pip install faster-whisper (o usa TRANSCRIPTION_ENGINE=api)."
                ) from e
            _model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _model_key = key
        return _model


def transcribe_audio_local(
    file_bytes: bytes,
    filename: str = "",
    *,
    model_size: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
) -> str:
    """Transcribe audio bytes to text with faster-whisper. Returns the transcript.

    `language` is an optional ISO hint (e.g. "es"/"en"); omit to auto-detect.
    faster-whisper decodes the container itself (PyAV), so WhatsApp Opus/Ogg
    voice notes work without re-encoding. There is no 25 MB cap here.
    Raises RuntimeError if the dependency is missing.
    """
    model = _get_model(model_size, device, compute_type)
    # vad_filter trims silence/noise, which keeps voice-note transcripts cleaner.
    segments, _info = model.transcribe(
        io.BytesIO(file_bytes),
        language=language,
        vad_filter=True,
    )
    # `segments` is a generator — iterating it is what actually runs inference.
    return "".join(seg.text for seg in segments).strip()
