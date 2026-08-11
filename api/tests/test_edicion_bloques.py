"""El contrato entre las dos compuertas previas y el backend: los campos por bloque.

Las dos UI —el preview del individual y el editor por fila del lote— dibujan los campos
de un slide desde `needs.sistema_texto` y los mandan como `image_slide_{bloque}_{i}`.
Ese ida y vuelta es donde un bloque se pierde sin dar error: el formulario manda tres
campos, el backend guarda dos y la tercera parte de la pieza sale en blanco.

Se prueba el recorrido entero (form crudo → `_campos_indexados` → `_aplicar_edicion`)
y no cada función suelta, porque el defecto vive en las costuras.
"""

import app
import job_runner as jr
import prompt_architect as parch
from config import Config

_CARRUSEL = {
    "redes": ["instagram"], "tipo_post": "post",
    "formato_instagram": "carrusel", "carrusel_slides": 4,   # portada + 3 de info
}


def _cfg() -> Config:
    return Config(anthropic_api_key="", perplexity_api_key="k", linkedin_account_id="",
                  instagram_account_id="", blotato_api_key="test-key")


def _job(sistema: str) -> dict:
    j = jr.make_job(_cfg(), dict(_CARRUSEL, sistema_texto=sistema))
    j["posts"] = {"instagram_text": "caption", "image_text": {"hook": "Hook", "slides": []}}
    return j


def _editar(job: dict, form: dict) -> dict:
    """Lo que hace `POST /jobs/{id}/edit` con el form crudo que manda la UI."""
    campos = {k: str(form[k]) for k in app._CAMPOS_EDICION if k in form}
    campos.update(app._campos_indexados(form))
    return app._aplicar_edicion(job["posts"], campos)


# ── El sistema decide qué campos pinta la UI ─────────────────────────────────

def test_needs_lleva_el_sistema_congelado_y_sus_bloques():
    needs = app._needs_job(_job("etiqueta_titular_cuerpo"))
    claves = [b["clave"] for b in needs["sistema_texto"]["bloques"]]
    assert claves == ["etiqueta", "titular", "cuerpo"]
    # Con el presupuesto de palabras: la UI lo muestra y el lint mide contra él.
    assert all(b["palabras"][1] > 0 for b in needs["sistema_texto"]["bloques"])


def test_un_job_sin_carrusel_no_pide_sistema():
    j = jr.make_job(_cfg(), {"redes": ["instagram"], "formato_instagram": "imagen-unica"})
    assert app._needs_job(j)["sistema_texto"] is None


# ── El ida y vuelta del formulario ───────────────────────────────────────────

def test_los_tres_bloques_de_un_slide_sobreviven_al_guardado():
    job = _job("etiqueta_titular_cuerpo")
    posts = _editar(job, {
        "image_hook": "El coste de la IA",
        "image_slide_etiqueta_0": "01", "image_slide_titular_0": "El coste sube",
        "image_slide_cuerpo_0": "Cada salto de contexto multiplica el gasto por token.",
    })
    assert posts["image_text"]["slides"][0] == {
        "etiqueta": "01", "titular": "El coste sube",
        "cuerpo": "Cada salto de contexto multiplica el gasto por token.",
    }


def test_un_slide_con_solo_titular_se_guarda_como_string():
    # El contrato viejo sigue siendo el de un sistema de un bloque: una pantalla que no
    # conozca los sistemas y una que sí tienen que guardar exactamente lo mismo.
    job = _job("titular")
    posts = _editar(job, {"image_slide_titular_0": "Una idea"})
    assert posts["image_text"]["slides"] == ["Una idea"]


def test_el_campo_historico_sigue_valiendo_como_titular():
    job = _job("titular_cuerpo")
    posts = _editar(job, {"image_slide_text_0": "Una idea", "image_slide_cuerpo_0": "Y su porqué."})
    assert posts["image_text"]["slides"][0] == {"titular": "Una idea", "cuerpo": "Y su porqué."}


def test_vaciar_un_bloque_deja_su_hueco_y_no_corre_al_siguiente():
    """La razón de que los campos vayan INDEXADOS. Si el slide 2 desapareciera al
    vaciarlo, el 3 pasaría a generarse con el texto del 2."""
    job = _job("titular")
    posts = _editar(job, {"image_slide_titular_0": "Uno", "image_slide_titular_1": "",
                          "image_slide_titular_2": "Tres"})
    assert posts["image_text"]["slides"] == ["Uno", "", "Tres"]


def test_un_bloque_inventado_no_se_guarda():
    # La UI pinta desde el catálogo, pero el endpoint es público: una clave que ningún
    # sistema imprime no puede acabar en el job.
    job = _job("titular")
    posts = _editar(job, {"image_slide_titular_0": "Uno", "image_slide_inventado_0": "x"})
    assert posts["image_text"]["slides"] == ["Uno"]


def test_lo_editado_a_mano_llega_al_prompt_con_su_bloque():
    """El otro extremo: lo que se guarda es lo que se imprime.

    Sin esto, el ida y vuelta podría estar bien y el bloque perderse al construir el
    prompt — que es justo la costura que este test cubre.
    """
    job = _job("titular_cuerpo")
    _editar(job, {"image_slide_titular_0": "El coste sube",
                  "image_slide_cuerpo_0": "Cada salto multiplica el gasto."})
    slide = job["posts"]["image_text"]["slides"][0]
    r = parch.construir(
        {"contenido": {"tema": "IA", "bloques": parch.bloques_de_slide(slide, jr._sistema(job)),
                       "rol_slide": "tension", "idioma": "es"},
         "prompt_base": "A worn power meter", "sistema_texto": jr._sistema(job)},
        cfg=None, usar_llm=False, autocritica=False)
    assert 'HEADLINE "EL COSTE SUBE"' in r.prompt
    assert 'BODY "Cada salto multiplica el gasto."' in r.prompt


# ── El lint mide contra el sistema congelado ─────────────────────────────────

def test_el_lint_avisa_del_bloque_que_falta_tras_editar():
    job = _job("titular_cuerpo")
    posts = _editar(job, {"image_slide_titular_0": "Uno", "image_slide_titular_1": "Dos",
                          "image_slide_titular_2": "Tres"})
    mensajes = " ".join(a["mensaje"] for a in app._lint_job(job, posts))
    assert "cuerpo" in mensajes
