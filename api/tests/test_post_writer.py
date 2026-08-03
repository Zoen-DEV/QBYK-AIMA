"""Tests del guion de video (`video_prompt`) en post_writer: el flag del user
message y la normalización del parser."""

import asyncio
import copy
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
    """¿El user message trae el bloque de video? (solo existe cuando hay video)"""
    return "video_prompt" in _requeridos(params)


def _requeridos(params: dict) -> str:
    """La línea REQUIRED VISUAL FIELDS del user message: qué se le pide a este job."""
    for linea in pw._user_message(_CONTENT, params, "").splitlines():
        if linea.startswith("REQUIRED VISUAL FIELDS"):
            return linea
    return ""


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
    assert "EXACTLY 4 spoken line(s)" in msg


def test_user_message_voiceover_off_when_no_video():
    msg = pw._user_message(_CONTENT, {"tipo_post": "post", "redes": ["linkedin"]}, "")
    assert "VOICEOVER WORDS PER LINE" not in msg


# El fallo que motivó separar las compuertas: un job de imagen mandaba
# "IMAGE PROMPT NEEDED: yes" pegado a "VIDEO PROMPT NEEDED: no" y a tres
# recordatorios de "set ... to empty", y el modelo aplicaba el vaciado al bloque
# equivocado — JSON válido, image_text entero y los tres prompts de imagen en
# blanco. Del medio que NO se genera ya no se dice absolutamente nada.

def test_user_message_no_menciona_el_video_en_un_job_de_imagen():
    msg = pw._user_message(_CONTENT, {"tipo_post": "post", "redes": ["linkedin"]}, "")
    for rastro in ("VIDEO PROMPT NEEDED", "VIDEO SEGMENTS NEEDED", "video_storyboard",
                   "video_voiceover", "video_style"):
        assert rastro not in msg


def test_user_message_no_menciona_las_imagenes_en_un_job_de_video():
    msg = pw._user_message(_CONTENT, {"tipo_post": "reel", "redes": ["instagram"]}, "")
    for rastro in ("IMAGE PROMPT NEEDED", "IMAGE SLIDE PROMPTS NEEDED", "image_slide_prompts",
                   "image_style"):
        assert rastro not in msg


def test_user_message_nunca_pide_vaciar_un_campo_visual():
    # Ninguna variante puede llevar una instrucción de vaciar campos visuales: es la
    # que el modelo aplicaba al bloque que sí tenía que escribir.
    for params in ({"tipo_post": "post", "redes": ["linkedin"]},
                   {"tipo_post": "post", "redes": ["instagram"],
                    "formato_instagram": "carrusel", "carrusel_slides": 5},
                   {"tipo_post": "reel", "redes": ["instagram"], "duracion_video": 20}):
        msg = pw._user_message(_CONTENT, params, "")
        for rastro in ("to an empty array", "to empty arrays", "empty strings and",
                       "NEEDED: no", "NEEDED: 0"):
            assert rastro not in msg, f"{rastro!r} sigue en el mensaje de {params}"


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


# ── Prompts de imagen (image_prompt / image_slide_prompts) ────────────────────

def _img_flags(params: dict) -> str:
    return pw._user_message(_CONTENT, params, "")


def test_image_prompt_asked_for_a_normal_post():
    req = _requeridos({"tipo_post": "post", "redes": ["linkedin"]})
    assert "image_prompt" in req and "image_style" in req
    # Imagen única: se pide la portada, no slides.
    assert "image_slide_prompts" not in req
    assert "IMAGE SLIDE PROMPTS NEEDED" not in _img_flags({"tipo_post": "post", "redes": ["linkedin"]})


def test_image_slide_prompts_match_the_carousel_slides():
    # 5 slides = portada + 4 de info (ya no hay slide de créditos) → 4 prompts.
    params = {"tipo_post": "post", "redes": ["instagram"],
              "formato_instagram": "carrusel", "carrusel_slides": 5}
    msg = _img_flags(params)
    assert "IMAGE SLIDE PROMPTS NEEDED: 4" in msg
    assert "INFO SLIDES NEEDED: 4" in msg
    assert "image_slide_prompts (x4)" in _requeridos(params)


def test_image_prompt_not_asked_for_video_jobs():
    req = _requeridos({"tipo_post": "reel", "redes": ["instagram"]})
    assert "image_prompt" not in req and "image_slide_prompts" not in req
    assert "video_prompt" in req


def test_image_prompt_not_asked_when_media_is_uploaded():
    # El medio ya existe: no se pide ningún prompt visual, solo el copy de la pieza.
    for origin in ("subir", "fotos"):
        req = _requeridos({"tipo_post": "post", "redes": ["linkedin"], "media_origin": origin})
        assert "image_prompt" not in req and "video_prompt" not in req


def test_wants_images_is_the_complement_of_wants_video():
    assert pw._wants_images({"tipo_post": "historia", "historia_formato": "imagen"})
    assert not pw._wants_images({"tipo_post": "historia", "historia_formato": "video"})


def _raw_img(image_prompt=None, slides=None) -> str:
    return json.dumps({
        "linkedin_text": "x", "instagram_text": "", "facebook_text": "",
        "image_prompt": image_prompt, "image_slide_prompts": slides,
    })


def test_parse_raw_keeps_and_strips_image_prompt():
    posts = pw._parse_raw(_raw_img("  A cracked ceramic mug on a windowsill.  ", []))
    assert posts["image_prompt"] == "A cracked ceramic mug on a windowsill."


def test_parse_raw_drops_empty_or_non_string_image_prompt():
    assert "image_prompt" not in pw._parse_raw(_raw_img("", []))
    assert "image_prompt" not in pw._parse_raw(_raw_img(42, []))


def test_parse_raw_cleans_image_slide_prompts():
    posts = pw._parse_raw(_raw_img("cover", ["  slide one  ", "slide two", "  "]))
    assert posts["image_slide_prompts"] == ["slide one", "slide two"]


def test_parse_raw_drops_empty_image_slide_prompts():
    assert "image_slide_prompts" not in pw._parse_raw(_raw_img("cover", []))


def test_sanitize_covers_image_prompts():
    posts = pw._sanitize_posts({
        "image_prompt": "A **single** lamp [1]",
        "image_slide_prompts": ["Texture **detail**", "   "],
    })
    assert posts["image_prompt"] == "A single lamp"
    assert posts["image_slide_prompts"] == ["Texture detail"]


# ── image_style: la dirección de arte compartida por todo el set ──────────────

def test_parse_raw_keeps_and_strips_image_style():
    raw = json.dumps({"linkedin_text": "x", "image_style": "  Warm oat palette, 50mm.  "})
    assert pw._parse_raw(raw)["image_style"] == "Warm oat palette, 50mm."


def test_parse_raw_drops_empty_or_non_string_image_style():
    # Sin image_style, job_runner cae al acabado genérico de respaldo.
    for value in ("", "   ", 42, None):
        raw = json.dumps({"linkedin_text": "x", "image_style": value})
        assert "image_style" not in pw._parse_raw(raw)


def test_sanitize_covers_image_style():
    posts = pw._sanitize_posts({"image_style": "**Warm** oat palette [2]"})
    assert posts["image_style"] == "Warm oat palette"


def test_el_prompt_pide_image_style_junto_a_las_escenas():
    sp = pw._system_prompt()
    assert "`image_style`" in sp
    assert "image_style" in _user_msg_images()


def _user_msg_images() -> str:
    return pw._user_message(
        {"title": "t", "transcript": "x" * 100, "channel": "c"},
        {"redes": ["linkedin"], "formato": "carrusel", "carrusel_slides": 3},
        "https://youtu.be/x",
    )


# ── Composición: cambia según se imprima texto sobre la imagen o no ───────────

def test_con_overlay_el_prompt_pide_el_esqueleto_de_poster():
    # La pieza es un póster: tipo en la banda alta y la baja, sujeto en la central.
    # Pedir solo "un área calma" devolvía el look de foto con caption encima.
    sp = pw._system_prompt(True)
    assert "CENTRAL band" in sp
    assert "top band and the bottom band" in sp
    assert "compose the full frame" not in sp.lower()


def test_el_estilo_del_post_no_inventa_paleta():
    # La paleta es identidad de marca (prompts/brand.json) y la inyecta el arquitecto:
    # si además la escribiera el LLM por post, habría dos paletas en el mismo prompt.
    sp = pw._system_prompt(True)
    assert "Do NOT name a palette" in sp


def test_sin_overlay_el_prompt_pide_llenar_el_cuadro():
    # Con el texto apagado, reservar media imagen deja un vacío que nada llena.
    sp = pw._system_prompt(False)
    assert "compose the full frame" in sp.lower()
    assert "where that text will sit" not in sp


# ── Muestra de la transcripción que viaja al LLM ──────────────────────────────

def test_transcript_excerpt_keeps_short_transcripts_whole():
    t = "palabra " * 100
    assert pw._transcript_excerpt(t) == t.strip()


def test_transcript_excerpt_covers_beginning_middle_and_end():
    # Un video largo: antes solo llegaba el arranque, así que los prompts
    # visuales salían siempre del primer minuto.
    body = "relleno " * 4000
    t = f"ARRANQUE {body} MITAD {body} CIERRE"
    out = pw._transcript_excerpt(t, budget=3000)
    assert "ARRANQUE" in out and "MITAD" in out and "CIERRE" in out
    assert out.count("[...]") == 2
    assert len(out) <= 3000 + 20  # presupuesto + los marcadores


def test_user_message_flags_the_excerpt():
    long_content = dict(_CONTENT, transcript="palabra " * 5000)
    msg = pw._user_message(long_content, {"redes": ["linkedin"]}, "")
    assert "excerpt: beginning, middle and end" in msg
    assert "[...]" in msg


def test_user_message_does_not_flag_a_whole_transcript():
    msg = pw._user_message(_CONTENT, {"redes": ["linkedin"]}, "")
    assert "excerpt: beginning, middle and end" not in msg


# ── El LLM incumple el contrato: rescate, detección y aviso ───────────────────
# Reproducido con sonar-pro cuando la fuente venía sin transcripción: o anida los
# prompts dentro de `image_text`, o devuelve un JSON roto. En los dos casos el
# preview aparecía sin ningún prompt y nada decía por qué.

_CARRUSEL = {"redes": ["linkedin"], "formato_instagram": "carrusel", "carrusel_slides": 3}


def test_parse_raw_rescues_visuals_nested_inside_image_text():
    raw = json.dumps({
        "linkedin_text": "x",
        "image_text": {
            "hook": "Un titular", "slides": ["idea uno", "idea dos"],
            "image_prompt": "A coin stack on oak",
            "image_style": "Hard overhead key, 50mm",
            "image_slide_prompts": ["slide uno", "slide dos"],
        },
    })
    diag = []
    posts = pw._parse_raw(raw, diag)
    assert posts["image_prompt"] == "A coin stack on oak"
    assert posts["image_style"] == "Hard overhead key, 50mm"
    assert posts["image_slide_prompts"] == ["slide uno", "slide dos"]
    assert posts["image_text"] == {"hook": "Un titular", "slides": ["idea uno", "idea dos"]}
    assert diag == ["campos_anidados"]


def test_parse_raw_nested_rescue_never_overwrites_a_real_top_level_field():
    raw = json.dumps({
        "linkedin_text": "x",
        "image_prompt": "El bueno",
        "image_text": {"hook": "h", "image_prompt": "El anidado"},
    })
    assert pw._parse_raw(raw)["image_prompt"] == "El bueno"


def test_parse_raw_flags_a_malformed_json_as_degraded():
    # Comilla sin escapar dentro del último valor: no parsea ni se repara, y el
    # respaldo de nivel 3 solo rescata los captions.
    raw = '{"linkedin_text": "hola", "instagram_text": "ig", "facebook_text": "fb", "image_prompt": '
    diag = []
    posts = pw._parse_raw(raw, diag)
    assert posts["linkedin_text"] == "hola"
    assert "image_prompt" not in posts
    assert diag == ["json_malformado"]


def test_info_slides_needed_matches_the_carousel_count():
    assert pw._info_slides_needed(_CARRUSEL) == 2
    assert pw._info_slides_needed({"formato_instagram": "carrusel", "carrusel_slides": 6}) == 5
    assert pw._info_slides_needed({"formato_instagram": "imagen-unica"}) == 0
    # Fuera de rango y basura: mismos clamps que el resto del pipeline.
    assert pw._info_slides_needed({"formato_instagram": "carrusel", "carrusel_slides": 99}) == 5
    assert pw._info_slides_needed({"formato_instagram": "carrusel", "carrusel_slides": "x"}) == 2


def test_faltantes_detecta_el_carrusel_sin_prompts():
    faltan = pw._faltantes({"linkedin_text": "x"}, _CARRUSEL)
    assert set(faltan) == {"image_prompt", "image_style", "image_text.hook",
                           "image_slide_prompts", "image_text.slides"}


def test_faltantes_vacio_cuando_el_llm_cumplio():
    posts = {
        "linkedin_text": "x", "image_prompt": "cover", "image_style": "estilo",
        "image_slide_prompts": ["a", "b"],
        "image_text": {"hook": "titular", "slides": ["uno", "dos"]},
    }
    assert pw._faltantes(posts, _CARRUSEL) == []


def test_faltantes_cuenta_los_slides_de_menos():
    posts = {
        "linkedin_text": "x", "image_prompt": "cover", "image_style": "estilo",
        "image_slide_prompts": ["solo uno"],
        "image_text": {"hook": "titular", "slides": ["uno", "dos"]},
    }
    assert pw._faltantes(posts, _CARRUSEL) == ["image_slide_prompts"]


def test_faltantes_ignora_lo_visual_cuando_el_medio_ya_existe():
    # media_origin=subir/fotos: no se genera nada visual, no hay nada que reclamar.
    # El caption SÍ se sigue reclamando: el post se publica igual.
    assert pw._faltantes({"linkedin_text": "x"}, dict(_CARRUSEL, media_origin="subir")) == []
    assert pw._faltantes({}, dict(_CARRUSEL, media_origin="subir")) == ["linkedin_text"]


def test_faltantes_revisa_el_video_en_un_reel():
    params = {"redes": ["instagram"], "tipo_post": "reel",
              "duracion_video": 10, "video_segment_seconds": 5}
    assert set(pw._faltantes({"instagram_text": "x"}, params)) == {
        "video_prompt", "video_style", "video_storyboard", "video_voiceover"}


def test_aviso_de_escritura_solo_cuando_falta_algo():
    assert pw._avisos_escritura([], []) == []
    aviso = pw._avisos_escritura(["image_prompt"], ["json_malformado"])[0]
    assert aviso["campo"] == "escritura" and aviso["nivel"] == "alto"
    assert "image_prompt" in aviso["mensaje"] and "roto" in aviso["mensaje"]
    # Sin diagnóstico de parseo, la causa es que el modelo los dejó vacíos.
    assert "vacíos" in pw._avisos_escritura(["image_style"], [])[0]["mensaje"]


class _Cfg:
    llm_provider = "perplexity"
    perplexity_api_key = "k"
    anthropic_api_key = ""
    image_text_in_prompt = True


_POSTS_OK = {
    "linkedin_text": "x", "image_prompt": "cover", "image_style": "estilo",
    "image_slide_prompts": ["a", "b"],
    "image_text": {"hook": "titular", "slides": ["uno", "dos"]},
}
_POSTS_ROTOS = {"linkedin_text": "x"}
_USAGE = {"service": "perplexity", "model": "sonar-pro",
          "units": {"input_tokens": 10, "output_tokens": 5, "requests": 1}}


def _fake_writer(monkeypatch, posts, diag=()):
    """Sustituye la escritura completa (1er intento) por una respuesta fija."""
    colas = []

    async def _fake(content, params, clean_url, queue, api_key, *, text_overlay=True,
                    diagnostico=None):
        colas.append(queue)
        if isinstance(posts, Exception):
            raise posts
        if diagnostico is not None:
            diagnostico.extend(diag)
        return copy.deepcopy(posts), dict(_USAGE)

    monkeypatch.setattr(pw, "_write_with_perplexity", _fake)
    return colas


def _fake_repair(monkeypatch, salida):
    """Sustituye la REPARACIÓN dirigida y captura el user message que recibió."""
    llamadas = []

    async def _fake(content, params, posts, faltan, cfg, clean_url=""):
        llamadas.append({"faltan": list(faltan),
                         "mensaje": pw._repair_user_message(content, params, posts, faltan,
                                                            clean_url)})
        if isinstance(salida, Exception):
            raise salida
        return copy.deepcopy(salida), dict(_USAGE)

    monkeypatch.setattr(pw, "_reparar", _fake)
    return llamadas


async def test_write_posts_no_reintenta_cuando_el_llm_cumple(monkeypatch):
    colas = _fake_writer(monkeypatch, _POSTS_OK)
    reparaciones = _fake_repair(monkeypatch, {})
    posts, usage, avisos = await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())
    assert len(colas) == 1 and reparaciones == []
    assert avisos == []
    assert usage["units"]["requests"] == 1
    assert posts["image_prompt"] == "cover"


async def test_write_posts_pide_solo_los_campos_que_faltan(monkeypatch):
    # El 1er intento trae los captions y el copy de la pieza pero ni un prompt: el
    # fallo real que motivó todo esto.
    parcial = {"linkedin_text": "x", "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    _fake_writer(monkeypatch, parcial)
    reparaciones = _fake_repair(monkeypatch, {
        "image_prompt": "cover", "image_style": "estilo", "image_slide_prompts": ["a", "b"],
    })
    posts, usage, avisos = await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())

    assert len(reparaciones) == 1
    assert reparaciones[0]["faltan"] == ["image_prompt", "image_style", "image_slide_prompts"]
    assert avisos == []
    assert posts["image_prompt"] == "cover"
    # Lo que ya estaba bien NO se vuelve a pedir ni se pierde en el camino.
    assert posts["image_text"] == {"hook": "titular", "slides": ["uno", "dos"]}
    assert posts["linkedin_text"] == "x"
    assert usage["units"]["requests"] == 2


async def test_reparacion_no_pide_los_captions_y_manda_lo_ya_escrito(monkeypatch):
    parcial = {"linkedin_text": "caption largo",
               "image_text": {"hook": "titular", "slides": ["uno", "dos"]},
               "image_prompt": "cover ya escrita"}
    _fake_writer(monkeypatch, parcial)
    reparaciones = _fake_repair(monkeypatch, {"image_style": "e", "image_slide_prompts": ["a", "b"]})
    await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())

    msg = reparaciones[0]["mensaje"]
    assert "REPAIR REQUEST" in msg
    assert "Do NOT return linkedin_text" in msg
    # La portada que YA estaba viaja como contexto para que los slides encajen con ella.
    assert "cover ya escrita" in msg
    assert "titular" in msg and "uno" in msg
    # Y no se le vuelve a pedir un campo que no falta.
    assert '"image_prompt"' not in msg.split("OUTPUT FORMAT")[1].split("\n")[1]


async def test_write_posts_avisa_cuando_la_reparacion_tampoco_alcanza(monkeypatch):
    _fake_writer(monkeypatch, _POSTS_ROTOS, ["json_malformado"])
    _fake_repair(monkeypatch, {"image_style": "estilo"})   # devuelve solo uno de los cinco
    posts, usage, avisos = await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())
    assert len(avisos) == 1 and avisos[0]["campo"] == "escritura"
    assert "image_prompt" in avisos[0]["mensaje"]
    # Lo poco que sí llegó se conserva: el aviso ya solo nombra lo que sigue faltando.
    assert posts["image_style"] == "estilo"
    assert "image_style" not in avisos[0]["mensaje"]
    assert usage["units"]["requests"] == 2


async def test_write_posts_sobrevive_a_una_reparacion_que_revienta(monkeypatch):
    _fake_writer(monkeypatch, _POSTS_ROTOS)
    _fake_repair(monkeypatch, RuntimeError("502 del proveedor"))
    posts, usage, avisos = await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())
    assert posts["linkedin_text"] == "x"      # se conserva el primer intento
    assert usage["units"]["requests"] == 1    # la reparación fallida no se cobra
    assert len(avisos) == 1


# ── Merge por campo: el reintento nunca puede tirar lo bueno del intento previo ──

def test_merge_no_pisa_lo_que_ya_estaba():
    base = {"linkedin_text": "bueno", "image_prompt": "cover buena", "image_style": "",
            "image_text": {"hook": "titular", "slides": []}}
    extra = {"linkedin_text": "otro", "image_prompt": "cover peor", "image_style": "estilo",
             "image_text": {"hook": "otro titular", "slides": ["uno", "dos"]}}
    out = pw._merge_posts(base, extra, _CARRUSEL)
    assert out["linkedin_text"] == "bueno"      # lo lleno no se toca
    assert out["image_prompt"] == "cover buena"
    assert out["image_style"] == "estilo"       # lo vacío sí se rellena
    assert out["image_text"]["hook"] == "titular"
    assert out["image_text"]["slides"] == ["uno", "dos"]


def test_merge_completa_una_lista_corta_pero_no_una_completa():
    # 2 slides de info en _CARRUSEL: una lista corta se reemplaza, una completa no.
    corta = {"image_slide_prompts": ["a"]}
    pw._merge_posts(corta, {"image_slide_prompts": ["x", "y"]}, _CARRUSEL)
    assert corta["image_slide_prompts"] == ["x", "y"]

    completa = {"image_slide_prompts": ["a", "b"]}
    pw._merge_posts(completa, {"image_slide_prompts": ["x", "y", "z"]}, _CARRUSEL)
    assert completa["image_slide_prompts"] == ["a", "b"]


def test_merge_junta_dos_intentos_a_medias():
    # El caso que el swap anterior resolvía mal: cada intento traía una mitad y se
    # tiraba la del que tuviera más faltantes.
    base = {"linkedin_text": "x", "image_prompt": "cover", "image_style": "estilo",
            "image_slide_prompts": [],
            "image_text": {"hook": "", "slides": ["uno", "dos"]}}
    extra = {"image_slide_prompts": ["a", "b"], "image_text": {"hook": "titular", "slides": []}}
    out = pw._merge_posts(base, extra, _CARRUSEL)
    assert pw._faltantes(out, _CARRUSEL) == []


# ── Reintento manual desde la compuerta previa ────────────────────────────────

async def test_rewrite_posts_no_llama_al_llm_si_no_falta_nada(monkeypatch):
    reparaciones = _fake_repair(monkeypatch, {})
    posts, usage, avisos = await pw.rewrite_posts({}, _CARRUSEL, dict(_POSTS_OK), _Cfg())
    assert reparaciones == [] and usage is None and avisos == []
    assert posts["image_prompt"] == "cover"


async def test_rewrite_posts_completa_y_respeta_lo_editado_a_mano(monkeypatch):
    # El usuario escribió la portada a mano y pide el resto: solo se pide el resto.
    a_mano = {"linkedin_text": "x", "image_prompt": "la que escribí yo",
              "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    reparaciones = _fake_repair(monkeypatch, {
        "image_prompt": "la del modelo", "image_style": "estilo",
        "image_slide_prompts": ["a", "b"],
    })
    posts, usage, avisos = await pw.rewrite_posts({}, _CARRUSEL, a_mano, _Cfg())
    assert reparaciones[0]["faltan"] == ["image_style", "image_slide_prompts"]
    assert posts["image_prompt"] == "la que escribí yo"   # no se pisa
    assert posts["image_slide_prompts"] == ["a", "b"]
    assert avisos == [] and usage["units"]["requests"] == 1


def test_merge_usage_suma_el_costo_del_reintento():
    a = {"service": "perplexity", "model": "sonar-pro",
         "units": {"input_tokens": 10, "output_tokens": 5, "requests": 1}}
    b = {"service": "perplexity", "model": "sonar-pro",
         "units": {"input_tokens": 7, "output_tokens": 3, "requests": 1}}
    merged = pw._merge_usage(a, b)
    assert merged["units"] == {"input_tokens": 17, "output_tokens": 8, "requests": 2}
    assert merged["service"] == "perplexity"
    assert pw._merge_usage(None, b) == b
    assert pw._merge_usage(a, None) == a


# ── Estructura del copy: el esqueleto ya no es siempre el mismo ───────────────
# El copy salía genérico por el PROMPT, no por el modelo: LinkedIn pedía siempre
# "3-5 takeaways con viñetas" y cerrar con pregunta, así que todos los posts tenían
# la misma forma. Ahora la forma la elige el contenido, de un catálogo.

def test_el_prompt_ofrece_un_catalogo_de_estructuras():
    sp = pw._system_prompt()
    assert "COPY STRUCTURE" in sp
    for estructura in ("ANECDOTE", "CONTRAST", "THE NUMBER", "THESIS", "STEP BY STEP",
                       "TAKEAWAYS", "SYMPTOM", "ANALOGY"):
        assert estructura in sp, f"falta la estructura {estructura} en el catálogo"


def test_las_vinetas_dejan_de_ser_obligatorias_en_linkedin():
    # La regla vieja ("3-5 key insights or takeaways with → or bullet formatting")
    # imponía la lista en TODOS los posts: es justo lo que se venía a arreglar.
    sp = pw._system_prompt()
    assert "3–5 key insights or takeaways with → or bullet formatting" not in sp
    assert "ONLY if that structure allows them" in sp


def test_la_lista_sigue_permitida_cuando_el_contenido_enumera():
    # No es una prohibición de listas: es que dejen de ser el default.
    sp = pw._system_prompt()
    assert "Structures 5 and 6 are the ONLY ones allowed to use bullets" in sp


def test_el_cierre_con_pregunta_deja_de_ser_obligatorio():
    sp = pw._system_prompt()
    assert "End with a question to spark engagement" not in sp
    assert "Vary the ending" in sp


def test_las_redes_no_pueden_compartir_estructura_ni_apertura():
    sp = pw._system_prompt()
    assert "must NOT all use the same structure" in sp


def test_el_checklist_revisa_la_forma_del_post_terminado():
    # La humanización miraba solo el vocabulario; el esqueleto pasaba entero.
    sp = pw._system_prompt()
    assert "hook + bulleted list + engagement question + hashtags" in sp


# ── Copy de los slides: una secuencia, no N frases sueltas ────────────────────

def test_los_slides_se_piden_como_secuencia_con_remate():
    sp = pw._system_prompt()
    assert "ONE SEQUENCE, NOT N INDEPENDENT PHRASES" in sp
    assert "lands the payoff" in sp
    # El último slide sigue siendo informativo: nunca créditos ni despedida.
    assert "never credits" in sp


def test_el_prompt_documenta_el_corte_titular_apoyo():
    sp = pw._system_prompt()
    assert "`Headline — support`" in sp
    assert "the dash itself is never printed" in sp


def test_la_reparacion_repite_las_reglas_del_carrusel():
    # Si la reparación pidiera las frases sin las reglas nuevas, el reintento
    # devolvería el formato viejo y el arreglo duraría hasta el primer fallo.
    spec = pw._SPEC_REPARACION["image_text.slides"]
    assert "ONE sequence" in spec and "Headline — support" in spec


def test_el_user_message_pide_la_secuencia_en_el_carrusel():
    msg = pw._user_message(
        _CONTENT, {"redes": ["instagram"], "formato_instagram": "carrusel",
                   "carrusel_slides": 4}, "")
    assert "read as one sequence" in msg


# ── Los captions son parte del contrato ──────────────────────────────────────
# `_faltantes` solo miraba lo visual, así que un caption vacío pasaba entero: sin
# reparación, sin aviso y sin campo donde escribirlo (la compuerta previa dibujaba
# el textarea solo si YA había texto). Se publicaba un post en blanco.

def test_captions_needed_sigue_a_las_redes_destino():
    assert pw.captions_needed({"redes": ["linkedin", "facebook"]}) == ["linkedin_text",
                                                                       "facebook_text"]
    # Sin `redes` el job va a las tres de feed: el escritor pide los tres captions.
    assert pw.captions_needed({}) == ["linkedin_text", "instagram_text", "facebook_text"]


def test_tiktok_reutiliza_el_caption_de_instagram():
    # TikTok no tiene caption propio (mismo criterio que `_user_message`): pedir uno
    # inexistente dejaría el reintento encendido para siempre.
    assert pw.captions_needed({"redes": ["tiktok"]}) == ["instagram_text"]


def test_faltantes_detecta_un_caption_vacio():
    posts = {"image_prompt": "cover", "image_style": "estilo",
             "image_slide_prompts": ["a", "b"],
             "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    assert pw._faltantes(posts, _CARRUSEL) == ["linkedin_text"]
    # En blanco o solo espacios es lo mismo: publica un post vacío igual.
    assert pw._faltantes({**posts, "linkedin_text": "   "}, _CARRUSEL) == ["linkedin_text"]


def test_faltantes_ignora_el_caption_de_una_red_que_no_es_destino():
    posts = {"linkedin_text": "x", "image_prompt": "cover", "image_style": "estilo",
             "image_slide_prompts": ["a", "b"],
             "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    assert pw._faltantes(posts, _CARRUSEL) == []


def test_el_aviso_nombra_el_caption_y_no_lo_llama_campo_visual():
    msg = pw._avisos_escritura(["instagram_text"], [])[0]["mensaje"]
    assert "caption" in msg and "visual" not in msg
    mixto = pw._avisos_escritura(["instagram_text", "image_prompt"], [])[0]["mensaje"]
    assert "1 caption(s) y 1 campo(s) visual(es)" in mixto


async def test_la_reparacion_pide_el_caption_que_falta(monkeypatch):
    # El bug: la reparación tenía una línea FIJA prohibiendo devolver los tres
    # captions, así que le prohibía justo lo que se le venía a pedir.
    parcial = {"image_prompt": "cover", "image_style": "estilo",
               "image_slide_prompts": ["a", "b"],
               "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    _fake_writer(monkeypatch, parcial)
    reparaciones = _fake_repair(monkeypatch, {"linkedin_text": "el caption reparado"})
    posts, _, avisos = await pw.write_posts({}, _CARRUSEL, "https://youtu.be/x",
                                            asyncio.Queue(), _Cfg())

    assert reparaciones[0]["faltan"] == ["linkedin_text"]
    msg = reparaciones[0]["mensaje"]
    assert '"linkedin_text"' in msg
    assert "Do NOT return linkedin_text" not in msg
    # Los otros dos siguen siendo intocables, y la URL viaja para el CTA de LinkedIn.
    assert "Do NOT return instagram_text, facebook_text" in msg
    assert "https://youtu.be/x" in msg
    assert posts["linkedin_text"] == "el caption reparado"
    assert avisos == []


async def test_la_reparacion_de_un_caption_ve_al_hermano_ya_escrito(monkeypatch):
    # Dos redes del mismo job no pueden compartir esqueleto ni apertura: sin ver el
    # caption ya escrito el modelo no puede cumplir esa regla.
    params = {"redes": ["linkedin", "instagram"], "formato_instagram": "imagen-unica"}
    parcial = {"linkedin_text": "El caption de LinkedIn que ya estaba",
               "image_prompt": "cover", "image_style": "estilo",
               "image_text": {"hook": "titular"}}
    _fake_writer(monkeypatch, parcial)
    reparaciones = _fake_repair(monkeypatch, {"instagram_text": "ig"})
    await pw.write_posts({}, params, "", asyncio.Queue(), _Cfg())

    msg = reparaciones[0]["mensaje"]
    assert "El caption de LinkedIn que ya estaba" in msg
    assert "must NOT repeat its structure or its opening line" in msg


async def test_sin_url_la_reparacion_no_inventa_un_cta(monkeypatch):
    parcial = {"image_prompt": "cover", "image_style": "estilo",
               "image_slide_prompts": ["a", "b"],
               "image_text": {"hook": "titular", "slides": ["uno", "dos"]}}
    _fake_writer(monkeypatch, parcial)
    reparaciones = _fake_repair(monkeypatch, {"linkedin_text": "x"})
    await pw.write_posts({}, _CARRUSEL, "", asyncio.Queue(), _Cfg())
    assert "There is NO source URL" in reparaciones[0]["mensaje"]
