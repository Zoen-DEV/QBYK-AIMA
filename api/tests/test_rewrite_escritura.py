"""Reintento MANUAL de la escritura desde la compuerta previa (los dos flujos).

Cuando el LLM devuelve los campos visuales vacíos, la compuerta previa avisaba pero
solo dejaba dos salidas: escribir los prompts a mano o relanzar el post entero. Peor:
el formulario donde escribirlos se dibujaba a partir de lo que el modelo había
entregado, así que con la escritura vacía no había ni dónde escribir.

Acá se ejercita el camino que arregla las dos cosas: `POST /jobs/{id}/rewrite` (que
pide SOLO lo que falta y lo funde sobre lo que hay) y el bloque `needs` del snapshot,
que es lo que permite a las dos revisiones dibujar los campos que el job NECESITA.
"""
import pytest
from fastapi import HTTPException

import app
import job_runner as jr
import post_writer as pw
from config import Config

_CARRUSEL = {
    "redes": ["instagram"], "tipo_post": "post",
    "formato_instagram": "carrusel", "carrusel_slides": 3,   # portada + 2 de info
}


def _cfg() -> Config:
    return Config(anthropic_api_key="", perplexity_api_key="k", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key")


@pytest.fixture
def job():
    """Un job parado en la compuerta previa con la escritura a medias: el fallo real."""
    j = jr.make_job(_cfg(), dict(_CARRUSEL))
    j["status"] = "preview"
    j["content"] = {"title": "Cómo ahorrar", "transcript": "hola " * 50}
    j["posts"] = {
        "instagram_text": "caption ya escrito",
        "image_text": {"hook": "titular", "slides": ["uno", "dos"]},
    }
    jr._avisar(j, "escritura", "alto", "La escritura no entregó 3 campo(s) visual(es)…")
    app.jobs[j["id"]] = j
    yield j
    app.jobs.pop(j["id"], None)


def _reparacion(monkeypatch, salida):
    """Sustituye la llamada al LLM y registra qué campos se le pidieron."""
    pedidos = []

    async def _fake(content, params, posts, faltan, cfg, clean_url=""):
        pedidos.append(list(faltan))
        if isinstance(salida, Exception):
            raise salida
        return dict(salida), {"service": "perplexity", "model": "sonar-pro",
                              "units": {"requests": 1}}

    monkeypatch.setattr(pw, "_reparar", _fake)
    return pedidos


# ── `needs`: lo que el job pide, no lo que el modelo entregó ──────────────────

def test_needs_dice_que_falta_y_cuantos_slides(job):
    needs = app._needs_job(job)
    assert needs["imagenes"] is True and needs["video"] is False
    assert needs["n_info"] == 2          # 3 slides = portada + 2 de info
    assert needs["faltan"] == ["image_prompt", "image_style", "image_slide_prompts"]


def test_needs_viaja_en_el_snapshot_que_ven_las_dos_compuertas(job):
    # El editor por fila del lote lee el mismo snapshot que el preview individual.
    assert app._job_snapshot(job)["needs"]["faltan"]


def test_needs_de_un_reel_pide_shots_y_no_imagenes():
    j = jr.make_job(_cfg(), {"redes": ["instagram"], "tipo_post": "reel",
                             "duracion_video": 20, "video_segment_seconds": 5})
    needs = app._needs_job(j)
    assert needs["video"] is True and needs["imagenes"] is False
    assert needs["n_shots"] == 4
    assert "video_storyboard" in needs["faltan"]


# ── El endpoint ───────────────────────────────────────────────────────────────

async def test_rewrite_completa_lo_que_falta_y_retira_el_aviso(job, monkeypatch):
    pedidos = _reparacion(monkeypatch, {
        "image_prompt": "a worn oak desk…", "image_style": "single hard key…",
        "image_slide_prompts": ["a brass key…", "a folded map…"],
    })
    res = await app.rewrite_job(job["id"])

    assert pedidos == [["image_prompt", "image_style", "image_slide_prompts"]]
    assert res["ok"] is True
    assert res["needs"]["faltan"] == []
    assert job["posts"]["image_prompt"] == "a worn oak desk…"
    # El aviso viejo describía un estado que este reintento acaba de cambiar.
    assert [a for a in job["avisos"] if a["campo"] == "escritura"] == []


async def test_rewrite_no_pisa_lo_que_el_usuario_ya_escribio(job, monkeypatch):
    job["posts"]["image_prompt"] = "la escena que escribí yo"
    pedidos = _reparacion(monkeypatch, {
        "image_prompt": "la del modelo", "image_style": "estilo",
        "image_slide_prompts": ["a", "b"],
    })
    await app.rewrite_job(job["id"])

    assert pedidos == [["image_style", "image_slide_prompts"]]
    assert job["posts"]["image_prompt"] == "la escena que escribí yo"
    assert job["posts"]["image_slide_prompts"] == ["a", "b"]
    # Y el caption nunca entra en juego.
    assert job["posts"]["instagram_text"] == "caption ya escrito"


async def test_rewrite_deja_el_aviso_si_el_modelo_sigue_sin_entregar(job, monkeypatch):
    _reparacion(monkeypatch, {"image_style": "estilo"})   # solo uno de los tres
    res = await app.rewrite_job(job["id"])

    assert res["ok"] is False
    assert res["needs"]["faltan"] == ["image_prompt", "image_slide_prompts"]
    aviso = [a for a in job["avisos"] if a["campo"] == "escritura"][0]
    assert "image_prompt" in aviso["mensaje"] and "image_style" not in aviso["mensaje"]
    # Lo poco que llegó se conserva igual: la próxima vuelta pide menos.
    assert job["posts"]["image_style"] == "estilo"


async def test_rewrite_no_tumba_la_revision_si_el_proveedor_falla(job, monkeypatch):
    _reparacion(monkeypatch, RuntimeError("502 del proveedor"))
    res = await app.rewrite_job(job["id"])
    assert res["ok"] is False
    assert job["posts"]["image_text"] == {"hook": "titular", "slides": ["uno", "dos"]}


async def test_rewrite_solo_vale_en_la_compuerta_previa(job, monkeypatch):
    _reparacion(monkeypatch, {})
    job["status"] = "review"
    with pytest.raises(HTTPException) as e:
        await app.rewrite_job(job["id"])
    assert e.value.status_code == 409


async def test_rewrite_de_un_job_inexistente_es_404():
    with pytest.raises(HTTPException) as e:
        await app.rewrite_job("no-existe")
    assert e.value.status_code == 404


# ── El caption vacío: el hueco que no se veía ni se podía llenar ──────────────
# Reportado en producción: el post salía solo con el texto de LinkedIn y las cards
# de Instagram y Facebook aparecían en blanco. Nada avisaba (los captions no se
# verificaban) y en el preview el textarea ni siquiera se dibujaba, porque se
# dibujaba a partir de lo que el modelo había entregado.

async def test_needs_lista_los_captions_que_pide_el_job(job):
    job["params"]["redes"] = ["linkedin", "instagram", "facebook"]
    needs = app._needs_job(job)
    assert needs["captions"] == ["linkedin_text", "instagram_text", "facebook_text"]


async def test_needs_marca_el_caption_vacio_como_faltante(job):
    job["params"]["redes"] = ["instagram", "facebook"]
    assert "facebook_text" in app._needs_job(job)["faltan"]
    # Y se apaga solo en cuanto se escribe a mano (sin guardar: `posts` de prueba).
    con_texto = dict(job["posts"], facebook_text="escrito a mano")
    assert "facebook_text" not in app._needs_job(job, con_texto)["faltan"]


async def test_rewrite_recupera_un_caption_vacio(job, monkeypatch):
    job["params"]["redes"] = ["instagram", "facebook"]
    job["posts"].update({"image_prompt": "cover", "image_style": "estilo",
                         "image_slide_prompts": ["a", "b"]})
    pedidos = _reparacion(monkeypatch, {"facebook_text": "el caption que faltaba"})
    res = await app.rewrite_job(job["id"])

    assert pedidos == [["facebook_text"]]
    assert job["posts"]["facebook_text"] == "el caption que faltaba"
    # El que ya estaba no se toca y el aviso se retira.
    assert job["posts"]["instagram_text"] == "caption ya escrito"
    assert res["ok"] is True
