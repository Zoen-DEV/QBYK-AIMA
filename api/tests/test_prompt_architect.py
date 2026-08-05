"""Tests del PromptArchitect: construcción, validador y pase de auto-crítica.

El módulo tiene un contrato duro: pase lo que pase con el LLM, lo que sale es un
prompt de NUEVE secciones en orden, con el texto exacto entrecomillado y una zona
de aire negativo declarada — o una excepción. Estos tests cubren el caso feliz y
todos los caminos de rechazo/degradación.
"""

import re

import pytest

import prompt_architect as pa
import prompt_config

_TEXTO = "China entra en la liga alta de la IA"
_CORTO = "Modelos abiertos"
_BASE = "A chipped ceramic jar of coins on a kitchen windowsill."


class _Cfg:
    """Config mínima: solo lo que mira `llm_json.disponible`."""

    def __init__(self, con_llm: bool = True):
        self.anthropic_api_key = "test-key" if con_llm else ""
        self.perplexity_api_key = ""


def _spec(**over) -> dict:
    contenido = {"tema": "IA en China", "angulo": "competencia global",
                 "texto_exacto_a_renderizar": _TEXTO, "rol_slide": "portada"}
    contenido.update(over.pop("contenido", {}))
    return {"contenido": contenido, "marca": over.pop("marca", {}),
            "prompt_base": over.pop("prompt_base", _BASE), **over}


@pytest.fixture
def sin_llm():
    """Camino determinista: sin keys, el arquitecto no llama a nadie."""
    return _Cfg(con_llm=False)


# ── Caso feliz ────────────────────────────────────────────────────────────────

def test_el_prompt_trae_las_nueve_secciones_en_orden(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    lineas = r.prompt.splitlines()
    assert len(lineas) == 9
    for i, sec in enumerate(pa._secciones_cfg(), 1):
        assert lineas[i - 1].startswith(f"{i}. {sec['etiqueta']}:")


def test_el_prompt_es_valido_de_punta_a_punta(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_el_texto_viaja_literal_y_entrecomillado(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO}), cfg=sin_llm)
    assert f'render this exact text: "{_CORTO}"' in r.prompt
    assert r.bloques == [_CORTO]


def test_declara_idioma_acentos_y_posicion(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": "Así se hace"}), cfg=sin_llm)
    texto_seccion = [l for l in r.prompt.splitlines() if l.startswith("4.")][0]
    assert "Spanish" in texto_seccion
    assert "accent" in texto_seccion                      # se pide conservar las tildes
    assert "upper band" in texto_seccion                  # posición
    assert "flush left" in texto_seccion                  # alineación


def test_declara_el_area_segura_cuantificada(sin_llm):
    # El defecto que se veía en producción: titulares cortados por el borde. La v1
    # decía "from the safe margin" sin cuantificarlo; ahora el margen es explícito y
    # se prohíbe que un glifo toque el borde.
    r = pa.construir(_spec(), cfg=sin_llm)
    texto_seccion = [l for l in r.prompt.splitlines() if l.startswith("4.")][0]
    assert "8% safe margin" in texto_seccion
    assert "cut by a frame edge" in texto_seccion
    # …y en los negativos, que es lo que el modelo lee como prohibición.
    assert "cropped" in [l for l in r.prompt.splitlines() if l.startswith("9.")][0]


def test_el_validador_exige_el_area_segura(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    # El validador compara en minúsculas, así que la manipulación también (el prompt
    # trae el marcador en caja alta: "SAFE AREA:").
    sin_area = re.sub(r"safe (margin|area)", "zone", r.prompt, flags=re.I)
    errores = pa.validar(sin_area, bloques=r.bloques, aspect_ratio="4:5")
    assert any("área segura" in e for e in errores)


def test_el_rol_del_slide_cambia_la_pieza_y_la_escala(sin_llm):
    portada = pa.construir(_spec(contenido={"rol_slide": "portada"}), cfg=sin_llm).prompt
    contenido = pa.construir(_spec(contenido={"rol_slide": "contenido"}), cfg=sin_llm).prompt
    assert "cover" in portada and "content slide" in contenido
    # El esqueleto (banda alta para el titular, banda baja para la segunda línea) es
    # COMPARTIDO —es lo que hace que el set se lea como un sistema— y lo que baja un
    # escalón en los slides es la escala del titular.
    assert "upper band" in portada and "upper band" in contenido
    assert "13-16% of the frame height" in portada
    assert "9-12% of the frame height" in contenido


def test_el_kicker_se_ancla_a_la_banda_baja(sin_llm):
    # Debajo del titular leería como caption; anclado al pie es un lockup de póster.
    r = pa.construir(_spec(), cfg=sin_llm)
    assert len(r.bloques) == 2
    assert "The second line locks into the bottom band" in r.prompt


def test_un_texto_de_un_bloque_no_declara_segunda_linea(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO}), cfg=sin_llm)
    assert "second line" not in r.prompt.lower()


def test_la_direccion_de_arte_del_post_entra_como_tono_visual(sin_llm):
    estilo = "Warm oat and faded denim palette, low window light, 50mm, fine grain."
    r = pa.construir(_spec(marca={"tono_visual": estilo}), cfg=sin_llm)
    assert estilo in r.prompt


def test_el_aspecto_pedido_se_declara_en_la_pieza(sin_llm):
    r = pa.construir(_spec(marca={"aspect_ratio": "9:16"}), cfg=sin_llm)
    assert "9:16" in r.prompt.splitlines()[0]
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="9:16") == []


# ── Sangrado: el aire es foto, no un panel de color ───────────────────────────
#
# En producción el mismo carrusel salía con unos slides a sangre y otros con un
# passe-partout de color liso (hueso, un color de la paleta). La causa era pedir las
# bandas "flat": para un modelo de imagen eso es un rectángulo de color plano, no una
# zona tranquila de la escena. La palabra solo puede aparecer PROHIBIDA.

@pytest.mark.parametrize("rol", ["portada", "contenido"])
def test_el_prompt_declara_sangrado_y_no_pide_bandas_planas(sin_llm, rol):
    r = pa.construir(_spec(contenido={"rol_slide": rol}), cfg=sin_llm)
    assert "full bleed" in r.prompt.lower()
    # Se sigue reservando el aire, pero nombrando de qué está hecho.
    assert "clear zone" in r.prompt.lower() or "negative space" in r.prompt.lower()
    for m in re.finditer(r"\bflat\b", r.prompt, flags=re.I):
        previo = r.prompt[max(0, m.start() - 60):m.start()].lower()
        assert "never" in previo or "no " in previo, (
            f'"flat" aparece como petición, no como prohibición: '
            f"...{r.prompt[max(0, m.start() - 60):m.start() + 30]}..."
        )


def test_el_rubric_no_premia_bandas_planas():
    """El rubric es el otro lado del arreglo: mientras premiara bandas 'flat', la
    auto-crítica reescribía el prompt hasta volver a pedirlas."""
    criterios = prompt_config.rubric().get("criterios") or []
    integracion = next(c for c in criterios if c.get("clave") == "integracion_tipo_imagen")
    desc = integracion["descripcion"].lower()
    assert "full bleed" in desc
    assert "genuinely flat" not in desc


# ── Texto largo: titular + subtítulo ──────────────────────────────────────────

def test_un_texto_corto_no_se_divide():
    assert pa.dividir_texto("Cuatro palabras justas aquí") == ("Cuatro palabras justas aquí", "")


def test_un_texto_largo_se_divide_en_titular_y_kicker():
    titular, kicker = pa.dividir_texto(_TEXTO)
    assert titular and kicker
    assert len(titular.split()) <= 8
    assert f"{titular} {kicker}" == _TEXTO          # no se pierde ni una palabra


def test_los_dos_bloques_llegan_al_prompt(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    assert len(r.bloques) == 2
    assert all(f'"{b}"' in r.prompt for b in r.bloques)
    assert "headline" in r.prompt and "second line" in r.prompt


def test_el_corte_prefiere_caer_tras_una_pausa():
    # 11 palabras: el corte teórico caería a mitad de sintagma ("...costes de entrenar y
    # sube"), pero hay una coma cerca y ahí es donde debe partir.
    titular, kicker = pa.dividir_texto("Bajan los costes de entrenar, y sube la competencia entre modelos")
    assert titular == "Bajan los costes de entrenar"
    assert kicker == "y sube la competencia entre modelos"


# ── Casos de rechazo ──────────────────────────────────────────────────────────

def test_sin_texto_es_error_de_validacion(sin_llm):
    for vacio in ("", "   ", None):
        with pytest.raises(pa.PromptInvalido):
            pa.construir(_spec(contenido={"texto_exacto_a_renderizar": vacio}), cfg=sin_llm)


def test_el_validador_rechaza_un_prompt_sin_el_texto_literal(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO}), cfg=sin_llm)
    manipulado = r.prompt.replace(_CORTO, "Otra cosa")
    errores = pa.validar(manipulado, texto=_CORTO, aspect_ratio="4:5")
    assert any("texto literal" in e for e in errores)


def test_el_validador_rechaza_si_falta_una_seccion(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    sin_camara = "\n".join(l for l in r.prompt.splitlines() if not l.startswith("8."))
    errores = pa.validar(sin_camara, bloques=r.bloques, aspect_ratio="4:5")
    assert any("falta la sección 8" in e for e in errores)


def test_el_validador_rechaza_una_seccion_vacia(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    vacia = "\n".join(l.split(":")[0] + ":" if l.startswith("2.") else l
                      for l in r.prompt.splitlines())
    errores = pa.validar(vacia, bloques=r.bloques, aspect_ratio="4:5")
    assert any("vacía" in e for e in errores)


def test_el_validador_rechaza_si_no_se_declara_el_aspecto(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    errores = pa.validar(r.prompt.replace("4:5", ""), bloques=r.bloques, aspect_ratio="4:5")
    assert any("aspect ratio" in e for e in errores)


def test_el_validador_rechaza_si_no_hay_zona_de_aire_negativo(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm)
    sin_aire = r.prompt.replace("negative space", "stuff").replace("clear zone", "area")
    sin_aire = sin_aire.replace("Reserve", "Fill").replace("reserved", "used")
    errores = pa.validar(sin_aire, bloques=r.bloques, aspect_ratio="4:5")
    assert any("aire negativo" in e for e in errores)


def test_el_validador_rechaza_por_longitud(sin_llm):
    assert any("corto" in e for e in pa.validar("1. PIECE & FORMAT: x", texto=_CORTO))
    largo = pa.construir(_spec(), cfg=sin_llm).prompt + " palabra" * 400
    assert any("largo" in e for e in pa.validar(largo, texto=_TEXTO, aspect_ratio="4:5"))


def test_los_adjetivos_vacios_se_detectan():
    assert pa.adjetivos_vacios("A beautiful and modern scene") == ["beautiful", "modern"]
    assert pa.adjetivos_vacios("A worn oak desk under low window light") == []


# ── Camino con LLM ────────────────────────────────────────────────────────────

_SECCIONES_LLM = {
    "sujeto": "Three stacked server blades from a decommissioned Shenzhen rack, dust on the vents, "
              "resting on a steel workbench with a visible contact shadow.",
    "composicion": "Blades anchored in the central band, cabling receding into depth on the right.",
    "luz": "Low raking light from the left at dusk, palette held to #0B0C0E and #C9F227.",
    "estilo": "Film-poster still, industrial reportage, hard-light product photography.",
    "camara": "35mm, f/2.8, shallow focus, fine grain, no digital sharpening.",
}


def _llm_fijo(respuestas, registro=None):
    """Devuelve un `complete_json` simulado que va sirviendo `respuestas` en orden."""
    pendientes = list(respuestas)

    def _fake(system, user, *, cfg, max_tokens=0):
        if registro is not None:
            registro.append(user)
        data = pendientes.pop(0) if pendientes else {}
        return data, {"service": "anthropic", "model": "claude-sonnet-4-6",
                      "units": {"input_tokens": 10, "output_tokens": 20}}

    return _fake


def test_el_llm_escribe_las_secciones_creativas(monkeypatch):
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([_SECCIONES_LLM]))
    r = pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    assert r.fuente == "llm"
    assert "Shenzhen" in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []
    assert r.usos and r.usos[0]["service"] == "anthropic"


def test_la_tipografia_la_escribe_la_app_y_no_el_llm(monkeypatch):
    # Cuando la escribía el modelo, cada post inventaba su fuente y su escala: sin
    # identidad entre posts. Aunque el LLM la devuelva, se ignora.
    intruso = dict(_SECCIONES_LLM,
                   tipografia="Playful rounded sans, light weight, wide tracking, pastel lilac.")
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([intruso]))
    r = pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    tipo = [l for l in r.prompt.splitlines() if l.startswith("5.")][0]
    assert "rounded sans" not in tipo and "lilac" not in tipo
    assert "condensed" in tipo.lower()
    assert "tipografia" not in pa._CLAVES_CREATIVAS


def test_la_tipografia_declara_escala_color_y_un_solo_acento(sin_llm):
    # La escala es lo que separa un póster de un pie de foto, y el acento tiene que
    # ser UN span: sin el límite el modelo pinta media frase de color.
    tipo = [l for l in pa.construir(_spec(), cfg=sin_llm).prompt.splitlines()
            if l.startswith("5.")][0]
    assert "% of the frame height" in tipo
    assert "#EDEAE0" in tipo                     # color del titular (marca)
    assert "#C9F227" in tipo                     # acento (marca)
    assert "One word only" in tipo


def test_sin_color_de_acento_no_se_pide_acento(sin_llm):
    r = pa.construir(_spec(marca={"color_acento": " "}), cfg=sin_llm)
    tipo = [l for l in r.prompt.splitlines() if l.startswith("5.")][0]
    assert "One word only" not in tipo
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_el_bloque_de_texto_nunca_lo_escribe_el_llm(monkeypatch):
    # Aunque el modelo cuele el texto en una sección creativa, se limpia: si no,
    # el render sacaría el titular dos veces.
    sucias = dict(_SECCIONES_LLM, sujeto=f'Server blades with "{_TEXTO}" painted on them.')
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([sucias]))
    r = pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    sujeto = [l for l in r.prompt.splitlines() if l.startswith("2.")][0]
    assert _TEXTO not in sujeto


def test_si_el_llm_falla_se_usa_el_respaldo_y_no_se_lanza(monkeypatch):
    def _explota(*a, **kw):
        raise RuntimeError("503")

    monkeypatch.setattr(pa.llm_json, "complete_json", _explota)
    r = pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    assert r.fuente == "respaldo"
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []
    assert any("sin LLM" in a for a in r.avisos)


def test_una_respuesta_inutil_del_llm_cae_al_respaldo(monkeypatch):
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([{"otra_cosa": 1}]))
    r = pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    assert r.fuente == "respaldo"
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


# ── Auto-crítica ──────────────────────────────────────────────────────────────

_PUNTAJES_OK = {"especificidad_sujeto": 5, "claridad_composicion": 5,
                "integracion_tipo_imagen": 5, "coherencia_marca": 4, "ausencia_genericidad": 4}
_PUNTAJES_BAJOS = dict(_PUNTAJES_OK, especificidad_sujeto=2)


def test_si_el_rubric_aprueba_no_se_reescribe(monkeypatch):
    monkeypatch.setattr(pa.llm_json, "complete_json",
                        _llm_fijo([_SECCIONES_LLM, {"puntajes": _PUNTAJES_OK}]))
    r = pa.construir(_spec(), cfg=_Cfg())
    assert r.iteraciones == 0
    assert r.puntajes == {k: float(v) for k, v in _PUNTAJES_OK.items()}


def test_un_criterio_bajo_dispara_una_reescritura(monkeypatch):
    mejor = dict(_SECCIONES_LLM, sujeto="A single liquid-cooled GPU tray on a Hefei factory pallet, "
                                        "condensation beading on the copper plate, resting on scuffed concrete.")
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([
        _SECCIONES_LLM,
        {"puntajes": _PUNTAJES_BAJOS, "secciones": mejor},
        {"puntajes": _PUNTAJES_OK},
    ]))
    r = pa.construir(_spec(), cfg=_Cfg())
    assert r.iteraciones == 1
    assert "Hefei" in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_la_autocritica_para_en_el_maximo_de_iteraciones(monkeypatch):
    # El crítico nunca queda contento: aun así, dos vueltas y se corta.
    variante = dict(_SECCIONES_LLM, sujeto="A copper busbar on a factory pallet, scuffed and cold.")
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo(
        [_SECCIONES_LLM] + [{"puntajes": _PUNTAJES_BAJOS, "secciones": variante}] * 5
    ))
    r = pa.construir(_spec(), cfg=_Cfg())
    assert r.iteraciones <= pa._entero(pa.prompt_config.rubric(), "max_iteraciones", 2)


def test_la_reescritura_conserva_el_texto_exacto(monkeypatch):
    # El crítico devuelve secciones que ignoran el texto: da igual, la sección 4 la
    # vuelve a poner la app palabra por palabra.
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([
        _SECCIONES_LLM,
        {"puntajes": _PUNTAJES_BAJOS, "secciones": dict(_SECCIONES_LLM, sujeto="Another rack, closer.")},
        {"puntajes": _PUNTAJES_OK},
    ]))
    r = pa.construir(_spec(), cfg=_Cfg())
    assert all(f'"{b}"' in r.prompt for b in r.bloques)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_la_autocritica_se_puede_apagar(monkeypatch):
    registro: list[str] = []
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([_SECCIONES_LLM], registro))
    pa.construir(_spec(), cfg=_Cfg(), autocritica=False)
    assert len(registro) == 1          # solo la llamada del arquitecto


# ── Refuerzo del bloque de texto (lo usa el reintento del QA) ─────────────────

def test_el_refuerzo_agrega_la_instruccion_dura(sin_llm):
    normal = pa.construir(_spec(), cfg=sin_llm).prompt
    reforzado = pa.construir(_spec(), cfg=sin_llm, refuerzo_texto=True).prompt
    assert "PRIMARY REQUIREMENT" not in normal
    assert "PRIMARY REQUIREMENT" in reforzado


# ── Acento elegido a mano (**así**) ───────────────────────────────────────────
# Es la única palanca del usuario sobre la jerarquía del titular. Los asteriscos
# son notación: se quitan antes de que el texto llegue al prompt, así que no
# pueden acabar impresos en la imagen.

def test_separar_acento_extrae_el_span_y_limpia_las_marcas():
    assert pa.separar_acento("Ecualizar **cambia** toda la mezcla") == (
        "Ecualizar cambia toda la mezcla", "cambia")


def test_separar_acento_acepta_una_frase_y_normaliza_espacios():
    limpio, acento = pa.separar_acento("El factor Q **cambia el ancho**  de banda")
    assert limpio == "El factor Q cambia el ancho de banda"
    assert acento == "cambia el ancho"


def test_separar_acento_sin_marcas_no_toca_nada():
    assert pa.separar_acento("Una voz puede sonar delgada") == (
        "Una voz puede sonar delgada", "")


def test_separar_acento_se_queda_con_el_primer_span():
    # Varios spans en color son confeti: se acentúa uno y los demás pierden las marcas.
    limpio, acento = pa.separar_acento("**Uno** y **dos**")
    assert limpio == "Uno y dos"
    assert acento == "Uno"


def test_separar_acento_limpia_asteriscos_sueltos():
    # Un `**` impar es un tecleo a medias, no algo que deba imprimirse.
    assert pa.separar_acento("Texto **a medias") == ("Texto a medias", "")


def test_el_acento_marcado_manda_sobre_la_eleccion_automatica(sin_llm):
    r = pa.construir(_spec(contenido={
        "texto_exacto_a_renderizar": "El factor Q **cambia** el ancho"}), cfg=sin_llm)
    tipo = [l for l in r.prompt.splitlines() if l.startswith("5.")][0]
    assert '"cambia"' in tipo
    assert "One word only" not in tipo            # ya no elige el modelo


def test_las_marcas_de_acento_no_llegan_al_prompt(sin_llm):
    r = pa.construir(_spec(contenido={
        "texto_exacto_a_renderizar": "El factor Q **cambia** el ancho"}), cfg=sin_llm)
    assert "**" not in r.prompt
    assert "El factor Q cambia el ancho" in r.prompt
    # Y el validador sigue conforme: los bloques que compara son los ya limpios.
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_sin_marcas_se_conserva_la_eleccion_automatica(sin_llm):
    tipo = [l for l in pa.construir(_spec(), cfg=sin_llm).prompt.splitlines()
            if l.startswith("5.")][0]
    assert "One word only" in tipo


# ── Bloqueo de luz: lo mismo en las N piezas del job ─────────────────────────
# La sección 6 la escribía entera el LLM, una vez por imagen y sin conocer a sus
# hermanas: en un carrusel eso son N esquemas de iluminación distintos, y en ningún
# punto del pipeline aparecía una temperatura de color. El esquema de iluminación es
# lo que hace que N fotos parezcan del mismo día, así que lo escribe la app.


def _luz(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("6.")][0]


def _bloqueo(prompt: str) -> str:
    """Solo el LIGHT LOCK: lo que la app escribe, sin el detalle de escena del LLM."""
    luz = _luz(prompt)
    i = luz.find("LIGHT LOCK")
    return luz[i:] if i >= 0 else ""


def test_el_bloqueo_de_luz_es_identico_en_todas_las_piezas(sin_llm):
    """La aserción central de la fase: byte a byte, portada y los cuatro beats."""
    bloqueos = set()
    for rol in ("portada",) + pa.ROLES_BEAT:
        r = pa.construir(_spec(contenido={
            "rol_slide": rol,
            "escena_portada": "" if rol == "portada" else "a dark mixing desk",
        }), cfg=sin_llm)
        bloqueos.add(_bloqueo(r.prompt))
    assert len(bloqueos) == 1
    assert bloqueos.pop().startswith("LIGHT LOCK")


def test_el_bloqueo_declara_temperatura_y_el_fondo_de_la_paleta(sin_llm):
    # La temperatura es el parámetro que frena la deriva cálida y es app-owned; el
    # fondo es el primer color de la paleta, que es contra el que muere la luz.
    bloqueo = _bloqueo(pa.construir(_spec(), cfg=sin_llm).prompt)
    assert "5400K" in bloqueo
    assert prompt_config.brand()["paleta"][0] in bloqueo


def test_la_luz_sale_de_la_identidad_y_no_del_estilo_del_post(sin_llm):
    """`image_style` manda en el tratamiento (sección 7) pero NO en la luz.

    Es la mitad delicada de la decisión: si la luz saliera del `image_style` que el
    LLM escribe por post, volvería a cambiar en cada pieza — que es el defecto.
    """
    r = pa.construir(_spec(marca={
        "tono_visual": "harsh midday sun through a warm window",   # lo pisa image_style
        "luz_identidad": "single cold key from camera left, deep falloff",
    }), cfg=sin_llm)
    bloqueo = _bloqueo(r.prompt)
    assert "single cold key from camera left" in bloqueo
    assert "harsh midday sun" not in bloqueo


def test_sin_identidad_la_luz_sale_de_brand_json(sin_llm):
    # No-regresión: un job sin identidad tiene que generar exactamente como antes.
    bloqueo = _bloqueo(pa.construir(_spec(), cfg=sin_llm).prompt)
    assert prompt_config.brand()["tono_visual"].split(":")[0] in bloqueo


def test_la_autocritica_no_duplica_el_bloqueo_de_luz(monkeypatch):
    # El bloqueo va PREFIJADO: si la reescritura no lo separase del texto del LLM,
    # cada iteración dejaría un LIGHT LOCK más en la sección 6.
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([
        {"sujeto": "A cracked bench vise on a steel table.",
         "luz": "One key raking across the jaws.", "composicion": "Centred.",
         "estilo": "Industrial reportage.", "camara": "50mm, f/4."},
        {"puntajes": {"anclaje": 2}, "secciones": {"luz": "Key catches the worn jaws."}},
        {"puntajes": {"anclaje": 5}},
    ]))
    r = pa.construir(_spec(), cfg=_Cfg())
    assert _luz(r.prompt).count("LIGHT LOCK") == 1


# ── Continuidad del set (reemplaza al image-to-image) ────────────────────────

def _composicion(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("3.")][0]


def test_el_slide_declara_el_set_compartido_y_el_objeto_distinto(sin_llm):
    r = pa.construir(_spec(contenido={
        "rol_slide": "contenido",
        "escena_portada": "A studio equalizer plugin screen on a dark mixing desk",
    }), cfg=sin_llm)
    comp = _composicion(r.prompt)
    assert "SET CONTINUITY" in comp
    assert "mixing desk" in comp          # el ancla: mismo mundo
    assert "DIFFERENT" in comp            # y la otra mitad: otro objeto y otro encuadre


@pytest.mark.parametrize("rol", pa.ROLES_BEAT)
def test_todo_beat_declara_la_continuidad_del_set(sin_llm, rol):
    """El fallo que se coló al introducir la escalera de beats.

    `_clausula_set` comparaba el rol contra el literal `"contenido"`, y desde la
    escalera los slides llegan con el nombre de su beat: la cláusula dejó de emitirse
    en TODOS los slides de carrusel, sin un solo error. Con ella se fue lo único que
    declaraba el mundo compartido — de ahí los carruseles con cinco localizaciones y,
    a la vez, el objeto de la portada repetido en tres piezas.

    El parametrizado va sobre `pa.ROLES_BEAT` y no sobre una lista escrita a mano: un
    beat nuevo no puede escaparse en silencio, que es exactamente como se escapó este.
    """
    r = pa.construir(_spec(contenido={
        "rol_slide": rol,
        "escena_portada": "A studio equalizer plugin screen on a dark mixing desk",
    }), cfg=sin_llm)
    comp = _composicion(r.prompt)
    assert "SET CONTINUITY" in comp
    assert "DIFFERENT hero object" in comp


@pytest.mark.parametrize("rol", pa.ROLES_BEAT)
def test_todo_beat_le_cuenta_al_arquitecto_que_la_portada_ya_esta_rodada(rol):
    # La misma comparación, en el briefing del LLM: sin esta línea el modelo toma la
    # escena de la portada como el sujeto a describir y el slide sale siendo la misma
    # foto contada de nuevo.
    norm = pa.normalizar_spec(_spec(contenido={
        "rol_slide": rol,
        "escena_portada": "A studio equalizer plugin screen on a dark mixing desk",
    }))
    assert "CAROUSEL COVER ALREADY SHOT" in pa._mensaje_arquitecto(norm)


def test_la_portada_no_lleva_clausula_de_continuidad(sin_llm):
    # La portada no continúa nada: es la que funda el set.
    r = pa.construir(_spec(contenido={"escena_portada": "A jar of coins"}), cfg=sin_llm)
    assert "SET CONTINUITY" not in r.prompt


def test_un_slide_sin_escena_de_portada_sigue_siendo_valido(sin_llm):
    r = pa.construir(_spec(contenido={"rol_slide": "contenido"}), cfg=sin_llm)
    assert "SET CONTINUITY" not in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_la_continuidad_no_desborda_el_limite_de_caracteres(sin_llm):
    # La escena de la portada entra recortada: es un ancla, no un segundo brief.
    largo = " ".join(["a very long cover scene clause"] * 40)
    r = pa.construir(_spec(contenido={"rol_slide": "contenido", "escena_portada": largo}),
                     cfg=sin_llm)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_los_negativos_prohiben_los_rotulos_dentro_de_la_escena(sin_llm):
    # El pseudo-texto en pantallas y perillas es lo que más delata una imagen
    # generada; el "no words beyond the quoted string" genérico no lo frenaba.
    negs = [l for l in pa.construir(_spec(), cfg=sin_llm).prompt.splitlines()
            if l.startswith("9.")][0].lower()
    assert "screens" in negs and "no readable words" in negs


def test_el_recorte_no_deja_un_parentesis_abierto(sin_llm):
    # La poda cortaba la paleta a mitad — "acid lime (#0B0C0E." — y ese paréntesis
    # colgando viajaba al modelo como ruido.
    roto = pa._recortar("palette held to near-black, bone white, acid lime "
                        "(#0B0C0E, #EDEAE0, #C9F227) and grain", 10)
    assert roto.count("(") == roto.count(")")
    assert "#0B0C0E" not in roto


def test_la_paleta_llega_entera_al_slide(sin_llm):
    r = pa.construir(_spec(contenido={
        "rol_slide": "contenido",
        "escena_portada": "A studio equalizer plugin screen on a dark mixing desk",
    }), cfg=sin_llm)
    luz = [l for l in r.prompt.splitlines() if l.startswith("6.")][0]
    assert luz.count("(") == luz.count(")")


# ── Corte explícito titular / apoyo ───────────────────────────────────────────
# El corte por longitud es el respaldo: decide la jerarquía de la pieza contando
# palabras, que no tiene nada que ver con lo que la frase quiere decir. Quien
# escribe el texto puede marcarlo con una raya espaciada.

def test_la_raya_parte_el_texto_aunque_sea_corto():
    # Cinco palabras: por longitud no se habría partido nunca.
    assert pa.dividir_texto("Ecualiza el bajo — todo cambia") == ("Ecualiza el bajo", "todo cambia")


def test_la_raya_no_se_imprime():
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": "Ecualiza el bajo — todo cambia"}),
                     cfg=_Cfg(con_llm=False))
    assert r.bloques == ["Ecualiza el bajo", "todo cambia"]
    assert "—" not in " ".join(r.bloques)
    assert all(f'"{b}"' in r.prompt for b in r.bloques)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_la_barra_tambien_vale_como_corte():
    assert pa.dividir_texto("Menos reuniones | más foco") == ("Menos reuniones", "más foco")


def test_un_guion_pegado_no_es_un_corte():
    # Palabras compuestas y rangos: el corte pide raya ESPACIADA justamente por esto.
    assert pa.dividir_texto("Ahorra 30-40% al mes") == ("Ahorra 30-40% al mes", "")


def test_un_titular_explicito_demasiado_largo_vuelve_al_corte_por_longitud():
    # Un bloque enorme arriba rompe el póster igual que un párrafo: la marca del
    # usuario no puede saltarse el límite, solo elegir dónde cortar dentro de él.
    largo = "Una frase muy larga que no cabe de ninguna manera arriba — y su apoyo"
    titular, kicker = pa.dividir_texto(largo)
    assert len(titular.split()) <= 8
    assert "—" not in f"{titular} {kicker}"
    assert f"{titular} {kicker}".split() == largo.replace(" — ", " ").split()


def test_sin_raya_todo_sigue_igual():
    assert pa.dividir_texto(_CORTO) == (_CORTO, "")
    titular, kicker = pa.dividir_texto(_TEXTO)
    assert f"{titular} {kicker}" == _TEXTO


# ── Beats del carrusel ────────────────────────────────────────────────────────
#
# El defecto que arreglan: con un único rol `contenido`, los 2-5 slides de info
# compartían pieza, banda, escala y composición, así que entre un slide y otro solo
# cambiaba el objeto — versiones de la misma imagen. La única variación que existía
# (la escalera de encuadres) viajaba en el `prompt_base`, que el arquitecto solo le
# enseña al modelo como "BASE PROMPT (weak, to rewrite)": con el LLM disponible no
# llegaba al prompt final, mientras que el lockup —que pide siempre el mismo cuadro—
# sí llegaba siempre.

def _tipografia(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("5.")][0]


def test_la_escalera_abre_con_tension_y_cierra_con_remate():
    for n in range(2, 6):
        roles = pa.roles_carrusel(n)
        assert len(roles) == n
        assert roles[0] == "tension"
        assert roles[-1] == "remate"


def test_la_escalera_estira_el_desarrollo_en_el_medio():
    assert pa.roles_carrusel(3) == ["tension", "desarrollo", "remate"]
    assert pa.roles_carrusel(4) == ["tension", "desarrollo", "prueba", "remate"]
    assert pa.roles_carrusel(7).count("desarrollo") == 4


def test_una_tabla_de_longitud_equivocada_se_descarta_entera(monkeypatch):
    # Media escalera es peor que la escalera de siempre: se ignora y se usa la canónica.
    cfg = dict(pa._cfg_arch(), secuencia_roles={"3": ["tension", "remate"]})
    monkeypatch.setattr(pa.prompt_config, "architect", lambda: cfg)
    assert pa.roles_carrusel(3) == ["tension", "desarrollo", "remate"]


def test_cada_beat_declara_su_plano_en_la_composicion(sin_llm):
    planos = set()
    for rol in pa.ROLES_BEAT:
        comp = _composicion(pa.construir(_spec(contenido={"rol_slide": rol}), cfg=sin_llm).prompt)
        assert "SHOT —" in comp
        planos.add(comp)
    # Cuatro beats, cuatro composiciones distintas: es lo único que impide que el
    # carrusel salga como el mismo cuadro con otro objeto.
    assert len(planos) == len(pa.ROLES_BEAT)


def test_el_plano_del_beat_no_se_puede_podar(sin_llm):
    # Va en la cláusula que la app pega SIEMPRE, no en las secciones creativas: la
    # poda por longitud no puede dejar un slide sin su plano.
    largo = " ".join(["a very long cover scene clause with materials"] * 40)
    r = pa.construir(_spec(contenido={"rol_slide": "prueba", "escena_portada": largo}), cfg=sin_llm)
    assert "SHOT — EVIDENCE" in _composicion(r.prompt)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


@pytest.mark.parametrize("rol", list(pa.ROLES_BEAT))
def test_todo_beat_produce_un_prompt_valido(sin_llm, rol):
    r = pa.construir(_spec(contenido={"rol_slide": rol, "escena_portada": "A dark mixing desk"}),
                     cfg=sin_llm)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_la_escala_del_titular_cambia_con_el_beat(sin_llm):
    escalas = {rol: _tipografia(pa.construir(_spec(contenido={"rol_slide": rol}),
                                             cfg=sin_llm).prompt)
               for rol in ("tension", "desarrollo", "remate")}
    assert "11-13%" in escalas["tension"]
    assert "9-11%" in escalas["desarrollo"]
    assert "12-15%" in escalas["remate"]


def test_el_beat_de_tension_calla_el_acento(sin_llm):
    # Un acento que aparece en todos los slides deja de ser un acento.
    tension = _tipografia(pa.construir(_spec(contenido={"rol_slide": "tension"}), cfg=sin_llm).prompt)
    remate = _tipografia(pa.construir(_spec(contenido={"rol_slide": "remate"}), cfg=sin_llm).prompt)
    assert "One word only" not in tension
    assert "One word only" in remate


def test_un_acento_marcado_a_mano_manda_incluso_en_el_beat_que_lo_calla(sin_llm):
    # La marca del usuario es su única palanca sobre la jerarquía: no se la come el ritmo.
    r = pa.construir(_spec(contenido={"rol_slide": "tension",
                                      "texto_exacto_a_renderizar": "El factor Q **cambia** todo"}),
                     cfg=sin_llm)
    assert '"cambia"' in _tipografia(r.prompt)


def test_el_esqueleto_del_lockup_es_el_mismo_en_todos_los_beats(sin_llm):
    # Lo que cambia entre slides es la escala, el acento y el plano — nunca dónde se
    # apoya el tipo: unificar las bandas fue una corrección deliberada.
    for rol in pa.ROLES_BEAT:
        p = pa.construir(_spec(contenido={"rol_slide": rol}), cfg=sin_llm).prompt
        texto = [l for l in p.splitlines() if l.startswith("4.")][0]
        assert "upper band" in texto
        assert "full bleed" in p.lower()
        assert "clear zone" in p.lower()


# ── Ritmo: el beat es estructura, su ejecución es marca ───────────────────────

_RITMO = ["Extreme macro of wet slate filling the frame.",
          "Table-height still life on bare pine.",
          "Overhead of one tool on an empty field.",
          "Wide room from low, the subject tiny."]


def test_el_ritmo_de_la_identidad_entra_en_el_prompt(sin_llm):
    r = pa.construir(_spec(contenido={"rol_slide": "tension"}, ritmo_carrusel=_RITMO), cfg=sin_llm)
    assert "Extreme macro of wet slate" in _composicion(r.prompt)
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_el_ritmo_se_indexa_por_beat_y_no_por_slide(sin_llm):
    # La posición dentro de `ritmo_carrusel` ES el beat: el remate toma el cuarto
    # plano aunque sea el segundo slide de un carrusel de tres.
    r = pa.construir(_spec(contenido={"rol_slide": "remate"}, ritmo_carrusel=_RITMO), cfg=sin_llm)
    assert "Wide room from low" in _composicion(r.prompt)


def test_un_hueco_del_ritmo_cae_al_respaldo_de_ese_beat(sin_llm):
    r = pa.construir(_spec(contenido={"rol_slide": "desarrollo"},
                           ritmo_carrusel=["Extreme macro of wet slate.", ""]), cfg=sin_llm)
    comp = _composicion(r.prompt)
    assert "Extreme macro" not in comp          # no se corre el plano del beat anterior
    assert "Mid-distance still life" in comp    # el respaldo de architect.json


def test_sin_ritmo_el_slide_usa_el_de_la_casa(sin_llm):
    # "Vacío = lo de siempre": una identidad sin el campo genera como antes.
    sin = pa.construir(_spec(contenido={"rol_slide": "prueba"}), cfg=sin_llm).prompt
    assert pa.encuadre_beat("prueba") in _composicion(sin)


def test_el_arquitecto_le_dice_al_llm_su_beat_y_le_prohibe_el_encuadre(monkeypatch):
    registro: list[str] = []
    monkeypatch.setattr(pa.llm_json, "complete_json", _llm_fijo([_SECCIONES_LLM], registro))
    pa.construir(_spec(contenido={"rol_slide": "tension"}), cfg=_Cfg(), autocritica=False)
    assert "CAROUSEL BEAT: tension" in registro[0]
    assert "never the camera distance" in registro[0]


def test_la_portada_no_lleva_beat(sin_llm):
    p = pa.construir(_spec(), cfg=sin_llm).prompt
    assert "SHOT —" not in p


def test_el_peor_caso_de_un_slide_cabe_en_el_presupuesto(sin_llm):
    """El caso peor real: beat + continuidad de set + kicker + un ritmo de identidad
    del largo máximo, con todo lo creativo al tope.

    Vale la pena fijarlo porque el fallo es silencioso y caro: si el prompt se pasa
    del techo, el validador lo tira entero y `job_runner._prompt_imagen` cae al
    prompt base — la imagen sale SIN bloque de texto y sin brief.
    """
    largo = " ".join(["a very long cover scene clause with materials and light"] * 10)
    for rol in pa.ROLES_BEAT:
        r = pa.construir(_spec(
            contenido={"rol_slide": rol, "escena_portada": largo,
                       "texto_exacto_a_renderizar": "China entra en la liga alta de la IA global"},
            marca={"tono_visual": "y" * 240},
            ritmo_carrusel=["x" * 160] * 4,
        ), cfg=sin_llm)
        assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == [], rol
