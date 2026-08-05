"""Tests de la identidad visual dentro del pipeline de generación.

Dos cosas que hay que poder afirmar sin matices:

1. **Sin identidad activa, nada cambia.** No "cambia poco": la marca que se le pasa al
   arquitecto tiene que ser exactamente la de antes de esta feature, y el prompt tiene
   que seguir saliendo con la paleta de `brand.json`.
2. **Con identidad activa, su paleta, su tipografía y sus referencias llegan al prompt
   final** — y los DOS flujos la congelan igual al crear el job.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app as api
import batch_runner as br
import job_runner as jr
import prompt_architect as pa
import prompt_config
import visual_identity as vi

_IDENTIDAD = {
    "paleta": ["#101014", "#F2EFE6", "#FF5C2B"],
    "paleta_nombres": ["ink", "paper", "ember"],
    "color_texto": "paper (#F2EFE6) over the ink",
    "color_acento": "ember (#FF5C2B)",
    "tipografia": "wide slab serif, ALL CAPS, loose tracking",
    "tipografia_secundaria": "same face, regular",
    "tono_visual": "overcast daylight, flat shadows",
    "aspect_ratio": "1:1",
    "referencias": ["nordic furniture catalogues"],
}

_TEXTO = "La identidad se nota antes que el texto"


class _Cfg:
    """Config mínima y SIN LLM: el arquitecto corre por su camino determinista, así
    que el prompt es reproducible y se puede comparar carácter a carácter."""

    anthropic_api_key = ""
    perplexity_api_key = ""
    image_text_in_prompt = True
    prompt_architect = True
    prompt_architect_critique = False


def _prompt(identidad: dict | None, *, posts: dict | None = None) -> str:
    prompt, _ = jr._prompt_imagen(
        _Cfg(), prompt_base="A chipped enamel jug on a windowsill.",
        posts=posts if posts is not None else {}, content={"title": "Identidad"},
        texto=_TEXTO, rol="portada", aspect="4:5", identidad=identidad,
    )
    return prompt


# ── Sin identidad: idéntico a antes ───────────────────────────────────────────

@pytest.mark.parametrize("identidad", [None, {}, "no soy un dict", []])
def test_sin_identidad_la_marca_es_exactamente_la_de_antes(identidad):
    """La forma pre-feature era `{aspect_ratio}` + `tono_visual` solo si el post trae
    `image_style`. Cualquier clave de más aquí sería un cambio de comportamiento."""
    assert jr._marca_post({}, aspect="4:5", identidad=identidad) == {"aspect_ratio": "4:5"}
    assert jr._marca_post({"image_style": "hard light"}, aspect="4:5", identidad=identidad) \
        == {"aspect_ratio": "4:5", "tono_visual": "hard light"}


def test_sin_identidad_el_prompt_lleva_la_paleta_de_la_casa():
    casa = prompt_config.brand()
    prompt = _prompt(None)
    for color in casa["paleta"]:
        assert color in prompt


def test_sin_identidad_el_prompt_es_el_mismo_que_con_la_identidad_de_la_casa():
    """La system ES brand.json, así que activarla no puede mover ni un carácter."""
    assert _prompt(None) == _prompt(vi.identidad_system())


@pytest.mark.parametrize("params, esperado", [
    ({}, {}),
    ({"identidad_visual": None}, {}),
    ({"identidad_visual": "rota"}, {}),
    ({"identidad_visual": {"paleta": ["#000000"]}}, {"paleta": ["#000000"]}),
])
def test_identidad_lee_el_snapshot_del_job(params, esperado):
    assert jr._identidad({"params": params}) == esperado


def test_identidad_aguanta_un_job_sin_params():
    assert jr._identidad({}) == {}


# ── Con identidad: llega al prompt ────────────────────────────────────────────

def test_la_paleta_de_la_identidad_llega_al_prompt():
    prompt = _prompt(_IDENTIDAD)
    for color in _IDENTIDAD["paleta"]:
        assert color in prompt
    # Y la de la casa ya no está: es una identidad, no un añadido.
    assert prompt_config.brand()["paleta"][2] not in prompt


def test_la_tipografia_de_la_identidad_llega_al_prompt():
    assert "wide slab serif" in _prompt(_IDENTIDAD)


def test_el_color_de_acento_de_la_identidad_llega_al_prompt():
    assert "#FF5C2B" in _prompt(_IDENTIDAD)


def test_las_referencias_de_la_identidad_llegan_al_prompt():
    """Van al nivel de arriba de la spec, no dentro de `marca`: `normalizar_spec` las
    lee de ahí. Puestas en `marca` se perderían sin un solo error."""
    assert "nordic furniture catalogues" in _prompt(_IDENTIDAD)
    assert prompt_config.brand()["referencias"][0] not in _prompt(_IDENTIDAD)


def test_el_prompt_sigue_siendo_valido_con_otra_identidad():
    prompt, res = jr._prompt_imagen(
        _Cfg(), prompt_base="A chipped enamel jug on a windowsill.", posts={},
        content={"title": "Identidad"}, texto=_TEXTO, rol="portada", aspect="4:5",
        identidad=_IDENTIDAD,
    )
    assert res is not None
    assert pa.validar(prompt, bloques=res.bloques, aspect_ratio="4:5") == []


# ── Lo que la identidad NO decide ─────────────────────────────────────────────

def test_el_aspecto_lo_fija_el_job_no_la_identidad():
    """La identidad trae `aspect_ratio` porque el esquema es el de brand.json, pero el
    aspecto real lo decide lo que se le pide al modelo."""
    marca = jr._marca_post({}, aspect="9:16", identidad=_IDENTIDAD)
    assert marca["aspect_ratio"] == "9:16"


def test_el_image_style_del_post_sigue_ganando_al_tono_visual():
    """Decisión explícita: la identidad fija paleta, tipografía y referencias; el
    tratamiento fotográfico lo sigue eligiendo el LLM por post, como hasta ahora."""
    marca = jr._marca_post({"image_style": "hard raking light, deep falloff"},
                           aspect="4:5", identidad=_IDENTIDAD)
    assert marca["tono_visual"] == "hard raking light, deep falloff"


def test_pero_la_luz_no_la_pisa_el_image_style():
    """El matiz de la regla anterior. El tratamiento fotográfico es creatividad por
    pieza; el esquema de iluminación es lo que hace que las N piezas de un job
    parezcan del mismo día, así que sale de la identidad y viaja aparte."""
    marca = jr._marca_post({"image_style": "hard raking light, deep falloff"},
                           aspect="4:5", identidad=_IDENTIDAD)
    assert marca["luz_identidad"] == "overcast daylight, flat shadows"


def test_la_luz_de_la_identidad_llega_al_bloqueo_del_prompt():
    prompt = _prompt(_IDENTIDAD, posts={"image_style": "hard raking light, deep falloff"})
    luz = [l for l in prompt.splitlines() if l.startswith("6.")][0]
    assert "LIGHT LOCK" in luz
    assert "overcast daylight" in luz.split("LIGHT LOCK")[1]


def test_sin_image_style_manda_el_tono_visual_de_la_identidad():
    assert jr._marca_post({}, aspect="4:5", identidad=_IDENTIDAD)["tono_visual"] \
        == "overcast daylight, flat shadows"


def test_un_campo_vacio_de_la_identidad_no_pisa_el_de_la_casa():
    """`normalizar_spec` resuelve con `marca.get(x) or marca_def.get(x)`: pasar un
    blanco impondría el blanco, así que los vacíos no se pasan."""
    marca = jr._marca_post({}, aspect="4:5", identidad={**_IDENTIDAD, "tipografia": ""})
    assert "tipografia" not in marca


def test_las_referencias_no_viajan_dentro_de_marca():
    assert "referencias" not in jr._marca_post({}, aspect="4:5", identidad=_IDENTIDAD)


# ── Plantilla de respaldo (el otro camino del texto) ──────────────────────────

class _CfgPlantilla(_Cfg):
    pass


def test_la_plantilla_de_respaldo_usa_los_colores_de_la_identidad(monkeypatch):
    monkeypatch.setattr(jr.improv, "es_plantilla", lambda src: True)
    lockup = jr._lockup_plantilla(_CfgPlantilla(), "assets/templates/template-1.png",
                                  texto=_TEXTO, rol="portada", identidad=_IDENTIDAD)
    assert lockup["color_texto"] == _IDENTIDAD["color_texto"]
    assert lockup["color_acento"] == _IDENTIDAD["color_acento"]


def test_sin_identidad_la_plantilla_usa_los_colores_de_la_casa(monkeypatch):
    monkeypatch.setattr(jr.improv, "es_plantilla", lambda src: True)
    lockup = jr._lockup_plantilla(_CfgPlantilla(), "assets/templates/template-1.png",
                                  texto=_TEXTO, rol="portada", identidad=None)
    assert lockup["color_texto"] == prompt_config.brand()["color_texto"]


def test_una_imagen_del_proveedor_no_lleva_lockup(monkeypatch):
    """No es "¿es una ruta local?": una salida del proveedor también puede serlo, y esa
    ya trae el texto puesto."""
    monkeypatch.setattr(jr.improv, "es_plantilla", lambda src: False)
    assert jr._lockup_plantilla(_CfgPlantilla(), "outputs/algo.png", texto=_TEXTO,
                                rol="portada", identidad=_IDENTIDAD) is None


# ── Los dos flujos congelan la identidad ──────────────────────────────────────

def test_params_identidad_es_la_forma_compartida():
    fila = {"id": "abc", "name": "Mía", "identity_json": _IDENTIDAD}
    assert api._params_identidad(fila) == {
        "identidad_visual": _IDENTIDAD,
        "identidad_visual_id": "abc",
        "identidad_visual_nombre": "Mía",
    }


@pytest.fixture
def sin_pipeline(monkeypatch):
    """Corta la generación: solo interesa con qué `params` se creó el job."""
    creados: list[dict] = []

    def _make_job(cfg, params, **kw):
        creados.append(params)
        return {"id": "job-1", "status": "running", "params": params, "_queue": None}

    async def _run_pipeline(job):
        return None

    monkeypatch.setattr(api, "make_job", _make_job)
    monkeypatch.setattr(api, "run_pipeline", _run_pipeline)
    monkeypatch.setattr(api, "load_config", lambda: _Cfg())
    return creados


def test_el_flujo_individual_congela_la_identidad_activa(sin_pipeline, identidades):
    with TestClient(api.app) as c:
        creada = c.post("/identities", json={"name": "Mía", "identity_json": _IDENTIDAD,
                                             "activar": True}).json()
        res = c.post("/jobs", data={"source_type": "manual", "manual_text": "hola"})
    assert res.status_code == 200
    params = sin_pipeline[0]
    assert params["identidad_visual"] == vi.normalizar(_IDENTIDAD)
    assert params["identidad_visual_id"] == creada["id"]
    assert params["identidad_visual_nombre"] == "Mía"


def test_el_flujo_individual_congela_la_identidad_elegida_en_el_form(sin_pipeline, identidades):
    """El campo del formulario gana a la activa: es la elección de ESTE post."""
    with TestClient(api.app) as c:
        c.post("/identities", json={"name": "La activa", "identity_json": _IDENTIDAD,
                                    "activar": True})
        otra = c.post("/identities", json={
            "name": "La elegida",
            "identity_json": {**_IDENTIDAD, "paleta": ["#FFFFFF", "#222222", "#0055FF"],
                              "paleta_nombres": ["white", "charcoal", "blue"],
                              "color_texto": "charcoal (#222222)",
                              "color_acento": "blue (#0055FF)"},
        }).json()
        res = c.post("/jobs", data={"source_type": "manual", "manual_text": "hola",
                                    "identidad_visual_id": otra["id"]})
    assert res.status_code == 200
    params = sin_pipeline[0]
    assert params["identidad_visual_id"] == otra["id"]
    assert params["identidad_visual_nombre"] == "La elegida"
    assert params["identidad_visual"]["paleta"] == ["#FFFFFF", "#222222", "#0055FF"]


def test_el_flujo_individual_puede_elegir_la_de_la_casa(sin_pipeline, identidades):
    with TestClient(api.app) as c:
        c.post("/identities", json={"name": "Mía", "identity_json": _IDENTIDAD,
                                    "activar": True})
        res = c.post("/jobs", data={"source_type": "manual", "manual_text": "hola",
                                    "identidad_visual_id": vi.SYSTEM_ID})
    assert res.status_code == 200
    assert sin_pipeline[0]["identidad_visual"] == vi.identidad_system()


def test_una_identidad_que_ya_no_existe_no_tumba_la_creacion(sin_pipeline, identidades):
    """Se borró entre que se pintó el formulario y se envió: se genera con la activa."""
    with TestClient(api.app) as c:
        creada = c.post("/identities", json={"name": "Mía", "identity_json": _IDENTIDAD,
                                             "activar": True}).json()
        res = c.post("/jobs", data={"source_type": "manual", "manual_text": "hola",
                                    "identidad_visual_id": "no-existe"})
    assert res.status_code == 200
    assert sin_pipeline[0]["identidad_visual_id"] == creada["id"]


def test_el_flujo_individual_sin_identidad_propia_congela_la_de_la_casa(sin_pipeline, sin_base):
    with TestClient(api.app) as c:
        c.post("/jobs", data={"source_type": "manual", "manual_text": "hola"})
    params = sin_pipeline[0]
    assert params["identidad_visual_id"] == vi.SYSTEM_ID
    assert params["identidad_visual"] == vi.identidad_system()


def test_el_lote_congela_la_identidad_una_vez_para_todas_las_filas(monkeypatch):
    """Una fila y otra del mismo lote no pueden salir con estéticas distintas."""
    vistos: list[dict] = []

    def _make_job(cfg, params, **kw):
        vistos.append(params)
        return {"id": f"job-{len(vistos)}", "status": "preview", "params": params,
                "content": {"title": "t"}, "error_msg": ""}

    async def _run_pipeline(job):
        job["status"] = "preview"

    monkeypatch.setattr(br, "make_job", _make_job)
    monkeypatch.setattr(br, "run_pipeline", _run_pipeline)

    batch = {
        "id": "b1", "status": "running", "dry_run": False, "account_params": {},
        "identidad_params": api._params_identidad(
            {"id": "abc", "name": "Mía", "identity_json": _IDENTIDAD}),
        "_cfg": _Cfg(),
        "rows": [{"index": i, "label": f"f{i}", "status": "queued", "job_id": None,
                  "result": {}, "error": None,
                  "_spec": {"params": {}, "upload_bytes": None, "upload_filename": None,
                            "schedule_dt": None}}
                 for i in (1, 2, 3)],
    }
    asyncio.run(br.run_batch(batch, {}))

    assert len(vistos) == 3
    assert all(p["identidad_visual"] == _IDENTIDAD for p in vistos)
    assert all(p["identidad_visual_id"] == "abc" for p in vistos)


def test_un_lote_sin_identidad_no_rompe(monkeypatch):
    """Un batch viejo (sin la clave) se comporta como antes de la feature."""
    vistos: list[dict] = []
    monkeypatch.setattr(br, "make_job", lambda cfg, params, **kw: (
        vistos.append(params) or {"id": "j", "status": "preview", "params": params,
                                  "content": {"title": "t"}, "error_msg": ""}))

    async def _run_pipeline(job):
        job["status"] = "preview"

    monkeypatch.setattr(br, "run_pipeline", _run_pipeline)
    batch = {
        "id": "b1", "status": "running", "dry_run": False, "account_params": {},
        "_cfg": _Cfg(),
        "rows": [{"index": 1, "label": "f1", "status": "queued", "job_id": None,
                  "result": {}, "error": None,
                  "_spec": {"params": {}, "upload_bytes": None, "upload_filename": None,
                            "schedule_dt": None}}],
    }
    asyncio.run(br.run_batch(batch, {}))
    assert jr._identidad({"params": vistos[0]}) == {}


def test_el_sheet_congela_la_identidad_al_subirlo(monkeypatch, identidades):
    """El otro punto de entrada del bulk usa el MISMO helper que el individual."""
    lanzados: list[dict] = []
    monkeypatch.setattr(api, "load_config", lambda: _Cfg())
    monkeypatch.setattr(api.sheets, "parse_sheet", lambda data, nombre: (
        [{"source": "manual", "label": "fila", "schedule_dt": None, "params": {},
          "upload_bytes": None, "upload_filename": None}], []))

    async def _run_batch(batch, jobs):
        lanzados.append(batch)

    monkeypatch.setattr(api, "run_batch", _run_batch)

    with TestClient(api.app) as c:
        c.post("/identities", json={"name": "Mía", "identity_json": _IDENTIDAD,
                                    "activar": True})
        res = c.post("/sheets/jobs", files={"sheet_file": ("posts.xlsx", b"xx")})

    assert res.status_code == 200
    assert lanzados[0]["identidad_params"]["identidad_visual"] == vi.normalizar(_IDENTIDAD)


def test_el_lote_congela_la_identidad_elegida_en_la_ui(monkeypatch, identidades):
    """La identidad del lote se elige en la UI (no es una columna del sheet) y pisa a
    la activa, igual que las cuentas y el dry-run."""
    lanzados: list[dict] = []
    monkeypatch.setattr(api, "load_config", lambda: _Cfg())
    monkeypatch.setattr(api.sheets, "parse_sheet", lambda data, nombre: (
        [{"source": "manual", "label": "fila", "schedule_dt": None, "params": {},
          "upload_bytes": None, "upload_filename": None}], []))

    async def _run_batch(batch, jobs):
        lanzados.append(batch)

    monkeypatch.setattr(api, "run_batch", _run_batch)

    with TestClient(api.app) as c:
        c.post("/identities", json={"name": "La activa", "identity_json": _IDENTIDAD,
                                    "activar": True})
        elegida = c.post("/identities", json={"name": "La elegida",
                                              "identity_json": _IDENTIDAD}).json()
        res = c.post("/sheets/jobs", files={"sheet_file": ("posts.xlsx", b"xx")},
                     data={"identidad_visual_id": elegida["id"]})

    assert res.status_code == 200
    assert lanzados[0]["identidad_params"]["identidad_visual_id"] == elegida["id"]
    assert lanzados[0]["identidad_params"]["identidad_visual_nombre"] == "La elegida"


# ── Cambiar de identidad cambia el prompt (el criterio de aceptación) ─────────

def test_dos_identidades_producen_prompts_distintos():
    otra = {**_IDENTIDAD, "paleta": ["#FFFFFF", "#222222", "#0055FF"],
            "paleta_nombres": ["white", "charcoal", "electric blue"],
            "color_texto": "charcoal (#222222)", "color_acento": "electric blue (#0055FF)"}
    assert _prompt(_IDENTIDAD) != _prompt(otra)
    assert "#0055FF" in _prompt(otra)
    assert "#0055FF" not in _prompt(_IDENTIDAD)
