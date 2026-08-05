"""Tests del extractor de identidad visual desde fotos.

Dos cosas que este archivo tiene que demostrar y no solo describir: que un número de
fotos fuera de rango **no llega a costar una llamada**, y que un JSON que no valida se
reintenta exactamente una vez con los errores del validador como feedback antes de
fallar limpio.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as api
import identity_extract as ie
import llm_json
import prompt_config
import visual_identity as vi

_IDENTIDAD = {
    "paleta": ["#101014", "#F2EFE6", "#FF5C2B"],
    "paleta_nombres": ["ink", "paper", "ember"],
    "color_texto": "paper (#F2EFE6) over the ink",
    "color_acento": "ember (#FF5C2B)",
    "tipografia": "condensed heavy grotesque, ALL CAPS, tight tracking",
    "tipografia_secundaria": "same face, bold, tracking opened",
    "tono_visual": "sunlit still life, hard shadows, deep falloff",
    "aspect_ratio": "4:5",
    "referencias": ["editorial food photography"],
}

# Un JSON que el modelo devuelve a menudo: el acento no es el tercer color de la paleta.
_ROTA = {**_IDENTIDAD, "color_acento": "hot pink (#FF00AA)"}

# Valida entera y produciría piezas de aficionado: el titular no se lee sobre su fondo.
# Es lo que sale de describir con fidelidad un set de fotos de teléfono mal iluminadas.
_FLOJA = {**_IDENTIDAD,
          "paleta": ["#8A9A8B", "#A8B4A6", "#FF5C2B"],
          "paleta_nombres": ["moss", "sage", "ember"],
          "color_texto": "sage (#A8B4A6) over the moss"}


class _Cfg:
    """`con_vision=False` es **sin ninguna key**: los dos proveedores leen imágenes, así
    que quedarse solo con la de Perplexity ya no deja al extractor sin modelo."""

    def __init__(self, con_vision: bool = True, proveedor: str = "anthropic"):
        self.anthropic_api_key = "test-key" if con_vision and proveedor == "anthropic" else ""
        self.perplexity_api_key = "test-key" if con_vision and proveedor == "perplexity" else ""


def _foto(ancho: int = 800, alto: int = 600, fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (ancho, alto), (40, 40, 48)).save(buf, format=fmt)
    return buf.getvalue()


def _fotos(n: int = 6) -> list[tuple[bytes, str]]:
    return [(_foto(), f"ref-{i}.jpg") for i in range(n)]


@pytest.fixture
def modelo(monkeypatch):
    """Doble del modelo de visión. `respuestas` se consume en orden; `mensajes` guarda
    lo que se le mandó, que es donde se comprueba el feedback del reintento."""

    class _Modelo:
        def __init__(self):
            self.respuestas: list[dict] = []
            self.mensajes: list[str] = []
            self.sistemas: list[str] = []
            self.imagenes: list[list] = []

        def __call__(self, sistema, mensaje, imagenes, *, cfg, max_tokens=1500):
            self.sistemas.append(sistema)
            self.mensajes.append(mensaje)
            self.imagenes.append(imagenes)
            data = self.respuestas.pop(0) if self.respuestas else {}
            return data, {"service": "anthropic", "model": "claude-sonnet-4-6",
                          "units": {"input_tokens": 10, "output_tokens": 5}}

        @property
        def llamadas(self) -> int:
            return len(self.mensajes)

    doble = _Modelo()
    monkeypatch.setattr(llm_json, "complete_json_vision_multi", doble)
    return doble


# ── La compuerta de las fotos (antes de gastar nada) ──────────────────────────

@pytest.mark.parametrize("n", [0, 1, 4, 11, 20])
def test_un_numero_de_fotos_fuera_de_rango_no_llama_al_modelo(n, modelo):
    with pytest.raises(ie.FotosInvalidas) as exc:
        ie.extraer(_fotos(n), cfg=_Cfg())
    assert f"entre {ie.MIN_FOTOS} y {ie.MAX_FOTOS}" in str(exc.value)
    assert f"subiste {n}" in str(exc.value)
    assert modelo.llamadas == 0


@pytest.mark.parametrize("n", [5, 6, 10])
def test_el_rango_admitido_pasa_la_compuerta(n):
    assert ie.revisar_fotos(_fotos(n)) == []


def test_un_formato_no_admitido_no_llama_al_modelo(modelo):
    fotos = _fotos(5) + [(b"%PDF-1.4 no soy una imagen", "brief.pdf")]
    with pytest.raises(ie.FotosInvalidas) as exc:
        ie.extraer(fotos, cfg=_Cfg())
    assert "brief.pdf" in str(exc.value) and ie.NOMBRES_FORMATO in str(exc.value)
    assert modelo.llamadas == 0


def test_una_foto_demasiado_pesada_se_rechaza(modelo):
    gorda = (b"\xff\xd8\xff" + b"\x00" * (ie.MAX_MB_FOTO * 1024 * 1024), "enorme.jpg")
    with pytest.raises(ie.FotosInvalidas) as exc:
        ie.extraer(_fotos(5) + [gorda], cfg=_Cfg())
    assert "enorme.jpg" in str(exc.value)
    assert modelo.llamadas == 0


def test_una_foto_vacia_se_rechaza():
    assert any("vacío" in e for e in ie.revisar_fotos(_fotos(5) + [(b"", "rota.jpg")]))


@pytest.mark.parametrize("fmt, esperado", [("JPEG", "image/jpeg"), ("PNG", "image/png"),
                                           ("WEBP", "image/webp")])
def test_los_formatos_se_detectan_por_los_bytes_no_por_la_extension(fmt, esperado):
    """El content-type y la extensión los escribe el cliente; los bytes no."""
    assert ie.tipo_imagen(_foto(fmt=fmt)) == esperado


def test_un_gif_no_es_formato_admitido():
    assert ie.tipo_imagen(b"GIF89a" + b"\x00" * 20) == ""


# ── Sin modelo de visión ──────────────────────────────────────────────────────

def test_sin_ninguna_key_falla_claro(modelo):
    with pytest.raises(ie.ExtraccionNoDisponible) as exc:
        ie.extraer(_fotos(), cfg=_Cfg(con_vision=False))
    assert "ANTHROPIC_API_KEY" in str(exc.value) and "PERPLEXITY_API_KEY" in str(exc.value)
    assert modelo.llamadas == 0


@pytest.mark.parametrize("proveedor", ["anthropic", "perplexity"])
def test_basta_con_cualquiera_de_los_dos_proveedores(modelo, proveedor):
    """La configuración por defecto del proyecto es solo-Perplexity: si el extractor
    exigiera Anthropic, la feature nacería inservible para quien la use así."""
    modelo.respuestas = [dict(_IDENTIDAD)]
    res = ie.extraer(_fotos(), cfg=_Cfg(proveedor=proveedor))
    assert vi.validar(res.identidad) == []
    assert modelo.llamadas == 1


# ── Caso feliz ────────────────────────────────────────────────────────────────

def test_extraer_devuelve_una_identidad_valida(modelo):
    modelo.respuestas = [dict(_IDENTIDAD)]
    res = ie.extraer(_fotos(6), cfg=_Cfg())
    assert vi.validar(res.identidad) == []
    assert res.identidad == vi.normalizar(_IDENTIDAD)
    assert res.intentos == 1 and res.avisos == []
    assert modelo.llamadas == 1


def test_se_manda_una_imagen_por_foto(modelo):
    modelo.respuestas = [dict(_IDENTIDAD)]
    ie.extraer(_fotos(7), cfg=_Cfg())
    assert len(modelo.imagenes[0]) == 7


def test_el_uso_se_devuelve_para_cobrarlo(modelo):
    modelo.respuestas = [dict(_IDENTIDAD)]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert res.usos[0]["service"] == "anthropic"


def test_el_modelo_solo_recibe_los_campos_del_esquema(modelo):
    """Aunque devuelva de más, lo que sale está normalizado al esquema."""
    modelo.respuestas = [{**_IDENTIDAD, "logo": "un círculo", "version": "9"}]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert list(res.identidad) == list(vi.CAMPOS)


# ── Reintento y fallo limpio ──────────────────────────────────────────────────

def test_un_json_invalido_se_reintenta_una_vez_con_el_error(modelo):
    modelo.respuestas = [dict(_ROTA), dict(_IDENTIDAD)]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert res.intentos == 2 and modelo.llamadas == 2
    assert vi.validar(res.identidad) == []
    # El segundo mensaje lleva el motivo exacto del rechazo y el JSON anterior.
    reintento = modelo.mensajes[1]
    assert "no es el tercer color de la paleta" in reintento
    assert "#FF00AA" in reintento
    assert len(res.avisos) == 1


def test_si_el_reintento_tampoco_valida_se_falla_limpio(modelo):
    modelo.respuestas = [dict(_ROTA), dict(_ROTA)]
    with pytest.raises(ie.ExtraccionInvalida) as exc:
        ie.extraer(_fotos(), cfg=_Cfg())
    # Exactamente un reintento: ni cero ni un bucle.
    assert modelo.llamadas == 2
    assert any("tercer color" in e for e in exc.value.errores)


def test_una_respuesta_vacia_tambien_se_reintenta(modelo):
    modelo.respuestas = [{}, dict(_IDENTIDAD)]
    assert ie.extraer(_fotos(), cfg=_Cfg()).intentos == 2


# ── Criterio de diseño ────────────────────────────────────────────────────────

def test_una_identidad_que_valida_pero_no_se_lee_se_reintenta(modelo):
    """El caso que motiva todo esto: el JSON está perfecto y la pieza saldría amateur."""
    modelo.respuestas = [dict(_FLOJA), dict(_IDENTIDAD)]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert res.intentos == 2 and res.identidad == vi.normalizar(_IDENTIDAD)
    assert "contrasta" in modelo.mensajes[1] and "#A8B4A6" in modelo.mensajes[1]


def test_un_reparo_de_diseno_que_sobrevive_no_tira_la_identidad(modelo):
    """Un reparo discutible no puede costar la extracción entera: vuelve como aviso,
    y el editor —donde cambiar un hex son dos segundos— ya está en pantalla."""
    modelo.respuestas = [dict(_FLOJA), dict(_FLOJA)]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert res.identidad == vi.normalizar(_FLOJA)
    assert vi.validar(res.identidad) == []
    assert any("reparo de diseño" in a for a in res.avisos)


def test_un_segundo_intento_peor_no_pierde_lo_que_ya_servia(modelo):
    """Válida-con-reparo primero y rota después: se devuelve la primera, no un 502."""
    modelo.respuestas = [dict(_FLOJA), dict(_ROTA)]
    res = ie.extraer(_fotos(), cfg=_Cfg())
    assert res.identidad == vi.normalizar(_FLOJA)


def test_un_json_roto_no_se_juzga_ademas_por_su_diseño(modelo):
    """Los reparos solo se miran sobre lo que ya valida: sobre una paleta rota dirían
    lo mismo dos veces y con peores palabras."""
    modelo.respuestas = [dict(_ROTA), dict(_ROTA)]
    with pytest.raises(ie.ExtraccionInvalida) as exc:
        ie.extraer(_fotos(), cfg=_Cfg())
    assert all("contrasta" not in e for e in exc.value.errores)


# ── Prompt ────────────────────────────────────────────────────────────────────

def test_las_reglas_del_prompt_salen_de_las_constantes_del_esquema():
    """Anti-drift: si alguien mueve un límite en `visual_identity`, el prompt se mueve
    con él. Es la razón de que estas reglas NO vivan en el JSON del prompt."""
    reglas = ie._reglas_esquema()
    assert f"{vi.MIN_COLORES}-{vi.MAX_COLORES}" in reglas
    assert str(vi.MAX_TEXTO) in reglas
    assert f"{vi.MIN_REFERENCIAS}-{vi.MAX_REFERENCIAS}" in reglas
    for campo in vi.CAMPOS:
        assert f"`{campo}`" in reglas


def test_el_prompt_declara_los_dos_contratos_que_fallan_en_silencio():
    reglas = ie._reglas_esquema().lower()
    assert "[background, text, accent]" in reglas       # el orden de la paleta
    assert "must contain the hex" in reglas             # el hex dentro de la frase


def test_el_prompt_prohibe_nombrar_colores_en_el_tono_visual():
    """Misma regla que vigila `prompt_lint`: dos paletas en un prompt se pelean."""
    assert "do not name any colour here" in ie._reglas_esquema().lower()


def test_las_reglas_de_diseno_llevan_los_numeros_que_aplica_el_codigo():
    """Mismo anti-drift que el esquema: pedir 3:1 y comprobar 4.5:1 sería pedirle al
    modelo que falle."""
    reglas = ie._reglas_diseno()
    assert str(vi.CONTRASTE_MIN) in reglas
    assert str(round(vi.SATURACION_ACENTO_MIN * 100)) in reglas


def test_el_prompt_prohibe_las_personas_en_el_ritmo_con_las_palabras_que_se_validan():
    """Mismo anti-drift: la lista que se le pide al modelo es la que aplica `validar`,
    así que su reintento puede corregirse solo en vez de volver a fallar igual."""
    reglas = ie._reglas_esquema()
    for palabra in vi.PALABRAS_PERSONA:
        assert palabra in reglas


def test_el_prompt_prohibe_las_familias_de_interfaz_que_se_validan():
    reglas = ie._reglas_esquema()
    for familia in vi.FAMILIAS_UI_PROHIBIDAS:
        assert familia in reglas


def test_las_reglas_de_diseno_piden_las_mismas_marcas_de_display_que_se_revisan():
    reglas = ie._reglas_diseno()
    for marca in vi.MARCAS_DISPLAY:
        assert marca in reglas
    for debil in vi.SECUNDARIA_DEBIL:
        assert debil in reglas


def test_el_sistema_lleva_el_encuadre_del_archivo_y_las_reglas_de_la_app():
    """El reparto del módulo, hecho comprobable: el JSON aporta el criterio creativo y
    la app pega detrás lo que no se delega."""
    cfg_ex = prompt_config.identity_extract()
    sistema = ie._sistema(cfg_ex)
    assert cfg_ex["sistema"] in sistema
    for linea in cfg_ex["criterio"]:
        assert linea in sistema
    for linea in cfg_ex["prohibiciones"]:
        assert linea in sistema
    assert ie._reglas_esquema() in sistema and ie._reglas_diseno() in sistema


def test_el_encuadre_pide_una_pieza_de_diseñador_no_una_descripcion_de_las_fotos():
    """Describir con fidelidad un set de fotos de teléfono produce una identidad de
    fotos de teléfono: el encuadre tiene que pedir la intención, no el inventario."""
    sistema = ie._sistema(prompt_config.identity_extract()).lower()
    assert "designer" in sistema
    assert "poster" in sistema          # la pieza que van a producir estos valores
    assert "production quality" in sistema


def test_sin_archivo_de_prompt_el_sistema_sigue_siendo_util():
    """`prompt_config.load` devuelve {} ante un archivo ausente o roto y eso no puede
    dejar al extractor sin criterio."""
    sistema = ie._sistema({})
    assert ie._reglas_esquema() in sistema and ie._reglas_diseno() in sistema
    assert "designer" in sistema.lower()


# ── Preparación de las imágenes ───────────────────────────────────────────────

def test_las_fotos_grandes_se_reducen_antes_de_mandarlas():
    datos, media_type = ie._preparar(_foto(2400, 1800))
    with Image.open(io.BytesIO(datos)) as img:
        assert max(img.size) == ie.LADO_MAXIMO
    assert media_type == "image/jpeg"
    assert len(datos) < len(_foto(2400, 1800))


def test_una_foto_pequena_no_se_agranda():
    with Image.open(io.BytesIO(ie._preparar(_foto(400, 300))[0])) as img:
        assert img.size == (400, 300)


def test_algo_que_pillow_no_sabe_abrir_se_manda_tal_cual():
    """Una foto rara puede ser perfectamente legible para el modelo."""
    crudo = b"\xff\xd8\xff" + b"basura"
    assert ie._preparar(crudo) == (crudo, "image/jpeg")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: _Cfg())
    with TestClient(api.app) as c:
        yield c


def _multipart(n: int) -> list:
    return [("photos", (f"ref-{i}.jpg", _foto(), "image/jpeg")) for i in range(n)]


def test_endpoint_con_pocas_fotos_da_400_sin_llamar_al_modelo(cliente, modelo):
    res = cliente.post("/identities/extract", files=_multipart(4))
    assert res.status_code == 400
    assert f"entre {ie.MIN_FOTOS} y {ie.MAX_FOTOS}" in res.json()["detail"]
    assert modelo.llamadas == 0


def test_endpoint_con_demasiadas_fotos_da_400(cliente, modelo):
    assert cliente.post("/identities/extract", files=_multipart(11)).status_code == 400
    assert modelo.llamadas == 0


def test_endpoint_devuelve_la_identidad_y_un_nombre_sugerido(cliente, modelo):
    modelo.respuestas = [dict(_IDENTIDAD)]
    data = cliente.post("/identities/extract", files=_multipart(6)).json()
    assert data["identity_json"] == vi.normalizar(_IDENTIDAD)
    assert data["name"] == "Ember · ink"
    assert data["intentos"] == 1


def test_el_endpoint_no_guarda_nada(cliente, modelo, identidades):
    """Extraer y guardar son dos pasos: en medio está la pantalla de preview editable."""
    modelo.respuestas = [dict(_IDENTIDAD)]
    cliente.post("/identities/extract", files=_multipart(6))
    assert identidades.docs == []


def test_endpoint_si_el_modelo_no_acierta_da_502_con_el_detalle(cliente, modelo):
    modelo.respuestas = [dict(_ROTA), dict(_ROTA)]
    res = cliente.post("/identities/extract", files=_multipart(6))
    assert res.status_code == 502
    assert "tercer color" in res.json()["detail"]


def test_endpoint_sin_ninguna_key_da_503(cliente, monkeypatch, modelo):
    monkeypatch.setattr(api, "load_config", lambda: _Cfg(con_vision=False))
    res = cliente.post("/identities/extract", files=_multipart(6))
    assert res.status_code == 503
    assert modelo.llamadas == 0


def test_endpoint_funciona_solo_con_perplexity(cliente, monkeypatch, modelo):
    monkeypatch.setattr(api, "load_config", lambda: _Cfg(proveedor="perplexity"))
    modelo.respuestas = [dict(_IDENTIDAD)]
    res = cliente.post("/identities/extract", files=_multipart(6))
    assert res.status_code == 200
    assert res.json()["identity_json"] == vi.normalizar(_IDENTIDAD)
