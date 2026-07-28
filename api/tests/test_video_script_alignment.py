"""Tests de `_align_video_script`: el guion de voz debe salir del writer con
EXACTAMENTE una línea por shot del storyboard.

Con conteos distintos `explainer_video` no puede armar los bloques y el reel sale
mudo. El aviso del preview queda para las ediciones del usuario; lo que genera la
app se reconcilia acá.
"""

import post_writer as pw

# 4 shots pedidos: 40s de duración / 10s por segmento.
_PARAMS = {"tipo_post": "reel", "redes": ["instagram"],
           "duracion_video": 40, "video_segment_seconds": 10}


def _posts(storyboard, voiceover) -> dict:
    return {"video_storyboard": list(storyboard), "video_voiceover": list(voiceover)}


def _sb(n: int) -> list[str]:
    return [f"shot {i}" for i in range(n)]


def test_short_voiceover_is_redistributed_across_shots():
    vo = ["Ahorrar no es magia. Es constancia.",
          "Cada peso cuenta. Y suma más rápido de lo que crees.",
          "Empezá hoy. Mañana ya es tarde."]
    posts = pw._align_video_script(_posts(_sb(4), vo), _PARAMS)
    assert len(posts["video_voiceover"]) == 4
    assert len(posts["video_storyboard"]) == 4  # no se acorta el video


def test_redistribution_preserves_every_word():
    vo = ["Ahorrar no es magia. Es constancia.",
          "Cada peso cuenta. Y suma más rápido de lo que crees.",
          "Empezá hoy. Mañana ya es tarde."]
    posts = pw._align_video_script(_posts(_sb(4), vo), _PARAMS)
    assert " ".join(posts["video_voiceover"]).split() == " ".join(vo).split()


def test_long_voiceover_is_merged_down_to_the_shots():
    vo = [f"Línea número {i} del guion hablado." for i in range(7)]
    posts = pw._align_video_script(_posts(_sb(4), vo), _PARAMS)
    assert len(posts["video_voiceover"]) == 4
    assert " ".join(posts["video_voiceover"]).split() == " ".join(vo).split()


def test_extra_shots_are_trimmed_to_the_requested_duration():
    # El LLM devolvió 6 shots donde la duración pedía 4: el reel duraría 60s.
    posts = pw._align_video_script(_posts(_sb(6), ["a b c d."] * 6), _PARAMS)
    assert len(posts["video_storyboard"]) == 4
    assert len(posts["video_voiceover"]) == 4


def test_unsplittable_script_trims_the_storyboard_instead():
    # Un guion de una sola frase corta no da para 4 bloques: mejor un reel corto
    # con voz que uno completo mudo.
    posts = pw._align_video_script(_posts(_sb(4), ["Hola."]), _PARAMS)
    assert len(posts["video_storyboard"]) == 1
    assert posts["video_voiceover"] == ["Hola."]


def test_matching_counts_are_left_untouched():
    vo = ["uno dos tres.", "cuatro cinco seis.", "siete ocho nueve.", "diez once doce."]
    posts = pw._align_video_script(_posts(_sb(4), vo), _PARAMS)
    assert posts["video_voiceover"] == vo
    assert posts["video_storyboard"] == _sb(4)


def test_no_video_job_is_untouched():
    # Un post de imagen no lleva guion; nada que reconciliar aunque el LLM insista.
    params = {"tipo_post": "post", "tipo_medio": "imagen", "redes": ["instagram"]}
    posts = pw._align_video_script(_posts(_sb(4), ["Hola."]), params)
    assert len(posts["video_storyboard"]) == 4


def test_uploaded_media_job_is_untouched():
    params = dict(_PARAMS, media_origin="subir")
    posts = pw._align_video_script(_posts(_sb(4), ["Hola."]), params)
    assert len(posts["video_storyboard"]) == 4


def test_missing_voiceover_does_not_invent_one():
    posts = pw._align_video_script({"video_storyboard": _sb(4)}, _PARAMS)
    assert "video_voiceover" not in posts
