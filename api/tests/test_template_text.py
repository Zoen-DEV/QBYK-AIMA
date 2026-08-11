"""La plantilla de respaldo también dice lo que la pieza tenía que decir.

El texto de la imagen lo imprime el modelo desde el prompt. Cuando la generación no
ocurre —sin token OAuth, Higgsfield caído, o `fuente_imagen=template`— la pieza salía
de un PNG de respaldo **mudo**: el post se publicaba con una foto genérica donde iba
el titular. Acá se ejercita el arreglo sobre `_run_media_phase` y `regenerate_image`
(el núcleo que comparten el individual y el bulk): la plantilla se dibuja con el mismo
copy, partido igual y con la misma notación que se le pide al modelo, y —lo que hay
que blindar— una imagen GENERADA nunca se sobreimprime encima.
"""

import pytest

import job_runner as jr
import prompt_architect as parch
from config import Config

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")

import image_provider as improv

_HOOK = "China entra en la liga alta de la IA"
_SLIDES = ["Modelos abiertos", "Costes a la baja", "Talento que vuelve"]


def _cfg(**overrides) -> Config:
    # Sin keys: ni el arquitecto ni el QA de visión llaman a nadie en estos tests.
    return Config(anthropic_api_key="", perplexity_api_key="", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key", **overrides)


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """Blotato simulado + salida en tmp. `entorno["falla"]=True` tumba a Higgsfield."""
    estado = {"falla": False, "uploads": []}

    def _png(i: int) -> str:
        p = tmp_path / f"gen-{i}.png"
        Image.new("RGB", (1080, 1350), (40 + i * 25, 90, 140)).save(p)
        return str(p)

    def generate_image_job(prompt, *, aspect_ratio="1:1", model="", reference=""):
        if estado["falla"]:
            raise RuntimeError("not_enough_credits")
        return {"job_id": "portada", "url": _png(0)}

    def submit_image(prompt, *, aspect_ratio="1:1", model="", reference=""):
        if estado["falla"]:
            raise RuntimeError("not_enough_credits")
        return {"job_id": "slide", "_src": _png(len(estado["uploads"]) + 1)}

    monkeypatch.setattr(jr, "_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(improv.hfmcp, "generate_image_job", generate_image_job)
    monkeypatch.setattr(improv.hfmcp, "submit_image", submit_image)
    monkeypatch.setattr(improv.hfmcp, "poll_image", lambda handle, **kw: handle["_src"])
    monkeypatch.setattr(improv.hfmcp, "is_configured", lambda: True)

    def upload_media_local(data, filename, **kw):
        estado["uploads"].append(filename)
        return f"https://blotato.test/{filename}"

    monkeypatch.setattr(jr.bc, "upload_media_local", upload_media_local)
    return estado


def _job(cfg=None, **params) -> dict:
    job = jr.make_job(
        cfg or _cfg(),
        {"redes": ["instagram", "linkedin"], "formato": "carrusel",
         "formato_instagram": "carrusel", "carrusel_slides": 4, "tipo_post": "post",
         "fuente_imagen": "higgsfield", **params},
    )
    job["content"] = {"title": "La IA en China", "channel": "Canal QBYK", "transcript": "x" * 200}
    job["posts"] = {
        "instagram_text": "Un caption.", "linkedin_text": "Otro caption.",
        "image_prompt": "A stack of decommissioned server blades on a steel workbench.",
        "image_style": "Ink black and burnt orange palette, low raking dusk light, 35mm.",
        "image_slide_prompts": ["Scene A.", "Scene B.", "Scene C."],
        "image_text": {"hook": _HOOK, "slides": list(_SLIDES)},
    }
    return job


def _plantilla_pelada(rol_idx: int, *, historia: bool = False) -> bytes:
    """La plantilla recortada SIN texto: la referencia contra la que comparar."""
    src = improv._template_file(rol_idx, 1)
    return ov.render_story(src) if historia else ov.render_feed(src)


# ── Qué fuente lleva texto dibujado y cuál no ────────────────────────────────


def test_es_plantilla_distingue_el_respaldo_de_una_imagen_generada(tmp_path):
    generada = tmp_path / "gen-0.png"
    generada.write_bytes(b"x")
    assert improv.es_plantilla(improv._template_file(1, 1)) is True
    assert improv.es_plantilla("https://higgsfield.test/img.png") is False
    # Una salida del proveedor puede ser un archivo local (mocks, backends que
    # descargan a disco): esa YA trae el texto puesto y no se puede sobreimprimir.
    assert improv.es_plantilla(str(generada)) is False
    assert improv.es_plantilla("") is False


def test_el_lockup_solo_sale_para_las_plantillas():
    cfg = _cfg()
    assert jr._lockup_plantilla(cfg, "https://higgsfield.test/x.png",
                                texto=_HOOK, rol="portada") is None
    lock = jr._lockup_plantilla(cfg, improv._template_file(1, 1), texto=_HOOK, rol="portada")
    assert lock is not None
    # Mismo reparto titular/kicker —y la misma caja— que recibe el modelo en su prompt.
    assert _textos(lock) == [b.upper() for b in parch.dividir_texto(_HOOK)]


def _textos(lock: dict) -> list[str]:
    """Los strings que la plantilla va a dibujar, en orden de bloque."""
    return [b["texto"] for b in lock["bloques"]]


def test_la_notacion_del_usuario_nunca_se_imprime():
    """Los `**` del acento y la raya del corte son notación, no contenido."""
    lock = jr._lockup_plantilla(_cfg(), improv._template_file(1, 1),
                                texto="Ecualizar **cambia** todo — y no cuesta nada",
                                rol="contenido")
    assert lock["acento"] == "cambia"
    assert "**" not in "".join(_textos(lock))
    # La raya marca el corte y tampoco se imprime.
    assert _textos(lock) == ["ECUALIZAR CAMBIA TODO", "Y NO CUESTA NADA"]


def test_los_bloques_del_sistema_llegan_a_la_plantilla():
    """La razón de ser de este módulo: la pieza dice lo mismo salga por donde salga.

    Si el respaldo siguiera imprimiendo solo titular + apoyo, un slide de un sistema
    con cuerpo saldría a medias justo cuando el modelo no está disponible.
    """
    lock = jr._lockup_plantilla(
        _cfg(), improv._template_file(1, 1), rol="desarrollo",
        sistema="etiqueta_titular_cuerpo",
        texto={"etiqueta": "02", "titular": "El coste sube",
               "cuerpo": "Cada salto de contexto multiplica el gasto."})
    assert [b["clave"] for b in lock["bloques"]] == ["etiqueta", "titular", "cuerpo"]
    # El cuerpo NO se pasa a caja alta aunque la identidad sea de caja alta: a ese
    # tamaño un párrafo en mayúsculas es ilegible (misma regla que el prompt).
    assert lock["bloques"][1]["texto"] == "EL COSTE SUBE"
    assert lock["bloques"][2]["texto"] == "Cada salto de contexto multiplica el gasto."
    # Y cada bloque llega con su banda resuelta: el overlay no relee la config.
    assert [b["banda"] for b in lock["bloques"]] == ["etiqueta", "alta", "pie"]


def test_la_portada_ignora_el_sistema_tambien_en_la_plantilla():
    # Mismo criterio que `normalizar_spec`: si acá se resolviera distinto, la portada
    # de respaldo llevaría bloques que su versión generada no tiene.
    lock = jr._lockup_plantilla(_cfg(), improv._template_file(1, 1), rol="portada",
                                sistema="etiqueta_titular_cuerpo", texto=_HOOK)
    assert [b["clave"] for b in lock["bloques"]] == ["titular", "apoyo"]


def test_sin_texto_que_decir_no_se_dibuja_nada():
    assert jr._lockup_plantilla(_cfg(), improv._template_file(1, 1),
                                texto="   ", rol="portada") is None


def test_la_caja_la_decide_la_identidad_y_no_el_renderizador():
    """La caja alta dejó de ser una constante del proyecto.

    Mientras todas las identidades eran caja alta, que `image_overlay` forzara
    `.upper()` daba igual. Desde que una identidad puede declararse en caja mixta, esa
    llamada haría que la pieza de respaldo contradijera a la generada — que es
    exactamente lo que este módulo existe para evitar. La decisión se toma una sola
    vez, con la misma función que cita el texto en el prompt.
    """
    caps = {"tipografia": "ultra-condensed heavy grotesque, ALL CAPS, tight tracking"}
    mixta = {"tipografia": "high-contrast Didone display serif, mixed case, tight tracking"}
    plantilla = improv._template_file(1, 1)
    titular = parch.dividir_texto(_HOOK)[0]
    assert _textos(jr._lockup_plantilla(_cfg(), plantilla, texto=_HOOK, rol="portada",
                                        identidad=caps))[0] == titular.upper()
    assert _textos(jr._lockup_plantilla(_cfg(), plantilla, texto=_HOOK, rol="portada",
                                        identidad=mixta))[0] == titular


def test_una_identidad_en_caja_mixta_no_se_dibuja_en_mayusculas():
    # El otro extremo del mismo contrato: que el flag llegue hasta el píxel.
    base = Image.new("RGB", (400, 500), (20, 20, 20))
    lock = {"titular": "El acceso", "kicker": "", "acento": "", "rol": "portada",
            "color_texto": "#EDEAE0", "color_acento": "#C9F227"}
    mixta = ov._dibujar_texto(base.copy(), dict(lock, caja_alta=False))
    alta = ov._dibujar_texto(base.copy(), dict(lock, caja_alta=True))
    assert mixta.tobytes() != alta.tobytes()
    # Sin el campo se comporta como siempre: caja alta.
    assert ov._dibujar_texto(base.copy(), lock).tobytes() == alta.tobytes()


# ── La fase de imágenes (los dos flujos) ─────────────────────────────────────


async def test_el_carrusel_caido_a_plantilla_sale_con_su_texto(entorno):
    entorno["falla"] = True
    job = _job()
    await jr._run_media_phase(job)

    imagenes = job["images"]["bytes"]
    assert sorted(imagenes) == ["ig-0", "ig-1", "ig-2", "ig-3"]
    assert imagenes["ig-0"] != _plantilla_pelada(1)        # la portada dice el hook
    for key in ("ig-1", "ig-2", "ig-3"):
        assert imagenes[key] != _plantilla_pelada(2)
    # Cada slide dice una idea distinta: mismo PNG de fondo, distinto texto encima.
    assert len({imagenes[k] for k in ("ig-1", "ig-2", "ig-3")}) == 3
    # Y sigue siendo un carrusel publicable con el aspecto de siempre.
    assert len(job["_ig_media_urls"]) == 4
    assert all(Image.open(__import__("io").BytesIO(p)).size == (1080, 1350)
               for p in imagenes.values())


async def test_la_imagen_generada_no_se_sobreimprime(entorno):
    """El texto ya lo puso el modelo: dibujarlo otra vez lo duplicaría.

    Con el grade apagado, la imagen publicada tiene que ser el recorte pelado de lo
    que devolvió el proveedor, byte a byte.
    """
    job = _job(_cfg(image_grade_match=False))
    await jr._run_media_phase(job)
    for key, src in job["images"]["raw_urls"].items():
        assert job["images"]["bytes"][key] == ov.render_feed(src)


async def test_elegir_plantillas_a_mano_tambien_dibuja_el_texto(entorno):
    job = _job(fuente_imagen="template")
    await jr._run_media_phase(job)
    assert job["images"]["provider"] == "template"
    assert job["images"]["bytes"]["ig-0"] != _plantilla_pelada(1)


async def test_el_bulk_hereda_lo_mismo(entorno):
    """Mismo núcleo: una fila del lote cae a plantilla y sale igual de escrita."""
    entorno["falla"] = True
    job = jr.make_job(
        _cfg(),
        {"redes": ["instagram"], "formato": "imagen-unica", "formato_instagram": "imagen-unica",
         "tipo_post": "post", "fuente_imagen": "higgsfield"},
        flow="bulk", batch_id="lote-1",
    )
    job["content"] = {"title": "La IA en China", "transcript": "x" * 200}
    job["posts"] = {"instagram_text": "Un caption.", "image_prompt": "A steel workbench.",
                    "image_text": {"hook": _HOOK, "slides": []}}
    await jr._run_media_phase(job)
    assert job["images"]["bytes"]["ig-single"] != _plantilla_pelada(1)


async def test_la_historia_vertical_tambien(entorno):
    entorno["falla"] = True
    job = _job(redes=["instagram"], formato="historia", formato_instagram="imagen-unica",
               tipo_post="historia", historia_formato="imagen")
    await jr._run_media_phase(job)
    png = job["images"]["bytes"]["ig-story"]
    assert Image.open(__import__("io").BytesIO(png)).size == (1080, 1920)
    assert png != _plantilla_pelada(1, historia=True)


async def test_apagar_el_texto_deja_la_plantilla_muda(entorno):
    """El interruptor es uno solo: si la pieza no lleva texto, no lo lleva por
    ningún camino (ni el del modelo ni el de la plantilla)."""
    entorno["falla"] = True
    job = _job(_cfg(image_text_in_prompt=False))
    await jr._run_media_phase(job)
    assert job["images"]["bytes"]["ig-0"] == _plantilla_pelada(1)


# ── Rehacer una imagen suelta ────────────────────────────────────────────────


async def test_rehacer_un_slide_caido_a_plantilla_lo_dibuja(entorno):
    job = _job()
    await jr._run_media_phase(job)
    entorno["falla"] = True

    res = await jr.regenerate_image(job, "ig-2")

    assert res["subkeys"] == ["ig-2"]
    assert "Higgsfield no disponible" in res["aviso"]
    png = job["images"]["bytes"]["ig-2"]
    assert png != _plantilla_pelada(2)
    # Dice lo mismo que decía: rehacer no puede cambiar el copy del slide.
    esperado = jr._lockup_plantilla(job["_cfg"], improv._template_file(2, 1),
                                    texto=_SLIDES[1], rol="contenido")
    assert _textos(esperado)[0].lower().startswith("costes")
