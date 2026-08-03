"""Fase de imágenes de punta a punta (carrusel), con el MCP y Blotato simulados.

Los tests de arriba cubren cada pieza por separado; este ejercita el recorrido
completo de `_run_media_phase` —el núcleo que comparten el flujo individual y el
bulk— para comprobar que las piezas están efectivamente cableadas: aspecto nativo
4:5 en todas las generaciones, la portada viajando como referencia a cada slide, y
un juego de slides subido y listo para publicar.
"""
import io

import pytest

import job_runner as jr
from config import Config

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")


def _cfg(**overrides) -> Config:
    """Config real con sus defaults (la fase de medios lee muchos campos)."""
    return Config(anthropic_api_key="", perplexity_api_key="", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key", **overrides)


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    """MCP + Blotato simulados. Registra qué se pidió y con qué parámetros."""
    calls = {"generations": [], "uploads": []}

    # Cada generación devuelve una imagen local distinta (color por índice), así el
    # grade tiene algo real que igualar.
    def _png(i: int) -> str:
        p = tmp_path / f"gen-{i}.png"
        img = Image.new("RGB", (1080, 1350), (40 + i * 25, 90, 140))
        px = img.load()
        for y in range(0, 1350, 3):      # textura: desviación > 0
            for x in range(0, 1080, 3):
                px[x, y] = (200, 200, 200)
        img.save(p)
        return str(p)

    def generate_image_job(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["generations"].append({"aspect_ratio": aspect_ratio, "reference": reference,
                                     "prompt": prompt, "kind": "base"})
        return {"job_id": "job-portada", "url": _png(0)}

    def submit_image(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["generations"].append({"aspect_ratio": aspect_ratio, "reference": reference,
                                     "prompt": prompt, "kind": "slide"})
        return {"job_id": f"job-{len(calls['generations'])}",
                "_src": _png(len(calls["generations"]))}

    def poll_image(handle, **kw):
        return handle["_src"]

    def upload_media_local(data, filename, *, api_key=""):
        calls["uploads"].append((filename, len(data)))
        return f"https://blotato.test/{filename}"

    import image_provider as improv
    # Las imágenes generadas se guardan en disco: que vayan al tmp del test y no a
    # api/outputs/, donde vive el material de los jobs reales.
    monkeypatch.setattr(jr, "_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(improv.hfmcp, "generate_image_job", generate_image_job)
    monkeypatch.setattr(improv.hfmcp, "submit_image", submit_image)
    monkeypatch.setattr(improv.hfmcp, "poll_image", poll_image)
    monkeypatch.setattr(improv.hfmcp, "is_configured", lambda: True)
    monkeypatch.setattr(jr.bc, "upload_media_local", upload_media_local)
    return calls


def _job(**params) -> dict:
    job = jr.make_job(
        _cfg(),
        {"redes": ["instagram", "linkedin"], "formato": "carrusel",
         "formato_instagram": "carrusel", "carrusel_slides": 4, "tipo_post": "post",
         "fuente_imagen": "higgsfield", **params},
    )
    job["content"] = {"title": "Cómo ahorrar sin sufrir", "channel": "Canal", "transcript": "x" * 200}
    job["posts"] = {
        "instagram_text": "Un caption.", "linkedin_text": "Otro caption.",
        "image_prompt": "A chipped ceramic jar of coins on a kitchen windowsill.",
        "image_style": "Warm oat and faded denim palette, low window light, 50mm, fine grain.",
        "image_slide_prompts": ["Scene A.", "Scene B.", "Scene C."],
        "image_text": {"hook": "Un hook", "slides": ["Idea uno", "Idea dos", "Idea tres"]},
    }
    return job


async def _run(job) -> None:
    # El cfg y la cola viajan dentro del job (así la fase se puede reanudar tras el
    # preview en el individual y correr de corrido en el bulk).
    await jr._run_media_phase(job)


async def test_carrusel_completo(mcp):
    job = _job()
    await _run(job)

    # 4 slides: portada + 3 de info (el último incluido). Una generación por slide.
    assert len(mcp["generations"]) == 4
    assert sorted(job["images"]["bytes"]) == ["ig-0", "ig-1", "ig-2", "ig-3"]
    # Un solo juego subido y compartido por las redes activas.
    assert len(job["_ig_media_urls"]) == 4
    assert job["_ig_media_urls"] == job["_li_media_urls"]
    assert job["status"] == "review"
    assert not job["images"]["notice"]


async def test_todo_se_pide_en_4_5_nativo(mcp):
    await _run(_job())
    assert {g["aspect_ratio"] for g in mcp["generations"]} == {"4:5"}


async def test_ningun_slide_recibe_la_portada_como_imagen_de_entrada(mcp):
    # El defecto que esto protege: pasar la portada en `medias` NO es una referencia
    # de estilo — con estos modelos es image-to-image, así que cada slide volvía
    # siendo la portada re-encuadrada, con su mismo objeto y otro recorte, ignorando
    # su propia escena. Por defecto ningún slide recibe imagen de entrada.
    await _run(_job())
    assert all(g["reference"] == "" for g in mcp["generations"])


async def test_la_direccion_de_arte_va_en_todas_las_imagenes(mcp):
    await _run(_job())
    style = "Warm oat and faded denim palette, low window light, 50mm, fine grain."
    assert all(style in g["prompt"] for g in mcp["generations"])


async def test_las_imagenes_publicadas_son_4_5(mcp):
    job = _job()
    await _run(job)
    for png in job["images"]["bytes"].values():
        assert Image.open(io.BytesIO(png)).size == (1080, 1350)


async def test_la_referencia_sigue_disponible_como_opt_in(mcp):
    # La vía queda cableada por si el catálogo llega a exponer un rol de estilo:
    # encendida, la portada vuelve a viajar en `medias` de cada slide.
    job = _job()
    job["_cfg"] = _cfg(image_reference_slides=True)
    await jr._run_media_phase(job)
    base, slides = mcp["generations"][0], mcp["generations"][1:]
    assert base["reference"] == ""                      # la portada no referencia nada
    assert all(s["reference"] == "job-portada" for s in slides)
    assert len(job["_ig_media_urls"]) == 4



# ── Los otros dos formatos de imagen (misma fase, mismas piezas) ──────────────

async def test_imagen_unica_tambien_sale_4_5_nativa(mcp):
    job = _job(formato="imagen-unica", formato_instagram="imagen-unica")
    await _run(job)
    # Una sola generación compartida por las redes, pedida ya en vertical.
    assert len(mcp["generations"]) == 1
    assert mcp["generations"][0]["aspect_ratio"] == "4:5"
    assert sorted(job["images"]["bytes"]) == ["ig-single", "li-hook"]
    for png in job["images"]["bytes"].values():
        assert Image.open(io.BytesIO(png)).size == (1080, 1350)


async def test_la_historia_sigue_pidiendo_9_16(mcp):
    # El vertical de historia ya se pedía nativo: la tabla de capacidades no puede
    # habérselo comido al introducir el 4:5 del feed.
    job = _job(redes=["instagram"], formato="historia", formato_instagram="imagen-unica",
               tipo_post="historia", historia_formato="imagen")
    await _run(job)
    assert [g["aspect_ratio"] for g in mcp["generations"]] == ["9:16"]
    assert Image.open(io.BytesIO(job["images"]["bytes"]["ig-story"])).size == (1080, 1920)
