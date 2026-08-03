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
