"""Tests del armado del prompt final de las imágenes en job_runner.

La escena la escribe el LLM anclada a la transcripción (`image_prompt` /
`image_slide_prompts`); antes el prompt se armaba SOLO con el título del video,
así que la imagen no tenía relación con lo que se decía adentro. job_runner le
suma lo que el modelo no debe decidir: el encuadre de cada slide, la dirección de
arte compartida (`image_style`), la composición y el "sin texto", y conserva el
camino viejo como fallback cuando el modelo no entrega escena.
"""

import pytest

import job_runner as jr
import prompt_architect as parch

_CONTENT = {"title": "Cómo ahorrar sin sufrir"}
_SCENE = "A chipped ceramic jar of coins on a kitchen windowsill."
_STYLE = "Warm oat and faded denim palette, low afternoon window light, 50mm, fine grain."


def test_cover_prompt_uses_the_llm_scene():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT)
    assert p.startswith(_SCENE)
    assert p.endswith(jr._NO_TEXT_SUFFIX)


def test_cover_prompt_always_carries_look_and_grounding():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT)
    assert jr._IMAGE_LOOK in p          # sin image_style, el acabado de respaldo
    assert jr._GROUNDING_SUFFIX in p


def test_cover_prompt_falls_back_to_the_title():
    # Sin escena del modelo (JSON incompleto o el usuario la vació en el preview).
    for posts in ({}, {"image_prompt": "   "}):
        p = jr._cover_image_prompt(posts, _CONTENT)
        assert "Cómo ahorrar sin sufrir" in p
        assert p.endswith(jr._NO_TEXT_SUFFIX)


# ── Dirección de arte compartida (image_style) ────────────────────────────────

def test_image_style_replaces_the_generic_look():
    p = jr._cover_image_prompt({"image_prompt": _SCENE, "image_style": _STYLE}, _CONTENT)
    assert _STYLE in p
    assert jr._IMAGE_LOOK not in p


def test_image_style_is_identical_in_the_cover_and_every_slide():
    # Es el mecanismo entero: el MISMO texto en todas las imágenes del set.
    posts = {"image_prompt": _SCENE, "image_style": _STYLE,
             "image_slide_prompts": ["Scene A.", "Scene B."]}
    prompts = [jr._cover_image_prompt(posts, _CONTENT)] + jr._slide_image_prompts(posts, _CONTENT, 2)
    assert all(_STYLE in p for p in prompts)


# ── Composición: depende de si se imprime texto encima ────────────────────────

def test_sin_texto_se_pide_llenar_el_cuadro():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT)
    assert jr._IMAGE_FULL_FRAME in p
    assert jr._IMAGE_SPACE_FEED not in p


def test_con_texto_se_reservan_las_bandas_del_titular():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT, con_texto=True)
    assert jr._IMAGE_SPACE_FEED in p
    assert jr._IMAGE_FULL_FRAME not in p


def test_la_historia_usa_la_variante_vertical():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT, vertical=True, con_texto=True)
    assert jr._IMAGE_SPACE_VERTICAL in p
    assert jr._IMAGE_SPACE_FEED not in p


def test_la_historia_sin_texto_tambien_llena_el_cuadro():
    p = jr._cover_image_prompt({"image_prompt": _SCENE}, _CONTENT, vertical=True)
    assert jr._IMAGE_FULL_FRAME_VERTICAL in p


# ── Slides del carrusel ───────────────────────────────────────────────────────

def test_slide_prompts_count_is_exactly_the_info_slides():
    # Ya no hay slide de créditos: uno por slide de info y nada más.
    prompts = jr._slide_image_prompts({"image_prompt": _SCENE}, _CONTENT, 3)
    assert len(prompts) == 3


def test_slide_prompts_use_each_llm_scene_in_order():
    posts = {"image_prompt": _SCENE, "image_slide_prompts": ["Scene A.", "Scene B."]}
    prompts = jr._slide_image_prompts(posts, _CONTENT, 2)
    assert prompts[0].startswith("Scene A.")
    assert prompts[1].startswith("Scene B.")


def test_slide_prompts_pad_missing_scenes_with_the_title_fallback():
    posts = {"image_prompt": _SCENE, "image_slide_prompts": ["Scene A."]}
    prompts = jr._slide_image_prompts(posts, _CONTENT, 3)
    assert prompts[0].startswith("Scene A.")
    assert "Cómo ahorrar sin sufrir" in prompts[1]
    assert "Cómo ahorrar sin sufrir" in prompts[2]


def test_slide_prompts_never_carry_text_into_the_image():
    prompts = jr._slide_image_prompts({}, _CONTENT, 2)
    assert all(p.endswith(jr._NO_TEXT_SUFFIX) for p in prompts)


def test_cada_slide_recibe_el_plano_de_su_beat():
    # El plano lo fija el BEAT que le toca al slide por posición, no el LLM: es lo
    # que le da al set una secuencia (tensión → desarrollo → remate) en vez de tres
    # versiones del mismo cuadro.
    posts = {"image_prompt": _SCENE, "image_slide_prompts": ["A.", "B.", "C."]}
    prompts = jr._slide_image_prompts(posts, _CONTENT, 3)
    for i, rol in enumerate(parch.roles_carrusel(3)):
        assert parch.encuadre_beat(rol) in prompts[i]


def test_los_planos_de_dos_slides_seguidos_son_distintos():
    prompts = jr._slide_image_prompts({"image_prompt": _SCENE}, _CONTENT, 4)
    planos = [parch.encuadre_beat(r) for r in parch.roles_carrusel(4)]
    assert len(set(planos)) == 4          # cuatro beats, cuatro distancias
    assert all(planos[i] in prompts[i] for i in range(4))


def test_el_ritmo_de_la_identidad_pisa_el_de_la_casa():
    # El beat es estructura y la ejecución fotográfica es marca: la identidad activa
    # decide con qué plano se recorre la escalera.
    ident = {"ritmo_carrusel": ["Extreme macro of wet slate.", "Table-height still life.",
                                "Overhead of one tool.", "Wide room, subject tiny."]}
    prompts = jr._slide_image_prompts({"image_prompt": _SCENE}, _CONTENT, 4, identidad=ident)
    assert "Extreme macro of wet slate." in prompts[0]
    assert "Wide room, subject tiny." in prompts[3]


def test_un_hueco_del_ritmo_cae_al_respaldo_de_ese_beat():
    # La posición es el beat: vaciar el segundo no puede correr el tercero a su sitio.
    # El hueco cae al `ritmo` de ESE beat en architect.json, no al de la casa: la
    # identidad de la casa solo entra cuando el ritmo llega vacío ENTERO.
    ident = {"ritmo_carrusel": ["Extreme macro of wet slate.", "", "Overhead of one tool."]}
    prompts = jr._slide_image_prompts({"image_prompt": _SCENE}, _CONTENT, 4, identidad=ident)
    assert "Extreme macro of wet slate." in prompts[0]
    assert "Overhead of one tool." in prompts[2]
    assert parch._cfg_arch()["roles"]["desarrollo"]["ritmo"] in prompts[1]


def test_el_ultimo_slide_es_de_info_como_los_del_centro():
    # El carrusel ya no cierra con créditos: el último slide sale de su propia escena
    # del LLM y del plano de su beat, exactamente igual que los del centro.
    posts = {"image_prompt": _SCENE, "image_slide_prompts": ["Scene A.", "Scene B.", "Scene C."]}
    prompts = jr._slide_image_prompts(posts, _CONTENT, 3)
    assert prompts[-1].startswith("Scene C.")
    assert parch.encuadre_beat("remate") in prompts[-1]


def test_rehacer_un_slide_no_le_cambia_el_beat():
    # `regenerate_image` recalcula el rol por índice: si contara otra secuencia, el
    # slide rehecho saldría con el plano y la escala de otro momento del carrusel.
    assert [jr._rol_slide(i, 4) for i in range(4)] == parch.roles_carrusel(4)


# ── La portada como CONTEXTO del slide, no como su encargo ────────────────────
# Defecto que esto cubre: `_prompt_imagen` le pasaba al arquitecto la escena de la
# portada como `angulo` en TODAS las imágenes. A cada slide se le pedía entonces,
# sin querer, el sujeto de la portada — una fuente de carruseles con la misma foto
# repetida independiente del image-to-image de `medias`.


class _CfgSinLLM:
    """Sin keys: el arquitecto resuelve por el camino determinista."""
    anthropic_api_key = ""
    perplexity_api_key = ""
    image_text_in_prompt = True
    prompt_architect = True
    prompt_architect_critique = False


_POSTS_IMG = {"image_prompt": _SCENE, "image_style": _STYLE}


def _spec_de(rol: str) -> dict:
    """La spec que `_prompt_imagen` le entrega al arquitecto para ese rol."""
    visto = {}

    class _Parch:
        """El módulo real, con `construir` interceptado: lo demás (`rol_base`,
        `encuadre_beat`) tiene que seguir resolviendo de verdad."""

        def __getattr__(self, nombre):
            return getattr(parch, nombre)

        @staticmethod
        def construir(spec, **kw):
            visto.update(spec)
            raise RuntimeError("corta acá: solo nos interesa la spec")

    original = jr.parch
    jr.parch = _Parch()
    try:
        jr._prompt_imagen(_CfgSinLLM(), prompt_base="base", posts=_POSTS_IMG,
                          content=_CONTENT, texto="Un titular", rol=rol, aspect="4:5")
    finally:
        jr.parch = original
    return visto["contenido"]


def test_la_portada_recibe_su_escena_como_angulo():
    c = _spec_de("portada")
    assert c["angulo"] == _SCENE
    assert c["escena_portada"] == ""


def test_el_slide_no_recibe_la_escena_de_la_portada_como_angulo():
    c = _spec_de("contenido")
    assert c["angulo"] == ""
    # Viaja como continuidad de set: mismo mundo, objeto distinto.
    assert c["escena_portada"] == _SCENE
