"""Referencia visual entre la portada y los slides del carrusel.

Los slides se generan pasando la PORTADA como referencia (`medias` del MCP, mismo
costo). Antes la coherencia era solo una frase dentro del prompt ("misma paleta que
la portada") que el modelo no podía ver. Lo que se protege acá es la degradación:
perder la coherencia siempre tiene que ser preferible a perder la imagen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import image_provider as improv


@pytest.fixture
def mcp(monkeypatch):
    """Espía del cliente MCP: registra las llamadas y deja programar fallos."""
    calls = {"submit": [], "base": 0, "fail_with_reference": False, "fail_always": False}

    def fake_generate_image_job(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["base"] += 1
        return {"job_id": "job-portada", "url": "https://cdn/portada.png"}

    def fake_submit_image(prompt, *, aspect_ratio="1:1", model="", reference=""):
        calls["submit"].append({"aspect_ratio": aspect_ratio, "reference": reference})
        if calls["fail_always"] or (calls["fail_with_reference"] and reference):
            raise RuntimeError("el server rechazó el submit")
        return {"job_id": f"job-{len(calls['submit'])}", "prompt": prompt}

    monkeypatch.setattr(improv.hfmcp, "generate_image_job", fake_generate_image_job)
    monkeypatch.setattr(improv.hfmcp, "submit_image", fake_submit_image)
    return calls


def test_la_portada_deja_su_job_id_como_referencia(mcp):
    p = improv.MCPProvider(image_model="nano_banana_pro")
    assert p.base_reference == ""
    p.generate_base("una escena", aspect_ratio="4:5")
    assert p.base_reference == "job-portada"


def test_los_slides_se_envian_mirando_la_portada(mcp):
    p = improv.MCPProvider(image_model="nano_banana_pro")
    p.generate_base("portada", aspect_ratio="4:5")
    p.prewarm_extras(["a", "b"], aspect_ratio="4:5", reference=p.base_reference)
    assert [c["reference"] for c in mcp["submit"]] == ["job-portada", "job-portada"]
    assert all(c["aspect_ratio"] == "4:5" for c in mcp["submit"])


def test_si_la_referencia_es_rechazada_se_reintenta_sin_ella(mcp):
    # Antes que caer a una plantilla local, mejor un slide generado sin coherencia.
    mcp["fail_with_reference"] = True
    p = improv.MCPProvider(image_model="nano_banana_pro")
    handles = p.prewarm_extras(["a"], aspect_ratio="4:5", reference="job-portada")
    assert [c["reference"] for c in mcp["submit"]] == ["job-portada", ""]
    assert not handles[0].get("fallback_template")
    assert handles[0]["job_id"]


def test_si_falla_tambien_sin_referencia_cae_a_plantilla(mcp):
    mcp["fail_always"] = True
    p = improv.MCPProvider(image_model="nano_banana_pro")
    handles = p.prewarm_extras(["a", "b"], aspect_ratio="4:5", reference="job-portada")
    assert all(h["fallback_template"] for h in handles)


def test_la_portada_caida_a_plantilla_no_deja_referencia_falsa(mcp, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("sin créditos")

    monkeypatch.setattr(improv.hfmcp, "generate_image_job", boom)
    p = improv.MCPProvider(image_model="nano_banana_pro")
    p.generate_base("portada", aspect_ratio="4:5")
    assert p.base_reference == ""


def test_el_aspecto_se_negocia_con_las_capacidades_del_modelo(mcp):
    # z_image no soporta 4:5: se pide 3:4 (vertical) y nunca el cuadrado.
    p = improv.MCPProvider(image_model="z_image")
    p.prewarm_extras(["a"], aspect_ratio="4:5", reference="")
    assert mcp["submit"][0]["aspect_ratio"] == "3:4"


def test_el_proveedor_de_plantillas_acepta_la_misma_firma():
    # La fachada tiene que ser uniforme: job_runner llama igual a los dos backends.
    t = improv.TemplateProvider()
    handles = t.prewarm_extras(["a", "b"], aspect_ratio="4:5", reference="job-portada")
    assert len(handles) == 2
    assert t.base_reference == ""
