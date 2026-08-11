"""Fase de imágenes con el TEXTO DENTRO DEL PROMPT (sin overlay posterior).

Espejo de `test_carousel_media_phase.py` para el camino nuevo: el texto de la pieza
ya no se dibuja con Pillow al final, viaja en el prompt y lo renderiza Higgsfield.
Aquí se comprueba que está cableado de punta a punta en `_run_media_phase` —el
núcleo compartido por el individual y el bulk—: prompt de 9 secciones por imagen,
el texto de cada slide dentro del suyo, el QA de visión reintentando cuando el
render sale mal escrito, y el respaldo de Pillow apagado.
"""

import pytest

import job_runner as jr
import prompt_architect as parch
import image_text_qa as iqa
from config import Config
from scripts import higgsfield_mcp as hfmcp

ov = pytest.importorskip("image_overlay")
Image = pytest.importorskip("PIL.Image")

_HOOK = "China entra en la liga alta de la IA"


def _cfg(**overrides) -> Config:
    # Con key de Anthropic: el QA de visión necesita un modelo de visión disponible.
    return Config(anthropic_api_key="test-key", perplexity_api_key="", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key", **overrides)


@pytest.fixture(autouse=True)
def arquitecto_determinista(monkeypatch):
    """El arquitecto no llama al LLM en los tests: mismo prompt, sin red."""
    monkeypatch.setattr(parch.llm_json, "disponible", lambda cfg: False)


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    """MCP + Blotato simulados; registra cada generación con su prompt."""
    calls = {"generations": [], "uploads": []}

    def _png(i: int) -> str:
        p = tmp_path / f"gen-{i}.png"
        Image.new("RGB", (1080, 1350), (40 + i * 20, 90, 140)).save(p)
        return str(p)

    def generate_image_job(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["generations"].append({"prompt": prompt, "kind": "base", "reference": reference})
        return {"job_id": "job-portada", "url": _png(0)}

    def submit_image(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["generations"].append({"prompt": prompt, "kind": "slide", "reference": reference})
        return {"job_id": f"job-{len(calls['generations'])}", "_src": _png(len(calls["generations"]))}

    import image_provider as improv
    monkeypatch.setattr(jr, "_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(improv.hfmcp, "generate_image_job", generate_image_job)
    monkeypatch.setattr(improv.hfmcp, "submit_image", submit_image)
    monkeypatch.setattr(improv.hfmcp, "poll_image", lambda handle, **kw: handle["_src"])
    monkeypatch.setattr(improv.hfmcp, "is_configured", lambda: True)
    monkeypatch.setattr(jr.bc, "upload_media_local",
                        lambda data, filename, **kw: f"https://blotato.test/{filename}")
    return calls


@pytest.fixture
def vision(monkeypatch):
    """QA de visión simulado. `estado["lee"]` decide qué texto ve en cada imagen.

    `esperado` llega como los bloques de la pieza (`[(clave, texto)]`), que es lo que
    permite exigir el titular exacto y el cuerpo por similitud. El doble los une para
    seguir hablando en strings, que es lo que a estos tests les interesa.
    """
    estado = {"lee": None, "llamadas": []}

    def _verificar(src, esperado, *, cfg):
        texto = (" ".join(t for _, t in esperado)
                 if isinstance(esperado, (list, tuple)) else str(esperado))
        estado["llamadas"].append(texto)
        visto = estado["lee"](texto, len(estado["llamadas"])) if estado["lee"] else texto
        if iqa.coincide(texto, visto):
            return iqa.ResultadoQA(ok=True, verificado=True, texto_visto=visto, motivo="texto correcto")
        return iqa.ResultadoQA(ok=False, verificado=True, texto_visto=visto,
                               motivo=f'esperaba "{texto}" y la imagen dice "{visto}"')

    monkeypatch.setattr(jr.iqa, "verificar", _verificar)
    return estado


def _job(**params) -> dict:
    job = jr.make_job(
        _cfg(),
        {"redes": ["instagram", "linkedin"], "formato": "carrusel",
         "formato_instagram": "carrusel", "carrusel_slides": 4, "tipo_post": "post",
         # Sistema fijado: `make_job` elige uno del repertorio de la casa y estos tests
         # comprueban el cableado de la fase, no el reparto de bloques (eso es
         # `test_prompt_architect`). Con uno al azar el prompt esperado cambiaría.
         "sistema_texto": "titular",
         "fuente_imagen": "higgsfield", **params},
    )
    job["content"] = {"title": "La IA en China", "channel": "Canal QBYK", "transcript": "x" * 200}
    job["posts"] = {
        "instagram_text": "Un caption.", "linkedin_text": "Otro caption.",
        "image_prompt": "A stack of decommissioned server blades on a steel workbench.",
        "image_style": "Ink black and burnt orange palette, low raking dusk light, 35mm, fine grain.",
        "image_slide_prompts": ["Scene A.", "Scene B.", "Scene C."],
        "image_text": {"hook": _HOOK,
                       "slides": ["Modelos abiertos", "Costes a la baja", "Talento que vuelve"]},
    }
    return job


def _prompt(job: dict, subkey: str) -> str:
    return job["images"]["prompts"][subkey]


def _secciones_completas(prompt: str) -> bool:
    lineas = prompt.splitlines()
    if len(lineas) != 9:
        return False
    return all(lineas[i - 1].startswith(f"{i}. {sec['etiqueta']}:")
               for i, sec in enumerate(parch._secciones_cfg(), 1))


# ── El sistema de texto, de punta a punta por la fase real ───────────────────

_SLIDES_CUERPO = [
    {"titular": "Modelos abiertos", "cuerpo": "Los pesos se publican y cualquiera "
                                              "puede afinarlos sin pedir permiso."},
    {"titular": "Costes a la baja", "cuerpo": "El precio por millón de tokens cayó "
                                              "un orden de magnitud en un año."},
    {"titular": "Talento que vuelve", "cuerpo": "Los equipos que se habían ido a "
                                                "California están montando laboratorio en casa."},
]


async def test_un_carrusel_con_cuerpo_imprime_los_dos_bloques(mcp, vision):
    """El defecto que arreglan los sistemas: un slide solo sabía imprimir un titular.

    Se ejercita por `_run_media_phase` —el núcleo compartido por los dos flujos— y no
    llamando al arquitecto a mano: lo que hay que blindar es el CABLEADO, que es donde
    un bloque se pierde sin dar error.
    """
    job = _job(sistema_texto="titular_cuerpo")
    job["posts"]["image_text"]["slides"] = _SLIDES_CUERPO
    await jr._run_media_phase(job)

    slide = _prompt(job, "ig-1")
    assert 'HEADLINE "MODELOS ABIERTOS"' in slide
    assert 'BODY "Los pesos se publican' in slide
    # El cuerpo conserva su caja aunque la identidad de la casa sea de caja alta.
    assert "LOS PESOS SE PUBLICAN" not in slide
    # Y el QA compara los DOS bloques: si solo mirara el titular, un cuerpo que el
    # modelo no imprimió pasaría el control.
    assert any("Los pesos se publican" in llamada for llamada in vision["llamadas"])


async def test_la_portada_no_lleva_cuerpo_aunque_el_job_use_ese_sistema(mcp, vision):
    # La portada es la pieza que funda el set: mantiene su lockup de siempre, y se
    # decide en un único sitio (`normalizar_spec`) para que ningún camino la cambie.
    job = _job(sistema_texto="etiqueta_titular_cuerpo")
    job["posts"]["image_text"]["slides"] = _SLIDES_CUERPO
    await jr._run_media_phase(job)
    portada = _prompt(job, "cover")
    assert "BODY" not in portada and "LABEL" not in portada
    assert "HEADLINE" in portada


# ── El texto viaja en el prompt ───────────────────────────────────────────────

async def test_cada_imagen_tiene_su_prompt_de_nueve_secciones(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    prompts = job["images"]["prompts"]
    # Portada + 3 slides de info (el último ya no es el de créditos).
    assert sorted(prompts) == ["cover", "ig-1", "ig-2", "ig-3"]
    assert all(_secciones_completas(p) for p in prompts.values())


async def test_la_portada_lleva_el_hook_literal(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    prompt = _prompt(job, "cover")
    # 9 palabras: se reparte en titular + kicker, y las dos partes van entrecomilladas.
    # En la caja que declara la identidad (`brand.json` pide ALL CAPS): el texto no
    # cambia ni un carácter, solo se cita en la caja en la que se va a imprimir.
    titular, kicker = parch.dividir_texto(_HOOK)
    assert f'HEADLINE "{titular.upper()}"' in prompt
    assert f'SECOND LINE "{kicker.upper()}"' in prompt
    assert f"{titular} {kicker}" == _HOOK
    # El kicker va anclado al pie, no debajo del titular: es el lockup de póster.
    assert "bottom band" in prompt


async def test_cada_slide_lleva_su_propia_idea(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    assert 'HEADLINE "MODELOS ABIERTOS"' in _prompt(job, "ig-1")
    assert 'HEADLINE "COSTES A LA BAJA"' in _prompt(job, "ig-2")


async def test_el_ultimo_slide_es_informativo_y_no_lleva_creditos(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    ultimo = _prompt(job, "ig-3")
    assert 'HEADLINE "TALENTO QUE VUELVE"' in ultimo
    # Ni atribución del canal ni pieza de cierre: es un slide de contenido más.
    assert "Canal QBYK" not in ultimo
    assert "closing slide" not in ultimo
    assert "content slide" in ultimo


async def test_el_prompt_pide_aire_negativo_y_no_pide_sin_texto(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    for prompt in job["images"]["prompts"].values():
        assert "clear zone" in prompt or "negative space" in prompt
        assert jr._NO_TEXT_SUFFIX not in prompt        # ya no se pide "sin texto"


async def test_la_direccion_de_arte_sigue_yendo_en_todas(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    estilo = "Ink black and burnt orange palette, low raking dusk light, 35mm, fine grain."
    assert all(estilo in g["prompt"] for g in mcp["generations"])


async def test_el_prompt_cabe_en_lo_que_acepta_higgsfield(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    for prompt in job["images"]["prompts"].values():
        assert len(prompt) <= hfmcp._MAX_PROMPT_CHARS     # el cliente trunca ahí


# ── Nadie dibuja texto después de generar ─────────────────────────────────────

async def test_el_texto_lo_pone_el_modelo_y_el_preview_lo_sabe(mcp, vision):
    # `image_overlay` ya solo recorta: no tiene con qué imprimir texto encima.
    assert not hasattr(ov, "render_hook") and not hasattr(ov, "render_info")
    job = _job()
    await jr._run_media_phase(job)
    assert job["images"]["text_overlay"] is True
    assert job["images"]["text_in_prompt"] is True


async def test_las_imagenes_publicadas_siguen_saliendo_4_5(mcp, vision):
    import io
    job = _job()
    await jr._run_media_phase(job)
    assert len(job["_ig_media_urls"]) == 4
    for png in job["images"]["bytes"].values():
        assert Image.open(io.BytesIO(png)).size == (1080, 1350)


# ── QA de visión + reintentos ─────────────────────────────────────────────────

async def test_si_el_texto_sale_bien_no_se_reintenta(mcp, vision):
    job = _job()
    await jr._run_media_phase(job)
    assert len(mcp["generations"]) == 4
    assert all(len(reg) == 1 and reg[0]["ok"] for reg in job["images"]["qa"].values())


async def test_un_texto_mal_renderizado_se_reintenta_y_queda_logueado(mcp, vision):
    # La primera lectura de la portada devuelve el hook sin tilde; la segunda, bien.
    vision["lee"] = lambda esperado, n: "China entra en la ligua alta de la IA" if n == 1 else esperado
    job = _job()
    await jr._run_media_phase(job)
    registro = job["images"]["qa"]["cover"]
    assert len(registro) == 2
    assert registro[0]["ok"] is False and registro[1]["ok"] is True
    assert "ligua" in registro[0]["texto_visto"]
    # Una generación extra: la portada se rehizo (antes de encolar los slides, que
    # se generan mirando la portada BUENA).
    assert len(mcp["generations"]) == 5
    assert mcp["generations"][1]["kind"] == "base"
    # El reintento pide el texto con la instrucción reforzada.
    assert "PRIMARY REQUIREMENT" in mcp["generations"][1]["prompt"]


async def test_el_reintento_para_en_el_maximo_y_avisa(mcp, vision):
    vision["lee"] = lambda esperado, n: "otra cosa"     # nunca sale bien
    job = _job()
    await jr._run_media_phase(job)
    maximo = iqa.max_intentos()
    assert len(job["images"]["qa"]["cover"]) == maximo + 1
    assert all(not i["ok"] for i in job["images"]["qa"]["cover"])
    # Sigue habiendo carrusel publicable: el texto mal escrito no aborta el job.
    assert len(job["_ig_media_urls"]) == 4
    assert job["status"] == "review"


async def test_un_slide_mal_escrito_se_rehace_solo_el(mcp, vision):
    vision["lee"] = lambda esperado, n: "" if esperado == "Costes a la baja" and n == 3 else esperado
    job = _job()
    await jr._run_media_phase(job)
    assert len(job["images"]["qa"]["ig-2"]) == 2
    assert len(job["images"]["qa"]["cover"]) == 1
    assert len(mcp["generations"]) == 5


async def test_el_qa_se_puede_apagar(mcp, vision):
    job = _job()
    job["_cfg"] = _cfg(image_text_qa=False)
    await jr._run_media_phase(job)
    assert job["images"]["qa"] == {}
    assert vision["llamadas"] == []
    assert len(mcp["generations"]) == 4


# ── Interruptor: volver al prompt clásico sin texto ───────────────────────────

async def test_apagar_el_texto_en_el_prompt_vuelve_al_camino_clasico(mcp, vision):
    job = _job()
    job["_cfg"] = _cfg(image_text_in_prompt=False)
    await jr._run_media_phase(job)
    # Sin arquitecto: prompt de una sola frase, con el "sin texto" de siempre.
    for g in mcp["generations"]:
        assert jr._NO_TEXT_SUFFIX in g["prompt"]
        assert "TEXT TO RENDER" not in g["prompt"]
    assert job["images"]["qa"] == {}
    assert len(job["_ig_media_urls"]) == 4


async def test_imagen_unica_tambien_lleva_el_texto_en_el_prompt(mcp, vision):
    job = _job(formato="imagen-unica", formato_instagram="imagen-unica")
    await jr._run_media_phase(job)
    assert len(mcp["generations"]) == 1
    prompt = _prompt(job, "cover")
    assert _secciones_completas(prompt)
    assert parch.dividir_texto(_HOOK)[0].upper() in prompt


async def test_la_historia_vertical_tambien(mcp, vision):
    job = _job(redes=["instagram"], formato="historia", formato_instagram="imagen-unica",
               tipo_post="historia", historia_formato="imagen")
    await jr._run_media_phase(job)
    prompt = _prompt(job, "ig-story")
    assert _secciones_completas(prompt)
    assert "9:16" in prompt.splitlines()[0]
    assert parch.dividir_texto(_HOOK)[0].upper() in prompt
