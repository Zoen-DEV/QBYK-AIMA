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


def test_video_prompt_flag_off_for_modo_fotos():
    # En modo "fotos" los frames son las imágenes subidas: no hace falta guion del LLM.
    assert not _needs_video({"tipo_post": "reel", "media_origin": "fotos", "redes": ["instagram"]})


def test_segments_needed_from_duration():
    assert pw._segments_needed({"duracion_video": 30, "video_segment_seconds": 5}) == 6
    assert pw._segments_needed({"duracion_video": 12, "video_segment_seconds": 5}) == 3  # ceil(12/5)
    assert pw._segments_needed({"duracion_video": 0}) == 1
    assert pw._segments_needed({}) == 1


def test_user_message_segments_line():
    msg = pw._user_message(
        _CONTENT,
        {"tipo_post": "reel", "redes": ["instagram"], "duracion_video": 20, "video_segment_seconds": 5},
        "",
    )
    assert "VIDEO SEGMENTS NEEDED: 4" in msg


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


def _raw_sb(video_storyboard) -> str:
    return json.dumps({
        "linkedin_text": "x", "instagram_text": "", "facebook_text": "",
        "video_prompt": "", "video_storyboard": video_storyboard,
    })


def test_parse_raw_keeps_and_cleans_storyboard():
    posts = pw._parse_raw(_raw_sb(["  shot one  ", "shot two", "   "]))
    assert posts["video_storyboard"] == ["shot one", "shot two"]


def test_parse_raw_drops_empty_storyboard():
    assert "video_storyboard" not in pw._parse_raw(_raw_sb([]))
    assert "video_storyboard" not in pw._parse_raw(_raw(""))  # sin la clave


def test_parse_raw_coerces_string_storyboard_to_list():
    posts = pw._parse_raw(_raw_sb("single shot"))
    assert posts["video_storyboard"] == ["single shot"]


# ── Estilo compartido (video_style) ───────────────────────────────────────────

def _raw_style(video_style) -> str:
    return json.dumps({
        "linkedin_text": "x", "instagram_text": "", "facebook_text": "",
        "video_style": video_style,
    })


def test_parse_raw_keeps_and_strips_video_style():
    posts = pw._parse_raw(_raw_style("  shot on 35mm, warm amber light  "))
    assert posts["video_style"] == "shot on 35mm, warm amber light"


def test_parse_raw_drops_empty_or_non_string_video_style():
    assert "video_style" not in pw._parse_raw(_raw_style(""))
    assert "video_style" not in pw._parse_raw(_raw_style(42))


# ── Voz en off (video_voiceover) ──────────────────────────────────────────────

def _raw_vo(video_voiceover) -> str:
    return json.dumps({
        "linkedin_text": "x", "instagram_text": "", "facebook_text": "",
        "video_voiceover": video_voiceover,
    })


def test_parse_raw_keeps_and_cleans_voiceover():
    posts = pw._parse_raw(_raw_vo(["  Hola.  ", "Segunda línea.", "   "]))
    assert posts["video_voiceover"] == ["Hola.", "Segunda línea."]


def test_parse_raw_drops_empty_voiceover():
    assert "video_voiceover" not in pw._parse_raw(_raw_vo([]))
    assert "video_voiceover" not in pw._parse_raw(_raw(""))  # sin la clave


def test_parse_raw_coerces_string_voiceover_to_list():
    posts = pw._parse_raw(_raw_vo("una sola línea"))
    assert posts["video_voiceover"] == ["una sola línea"]


def test_voiceover_word_budget_from_segment_seconds():
    # Rango ~2.2–2.8 palabras/segundo hablado; piso 8 y ancho mínimo de +3.
    assert pw._voiceover_word_budget({"video_segment_seconds": 5}) == (11, 14)
    assert pw._voiceover_word_budget({"video_segment_seconds": 10}) == (22, 28)
    assert pw._voiceover_word_budget({"video_segment_seconds": 2}) == (8, 11)  # pisos
    assert pw._voiceover_word_budget({}) == (11, 14)                           # default 5s


def test_user_message_voiceover_line():
    msg = pw._user_message(
        _CONTENT,
        {"tipo_post": "reel", "redes": ["instagram"], "duracion_video": 20, "video_segment_seconds": 5},
        "",
    )
    assert "VOICEOVER WORDS PER LINE: 11-14" in msg
    assert "exactly 4 spoken line(s)" in msg


def test_user_message_voiceover_off_when_no_video():
    msg = pw._user_message(_CONTENT, {"tipo_post": "post", "redes": ["linkedin"]}, "")
    assert "video_storyboard and video_voiceover to empty arrays" in msg


# ── _sanitize_posts (red de seguridad compartida por ambos proveedores) ──────────

def test_sanitize_strips_citation_markers():
    posts = {"linkedin_text": "Un dato clave [1] del video [2].", "instagram_text": "Otra idea [3]."}
    out = pw._sanitize_posts(posts)
    assert out["linkedin_text"] == "Un dato clave del video."
    assert out["instagram_text"] == "Otra idea."


def test_sanitize_unwraps_markdown_bold():
    # Caso real (sonar, jul 2026): el caption de IG salía con **negrita** literal.
    posts = {"instagram_text": "**Nunca te abandonaré.**\n\nEsta canción es un recordatorio."}
    out = pw._sanitize_posts(posts)
    assert out["instagram_text"].startswith("Nunca te abandonaré.")
    assert "**" not in out["instagram_text"]


def test_sanitize_covers_image_text_and_video_fields():
    posts = {
        "image_text": {"hook": "**Hook fuerte** [1]", "slides": ["Idea **uno**", "Idea dos [2]"]},
        "video_prompt": "A single lamp **glows** [1]",
        "video_voiceover": ["Línea **uno**", "  "],
    }
    out = pw._sanitize_posts(posts)
    assert out["image_text"]["hook"] == "Hook fuerte"
    assert out["image_text"]["slides"] == ["Idea uno", "Idea dos"]
    assert out["video_prompt"] == "A single lamp glows"
    assert out["video_voiceover"] == ["Línea uno"]


def test_sanitize_leaves_clean_text_untouched():
    posts = {"linkedin_text": "Texto normal con 2*3 asteriscos sueltos * y [nota] no numérica."}
    out = pw._sanitize_posts(posts)
    assert out["linkedin_text"] == "Texto normal con 2*3 asteriscos sueltos * y [nota] no numérica."
