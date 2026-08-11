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
    # En la caja que declara la identidad: `brand.json` pide ALL CAPS, así que el
    # bloque se cita ya en mayúsculas (ver `pide_caja_alta`). Lo que no puede cambiar
    # es ni un carácter del texto.
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO}), cfg=sin_llm)
    assert f'HEADLINE "{_CORTO.upper()}"' in r.prompt
    assert r.bloques == [_CORTO.upper()]
    assert r.bloques_por_clave == {"titular": _CORTO.upper()}


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


def _escala_pct(prompt: str) -> int:
    """El primer porcentaje de alto que declara la sección 5 (el cuerpo del titular)."""
    return int(re.search(r"(\d+)-\d+% of frame height", _tipografia(prompt)).group(1))


def test_el_rol_del_slide_cambia_la_pieza_y_la_escala(sin_llm):
    portada = pa.construir(_spec(contenido={"rol_slide": "portada"}), cfg=sin_llm).prompt
    contenido = pa.construir(_spec(contenido={"rol_slide": "contenido"}), cfg=sin_llm).prompt
    assert "cover" in portada and "content slide" in contenido
    # El esqueleto (banda alta para el titular, banda baja para la segunda línea) es
    # COMPARTIDO: es lo que hace que el set se lea como un sistema.
    assert "upper band" in portada and "upper band" in contenido
    # Lo que cambia es la JERARQUÍA dentro de ese esqueleto, y va en este sentido: una
    # portada engancha con una imagen, un slide de contenido transmite y lo que
    # transmite es el texto. Se comprueba como propiedad y no contra cifras fijas
    # porque el defecto que blinda es exactamente ese —durante mucho tiempo el slide
    # tuvo el titular MÁS PEQUEÑO del set— y volvería a colarse sin un solo error.
    assert _escala_pct(contenido) > _escala_pct(portada)
    assert "TYPE-LED" in contenido and "TYPE-LED" not in portada


def test_el_kicker_se_ancla_a_la_banda_baja(sin_llm):
    # Debajo del titular leería como caption; anclado al pie es un lockup de póster.
    r = pa.construir(_spec(), cfg=sin_llm)
    assert len(r.bloques) == 2
    assert "SECOND LINE" in r.prompt
    assert "flush left in the bottom band" in r.prompt


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
    assert "HEADLINE" in r.prompt and "SECOND LINE" in r.prompt


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
    assert "% of frame height" in tipo
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
    # El acento va en la misma caja que el titular, o no se encontraría dentro de él.
    assert '"CAMBIA"' in tipo
    assert "One word only" not in tipo            # ya no elige el modelo


def test_las_marcas_de_acento_no_llegan_al_prompt(sin_llm):
    r = pa.construir(_spec(contenido={
        "texto_exacto_a_renderizar": "El factor Q **cambia** el ancho"}), cfg=sin_llm)
    assert "**" not in r.prompt
    assert "EL FACTOR Q CAMBIA EL ANCHO" in r.prompt
    # Y el validador sigue conforme: los bloques que compara son los ya limpios.
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_sin_marcas_se_conserva_la_eleccion_automatica(sin_llm):
    tipo = [l for l in pa.construir(_spec(), cfg=sin_llm).prompt.splitlines()
            if l.startswith("5.")][0]
    assert "One word only" in tipo


# ── Caja y cortes de línea (flag IMAGE_LINE_BREAKS) ──────────────────────────
#
# Los dos defectos que ataca: un slide en caja baja cuando la identidad dice `all
# caps` (el brief se contradecía: pedía una caja en la sección 5 y citaba la contraria
# en la 4), y la viuda tipográfica —"EN" solo en la tercera línea, al 14% del alto—.


def _texto_seccion(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("4.")][0]


@pytest.mark.parametrize("familia, alta", [
    ("ultra-condensed heavy display grotesque, ALL CAPS, tight tracking", True),
    ("wide slab serif in caps", True),
    ("humanist sans, sentence case, open tracking", False),
    ("", False),
])
def test_pide_caja_alta_lee_la_familia_de_la_identidad(familia, alta):
    assert pa.pide_caja_alta(familia) is alta


def test_con_caja_alta_el_texto_se_cita_en_mayusculas(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO}), cfg=sin_llm)
    assert f'"{_CORTO.upper()}"' in r.prompt
    # Y el validador sigue conforme: compara contra los bloques ya transformados.
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_sin_caja_alta_el_texto_se_cita_como_viene(sin_llm):
    r = pa.construir(_spec(contenido={"texto_exacto_a_renderizar": _CORTO},
                           marca={"tipografia": "humanist sans, sentence case"}), cfg=sin_llm)
    assert f'"{_CORTO}"' in r.prompt
    assert r.bloques == [_CORTO]


def test_la_seccion_4_prohibe_cambiar_la_caja(sin_llm):
    assert "never change the case of a word" in _texto_seccion(
        pa.construir(_spec(), cfg=sin_llm).prompt)


# ── Cortes de línea ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("titular", [
    "LA IA YA NO ES UN LUJO EN",          # el caso auditado: "EN" quedaba sola
    "TODO CAMBIA SI LO MIDES DE",
    "UN MODELO ESCAPO DE PRUEBAS EN",
])
def test_la_ultima_linea_nunca_es_una_viuda(titular):
    lineas = pa.lineas_titular(titular)
    ultima = lineas[-1].split()
    assert not (len(ultima) == 1 and len(ultima[0]) <= pa._VIUDA_MAX), lineas


@pytest.mark.parametrize("max_lineas", [2, 3])
def test_nunca_se_pasa_del_maximo_de_lineas(max_lineas):
    largo = "UN ECUALIZADOR CORRIGE EL EQUILIBRIO DE CADA SONIDO GRABADO"
    assert len(pa.lineas_titular(largo, max_lineas=max_lineas)) <= max_lineas


def test_las_lineas_conservan_el_titular_entero_y_en_orden():
    titular = "CONTROLA TU CATALOGO EN ALTISIMA CALIDAD"
    assert " ".join(pa.lineas_titular(titular)) == titular


def test_el_reparto_es_equilibrado_y_no_greedy():
    # Greedy llena la primera línea y deja el resto colgando: "CHINA ENTRA / EN LA /
    # LIGA". La partición equilibrada es la que produce un titular de póster.
    assert pa.lineas_titular("CHINA ENTRA EN LA LIGA") == ["CHINA ENTRA", "EN LA LIGA"]


def test_el_corte_prefiere_caer_detras_de_una_pausa():
    # El reparto perfectamente equilibrado sería "NO ES MAGIA, ES / PURA INGENIERIA"
    # (15/15), que parte el sintagma. La pausa gana por poco, que es lo que se quiere.
    assert pa.lineas_titular("NO ES MAGIA, ES PURA INGENIERIA") == \
        ["NO ES MAGIA,", "ES PURA INGENIERIA"]


def test_pero_la_pausa_no_puede_imponer_un_reparto_desigual():
    # A cuerpo de póster, una línea del doble que la otra se nota más que el sintagma
    # partido: la pausa inclina el reparto, no lo decide.
    assert pa.lineas_titular("SIN DATOS, TODO ES UNA OPINION") == \
        ["SIN DATOS, TODO", "ES UNA OPINION"]


def test_un_titular_de_una_palabra_no_se_parte():
    assert pa.lineas_titular("IRREVERSIBLE") == ["IRREVERSIBLE"]
    assert pa.lineas_titular("") == []


def test_los_cortes_llegan_a_la_seccion_4_y_se_marcan_como_instruccion(sin_llm):
    seccion = _texto_seccion(pa.construir(_spec(), cfg=sin_llm).prompt)
    assert "Break the headline over exactly these lines" in seccion
    # Sin esta frase el modelo imprime "line 1" DENTRO de la imagen.
    assert "never printed" in seccion


def test_con_el_flag_apagado_no_se_dictan_cortes():
    class _Sin(_Cfg):
        image_line_breaks = False

    r = pa.construir(_spec(), cfg=_Sin(con_llm=False))
    assert "Break the headline" not in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


# ── La identidad no puede escribir LAYOUT en la capa dura ────────────────────
# La sección 5 pega cuatro campos de la identidad verbatim. Si esos strings traen
# vocabulario de layout, la identidad está escribiendo layout sin permiso: es lo que
# fabricó el letterbox del carrusel auditado ("headline band" + "over the dark field").


def test_tinta_deja_solo_el_nombre_y_el_hex():
    assert pa.tinta("Soft bone white (#F5F3EE) for all caps headline over the dark field.") \
        == "Soft bone white (#F5F3EE)"
    assert pa.tinta("bone white #EDEAE0 on the near-black") == "bone white #EDEAE0"


def test_tinta_sin_hex_no_rompe_nada():
    # `brand.json` no pasa por el validador de identidades: degradar, nunca romper.
    assert pa.tinta("warm off-white") == "warm off-white"
    assert pa.tinta("") == ""


@pytest.mark.parametrize("palabra", ["band", "panel", "frame", "matte", "letterbox",
                                     "backdrop", "field"])
def test_sin_layout_quita_el_sintagma_y_conserva_el_resto(palabra):
    valor = f"condensed grotesque, ALL CAPS, tight tracking in a headline {palabra}"
    limpio = pa.sin_layout(valor)
    assert palabra not in limpio
    assert "condensed grotesque" in limpio and "ALL CAPS" in limpio


def test_sin_layout_devuelve_el_original_si_lo_dejaria_vacio():
    # Quedarse sin familia tipográfica es peor que arrastrar la palabra.
    assert pa.sin_layout("headline band") == "headline band"


def test_el_dark_field_de_la_identidad_no_llega_a_la_tipografia(sin_llm):
    r = pa.construir(_spec(marca={
        "color_texto": "Soft bone white (#F5F3EE) for all caps body over the dark field",
        "tipografia": "grotesque-inspired sans, ALL CAPS, set inside a headline band",
    }), cfg=sin_llm)
    tipo = _tipografia(r.prompt)
    assert "#F5F3EE" in tipo                      # la tinta sí, entera
    assert "dark field" not in tipo               # el fondo no: lo declaran 1, 3 y 6
    assert "headline band" not in tipo
    assert "grotesque-inspired sans" in tipo      # y la familia se conserva


@pytest.mark.parametrize("rol", ("portada",) + pa.ROLES_BEAT)
def test_la_seccion_1_declara_el_sangrado_en_positivo(sin_llm, rol):
    # El negativo por sí solo se demostró insuficiente dos veces; la sección 1 es la
    # más autoritativa del brief.
    r = pa.construir(_spec(contenido={"rol_slide": rol}), cfg=sin_llm)
    pieza = [l for l in r.prompt.splitlines() if l.startswith("1.")][0]
    assert "bleeds past all four edges" in pieza


def test_el_refuerzo_de_sangrado_entra_en_la_seccion_1(sin_llm):
    r = pa.construir(_spec(), cfg=sin_llm, refuerzo_sangrado=True)
    pieza = [l for l in r.prompt.splitlines() if l.startswith("1.")][0]
    assert "FLAT COLOUR BAND" in pieza
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_sin_refuerzo_de_sangrado_no_aparece(sin_llm):
    assert "FLAT COLOUR BAND" not in pa.construir(_spec(), cfg=sin_llm).prompt


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


# ── Sistemas de texto: cuántos niveles imprime un slide ─────────────────────
# Un slide solo sabía imprimir un titular, así que un carrusel de cuatro tenía ~56
# palabras para contar un video entero: con ese presupuesto no se narra, solo se titula.


def _texto_sec(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("4.")][0]


def test_el_sistema_dicta_que_bloques_se_imprimen(sin_llm):
    r = pa.construir(_spec(
        contenido={"rol_slide": "desarrollo",
                   "bloques": {"titular": "El coste sube",
                               "cuerpo": "Cada salto de contexto multiplica el gasto por token."}},
    ) | {"sistema_texto": "titular_cuerpo"}, cfg=sin_llm)
    texto = _texto_sec(r.prompt)
    assert 'HEADLINE "EL COSTE SUBE"' in texto
    assert 'BODY "Cada salto de contexto multiplica el gasto por token."' in texto
    assert r.bloques_por_clave["cuerpo"].startswith("Cada salto")
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_el_cuerpo_no_se_pasa_a_caja_alta(sin_llm):
    """`pide_caja_alta` mira la familia de DISPLAY, que es la del titular.

    Un párrafo de 30 palabras al 5% del alto en caja alta es ilegible, así que la caja
    alta es de los bloques de display y el cuerpo conserva la que se escribió.
    """
    r = pa.construir(_spec(
        contenido={"rol_slide": "prueba",
                   "bloques": {"titular": "El coste sube", "cuerpo": "Cada salto lo multiplica."}},
    ) | {"sistema_texto": "titular_cuerpo"}, cfg=sin_llm)
    assert r.bloques_por_clave["titular"] == "EL COSTE SUBE"
    assert r.bloques_por_clave["cuerpo"] == "Cada salto lo multiplica."


def test_la_portada_siempre_lleva_el_lockup_de_siempre(sin_llm):
    """La portada es la pieza que ya funcionaba y la que funda el set.

    Se fuerza en `normalizar_spec`, un único sitio, para que ningún camino pueda
    pedirle otra cosa por mucho que el job haya congelado un sistema de tres bloques.
    """
    r = pa.construir(_spec(contenido={
        "rol_slide": "portada",
        "bloques": {"etiqueta": "01", "titular": "El coste sube", "cuerpo": "Y sigue subiendo."},
    }) | {"sistema_texto": "etiqueta_titular_cuerpo"}, cfg=sin_llm)
    assert r.sistema_texto == "titular"
    assert "LABEL" not in r.prompt and "BODY" not in r.prompt


def test_un_texto_suelto_se_reparte_como_siempre(sin_llm):
    """«Vacío = lo de siempre»: un job anterior a los sistemas se reparte como se repartía."""
    r = pa.construir(_spec(contenido={"rol_slide": "remate"}), cfg=sin_llm)
    assert list(r.bloques_por_clave) == ["titular", "apoyo"]
    titular, kicker = pa.dividir_texto(_TEXTO)
    assert r.bloques_por_clave == {"titular": titular.upper(), "apoyo": kicker.upper()}


def test_un_texto_suelto_llena_el_cuerpo_del_sistema_que_lo_tiene(sin_llm):
    # Lo escrito a mano en la compuerta previa tiene que caber en cualquier sistema.
    r = pa.construir(_spec(contenido={"rol_slide": "remate"}) | {"sistema_texto": "titular_cuerpo"},
                     cfg=sin_llm)
    assert list(r.bloques_por_clave) == ["titular", "cuerpo"]


def test_un_bloque_vacio_deja_su_hueco_y_no_corre_al_siguiente(sin_llm):
    r = pa.construir(_spec(
        contenido={"rol_slide": "prueba",
                   "bloques": {"etiqueta": "", "titular": "El coste sube", "cuerpo": "Y sigue."}},
    ) | {"sistema_texto": "etiqueta_titular_cuerpo"}, cfg=sin_llm)
    assert "LABEL" not in r.prompt                       # no se emite el que falta
    assert 'HEADLINE "EL COSTE SUBE"' in r.prompt        # …ni el siguiente ocupa su sitio
    assert any("etiqueta" in a for a in r.avisos)


def test_el_repertorio_congela_uno_y_es_reproducible():
    # Mismo patrón que el arco y el mundo: se elige UNA vez por job y se congela, o dos
    # imágenes del mismo carrusel saldrían con estructuras de texto distintas.
    ident = {"sistemas_texto": ["titular", "etiqueta_titular_cuerpo"]}
    assert pa.elegir_sistema("job-1", ident) == pa.elegir_sistema("job-1", ident)
    elegidos = {pa.elegir_sistema(f"job-{i}", ident) for i in range(40)}
    assert elegidos == {"titular", "etiqueta_titular_cuerpo"}


def test_sin_repertorio_se_cae_de_fuente_en_fuente():
    # identidad → brand.json → architect.json, la misma cadena que `escenarios_de`.
    assert pa.sistemas_de({"sistemas_texto": ["titular_cuerpo"]}) == ["titular_cuerpo"]
    assert pa.sistemas_de({}) == list(prompt_config.brand()["sistemas_texto"])
    # Un nombre que no existe en el catálogo no puede colarse al prompt.
    assert pa.sistemas_de({"sistemas_texto": ["inventado"]}) != ["inventado"]


def test_los_sistemas_con_cuerpo_no_dictan_cortes_de_linea(sin_llm):
    """La viuda que esa cláusula corrige es de titular largo a tamaño de póster.

    Con cuerpo el titular baja a 6 palabras sobre 1-2 líneas, así que pagar sus ~140
    caracteres fijos es justo lo que hay que recortar antes de tocar el techo.
    """
    con = pa.construir(_spec(contenido={"rol_slide": "remate"}), cfg=sin_llm).prompt
    sin = pa.construir(_spec(contenido={"rol_slide": "remate"}) | {"sistema_texto": "titular_cuerpo"},
                       cfg=sin_llm).prompt
    assert "Break the headline over exactly these lines" in con
    assert "Break the headline over exactly these lines" not in sin


# ── El acento no puede cambiar entre piezas del mismo carrusel ───────────────
# Defecto reportado: un carrusel con el acento de un color distinto en cada slide.
# Tres causas independientes, y las tres tienen su test acá porque arreglar una sola
# deja el defecto en pie.


def test_el_bloqueo_de_paleta_es_identico_en_todas_las_piezas(sin_llm):
    """Causa 1: la sección 6 la escribía el LLM por pieza, con la paleta redactada N veces."""
    bloqueos = set()
    for rol in ("portada",) + pa.ROLES_BEAT:
        r = pa.construir(_spec(contenido={
            "rol_slide": rol,
            "escena_portada": "" if rol == "portada" else "a dark mixing desk",
        }), cfg=sin_llm)
        luz = _luz(r.prompt)
        i = luz.find("PALETTE LOCK")
        assert i >= 0, f"el beat {rol} se quedó sin bloqueo de paleta"
        bloqueos.add(luz[i:i + luz[i:].find(".") + 1])
    assert len(bloqueos) == 1
    assert prompt_config.brand()["paleta"][2] in bloqueos.pop()   # el acento, con su hex


def test_el_respaldo_de_luz_ya_no_redacta_la_paleta(sin_llm):
    """La paleta se declara UNA vez. Que el respaldo la repitiera es el mismo defecto."""
    luz = _luz(pa.construir(_spec(), cfg=sin_llm).prompt)
    assert luz.count("#C9F227") == 1
    assert "palette held to" not in luz


def test_el_beat_que_calla_el_acento_lo_PROHIBE_en_vez_de_omitirlo(sin_llm):
    """Causa 2: el silencio no es una prohibición.

    Mientras la tensión se limitaba a no emitir la cláusula, el modelo pintaba igual
    una palabra y elegía el color por su cuenta — que es justo el acento a la deriva.
    """
    tipo = [l for l in pa.construir(_spec(contenido={"rol_slide": "tension"}),
                                    cfg=sin_llm).prompt.splitlines() if l.startswith("5.")][0]
    assert "One word only" not in tipo                 # sigue sin elegirlo el modelo
    assert "no second colour" in tipo                  # …pero ahora se dice
    assert prompt_config.brand()["paleta"][1] in tipo  # y se nombra el color único


def test_el_acento_marcado_a_mano_se_cita_con_su_tinta(sin_llm):
    """Causa 3: la rama explícita pegaba el color crudo y la automática lo reducía.

    Dos formulaciones del mismo color son dos colores para el modelo, así que el acento
    salía distinto según lo hubiera elegido el usuario o el modelo.
    """
    r = pa.construir(_spec(
        contenido={"texto_exacto_a_renderizar": "El factor Q **cambia** el ancho"},
        marca={"color_acento": "acid lime (#C9F227) painted over the dark field"},
    ), cfg=sin_llm)
    tipo = [l for l in r.prompt.splitlines() if l.startswith("5.")][0]
    assert "acid lime (#C9F227)" in tipo
    assert "over the dark field" not in tipo


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
    assert "DIFFERENT" in comp            # la mitad que cambia: otro objeto y otro encuadre


def test_la_continuidad_ya_no_cita_la_escena_de_la_portada(sin_llm):
    """El mundo compartido lo declara el bloqueo de mundo, no una cita de la portada.

    Citarla era re-derivar lo invariante del set a partir de UNA pieza —una copia, no
    un ancla— y era además la única parte variable de la cláusula, con su propio tope
    de palabras. Cuando el mundo se declara idéntico en todas las piezas, repetir la
    portada dentro de cada slide solo gasta presupuesto.
    """
    r = pa.construir(_spec(contenido={
        "rol_slide": "contenido",
        "escena_portada": "A studio equalizer plugin screen on a dark mixing desk",
    }, escenario="A workshop floor, concrete and steel racking."), cfg=sin_llm)
    assert "mixing desk" not in r.prompt
    assert "WORLD LOCK" in r.prompt and "concrete and steel racking" in r.prompt


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


def test_un_slide_sin_escena_de_portada_sigue_declarando_la_continuidad(sin_llm):
    """La continuidad dejó de depender de que hubiera escena de portada.

    Mientras la cláusula la citaba, un slide sin ella se quedaba sin continuidad Y sin
    la instrucción de cambiar de objeto: el hueco exacto por el que el modelo repetía
    la portada por su cuenta. Ahora lo compartido se declara siempre.
    """
    r = pa.construir(_spec(contenido={"rol_slide": "contenido"}), cfg=sin_llm)
    assert "SET CONTINUITY" in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_una_escena_de_portada_enorme_ya_no_llega_al_prompt(sin_llm):
    # Antes entraba recortada y pagaba su recorte; ahora no entra, así que no puede
    # desbordar el techo ni comerse el presupuesto del slide.
    largo = " ".join(["a very long cover scene clause"] * 40)
    r = pa.construir(_spec(contenido={"rol_slide": "contenido", "escena_portada": largo}),
                     cfg=sin_llm)
    assert "very long cover scene" not in r.prompt
    assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == []


def test_los_negativos_prohiben_los_rotulos_dentro_de_la_escena(sin_llm):
    # El pseudo-texto en pantallas y perillas es lo que más delata una imagen
    # generada; el "no words beyond the quoted string" genérico no lo frenaba.
    negs = [l for l in pa.construir(_spec(), cfg=sin_llm).prompt.splitlines()
            if l.startswith("9.")][0].lower()
    assert "screens" in negs and "no readable words" in negs


# ── Atrezzo, física y contexto cultural ──────────────────────────────────────


def _negativos(prompt: str) -> str:
    return [l for l in prompt.splitlines() if l.startswith("9.")][0]


def test_el_negativo_de_moneda_aparece_en_espanol_y_no_en_ingles(sin_llm):
    # El atrezzo por defecto de estos modelos es estadounidense: en un post sobre
    # España salían billetes de dólar. En un post en inglés ese atrezzo puede ser el
    # correcto, así que el negativo NO se pone.
    es = pa.construir(_spec(contenido={"idioma": "es"}), cfg=sin_llm).prompt
    en = pa.construir(_spec(contenido={"idioma": "en"}), cfg=sin_llm).prompt
    assert "US currency" in _negativos(es)
    assert "US currency" not in _negativos(en)


@pytest.mark.parametrize("codigo, nombre", [("es", "Spanish"), ("en", "English")])
def test_el_contexto_cultural_llega_al_briefing(codigo, nombre):
    # El idioma llegaba solo a la sección de texto (para las tildes), nunca al brief
    # del sujeto: por eso los props salían con el default del modelo.
    mensaje = pa._mensaje_arquitecto(pa.normalizar_spec(_spec(contenido={"idioma": codigo})))
    assert "CULTURAL CONTEXT" in mensaje
    assert nombre in mensaje


def test_la_instruccion_prohibe_las_superficies_rotulables():
    # Quitar la superficie funciona donde prohibir el texto no: cada carátula, pantalla
    # o etiqueta es un sitio más donde el generador escribe pseudo-texto.
    instruccion = (prompt_config.architect().get("llm") or {}).get("instruccion", "")
    assert "At most 2 secondary objects" in instruccion
    assert "cluttered" in instruccion


def test_la_instruccion_exige_plausibilidad_fisica():
    # El disco flotando sin bandeja y el objeto sin sombra de contacto del set auditado.
    instruccion = (prompt_config.architect().get("llm") or {}).get("instruccion", "")
    assert "PHYSICAL PLAUSIBILITY" in instruccion
    assert "nothing floats" in instruccion


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
    assert r.bloques == ["ECUALIZA EL BAJO", "TODO CAMBIA"]
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
    escalas = {rol: _escala_pct(pa.construir(_spec(contenido={"rol_slide": rol}),
                                             cfg=sin_llm).prompt)
               for rol in ("portada", "tension", "desarrollo", "remate")}
    # La tensión aprieta y el remate cierra: los dos suben sobre el tamaño de lectura.
    assert escalas["tension"] > escalas["desarrollo"]
    assert escalas["remate"] > escalas["desarrollo"]
    # Y TODO beat va por encima de la portada: en el slide manda el texto.
    for rol in ("tension", "desarrollo", "remate"):
        assert escalas[rol] > escalas["portada"], rol


def test_el_slide_subordina_el_sujeto_al_tipo(sin_llm):
    """La otra mitad de la inversión: la escala sola no basta.

    El modelo cumple el porcentaje del titular y aun así fotografía un objeto que se
    lleva más cuadro, así que la sección 3 tiene que decirlo. Se comprueba en el beat
    porque es el camino real de un carrusel — la portada NO lo lleva: ahí el sujeto
    sigue siendo el asunto de la pieza.
    """
    slide = pa.construir(_spec(contenido={"rol_slide": "desarrollo"}), cfg=sin_llm).prompt
    portada = pa.construir(_spec(contenido={"rol_slide": "portada"}), cfg=sin_llm).prompt
    assert "subordinate" in _composicion(slide)
    assert "anchor the subject in the central band" in _composicion(portada)
    assert "subordinate" not in _composicion(portada)


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
    assert '"CAMBIA"' in _tipografia(r.prompt)


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
    assert "Reading distance" in comp           # el respaldo de architect.json


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


# ── El mundo y el arco: los dos ejes que el job congela ──────────────────────
#
# Corrigen los dos defectos que se veían de un vistazo en las piezas generadas: que
# todas eran un objeto sobre una mesa —con identidades distintas— y que las N imágenes
# de un carrusel no contaban nada juntas.

_MUNDO = "A workshop after hours: concrete floor, steel racking, tools left where they were used."


def _mundo(prompt: str) -> str:
    """Solo el WORLD LOCK: lo que la app prefija a la sección 2."""
    sujeto = [l for l in prompt.splitlines() if l.startswith("2.")][0]
    i = sujeto.find("WORLD LOCK")
    return sujeto[i:] if i >= 0 else ""


def test_el_bloqueo_de_mundo_es_identico_en_todas_las_piezas(sin_llm):
    """La aserción central: byte a byte en la portada y en los cuatro beats.

    Es lo mismo que se exige del bloqueo de luz y por lo mismo — un invariante del set
    que se recalculara por pieza no sería un invariante—, pero acá además es lo que
    hace que las piezas compartan LUGAR, que era lo que ninguna otra cláusula decía.
    """
    bloqueos = set()
    for rol in ("portada",) + pa.ROLES_BEAT:
        r = pa.construir(_spec(contenido={"rol_slide": rol}, escenario=_MUNDO), cfg=sin_llm)
        bloqueos.add(_mundo(r.prompt))
    assert len(bloqueos) == 1
    assert bloqueos.pop().startswith("WORLD LOCK")


def test_el_bloqueo_de_mundo_va_prefijado_y_la_poda_no_lo_toca(sin_llm):
    # Va antes de la sección creativa a propósito: dentro de ella, `_ajustar_longitud`
    # se lo comería justo en el caso peor, que es cuando más falta hace.
    r = pa.construir(_spec(escenario=_MUNDO), cfg=sin_llm)
    sujeto = [l for l in r.prompt.splitlines() if l.startswith("2.")][0]
    assert sujeto.index("WORLD LOCK") < sujeto.index(_BASE.split()[1])


def test_sin_escenario_el_prompt_sale_como_antes(sin_llm):
    # «Vacío significa lo de siempre»: un job anterior a esta versión no puede cambiar.
    assert "WORLD LOCK" not in pa.construir(_spec(), cfg=sin_llm).prompt


@pytest.mark.parametrize("arco", pa.ARCOS)
def test_el_enlace_del_arco_se_emite_en_los_slides(sin_llm, arco):
    # El parametrizado va sobre `pa.ARCOS` y no sobre una lista escrita a mano: un arco
    # nuevo sin cláusula no puede escaparse en silencio (es como se escapó la
    # continuidad de set durante meses).
    r = pa.construir(_spec(contenido={"rol_slide": "desarrollo"}, arco_carrusel=arco),
                     cfg=sin_llm)
    assert "SET ARC" in _composicion(r.prompt)


@pytest.mark.parametrize("arco", pa.ARCOS)
def test_la_portada_no_lleva_arco(sin_llm, arco):
    # La portada no continúa nada: funda el set, igual que con la continuidad.
    assert "SET ARC" not in pa.construir(_spec(arco_carrusel=arco), cfg=sin_llm).prompt


def test_sin_arco_el_slide_conserva_la_instruccion_de_objeto_distinto(sin_llm):
    """El respaldo que evita que «vacío = lo de siempre» signifique «peor que antes».

    Antes de los arcos, «a DIFFERENT hero object» era una constante dentro de la
    continuidad de set y era lo único que impedía que el modelo repitiera el objeto de
    la portada. Al salir de ahí, un job sin arco se habría quedado sin ella.
    """
    comp = _composicion(pa.construir(_spec(contenido={"rol_slide": "prueba"}), cfg=sin_llm).prompt)
    assert "DIFFERENT hero object" in comp


@pytest.mark.parametrize("arco", pa.ARCOS)
def test_ningun_arco_habla_de_distancia_ni_de_encuadre(arco):
    """La frontera dura entre el arco y el beat: el arco dice QUÉ, el beat dice CÓMO.

    Un `enlace` que nombrara una distancia o un encuadre chocaría con la cláusula de
    plano del beat, que va pegada a él en la misma sección — y ante dos instrucciones
    de cámara contradictorias el modelo elige una. Es la lección del paso 12, aplicada
    a la capa nueva.
    """
    enlace = pa._clausula_arco({"contenido": {"rol_slide": "desarrollo"},
                                "arco_carrusel": arco}).lower()
    prohibidas = ("close-up", "macro", "wide shot", "mid-distance", "overhead",
                  "framing", "shot distance", "camera height", "zoom")
    assert [p for p in prohibidas if p in enlace] == []


def test_la_eleccion_del_arco_y_del_mundo_es_reproducible():
    """No puede depender de `hash()`: está aleatorizado por proceso.

    Si lo fuera, un reinicio del servidor le daría otro arco al mismo job — y rehacer
    un slide desde la revisión tiene que reconstruir el MISMO prompt.
    """
    assert pa.elegir_arco("job-abc") == pa.elegir_arco("job-abc")
    assert pa.elegir_escenario("job-abc") == pa.elegir_escenario("job-abc")


def test_el_arco_y_el_mundo_no_salen_emparejados():
    """Con la misma semilla y cuatro de cada uno quedarían casados uno a uno.

    El taller saldría siempre con transformación y el carrusel perdería la mitad de su
    variedad sin que nada fallara.
    """
    pares = {(pa.elegir_arco(f"job-{i}"), pa.elegir_escenario(f"job-{i}")) for i in range(60)}
    arcos = {a for a, _ in pares}
    assert len(pares) > len(arcos)


def test_la_eleccion_recorre_todos_los_arcos_y_todos_los_mundos():
    # Si la rotación se quedara en un subconjunto, «no siempre lo mismo» sería mentira.
    semillas = [f"job-{i}" for i in range(200)]
    assert {pa.elegir_arco(s) for s in semillas} == set(pa.arcos_disponibles())
    assert {pa.elegir_escenario(s) for s in semillas} == set(pa.escenarios_de())


def test_el_repertorio_de_mundos_sale_de_la_identidad_antes_que_de_la_casa():
    propios = ["A flooded quarry at dawn.", "A tiled municipal pool, empty."]
    assert pa.escenarios_de({"escenarios": propios}) == propios
    assert pa.elegir_escenario("cualquiera", {"escenarios": propios}) in propios


def test_el_peor_caso_con_mundo_y_arco_cabe_en_el_presupuesto(sin_llm):
    """El caso peor de verdad tras esta fase: beat + continuidad + ARCO + MUNDO + kicker.

    Mismo motivo que el test hermano de más arriba: pasarse del techo no degrada la
    imagen, la deja sin brief y sin bloque de texto.
    """
    for arco in pa.ARCOS:
        for rol in pa.ROLES_BEAT:
            r = pa.construir(_spec(
                contenido={"rol_slide": rol,
                           "texto_exacto_a_renderizar": "China entra en la liga alta de la IA global"},
                marca={"tono_visual": "y" * 240},
                ritmo_carrusel=["x" * 160] * 4,
                arco_carrusel=arco,
                escenario=" ".join(["weather-beaten"] * 22),
            ), cfg=sin_llm)
            assert pa.validar(r.prompt, bloques=r.bloques, aspect_ratio="4:5") == [], (arco, rol)


# ── La guardia contra el carrusel de mesas ───────────────────────────────────

# El defecto reportado: todas las piezas eran «un objeto sobre una mesa», con
# identidades visuales distintas. No lo elegía el LLM — estaba escrito en la capa dura,
# en seis sitios a la vez, y por eso ninguna identidad podía cambiarlo. Estos textos son
# los que la app emite SIEMPRE, así que basta con que uno vuelva a nombrar una mesa para
# que el default vuelva. El bodegón sigue siendo legítimo: vive en los repertorios de
# mundos, que es donde el usuario puede elegirlo o no.
_MESAS = ("table", "tabletop", "desk", "worktop", "countertop")


def _nombra_mesa(texto: str) -> list[str]:
    bajo = str(texto or "").lower()
    return [m for m in _MESAS if re.search(rf"\b{m}\b", bajo)]


def test_ningun_respaldo_determinista_nombra_una_mesa():
    """Los planos por beat y los respaldos creativos: lo que sale cuando no hay nada más.

    Es el camino que nadie mira —el degradado— y por eso es donde el default sobrevive.
    """
    arch = prompt_config.architect()
    sospechosos: dict[str, str] = {}
    for beat, cfg_rol in (arch.get("roles") or {}).items():
        for clave in ("ritmo", "composicion"):
            sospechosos[f"roles.{beat}.{clave}"] = cfg_rol.get(clave, "")
    for clave, valor in (arch.get("respaldos") or {}).items():
        sospechosos[f"respaldos.{clave}"] = valor if isinstance(valor, str) else ""
    for clave, valor in (pa._RITMO_FALLBACK | pa._COMPOSICION_BEAT_FALLBACK).items():
        sospechosos[f"código.{clave}"] = valor
    malos = {k: _nombra_mesa(v) for k, v in sospechosos.items() if _nombra_mesa(v)}
    assert malos == {}, f"vuelve el default de la mesa en: {malos}"


def test_el_ritmo_de_la_casa_no_nombra_una_mesa():
    # `ritmo_carrusel` es distancia y altura de cámara; el LUGAR es de `escenarios`.
    # Mientras decía «still life on a bare table» nombraba el mundo sin permiso, y lo
    # nombraba igual para todos los posts de la casa.
    malos = [r for r in (prompt_config.brand().get("ritmo_carrusel") or []) if _nombra_mesa(r)]
    assert malos == []


def test_el_repertorio_de_mundos_no_es_todo_mesas():
    """Ni el de la casa ni el compartido. Que UNO lo sea es la decisión correcta.

    El usuario lo dijo así: una imagen de un objeto en una mesa no está mal — lo que
    está mal es que sea siempre el formato. Con un mundo de mesa entre varios, sale a
    veces; con todos, vuelve el defecto entero.
    """
    for nombre, repertorio in (("brand.json", prompt_config.brand().get("escenarios") or []),
                               ("architect.json", pa.escenarios_de())):
        assert len(repertorio) >= 2, nombre
        con_mesa = [e for e in repertorio if _nombra_mesa(e)]
        assert len(con_mesa) < len(repertorio), f"{nombre}: todos los mundos son una mesa"
