"""Params de generate_image: aspecto nativo, resolución y referencia visual.

Las capacidades salen de una tabla verificada contra el catálogo en vivo
(`mcp_bootstrap.py --models image`, jul 2026). Lo que se protege acá es que a un
modelo nunca se le pida algo que no soporta —un aspecto inexistente o un param de
más pueden tumbar el submit entero— y que el que sí lo soporta lo reciba.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import higgsfield_mcp as hfmcp


# ── Aspecto ───────────────────────────────────────────────────────────────────

def test_feed_pide_4_5_en_los_modelos_que_lo_soportan():
    for model in ("nano_banana_pro", "nano_banana_2", "nano_banana"):
        assert hfmcp.image_aspect(hfmcp.FEED_IMAGE_ASPECT, model=model) == "4:5"


def test_modelos_sin_4_5_caen_al_mejor_vertical_no_a_1_1():
    # 3:4 sigue siendo vertical: recortarlo a 4:5 quita alto pero NO escala. Caer a
    # 1:1 obligaría al upscale del 25% que este cambio vino a eliminar.
    for model in ("gpt_image_2", "z_image"):
        assert hfmcp.image_aspect(hfmcp.FEED_IMAGE_ASPECT, model=model) == "3:4"


def test_modelo_desconocido_se_queda_en_lo_seguro():
    assert hfmcp.image_aspect(hfmcp.FEED_IMAGE_ASPECT, model="modelo_que_no_existe") == "1:1"


def test_un_aspecto_soportado_se_respeta_tal_cual():
    # La historia pide 9:16 nativo: si esto cayera a 4:5, el vertical volvería a
    # fabricarse recortando, que es justo lo que se vino a eliminar.
    for model in ("nano_banana_pro", "gpt_image_2", "z_image"):
        assert hfmcp.image_aspect("9:16", model=model) == "9:16"


# ── Resolución ────────────────────────────────────────────────────────────────

def test_se_pide_2k_al_modelo_que_lo_acepta():
    # 2k cuesta lo mismo que 1k (preflight get_cost) y 1k queda por debajo del
    # lienzo de 1080x1350.
    p = hfmcp._image_params("x", "4:5", "nano_banana_pro")
    assert p["resolution"] == "2k"


def test_no_se_manda_resolution_al_modelo_que_no_la_tiene():
    for model in ("nano_banana", "z_image", "modelo_que_no_existe"):
        assert "resolution" not in hfmcp._image_params("x", "4:5", model)


# ── Referencia visual (coherencia del carrusel) ───────────────────────────────

def test_la_referencia_viaja_con_el_rol_del_modelo():
    p = hfmcp._image_params("x", "4:5", "nano_banana_pro", "job-de-la-portada")
    assert p["medias"] == [{"value": "job-de-la-portada", "role": "image"}]
    # nano_banana usa otro nombre de rol para lo mismo.
    p2 = hfmcp._image_params("x", "4:5", "nano_banana", "job-de-la-portada")
    assert p2["medias"] == [{"value": "job-de-la-portada", "role": "image_references"}]


def test_sin_referencia_no_se_manda_medias():
    assert "medias" not in hfmcp._image_params("x", "4:5", "nano_banana_pro")


def test_modelo_sin_soporte_de_referencia_la_ignora():
    # z_image no acepta `medias`: mandarlas sería un submit fallido garantizado.
    p = hfmcp._image_params("x", "1:1", "z_image", "job-de-la-portada")
    assert "medias" not in p
    assert hfmcp.image_reference_role("z_image") == ""


def test_el_prompt_se_recorta_al_limite_del_server():
    p = hfmcp._image_params("x" * 5000, "4:5", "nano_banana_pro")
    assert len(p["prompt"]) == hfmcp._MAX_PROMPT_CHARS


def test_el_corte_del_prompt_queda_por_encima_del_presupuesto_del_arquitecto():
    # Si el corte cayera por debajo, lo que se pierde es la ÚLTIMA sección del brief
    # —los negativos— sin que nada avise.
    import prompt_config
    presupuesto = prompt_config.architect()["validacion"]["max_caracteres"]
    assert hfmcp._MAX_PROMPT_CHARS > presupuesto
