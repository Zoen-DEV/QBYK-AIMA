"""Tests de la traducción de params del cliente MCP para los modelos nuevos.

Cubre el id de app `elevenlabs` (→ model=text2speech_v2 + variant, que exige
voz — con respaldo si el .env no fija una) y el apagado del audio nativo de
Seedance (los clips van mudos: la voz la pone el TTS + explainer_video).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import higgsfield_mcp as hfmcp


def test_tts_params_elevenlabs_translates_to_text2speech_v2():
    p = hfmcp._tts_params("hola", "elevenlabs", "", "")
    assert p["model"] == "text2speech_v2"
    assert p["variant"] == "elevenlabs"
    # text2speech_v2 exige voz: sin configurar se usa la de respaldo del módulo.
    assert p["voice_type"] == "preset"
    assert p["voice_id"]


def test_tts_params_elevenlabs_respeta_voz_configurada():
    p = hfmcp._tts_params("hola", "elevenlabs", "preset", "mi-voz")
    assert (p["voice_type"], p["voice_id"]) == ("preset", "mi-voz")


def test_tts_params_seed_audio_sin_variant_ni_voz_forzada():
    p = hfmcp._tts_params("hola", "seed_audio", "", "")
    assert p["model"] == "seed_audio"
    assert "variant" not in p
    assert "voice_type" not in p  # la voz sigue siendo opcional en seed_audio


def test_video_params_seedance_apaga_audio_nativo():
    p = hfmcp._video_params("x", "9:16", 10, "seedance_2_0", None)
    assert p["generate_audio"] is False
    assert p["duration"] == 10
    p_mini = hfmcp._video_params("x", "9:16", None, "seedance_2_0_mini", None)
    assert p_mini["generate_audio"] is False


def test_video_params_kling_no_manda_flag_de_audio():
    # Kling no acepta generate_audio: mandarlo podría disparar un 422.
    p = hfmcp._video_params("x", "9:16", None, "kling3_0_turbo", None)
    assert "generate_audio" not in p
    assert "duration" not in p
