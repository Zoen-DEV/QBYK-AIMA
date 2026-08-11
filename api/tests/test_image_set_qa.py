"""QA de conjunto: ¿las N piezas del carrusel se leen como un set?

Es la pieza que faltaba en la arquitectura. Ningún QA por imagen puede detectar que
cinco piezas no se parecen entre sí: `rubric.json` puntúa un prompt sin conocer a sus
hermanos y `image_text_qa` solo mira ortografía y recorte. Para verlo hay que verlas
juntas, y eso es una llamada más — por eso todo aquí es best-effort y tras un flag.

Lo que estos tests fijan, en orden de importancia:

1. **Nunca interrumpe.** Sin flag, sin modelo de visión o con la llamada rota, la fase
   de medios termina exactamente igual.
2. **Una sola ronda.** Regenerar cuesta créditos por imagen y el veredicto es una
   opinión, no una medida: un bucle convierte un carrusel caro en uno carísimo.
3. **El veredicto llega a las dos UI**, que es donde está el botón que lo arregla.
"""

import io

import pytest

import image_set_qa as sqa
import job_runner as jr

Image = pytest.importorskip("PIL.Image")


class _Cfg:
    anthropic_api_key = "test-key"
    perplexity_api_key = ""
    image_set_qa = True


def _png(color=(40, 90, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 300), color).save(buf, format="PNG")
    return buf.getvalue()


def _respuesta(fallos: dict[int, list[str]], n: int = 4, peor: int | None = None) -> dict:
    imagenes = []
    for i in range(n):
        item = {"indice": i, "motivo": "" if i not in fallos else "otra localización"}
        for v in sqa.veredictos():
            item[v] = v not in fallos.get(i, [])
        imagenes.append(item)
    return {"imagenes": imagenes,
            "peor": peor if peor is not None else (min(fallos) if fallos else -1)}


@pytest.fixture
def vision(monkeypatch):
    """Simula el modelo de visión múltiple; devuelve el setter de la respuesta."""
    estado: dict = {"data": _respuesta({}), "llamadas": 0}

    def _fake(system, user, images, *, cfg, max_tokens=0):
        estado["llamadas"] += 1
        estado["ultimo_user"] = user
        estado["n_imagenes"] = len(images)
        data = estado["data"]
        if callable(data):
            data = data(estado["llamadas"])
        return data, {"service": "anthropic", "model": "claude-sonnet-4-6",
                      "units": {"input_tokens": 900, "output_tokens": 120}}

    monkeypatch.setattr(sqa.llm_json, "complete_json_vision_multi", _fake)
    return estado


# ── Veredicto ─────────────────────────────────────────────────────────────────

def test_un_set_coherente_pasa(vision):
    res = sqa.revisar([_png()] * 4, cfg=_Cfg())
    assert res.ok and res.verificado
    assert res.peor == -1
    assert all(p.ok for p in res.piezas)


def test_un_outlier_se_marca_con_su_motivo(vision):
    vision["data"] = _respuesta({2: ["mismo_mundo", "mismo_grade"]})
    res = sqa.revisar([_png()] * 4, cfg=_Cfg())
    assert not res.ok and res.verificado
    assert res.peor == 2
    assert res.piezas[2].fallos == ["mismo_mundo", "mismo_grade"]
    assert all(p.ok for i, p in enumerate(res.piezas) if i != 2)


def test_el_peor_lo_dice_el_modelo_si_es_valido(vision):
    vision["data"] = _respuesta({1: ["mismo_mundo"], 3: ["mismo_grade"]}, peor=3)
    assert sqa.revisar([_png()] * 4, cfg=_Cfg()).peor == 3


def test_un_peor_inventado_se_ignora_y_manda_el_que_mas_rompe(vision):
    # El modelo señala una pieza que él mismo dio por buena: se cae al que más
    # veredictos rompe, que es lo que se puede accionar.
    vision["data"] = _respuesta({1: ["mismo_mundo", "mismo_grade"], 3: ["mismo_grade"]}, peor=0)
    assert sqa.revisar([_png()] * 4, cfg=_Cfg()).peor == 1


def test_una_pieza_que_el_modelo_no_menciona_no_es_un_fallo(vision):
    # Un QA que inventa fallos por un JSON incompleto es peor que no tenerlo: dispara
    # regeneraciones que cuestan créditos.
    vision["data"] = {"imagenes": [{"indice": 0, "mismo_mundo": True}], "peor": -1}
    res = sqa.revisar([_png()] * 4, cfg=_Cfg())
    assert res.ok and all(p.ok for p in res.piezas)


def test_el_conjunto_juzga_tambien_el_color_de_acento(vision):
    """El acento a la deriva se arregló en el prompt, pero solo se VE comparando piezas.

    Ningún QA por imagen puede detectarlo —`image_text_qa` mira ortografía— y
    `mismo_sistema_tipografico` decía «sí» con cinco acentos distintos, porque mira
    familia, caja y jerarquía: el color no es la forma. Sin un veredicto propio, la
    próxima regresión vuelve a pasar meses sin que nadie se entere.
    """
    assert "mismo_acento" in sqa.veredictos()
    sqa.revisar([_png()] * 4, cfg=_Cfg())
    # Fuente única: el JSON que se pide y el que se lee salen de la misma lista.
    assert all(f'"{v}"' in vision["ultimo_user"] for v in sqa.veredictos())
    # Y la tolerancia que evita el falso positivo caro: un slide sin acento (la tensión
    # lo calla a propósito) no puede contar como fallo, o dispara una regeneración.
    assert "NO highlighted word at all is CORRECT" in vision["ultimo_user"]


def test_las_imagenes_van_todas_en_la_misma_llamada(vision):
    sqa.revisar([_png()] * 5, cfg=_Cfg())
    assert vision["n_imagenes"] == 5
    assert vision["llamadas"] == 1
    # La instrucción declara cuántas son y en qué orden: sin eso el modelo describe la
    # última en vez del conjunto.
    assert "5 images" in vision["ultimo_user"]


# ── Nunca interrumpe ──────────────────────────────────────────────────────────

def test_con_menos_de_tres_piezas_no_hay_conjunto(vision):
    res = sqa.revisar([_png(), _png()], cfg=_Cfg())
    assert res.ok and not res.verificado
    assert vision["llamadas"] == 0


def test_con_el_flag_apagado_no_se_llama(vision):
    class _Off(_Cfg):
        image_set_qa = False

    res = sqa.revisar([_png()] * 4, cfg=_Off())
    assert res.ok and not res.verificado
    assert vision["llamadas"] == 0


def test_sin_modelo_de_vision_no_se_llama(vision):
    class _SinKey(_Cfg):
        anthropic_api_key = ""

    assert not sqa.disponible(_SinKey())
    res = sqa.revisar([_png()] * 4, cfg=_SinKey())
    assert res.ok and not res.verificado
    assert vision["llamadas"] == 0


def test_un_fallo_de_la_llamada_no_lanza(monkeypatch):
    def _explota(*a, **k):
        raise RuntimeError("502")

    monkeypatch.setattr(sqa.llm_json, "complete_json_vision_multi", _explota)
    res = sqa.revisar([_png()] * 4, cfg=_Cfg())
    assert res.ok and not res.verificado
    assert "no se pudo verificar" in res.motivo


# ── Enganche en el pipeline ───────────────────────────────────────────────────


def _job() -> dict:
    return {"id": "j1", "images": {"bytes": {f"ig-{i}": _png() for i in range(4)},
                                   "qa_set": []}}


async def _correr(job, cfg, rehacer) -> None:
    await jr._verificar_conjunto(job, jr.asyncio.Queue(), cfg,
                                 claves=[f"ig-{i}" for i in range(4)], rehacer=rehacer)


async def test_un_set_coherente_no_rehace_nada(vision):
    job = _job()
    rehechos: list[str] = []

    async def _rehacer(subkey):
        rehechos.append(subkey)
        return True

    await _correr(job, _Cfg(), _rehacer)
    assert rehechos == []
    assert job["images"]["qa_set"][-1]["ok"] is True


async def test_un_outlier_dispara_una_regeneracion_y_una_sola_ronda(vision):
    # Aunque la segunda revisión siga viendo el mismo fallo: una ronda, y para.
    vision["data"] = _respuesta({2: ["mismo_mundo"]})
    job = _job()
    rehechos: list[str] = []

    async def _rehacer(subkey):
        rehechos.append(subkey)
        return True

    await _correr(job, _Cfg(), _rehacer)
    assert rehechos == ["ig-2"]
    assert vision["llamadas"] == 2                  # la inicial + la de después de rehacer
    assert len(job["images"]["qa_set"]) == 2


async def test_si_el_reintento_arregla_el_set_se_queda_asi(vision):
    vision["data"] = lambda n: _respuesta({2: ["mismo_mundo"]}) if n == 1 else _respuesta({})
    job = _job()

    async def _rehacer(subkey):
        return True

    await _correr(job, _Cfg(), _rehacer)
    assert job["images"]["qa_set"][-1]["ok"] is True


async def test_un_rehacer_que_falla_no_interrumpe(vision):
    vision["data"] = _respuesta({2: ["mismo_mundo"]})
    job = _job()

    async def _rehacer(subkey):
        raise RuntimeError("el proveedor no respondió")

    await _correr(job, _Cfg(), _rehacer)          # no lanza
    assert job["images"]["qa_set"]                 # y el veredicto queda registrado


async def test_el_veredicto_queda_en_el_job_con_su_subkey(vision):
    vision["data"] = _respuesta({2: ["mismo_grade"]})
    job = _job()

    async def _rehacer(subkey):
        return False

    await _correr(job, _Cfg(), _rehacer)
    ultima = job["images"]["qa_set"][-1]
    assert ultima["peor"] == "ig-2"
    # Las dos UI leen por subkey, no por índice: un índice no sabe qué botón pulsar.
    assert [p["subkey"] for p in ultima["piezas"]] == ["ig-0", "ig-1", "ig-2", "ig-3"]
    assert [p["subkey"] for p in ultima["piezas"] if not p["ok"]] == ["ig-2"]
