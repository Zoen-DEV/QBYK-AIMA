"""Rehacer UNA imagen desde la revisión, sin tocar el resto del set.

La compuerta de revisión sirve para descartar lo que salió mal, pero hasta ahora la
unidad de reintento era el post entero: un slide feo costaba volver a generar todo.
Acá se ejercita `regenerate_image` sobre un job ya generado (el mismo núcleo que
comparten el individual y el bulk) para comprobar lo que importa: que solo cambia
la imagen pedida, que dice exactamente lo mismo que decía, que sigue mirando a la
portada y que el juego queda otra vez subido y publicable.
"""
import pytest

import job_runner as jr
from config import Config

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")


def _cfg(**overrides) -> Config:
    return Config(anthropic_api_key="", perplexity_api_key="", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key", **overrides)


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    """MCP + Blotato simulados. Cada portada trae un job_id distinto, para poder ver
    si la referencia visual del set se actualiza al rehacerla."""
    calls = {"generations": [], "uploads": []}

    def _png(i: int) -> str:
        p = tmp_path / f"gen-{i}.png"
        img = Image.new("RGB", (1080, 1350), (40 + i * 25, 90, 140))
        px = img.load()
        for y in range(0, 1350, 3):      # textura: desviación > 0
            for x in range(0, 1080, 3):
                px[x, y] = (200, 200, 200)
        img.save(p)
        return str(p)

    def _registrar(kind, prompt, aspect_ratio, reference):
        calls["generations"].append({"kind": kind, "prompt": prompt,
                                     "aspect_ratio": aspect_ratio, "reference": reference})
        return len(calls["generations"])

    def generate_image_job(prompt, *, aspect_ratio="1:1", model="", reference=""):
        n = _registrar("base", prompt, aspect_ratio, reference)
        return {"job_id": f"portada-{n}", "url": _png(n)}

    def submit_image(prompt, *, aspect_ratio="1:1", model="", reference=""):
        n = _registrar("slide", prompt, aspect_ratio, reference)
        return {"job_id": f"slide-{n}", "_src": _png(n)}

    def poll_image(handle, **kw):
        return handle["_src"]

    subidas = 0

    def upload_media_local(data, filename, *, api_key="", mime=""):
        # Contador propio (no `len(uploads)`): los tests vacían la lista para mirar
        # solo lo que subió la regeneración, y las URLs tienen que seguir siendo nuevas.
        nonlocal subidas
        subidas += 1
        calls["uploads"].append(filename)
        return f"https://blotato.test/{filename}-{subidas}"

    import image_provider as improv
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
        "facebook_text": "Un tercero.",
        "image_prompt": "A chipped ceramic jar of coins on a kitchen windowsill.",
        "image_style": "Warm oat and faded denim palette, low window light, 50mm, fine grain.",
        "image_slide_prompts": ["Scene A.", "Scene B.", "Scene C."],
        "image_text": {"hook": "Un hook", "slides": ["Idea uno", "Idea dos", "Idea tres"]},
    }
    return job


async def _generado(**params) -> dict:
    """Un job que ya pasó por la fase de imágenes (el estado real de la revisión)."""
    job = _job(**params)
    await jr._run_media_phase(job)
    assert job["status"] == "review"
    return job


# ── Qué se puede rehacer ─────────────────────────────────────────────────────


async def test_el_carrusel_ofrece_todos_sus_slides(mcp):
    job = await _generado()
    assert jr.subkeys_regenerables(job) == ["ig-0", "ig-1", "ig-2", "ig-3"]


async def test_la_imagen_unica_ofrece_una_por_red(mcp):
    job = await _generado(formato="imagen-unica", formato_instagram="imagen-unica")
    assert jr.subkeys_regenerables(job) == ["li-hook", "ig-single"]


async def test_el_reel_no_ofrece_ninguna(mcp):
    # En video la unidad de reintento no es una imagen: no hay nada que rehacer acá.
    job = _job(formato="reel", tipo_post="reel")
    assert jr.subkeys_regenerables(job) == []


async def test_el_medio_subido_por_el_usuario_no_se_rehace(mcp):
    job = _job(media_origin="subir")
    assert jr.subkeys_regenerables(job) == []


async def test_un_subkey_que_no_es_de_este_post_se_rechaza(mcp):
    job = await _generado()
    with pytest.raises(ValueError):
        await jr.regenerate_image(job, "ig-9")


# ── Rehacer un slide ─────────────────────────────────────────────────────────


async def test_rehacer_un_slide_solo_cambia_ese_slide(mcp):
    job = await _generado()
    antes = dict(job["images"]["bytes"])
    generadas = len(mcp["generations"])

    res = await jr.regenerate_image(job, "ig-2")

    assert res["subkeys"] == ["ig-2"]
    assert len(mcp["generations"]) == generadas + 1     # una sola generación más
    ahora = job["images"]["bytes"]
    assert ahora["ig-2"] != antes["ig-2"]
    assert {k: v for k, v in ahora.items() if k != "ig-2"} == \
           {k: v for k, v in antes.items() if k != "ig-2"}


async def test_el_slide_rehecho_se_genera_como_los_demas(mcp):
    # Rehacer tiene que repetir las condiciones de la primera tirada, y esas ya no
    # incluyen pasar la portada en `medias` (era image-to-image: devolvía la portada
    # re-encuadrada). Lo que sí se conserva es el aspecto nativo.
    job = await _generado()
    await jr.regenerate_image(job, "ig-2")
    ultima = mcp["generations"][-1]
    assert ultima["reference"] == ""
    assert ultima["aspect_ratio"] == "4:5"


async def test_el_slide_rehecho_dice_exactamente_lo_mismo(mcp):
    # El texto y la escena salen de las mismas funciones que la primera vez: rehacer
    # una imagen cambia la tirada del modelo, no lo que la pieza cuenta.
    job = await _generado()
    prompt_original = job["images"]["prompts"]["ig-2"]
    await jr.regenerate_image(job, "ig-2")
    assert job["images"]["prompts"]["ig-2"] == prompt_original
    assert "Scene B." in prompt_original


async def test_rehacer_deja_el_juego_otra_vez_publicable(mcp):
    job = await _generado()
    urls_antes = list(job["_ig_media_urls"])
    mcp["uploads"].clear()

    await jr.regenerate_image(job, "ig-1")

    # Se vuelve a subir el juego COMPLETO (Blotato guarda una URL por archivo).
    assert mcp["uploads"] == ["ig-0.png", "ig-1.png", "ig-2.png", "ig-3.png"]
    assert len(job["_ig_media_urls"]) == 4
    assert job["_ig_media_urls"] != urls_antes
    assert job["_ig_media_urls"] == job["_li_media_urls"]
    assert job["images"]["blotato_urls"]["instagram"] == job["_ig_media_urls"]


async def test_el_slide_rehecho_conserva_el_formato_de_publicacion(mcp):
    import io
    job = await _generado()
    await jr.regenerate_image(job, "ig-3")
    assert Image.open(io.BytesIO(job["images"]["bytes"]["ig-3"])).size == (1080, 1350)


# ── Rehacer la portada ───────────────────────────────────────────────────────


async def test_la_portada_nueva_pasa_a_ser_la_referencia_del_set(mcp):
    # `images.reference` sigue siendo el job_id de la portada VIGENTE. Hoy no se pasa
    # a nadie (ver `image_reference_slides`), pero rehacer la portada tiene que
    # actualizarlo igual: es la traza de qué generación es la portada buena, y lo que
    # se volvería a usar si el catálogo llegara a exponer un rol de estilo.
    job = await _generado()
    assert job["images"]["reference"] == "portada-1"

    await jr.regenerate_image(job, "ig-0")
    assert job["images"]["reference"] == "portada-5"   # 4 de la generación + esta


async def test_rehacer_la_portada_cambia_la_imagen_de_todas_las_redes(mcp):
    # En imagen única las tres redes son la MISMA base con recortes distintos: no
    # tendría sentido dejar a LinkedIn con una foto y a Instagram con otra.
    job = await _generado(redes=["instagram", "linkedin", "facebook"],
                          formato="imagen-unica", formato_instagram="imagen-unica")
    antes = dict(job["images"]["bytes"])

    res = await jr.regenerate_image(job, "li-hook")

    assert sorted(res["subkeys"]) == ["fb-hook", "ig-single", "li-hook"]
    assert all(job["images"]["bytes"][k] != antes[k] for k in antes)
    assert job["images"]["blotato_urls"]["linkedin"]
    assert job["images"]["blotato_urls"]["facebook"]
    assert len(job["images"]["blotato_urls"]["instagram"]) == 1


# ── Historia (imagen vertical única) ─────────────────────────────────────────


async def test_la_historia_se_rehace_en_9_16(mcp):
    import io
    job = await _generado(redes=["instagram", "facebook"], formato="historia",
                          formato_instagram="imagen-unica", tipo_post="historia",
                          historia_formato="imagen")
    antes = job["images"]["bytes"]["ig-story"]

    res = await jr.regenerate_image(job, "ig-story")

    assert res["subkeys"] == ["ig-story"]
    assert mcp["generations"][-1]["aspect_ratio"] == "9:16"
    assert job["images"]["bytes"]["ig-story"] != antes
    assert Image.open(io.BytesIO(job["images"]["bytes"]["ig-story"])).size == (1080, 1920)
    # Y queda subida y compartida por las dos redes de la historia.
    assert job["_ig_media_urls"] == job["_fb_media_urls"]
    assert job["images"]["blotato_urls"]["facebook"] == job["_fb_media_urls"][0]


# ── Degradaciones (rehacer nunca puede tumbar el post) ───────────────────────


async def test_si_higgsfield_falla_se_avisa_y_se_sigue(mcp, monkeypatch):
    job = await _generado()
    import image_provider as improv

    def _revienta(*a, **kw):
        raise RuntimeError("not_enough_credits")

    monkeypatch.setattr(improv.hfmcp, "generate_image_job", _revienta)
    res = await jr.regenerate_image(job, "ig-2")

    # Cae a la plantilla local, lo dice, y el juego sigue publicable.
    assert "Higgsfield no disponible" in res["aviso"]
    assert res["subkeys"] == ["ig-2"]
    assert len(job["_ig_media_urls"]) == 4


async def test_el_tracking_de_costos_cuenta_la_regeneracion(mcp, monkeypatch):
    eventos = []

    async def _record_event(**kw):
        eventos.append(kw)

    monkeypatch.setattr(jr.cost_tracker, "record_event", _record_event)
    job = await _generado()
    eventos.clear()

    await jr.regenerate_image(job, "ig-2")

    imagenes = [e for e in eventos if e["operation"] == "image_generation"]
    assert imagenes and imagenes[0]["units"] == {"generations": 1}
