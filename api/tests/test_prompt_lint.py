"""Lint de los prompts: lo que el pipeline se tragaba en silencio, dicho a tiempo.

Dos cosas importan acá y tiran en direcciones opuestas: que **avise** de lo que de
verdad va a salir mal (escenas repetidas, clichés prohibidos, escenas que faltan y se
rellenan con el título) y que **no moleste** con lo que es correcto — dos slides del
mismo mundo visual comparten materiales y ambiente a propósito, y avisar de eso
entrenaría a ignorar los avisos.
"""
import prompt_lint as pl
from post_writer import _system_prompt


def _msgs(avisos, campo=None):
    return [a["mensaje"] for a in avisos if campo is None or a["campo"] == campo]


def _posts(**overrides) -> dict:
    base = {
        "image_prompt": "A chipped ceramic jar of coins on a kitchen windowsill, morning light.",
        "image_style": "Single hard overhead key, deep falloff, brushed steel surfaces, 50mm, "
                       "shallow depth, fine grain.",
        "image_slide_prompts": [
            "A folded paper receipt curling at the edge on a worn oak table.",
            "A glass jar of dried beans on a pantry shelf, lid half open.",
            "A wall calendar with one date circled in pencil, seen from below.",
        ],
        "image_text": {"hook": "Ahorrar sin sufrir", "slides": ["Idea uno", "Idea dos", "Idea tres"]},
    }
    base.update(overrides)
    return base


# ── La fuente de los clichés no puede separarse del prompt ───────────────────


def test_los_cliches_son_los_que_prohibe_el_prompt_del_sistema():
    # El lint busca exactamente lo que el prompt prohíbe: si alguien edita la lista de
    # uno, este test obliga a editar la del otro.
    prompt = _system_prompt().lower()
    for cliche in pl.CLICHES:
        assert cliche in prompt, f"«{cliche}» ya no está prohibido en el prompt del sistema"


# ── Lo que tiene que avisar ──────────────────────────────────────────────────


def test_avisa_cuando_faltan_escenas_y_la_app_rellena_con_el_titulo():
    # Este era el caso silencioso: menos escenas de las pedidas → la app cuelga el
    # relleno del TÍTULO, justo lo que el resto del sistema evita.
    avisos = pl.revisar(_posts(image_slide_prompts=["Una sola escena concreta del contenido."]),
                        n_info=3, is_carousel=True)
    msg = " ".join(_msgs(avisos, "image_slide_prompts"))
    assert "TÍTULO" in msg and "1 escena(s) para 3" in msg


def test_avisa_cuando_dos_escenas_son_casi_la_misma():
    p = _posts(image_slide_prompts=[
        "A folded paper receipt curling at the edge on a worn oak table.",
        "A folded paper receipt curling on a worn oak table at the edge.",
        "A wall calendar with one date circled in pencil, seen from below.",
    ])
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True), "image_slide_prompts"))
    assert "casi la misma" in msg


def test_avisa_cuando_un_slide_repite_la_portada():
    p = _posts(image_slide_prompts=[
        "A chipped ceramic jar of coins on a kitchen windowsill in morning light.",
        "A wall calendar with one date circled in pencil, seen from below.",
        "A glass jar of dried beans on a pantry shelf, lid half open.",
    ])
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True)))
    assert "de la portada" in msg


def test_avisa_de_los_cliches_prohibidos():
    p = _posts(image_prompt="Business people in a meeting around a glass table.")
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True)))
    assert "business people in a meeting" in msg


def test_avisa_cuando_las_manos_son_el_sujeto():
    # Es donde el modelo dibuja seis dedos.
    p = _posts(image_prompt="Hands typing on a mechanical keyboard, close up.")
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True)))
    assert "dedos de más" in msg


def test_una_mano_quieta_no_dispara_el_aviso():
    # La regla prohíbe la mano HACIENDO algo, no la presencia humana quieta.
    p = _posts(image_prompt="A forearm at rest entering the frame beside a cold cup of coffee.")
    assert "dedos de más" not in " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True)))


def test_avisa_cuando_la_direccion_de_arte_inventa_paleta():
    # La paleta es identidad de marca y la pone la app: dos paletas en el mismo
    # prompt es exactamente lo que el paso 6 vino a arreglar.
    p = _posts(image_style="Warm oat and faded denim palette, low window light, 50mm, fine grain.")
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True), "image_style"))
    assert "nombra colores" in msg and "denim" in msg


def test_avisa_cuando_no_hay_direccion_de_arte():
    msg = " ".join(_msgs(pl.revisar(_posts(image_style=""), n_info=3, is_carousel=True), "image_style"))
    assert "acabado genérico" in msg


def test_avisa_cuando_falta_copy_de_los_slides():
    p = _posts(image_text={"hook": "Ahorrar sin sufrir", "slides": ["Idea uno"]})
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True), "image_slides"))
    assert "1 frase(s) para 3" in msg


def test_avisa_cuando_no_hay_escena_de_portada():
    msg = " ".join(_msgs(pl.revisar(_posts(image_prompt=""), n_info=3, is_carousel=True)))
    assert "TÍTULO" in msg


# ── Lo que NO tiene que avisar (o el aviso deja de valer nada) ────────────────


def test_un_carrusel_bien_escrito_no_produce_avisos():
    assert pl.revisar(_posts(), n_info=3, is_carousel=True) == []


def test_escenas_del_mismo_mundo_visual_no_son_repetidas():
    # Se les PIDE compartir ambiente y materiales; eso no es repetir la imagen.
    p = _posts(
        image_prompt="A chipped ceramic jar of coins on a kitchen windowsill, morning light.",
        image_slide_prompts=[
            "A stack of coins beside a folded grocery receipt on the same windowsill.",
            "An empty ceramic mug drying upside down on a wooden rack in the same kitchen.",
            "A pencil-marked notebook page held flat by a spoon on the kitchen counter.",
        ],
    )
    assert _msgs(pl.revisar(p, n_info=3, is_carousel=True)) == []


def test_la_imagen_unica_no_pide_escenas_de_slides():
    p = _posts(image_slide_prompts=[], image_text={"hook": "Ahorrar sin sufrir", "slides": []})
    assert pl.revisar(p, n_info=1, is_carousel=False) == []


def test_un_job_de_video_no_revisa_imagenes():
    p = {"video_storyboard": ["Un shot.", "Otro shot distinto del anterior."],
         "video_voiceover": ["Una línea.", "Otra línea."]}
    assert pl.revisar(p, quiere_imagenes=False, quiere_video=True) == []


# ── Video ────────────────────────────────────────────────────────────────────


def test_avisa_cuando_la_voz_no_calza_con_los_shots():
    # Con conteos distintos el reel sale mudo y sin subtítulos.
    p = {"video_storyboard": ["Un shot.", "Otro shot.", "Un tercero."],
         "video_voiceover": ["Una línea.", "Otra línea."]}
    msg = " ".join(_msgs(pl.revisar(p, quiere_imagenes=False, quiere_video=True)))
    assert "2 línea(s)" in msg and "3 shot(s)" in msg


def test_avisa_de_shots_repetidos_en_el_storyboard():
    p = {"video_storyboard": [
        "A kettle boiling on a gas stove, steam rising against the window.",
        "A kettle boiling on the gas stove with steam rising against a window.",
    ]}
    msg = " ".join(_msgs(pl.revisar(p, quiere_imagenes=False, quiere_video=True)))
    assert "casi la misma" in msg


# ── Nunca puede tapar la pantalla ────────────────────────────────────────────


def test_con_datos_rotos_devuelve_lista_vacia_sin_reventar():
    for basura in ({}, {"image_prompt": None, "image_slide_prompts": "no soy lista"},
                   {"image_text": "tampoco soy dict"}):
        assert isinstance(pl.revisar(basura, n_info=3, is_carousel=True), list)


# ── Estructura del copy ──────────────────────────────────────────────────────
# Lo que delata un post de IA no es el vocabulario sino la FORMA: gancho, lista de
# viñetas, pregunta de cierre, hashtags. El prompt del sistema ofrece un catálogo
# de estructuras para escapar de ahí; esto avisa cuando volvió a caer en la
# plantilla. La lista NO está prohibida (hay contenido que enumera de verdad), así
# que el aviso solo salta cuando TODOS los captions tienen esa forma.

_GENERICO = """Ahorrar es más fácil de lo que parece.

→ Automatiza la transferencia
→ Revisa las suscripciones
→ Cocina en casa dos noches

¿Cuál vas a probar esta semana?

#ahorro #finanzas"""

_PROSA = """La primera vez que revisó sus suscripciones encontró once activas y usaba dos.

Canceló nueve esa misma tarde. No cambió de trabajo ni renegoció el alquiler: dejó
de pagar por cosas que ya no abría desde hacía meses.

Eso son 47 euros al mes que antes se iban sin que nadie los echara de menos.

#ahorro #finanzas"""


def test_avisa_cuando_todos_los_captions_son_la_plantilla_generica():
    p = _posts(linkedin_text=_GENERICO, instagram_text=_GENERICO)
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True), "copy"))
    assert "esqueleto genérico" in msg


def test_no_avisa_cuando_la_lista_convive_con_otra_estructura():
    # Una lista puede ser la forma correcta para ESE contenido: solo es síntoma
    # cuando es la forma de todos los posts del job.
    p = _posts(linkedin_text=_GENERICO, instagram_text=_PROSA)
    assert _msgs(pl.revisar(p, n_info=3, is_carousel=True), "copy") == []


def test_no_avisa_de_un_post_en_prosa():
    otro = ("Nadie cancela una suscripción que no recuerda haber contratado.\n\n"
            "Por eso el truco no es ahorrar más: es mirar el extracto una vez al mes.\n\n"
            "#ahorro #finanzas")
    p = _posts(linkedin_text=_PROSA, facebook_text=otro)
    assert _msgs(pl.revisar(p, n_info=3, is_carousel=True), "copy") == []


def test_avisa_cuando_dos_redes_abren_con_la_misma_frase():
    apertura = "Once suscripciones activas y usaba dos de ellas."
    p = _posts(linkedin_text=f"{apertura}\n\nY así durante meses.",
               facebook_text=f"{apertura}\n\nLo contaba ayer en el canal.")
    msg = " ".join(_msgs(pl.revisar(p, n_info=3, is_carousel=True), "copy"))
    assert "abren con la misma frase" in msg
    assert "LinkedIn y Facebook" in msg


def test_sin_captions_el_lint_de_copy_no_dice_nada():
    assert _msgs(pl.revisar(_posts(), n_info=3, is_carousel=True), "copy") == []


# ── Red de seguridad: la continuidad del set y la identidad activa ───────────
#
# Estos dos no miran lo que escribió el LLM sino lo que va a HACER la app con ello.
# Existen porque los dos defectos que cubren son silenciosos por naturaleza: la
# continuidad de set se cayó entera durante meses sin un solo error en el log, y una
# identidad guardada sigue generando mal para siempre porque `validar` no corre al
# leerla.

import prompt_architect as pa                       # noqa: E402
import visual_identity as vi                        # noqa: E402


def test_un_carrusel_correcto_no_avisa_de_la_continuidad():
    assert _msgs(pl.revisar(_posts(), n_info=3, is_carousel=True),
                 "image_slide_prompts") == []


def test_si_la_clausula_de_set_deja_de_emitirse_el_lint_lo_canta(monkeypatch):
    # El canario de la regresión de la fase 1, simulada: si `_clausula_set` volviera a
    # comparar contra el literal "contenido", ningún beat la recibiría.
    monkeypatch.setattr(pa, "_clausula_set", lambda norm: "")
    avisos = _msgs(pl.revisar(_posts(), n_info=3, is_carousel=True), "image_slide_prompts")
    assert any("continuidad de set" in m for m in avisos)


def test_sin_escena_de_portada_no_se_avisa_dos_veces():
    # Ya hay un aviso de "sin escena de portada": el de continuidad sería su eco.
    avisos = _msgs(pl.revisar(_posts(image_prompt=""), n_info=3, is_carousel=True),
                   "image_slide_prompts")
    assert not any("continuidad de set" in m for m in avisos)


# ── Identidad activa ─────────────────────────────────────────────────────────

_IDENTIDAD_OK = {
    "paleta": ["#0B0C0E", "#EDEAE0", "#C9F227"],
    "paleta_nombres": ["near-black", "bone white", "acid lime"],
    "color_texto": "bone white (#EDEAE0)",
    "color_acento": "acid lime (#C9F227)",
    "tipografia": "ultra-condensed heavy display grotesque, ALL CAPS, tight tracking",
    "tipografia_secundaria": "same face, bold, tracking opened",
    "tono_visual": "cinematic poster still, one spotlit subject",
    "aspect_ratio": "4:5",
    "referencias": ["film-poster art direction"],
}


def _con_identidad(identidad):
    return _msgs(pl.revisar(_posts(), n_info=3, is_carousel=True, identidad=identidad),
                 "identidad")


def test_sin_identidad_no_hay_aviso_de_identidad():
    assert _con_identidad(None) == [] and _con_identidad({}) == []


def test_una_identidad_sana_no_genera_ruido():
    assert _con_identidad(_IDENTIDAD_OK) == []
    assert _con_identidad(vi.identidad_system()) == []


def test_un_ritmo_con_personas_se_avisa_antes_de_gastar_creditos():
    ident = {**_IDENTIDAD_OK,
             "ritmo_carrusel": ["Tight shot of a person at the bench.", "", "", ""]}
    avisos = _con_identidad(ident)
    assert avisos and "person" in avisos[0]
    assert "/cuenta" in avisos[0]


def test_una_tipografia_de_interfaz_se_avisa():
    avisos = _con_identidad({**_IDENTIDAD_OK, "tipografia": "Helvetica bold, ALL CAPS"})
    assert avisos and "helvetica" in avisos[0].lower()


def test_un_reparo_que_no_afecta_a_la_imagen_no_se_repite_aqui():
    # De una paleta rota ya se queja el editor de identidades: repetirlo en otra
    # pantalla es ruido, y el ruido entrena a ignorar los avisos.
    assert _con_identidad({**_IDENTIDAD_OK, "paleta": ["#000000"]}) == []


def test_el_lint_no_avisa_de_la_identidad_cuando_el_job_no_lleva_imagenes():
    ident = {**_IDENTIDAD_OK, "tipografia": "Inter, medium"}
    assert _msgs(pl.revisar(_posts(), quiere_imagenes=False, quiere_video=True,
                            identidad=ident), "identidad") == []
