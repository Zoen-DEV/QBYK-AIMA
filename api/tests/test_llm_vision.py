"""Tests de la lectura de imágenes por proveedor.

Los dos proveedores leen imágenes, pero con formatos de bloque distintos y cada uno
tiene que recibir el suyo. Como no se puede probar contra los endpoints reales, lo que
se blinda aquí es el **cuerpo exacto** que sale por el cable: es la única forma de que
un cambio en el armado no se descubra en producción.
"""

import base64
import json

import pytest

import image_text_qa as iqa
import llm_json


class _Cfg:
    def __init__(self, anthropic: str = "", perplexity: str = ""):
        self.anthropic_api_key = anthropic
        self.perplexity_api_key = perplexity


_IMAGENES = [(b"\xff\xd8\xffprimera", "image/jpeg"), (b"\x89PNGsegunda", "image/png")]


@pytest.fixture
def perplexity(monkeypatch):
    """Captura el cuerpo del POST a Perplexity sin salir a la red."""
    capturado: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @staticmethod
        def read():
            return json.dumps({
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }).encode()

    def _urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(llm_json.urllib.request, "urlopen", _urlopen)
    return capturado


# ── Qué proveedor lee imágenes ────────────────────────────────────────────────

@pytest.mark.parametrize("cfg, esperado", [
    (_Cfg(anthropic="k"), True),
    (_Cfg(perplexity="k"), True),
    (_Cfg(anthropic="k", perplexity="k"), True),
    (_Cfg(), False),
])
def test_vision_disponible_con_cualquiera_de_los_dos(cfg, esperado):
    assert llm_json.vision_disponible(cfg) is esperado


def test_el_qa_de_imagenes_sigue_pidiendo_anthropic():
    """Perplexity podría, pero encenderlo añadiría una llamada por imagen generada a
    todo el que hoy tiene el QA apagado. Es una decisión aparte, no un efecto colateral."""
    assert iqa.disponible(_Cfg(perplexity="k")) is False
    assert iqa.disponible(_Cfg(anthropic="k")) is True


def test_sin_ninguna_key_se_dice_claro():
    with pytest.raises(llm_json.LLMNoDisponible) as exc:
        llm_json.complete_json_vision_multi("s", "u", _IMAGENES, cfg=_Cfg())
    assert "ANTHROPIC_API_KEY" in str(exc.value) and "PERPLEXITY_API_KEY" in str(exc.value)


def test_sin_imagenes_es_un_error_de_programacion():
    with pytest.raises(ValueError):
        llm_json.complete_json_vision_multi("s", "u", [], cfg=_Cfg(perplexity="k"))


# ── Perplexity: el cuerpo exacto ──────────────────────────────────────────────

def test_perplexity_recibe_el_texto_primero_y_las_imagenes_detras(perplexity):
    """El shape del ejemplo de su guía, al pie de la letra: es el único documentado."""
    llm_json.complete_json_vision_multi("sistema", "instrucción", _IMAGENES,
                                        cfg=_Cfg(perplexity="k"))
    mensajes = perplexity["body"]["messages"]
    assert mensajes[0] == {"role": "system", "content": "sistema"}
    bloques = mensajes[1]["content"]
    assert bloques[0] == {"type": "text", "text": "instrucción"}
    assert [b["type"] for b in bloques[1:]] == ["image_url", "image_url"]


def test_perplexity_recibe_las_imagenes_como_data_uri(perplexity):
    llm_json.complete_json_vision_multi("s", "u", _IMAGENES, cfg=_Cfg(perplexity="k"))
    bloques = perplexity["body"]["messages"][1]["content"]
    esperado = "data:image/jpeg;base64," + base64.b64encode(_IMAGENES[0][0]).decode()
    assert bloques[1]["image_url"]["url"] == esperado
    assert bloques[2]["image_url"]["url"].startswith("data:image/png;base64,")


def test_perplexity_usa_su_modelo_y_devuelve_el_uso(perplexity):
    data, uso = llm_json.complete_json_vision_multi("s", "u", _IMAGENES,
                                                    cfg=_Cfg(perplexity="k"))
    assert perplexity["body"]["model"] == llm_json.PERPLEXITY_MODEL
    assert data == {"ok": True}
    assert uso["service"] == "perplexity"
    assert uso["units"]["input_tokens"] == 12


def test_anthropic_gana_si_estan_las_dos_keys(monkeypatch, perplexity):
    llamado = {}

    def _fake(system, user, api_key, *, max_tokens):
        llamado["user"] = user
        return {}, None

    monkeypatch.setattr(llm_json, "_anthropic_json", _fake)
    llm_json.complete_json_vision_multi("s", "u", _IMAGENES,
                                        cfg=_Cfg(anthropic="k", perplexity="k"))
    assert "body" not in perplexity            # no se tocó Perplexity
    # Anthropic sí numera cada imagen y deja el texto al final.
    assert [b.get("text") for b in llamado["user"] if b["type"] == "text"] \
        == ["Image 1:", "Image 2:", "u"]


# ── El camino de texto no se movió ────────────────────────────────────────────

def test_el_camino_de_texto_manda_el_string_pelado(perplexity):
    """Es el que usa todo el pipeline: su cuerpo tiene que ser idéntico al de siempre."""
    llm_json.complete_json("sistema", "un mensaje", cfg=_Cfg(perplexity="k"))
    assert perplexity["body"]["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "un mensaje"},
    ]
    assert perplexity["body"]["web_search_options"] == {"search_context_size": "low"}
