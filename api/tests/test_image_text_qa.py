"""Tests del QA de visión: ¿lo que quedó impreso en la imagen es lo que se pidió?

El fallo típico del generador es comerse una tilde o cambiar una letra, así que la
comparación ignora mayúsculas y puntuación pero NO los acentos. Y todo el módulo es
best-effort: nada de lo que pase aquí puede interrumpir la generación.
"""

import pytest

import image_text_qa as iqa


class _Cfg:
    def __init__(self, con_vision: bool = True):
        self.anthropic_api_key = "test-key" if con_vision else ""
        self.perplexity_api_key = "otra"


# ── Comparación ───────────────────────────────────────────────────────────────

def test_coincide_ignorando_mayusculas_y_puntuacion():
    assert iqa.coincide("China entra en la liga alta de la IA",
                        "CHINA ENTRA EN LA LIGA ALTA DE LA IA")
    assert iqa.coincide("Modelos abiertos, costes bajos", "Modelos abiertos costes bajos")


def test_una_tilde_perdida_no_coincide():
    assert not iqa.coincide("Así se hace", "Asi se hace")
    assert not iqa.coincide("Innovación en China", "Innovacion en China")


def test_coincide_con_texto_extra_alrededor():
    # El titular está bien escrito aunque el modelo haya añadido algo más: eso es
    # otro problema (los negativos), no un fallo de texto.
    assert iqa.coincide("Costes a la baja", "Costes a la baja — QBYK")


def test_una_letra_cambiada_no_coincide():
    assert not iqa.coincide("Costes a la baja", "Costes a la bajo")


# ── Casos en los que no se verifica (y no se rompe nada) ──────────────────────

def test_sin_texto_esperado_no_se_verifica():
    r = iqa.verificar("https://x.test/a.png", "", cfg=_Cfg())
    assert r.ok and not r.verificado


def test_una_plantilla_local_no_se_verifica():
    r = iqa.verificar(r"C:\assets\template-1.png", "Un hook", cfg=_Cfg())
    assert r.ok and not r.verificado
    assert "plantilla local" in r.motivo


def test_sin_modelo_de_vision_no_se_verifica():
    r = iqa.verificar("https://x.test/a.png", "Un hook", cfg=_Cfg(con_vision=False))
    assert r.ok and not r.verificado


def test_un_fallo_de_red_no_lanza(monkeypatch):
    def _explota(src):
        raise OSError("timeout")

    monkeypatch.setattr(iqa, "_descargar", _explota)
    r = iqa.verificar("https://x.test/a.png", "Un hook", cfg=_Cfg())
    assert r.ok and not r.verificado
    assert "no se pudo verificar" in r.motivo


# ── Veredictos con el modelo de visión simulado ───────────────────────────────

@pytest.fixture
def vision(monkeypatch):
    """Simula la descarga y el modelo de visión; devuelve el setter de la respuesta."""
    monkeypatch.setattr(iqa, "_descargar", lambda src: b"\x89PNG-falso")
    monkeypatch.setattr(iqa, "_preparar", lambda datos: (datos, "image/png"))
    estado: dict = {"data": {}}

    def _fake(system, user, image, *, media_type="image/png", cfg, max_tokens=0):
        return estado["data"], {"service": "anthropic", "model": "claude-sonnet-4-6",
                                "units": {"input_tokens": 5, "output_tokens": 5}}

    monkeypatch.setattr(iqa.llm_json, "complete_json_vision", _fake)
    return estado


def test_texto_correcto(vision):
    vision["data"] = {"texto": "China entra en la liga alta de la IA", "otros": [], "legible": True}
    r = iqa.verificar("https://x.test/a.png", "China entra en la liga alta de la IA", cfg=_Cfg())
    assert r.ok and r.verificado
    assert r.uso["service"] == "anthropic"


def test_titular_y_kicker_en_bloques_separados(vision):
    # Un texto largo se renderiza en dos bloques; el QA lo lee como titular + resto.
    vision["data"] = {"texto": "China entra en la liga", "otros": ["alta de la IA"], "legible": True}
    r = iqa.verificar("https://x.test/a.png", "China entra en la liga alta de la IA", cfg=_Cfg())
    assert r.ok and r.verificado


# ── La exigencia va por NIVEL ────────────────────────────────────────────────
# Un titular son 3-6 palabras a tamaño de póster: una letra mal se ve desde el otro
# lado de la sala. Un cuerpo son 30 palabras al 5% del alto: ningún generador las
# clava, y exigirle lo mismo convierte cada errata en una regeneración pagada.

_TITULAR = "El coste sube"
_CUERPO = ("Cada salto de contexto multiplica el gasto por token y el precio por tarea "
           "sube aunque el modelo mejore")


def test_una_errata_en_el_cuerpo_no_dispara_la_regeneracion(vision):
    roto = _CUERPO.replace("multiplica", "multiplca")
    vision["data"] = {"texto": _TITULAR, "otros": [roto], "legible": True}
    r = iqa.verificar("https://x.test/a.png",
                      [("titular", _TITULAR), ("cuerpo", _CUERPO)], cfg=_Cfg())
    assert r.ok and r.verificado


def test_un_cuerpo_que_dice_otra_cosa_si_falla(vision):
    # La tolerancia es para las erratas, no para que el modelo escriba otro texto.
    vision["data"] = {"texto": _TITULAR, "otros": ["Una frase completamente distinta que "
                                                   "no tiene nada que ver con la fuente"],
                      "legible": True}
    r = iqa.verificar("https://x.test/a.png",
                      [("titular", _TITULAR), ("cuerpo", _CUERPO)], cfg=_Cfg())
    assert not r.ok and r.verificado
    assert "cuerpo" in r.motivo


def test_el_titular_sigue_siendo_exacto_aunque_el_cuerpo_este_bien(vision):
    vision["data"] = {"texto": "El costo sube", "otros": [_CUERPO], "legible": True}
    r = iqa.verificar("https://x.test/a.png",
                      [("titular", _TITULAR), ("cuerpo", _CUERPO)], cfg=_Cfg())
    assert not r.ok and r.verificado
    assert "titular" in r.motivo


def test_una_etiqueta_mal_escrita_falla(vision):
    # La etiqueta es de display: dos palabras a la vista, se exige exacta.
    vision["data"] = {"texto": _TITULAR, "otros": ["03", _CUERPO], "legible": True}
    r = iqa.verificar("https://x.test/a.png",
                      [("etiqueta", "02"), ("titular", _TITULAR), ("cuerpo", _CUERPO)],
                      cfg=_Cfg())
    assert not r.ok and "etiqueta" in r.motivo


def test_texto_mal_escrito_falla_y_lo_dice(vision):
    vision["data"] = {"texto": "China entra en la ligua alta de la IA", "otros": [], "legible": True}
    r = iqa.verificar("https://x.test/a.png", "China entra en la liga alta de la IA", cfg=_Cfg())
    assert not r.ok and r.verificado
    assert "ligua" in r.motivo


def test_imagen_sin_texto_falla(vision):
    vision["data"] = {"texto": "", "otros": [], "legible": True}
    r = iqa.verificar("https://x.test/a.png", "Un hook", cfg=_Cfg())
    assert not r.ok and r.verificado


def test_texto_ilegible_falla(vision):
    vision["data"] = {"texto": "", "otros": [], "legible": False}
    r = iqa.verificar("https://x.test/a.png", "Un hook", cfg=_Cfg())
    assert not r.ok and r.verificado
    assert "no es legible" in r.motivo


def test_un_titular_cortado_por_el_borde_falla(vision):
    # Bien escrito pero recortado: la comparación de strings lo daba por bueno (el
    # modelo de visión lee la palabra igual aunque le falte la mitad de arriba), y era
    # justo el defecto que salía en producción.
    vision["data"] = {"texto": "China entra en la liga alta de la IA", "otros": [],
                      "legible": True, "recortado": True}
    r = iqa.verificar("https://x.test/a.png", "China entra en la liga alta de la IA", cfg=_Cfg())
    assert not r.ok and r.verificado and r.recortado
    assert "corta el texto" in r.motivo


def test_sin_recorte_el_texto_correcto_sigue_pasando(vision):
    vision["data"] = {"texto": "Costes a la baja", "otros": [], "legible": True, "recortado": False}
    r = iqa.verificar("https://x.test/a.png", "Costes a la baja", cfg=_Cfg())
    assert r.ok and not r.recortado


def test_una_respuesta_rota_del_modelo_no_lanza(vision):
    vision["data"] = {}
    r = iqa.verificar("https://x.test/a.png", "Un hook", cfg=_Cfg())
    assert not r.ok          # no se leyó el texto → hay que reintentar
    assert r.verificado
