"""Mide el PEOR CASO del brief de 9 secciones contra su techo de caracteres.

Script de **diagnóstico por terminal**: no lo importa la app, no llama a ningún
modelo y no toca la red. Existe porque varias correcciones de calidad añaden texto
FIJO a todos los prompts (la cláusula de continuidad de set, el bloqueo de luz, el
sangrado en positivo…) y ese presupuesto es finito: `architect.json` →
`validacion.max_caracteres` tiene que seguir 50 caracteres por debajo del corte del
cliente (`higgsfield_mcp._MAX_PROMPT_CHARS`).

Lo que se juega si el peor caso se pasa del techo no es cosmético: `_ajustar_longitud`
poda las secciones creativas hasta 10 palabras y, si aun así no entra, `validar` tira
el prompt ENTERO — y entonces la imagen se genera con el prompt base, sin bloque de
texto. Por eso se mide el peor caso y no el típico.

Qué es "el peor caso" acá:

  - slide de info con beat `remate` (lleva cláusula de plano Y continuidad de set),
  - texto largo → titular **+ kicker** (dos bloques, y el kicker añade su propia
    cláusula de banda baja),
  - `escena_portada` larga (se recorta, pero paga el recorte).

Y se mide con DOS identidades, porque no miden lo mismo:

  - **casa** — los valores reales de `brand.json`. Es lo que se genera hoy en
    producción, así que su margen es el presupuesto **usable** por las fases nuevas.
  - **esquema** — todos los campos de texto en su tope (`visual_identity.MAX_TEXTO`,
    `MAX_RITMO_ITEM`, `MAX_REFERENCIA`). No es teórico: `validar` acepta exactamente
    eso, así que un usuario puede guardarlo y generar con ello mañana. Sirve de alarma:
    lo que la sección 5 paga por esa identidad es fijo y la poda **no lo toca**.

Se mide por los dos caminos, porque el techo lo tienen que respetar los dos:

  A. **respaldo** — sin LLM (`usar_llm=False`): es lo que sale cuando no hay keys o
     el modelo falla. Marca el suelo del prompt.
  B. **LLM al máximo** — las cinco secciones creativas devueltas con exactamente
     `validacion.max_palabras_seccion` palabras largas. Marca el techo real.

Uso (desde `api/`):

    python scripts/medir_prompt.py            # tabla + margen disponible
    python scripts/medir_prompt.py --prompt   # además, el prompt completo del peor caso
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# La consola de Windows llega en cp1252 y el informe lleva acentos, comillas latinas y
# flechas: sin esto el script muere con UnicodeEncodeError justo al imprimir el margen.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — una consola que no deja reconfigurar no es un error
    pass

import llm_json                     # noqa: E402
import prompt_architect as parch    # noqa: E402
import visual_identity as vi        # noqa: E402

# ── Línea base registrada ─────────────────────────────────────────────────────
#
# La línea base (antes de las fases de calidad de carrusel) y las mediciones
# posteriores se anotan en `docs/calidad-imagenes.md`, no acá: son historia del
# proyecto y este archivo solo sabe medir.
#
# Lo que sí conviene dejar dicho porque no es obvio y lo descubrió la primera
# medición: con la identidad **esquema** (todos los campos a 240 caracteres) el
# prompt YA se pasaba del techo antes de tocar nada. La causa es estructural: la
# sección 5 pega `tipografia`, `tipografia_secundaria`, `color_texto` y `color_acento`
# verbatim —hasta ~960 caracteres— y `_ajustar_longitud` solo poda las creativas, así
# que ese coste no se puede recuperar. El margen que reparten las fases nuevas es el
# de la identidad **casa**; la fila de esquema es una alarma, no un presupuesto.

# Palabras largas de vocabulario real de dirección de arte: el peor caso de una
# sección creativa no son 26 palabras cualesquiera, son 26 palabras largas.
_PALABRAS = (
    "chiaroscuro", "monochromatic", "three-quarter", "anamorphic", "incandescent",
    "counterweighted", "phosphorescent", "cross-processed", "retroreflective",
    "hard-edged", "photographic", "architectural", "instrumentation", "silhouetted",
    "asymmetrical", "foreshortened", "desaturated", "high-contrast", "reflective",
    "weather-beaten", "industrial", "backlighting", "perspective", "granular",
    "cinematic", "underexposed", "polarising", "diffraction", "vignetting",
)


def _relleno(palabras: int) -> str:
    """Una sección creativa de exactamente `palabras` palabras largas."""
    return " ".join(_PALABRAS[i % len(_PALABRAS)] for i in range(palabras)) + "."


def _identidad_casa() -> dict:
    """La identidad de la casa (`prompts/brand.json`), que es lo que se genera hoy."""
    return vi.identidad_system()


def _identidad_maxima() -> dict:
    """Identidad de usuario con cada campo de texto en su tope de esquema.

    No es un caso teórico: `visual_identity.validar` acepta exactamente esto, así que
    un usuario puede guardarlo y generar con ello mañana.
    """
    relleno = "x" * (vi.MAX_TEXTO - 30)  # el hueco es para el hex y su nombre
    return vi.normalizar({
        "paleta": ["#0B0C0E", "#EDEAE0", "#C9F227"],
        "paleta_nombres": ["near-black", "bone white", "acid lime"],
        "color_texto": f"bone white (#EDEAE0) {relleno}"[:vi.MAX_TEXTO],
        "color_acento": f"acid lime (#C9F227) {relleno}"[:vi.MAX_TEXTO],
        "tipografia": ("ultra-condensed heavy display grotesque, ALL CAPS, tight tracking "
                       + relleno)[:vi.MAX_TEXTO],
        "tipografia_secundaria": ("same face, bold, tracking opened " + relleno)[:vi.MAX_TEXTO],
        "tono_visual": ("cinematic poster still, one spotlit subject, hard rim light "
                        + relleno)[:vi.MAX_TEXTO],
        "aspect_ratio": "4:5",
        "referencias": ["r" * vi.MAX_REFERENCIA] * vi.MAX_REFERENCIAS,
        "ritmo_carrusel": ["s" * vi.MAX_RITMO_ITEM] * vi.MAX_RITMO,
    })


# Texto largo a propósito: pasa de `max_palabras_bloque`, así que se parte en
# titular + kicker y el brief paga las DOS cláusulas de banda.
_TEXTO = ("La latencia del modelo se paga en atención perdida y en confianza del "
          "equipo que ya no espera")
_ESCENA_PORTADA = (
    "A scuffed rack-mount server chassis pulled half out of its cabinet on a cold "
    "concrete floor, dust on the fan grilles, one amber status light still burning, "
    "cables draped over the rails and pooling in the foreground"
)


def _spec(rol: str, identidad: dict) -> dict:
    """La spec del peor caso para ese rol, tal como la arma `job_runner._prompt_imagen`."""
    es_slide = parch.rol_base(rol) == "contenido"
    marca = {k: v for k, v in identidad.items()
             if k in ("paleta", "paleta_nombres", "color_texto", "color_acento",
                      "tipografia", "tipografia_secundaria", "tono_visual")}
    marca["aspect_ratio"] = identidad["aspect_ratio"]
    return {
        "contenido": {
            "tema": "Por qué la latencia mata la adopción de un asistente interno",
            "angulo": "" if es_slide else _ESCENA_PORTADA,
            "escena_portada": _ESCENA_PORTADA if es_slide else "",
            "texto_exacto_a_renderizar": _TEXTO,
            "rol_slide": rol,
            "idioma": "es",
        },
        "marca": marca,
        "prompt_base": _ESCENA_PORTADA,
        "referencias": list(identidad["referencias"]),
        "ritmo_carrusel": list(identidad["ritmo_carrusel"]),
    }


class _CfgFalso:
    """Lo mínimo que miran `llm_json.disponible` y `prompt_architect.construir`."""

    anthropic_api_key = "medicion"
    perplexity_api_key = ""
    prompt_architect = True
    prompt_architect_critique = False


def _construir(spec: dict, *, palabras: int = 0) -> tuple[parch.ResultadoPrompt | None, int, str]:
    """Construye el prompt y devuelve `(resultado, longitud, motivo_del_fallo)`.

    `palabras > 0` simula un LLM que devuelve sus cinco secciones creativas con
    exactamente esa cantidad de palabras. Se parchea `llm_json` en vez de llamar a las
    privadas de `prompt_architect`: así se ejerce el camino real —poda, validación y
    respaldo incluidos— y la medición no envejece cuando el ensamblado cambie.

    Un `PromptInvalido` no se propaga: **es un resultado**, y de los importantes —es
    exactamente lo que en producción deja la imagen sin bloque de texto—. Se mide su
    longitud igual, reconstruyendo por el camino determinista.
    """
    if not palabras:
        try:
            res = parch.construir(spec, cfg=None, usar_llm=False, autocritica=False)
            return res, len(res.prompt), ""
        except parch.PromptInvalido as e:
            return None, _largo_rechazado(spec), "; ".join(e.errores)

    seccion = _relleno(palabras)
    original_disp, original_json = llm_json.disponible, llm_json.complete_json
    llm_json.disponible = lambda _cfg: True
    llm_json.complete_json = lambda *_a, **_k: (
        {k: seccion for k in ("sujeto", "composicion", "luz", "estilo", "camara")}, None,
    )
    try:
        res = parch.construir(spec, cfg=_CfgFalso(), usar_llm=True, autocritica=False)
        return res, len(res.prompt), ""
    except parch.PromptInvalido as e:
        return None, _largo_rechazado(spec), "; ".join(e.errores)
    finally:
        llm_json.disponible, llm_json.complete_json = original_disp, original_json


def _largo_rechazado(spec: dict) -> int:
    """Cuánto medía el prompt que el validador tiró (para poder reportar el exceso)."""
    norm = parch.normalizar_spec(spec)
    bloques = parch._bloques(norm["contenido"]["texto_exacto_a_renderizar"])
    fijas = {
        "pieza": parch._seccion_pieza(norm),
        "texto": parch._seccion_texto(norm, bloques),
        "tipografia": parch._seccion_tipografia(norm, bloques),
        "negativos": parch._seccion_negativos(norm),
    }
    creativas = {k: parch._recortar(v, 10) for k, v in parch._respaldos(norm).items()}
    completas = dict(fijas)
    completas.update(creativas)
    completas["composicion"] = (
        f"{completas.get('composicion', '').strip()} {parch._clausula_aire(norm)}".strip())
    return len(parch.ensamblar(completas))


def _poda(res: parch.ResultadoPrompt | None, pedidas: int) -> int:
    """A cuántas palabras quedó cada sección creativa (`pedidas` = sin podar).

    Es el número que de verdad importa, y no salta a la vista en el total: cuando se
    añade texto fijo, `_ajustar_longitud` compensa recortando las creativas, así que el
    prompt puede MEDIR MENOS y ser peor — lo que se fue es el anclaje concreto del
    sujeto, que es lo único que evita que la imagen salga genérica. Se mide sobre
    `sujeto` porque es la única creativa a la que la app no le pega nada detrás.
    """
    if res is None:
        return 0
    return len((res.secciones.get("sujeto") or "").split()) or pedidas


def _medir(nombre: str, identidad: dict, techo: int, max_palabras: int) -> tuple[int, str, object]:
    """Imprime la tabla de un perfil de identidad. Devuelve `(peor_largo, rol, resultado)`."""
    print(f"\nIDENTIDAD «{nombre}»")
    print(f"{'rol':<12}{'respaldo':>10}{'LLM máx.':>10}{'margen':>10}{'poda':>8}")
    print("-" * 50)

    peor_rol, peor_largo, peor_res = "", 0, None
    for rol in ("portada",) + parch.ROLES_BEAT:
        spec = _spec(rol, identidad)
        _, largo_resp, fallo_resp = _construir(spec)
        res, largo, fallo = _construir(spec, palabras=max_palabras)
        marca = " RECHAZADO" if (fallo or fallo_resp) else ""
        poda = f"{_poda(res, max_palabras)}/{max_palabras}"
        print(f"{rol:<12}{largo_resp:>10}{largo:>10}{techo - largo:>10}{poda:>8}{marca}")
        if largo > peor_largo:
            peor_rol, peor_largo, peor_res = rol, largo, res
    return peor_largo, peor_rol, peor_res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", action="store_true",
                    help="imprime además el prompt completo del peor caso de la casa")
    args = ap.parse_args()

    val = parch._validacion_cfg()
    techo = parch._entero(val, "max_caracteres", parch._MAX_CARACTERES)
    max_palabras = parch._entero(val, "max_palabras_seccion", parch._MAX_PALABRAS_SECCION)

    print(f"Techo: validacion.max_caracteres = {techo}")
    print(f"Secciones creativas al máximo: {max_palabras} palabras "
          f"({len(_relleno(max_palabras))} caracteres cada una)")

    largo_casa, rol_casa, res_casa = _medir("casa (brand.json)", _identidad_casa(),
                                            techo, max_palabras)
    largo_max, rol_max, _ = _medir("esquema (todos los campos al tope)",
                                   _identidad_maxima(), techo, max_palabras)

    print(f"\nPRESUPUESTO USABLE (identidad de la casa): peor caso `{rol_casa}` con "
          f"{largo_casa} de {techo} → {techo - largo_casa} caracteres de margen.")
    if res_casa is not None:
        print("\nLongitud por sección (peor caso de la casa):")
        for sec in parch._secciones_cfg():
            print(f"  {sec['etiqueta']:<20}{len(res_casa.secciones.get(sec['clave'], '')):>6}")
        if res_casa.avisos:
            print("\nAvisos del arquitecto:")
            for aviso in res_casa.avisos:
                print(f"  - {aviso}")
        if args.prompt:
            print("\n" + "=" * 70 + f"\n{res_casa.prompt}\n" + "=" * 70)

    if largo_max > techo:
        print(f"\n[alarma] Con la identidad al tope del esquema el peor caso (`{rol_max}`) "
              f"mide {largo_max} y se pasa por {largo_max - techo}: el validador tira el "
              "prompt entero y esa imagen sale con el prompt base, SIN bloque de texto. Lo "
              "que se pasa es la sección 5 (tipografía), que la poda no toca.")
    if largo_casa > techo:
        print("\n[ERROR] Ni la identidad de la casa entra. Esto rompe la generación normal.")
        return 1
    if res_casa is not None and any("acortadas" in a for a in res_casa.avisos):
        print("\n[aviso] El peor caso de la casa entra PODADO: se están recortando las "
              "secciones creativas, que son el anclaje concreto del sujeto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
