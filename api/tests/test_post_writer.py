"""Tests del guion de video (`video_prompt`) en post_writer: el flag del user
message y la normalización del parser."""

import json

import post_writer as pw

_CONTENT = {
    "title": "Cómo ahorrar",
    "transcript": "hola " * 50,
    "tags": [],
    "chapters": [],
    "channel": "Canal",
}


def _needs_video(params: dict) -> bool:
    msg = pw._user_message(_CONTENT, params, "")
    return "VIDEO PROMPT NEEDED: yes" in msg


def test_video_prompt_flag_reel():
    assert _needs_video({"tipo_post": "reel", "redes": ["instagram"]})


def test_video_prompt_flag_historia_video():
    assert _needs_video({"tipo_post": "historia", "historia_formato": "video", "redes": ["instagram"]})


def test_video_prompt_flag_tipo_medio_video():
    assert _needs_video({"tipo_post": "post", "tipo_medio": "video", "redes": ["facebook"]})


def test_video_prompt_flag_off_for_normal_post():
    assert not _needs_video({"tipo_post": "post", "redes": ["linkedin"]})


def test_video_prompt_flag_off_for_historia_imagen():
    assert not _needs_video({"tipo_post": "historia", "historia_formato": "imagen", "redes": ["instagram"]})


def test_video_prompt_flag_off_for_modo_subir():
    # En modo "subir" el medio ya existe: no se genera video ni hace falta guion.
    assert not _needs_video({"tipo_post": "reel", "media_origin": "subir", "redes": ["instagram"]})


def _raw(video_prompt) -> str:
    return json.dumps({
        "linkedin_text": "x", "instagram_text": "", "facebook_text": "",
        "video_prompt": video_prompt,
    })


def test_parse_raw_keeps_and_strips_video_prompt():
    posts = pw._parse_raw(_raw("  A coin jar on a desk, slow push-in.  "))
    assert posts["video_prompt"] == "A coin jar on a desk, slow push-in."


def test_parse_raw_drops_empty_video_prompt():
    assert "video_prompt" not in pw._parse_raw(_raw(""))


def test_parse_raw_drops_non_string_video_prompt():
    assert "video_prompt" not in pw._parse_raw(_raw(42))
