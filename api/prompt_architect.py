"""PromptArchitect — convierte el prompt genérico actual en un prompt final estructurado.

Por qué existe: hasta ahora el prompt de imagen era una frase suelta ("escena +
look + sin texto") y el texto de la pieza se imprimía DESPUÉS con Pillow. Ahora el
texto lo renderiza el propio modelo, y para eso el prompt tiene que decir mucho más
y decirlo en un orden fijo. Este módulo produce ese prompt con **nueve secciones
obligatorias**, siempre en el mismo orden:

    1. PIECE & FORMAT   5. TYPOGRAPHY
    2. SUBJECT & SCENE  6. LIGHTING & PALETTE
    3. COMPOSITION      7. STYLE & REFERENCES
    4. TEXT TO RENDER   8. CAMERA / RENDER
                        9. NEGATIVE

Reparto de responsabilidades (importante para entender el diseño):

- Las secciones 1, 4, 5 y 9 —formato, texto a renderizar, tipografía y negativos—
  las escribe SIEMPRE la app, con plantillas deterministas. Son las que no pueden
  salir mal: el string exacto entre comillas, sus acentos, su jerarquía, su posición
  y la familia tipográfica. Nunca se delegan al modelo, ni siquiera en la reescritura
  de la auto-crítica. La tipografía entró en este grupo al ver que, escrita por el
  LLM, cada post inventaba su propia fuente y su propia escala: no había identidad
  entre posts ni jerarquía de póster dentro del carrusel.
- Las secciones creativas (2, 3, 6, 7, 8) las escribe el LLM a partir del prompt
  base y de los datos de marca. Si no hay LLM, o falla, o devuelve basura, entran
  los respaldos de `architect.json` y el prompt sale igual de válido — solo menos
  afinado. **Generar nunca se interrumpe por esto.**
- A la sección 3 la app le añade siempre, literal, la cláusula del lockup de póster:
  las dos bandas reservadas para el tipo y el sujeto anclado en la banda central.
- En un carrusel, el rol del slide no es un genérico "contenido" sino un **beat** con
  función narrativa (`tension` → `desarrollo` → `prueba` → `remate`), y de él dependen
  la escala del titular, la presencia del acento y el plano. Los tres los escribe la
  app, en secciones que el modelo no puede pisar: cuando esa variación vivía en el
  `prompt_base` —que el arquitecto solo ve como "BASE PROMPT (weak, to rewrite)"— se
  perdía casi siempre y los slides salían como versiones de la misma imagen.

Todo es síncrono (quien llama lo envuelve en `job_runner._run`). Los textos de
plantilla, el rubric y la marca viven en `api/prompts/*.json`, no aquí.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import llm_json
import prompt_config

# Defaults mínimos por si `architect.json` falta entero (despliegue incompleto):
# el módulo tiene que seguir produciendo un prompt válido de 9 secciones.
_SECCIONES_FALLBACK = [
    {"clave": "pieza", "etiqueta": "PIECE & FORMAT"},
    {"clave": "sujeto", "etiqueta": "SUBJECT & SCENE"},
    {"clave": "composicion", "etiqueta": "COMPOSITION"},
    {"clave": "texto", "etiqueta": "TEXT TO RENDER"},
    {"clave": "tipografia", "etiqueta": "TYPOGRAPHY"},
    {"clave": "luz", "etiqueta": "LIGHTING & PALETTE"},
    {"clave": "estilo", "etiqueta": "STYLE & REFERENCES"},
    {"clave": "camara", "etiqueta": "CAMERA / RENDER"},
    {"clave": "negativos", "etiqueta": "NEGATIVE"},
]
_CLAVES_CREATIVAS = ("sujeto", "composicion", "luz", "estilo", "camara")
# Beats del carrusel, en el orden canónico. Es el orden en el que una identidad
# visual escribe su `ritmo_carrusel` (lista ORDENADA, igual que `paleta`), así que
# mover uno de sitio reasigna el ritmo de todas las identidades guardadas.
ROLES_BEAT = ("tension", "desarrollo", "prueba", "remate")
# `contenido` sigue siendo un rol válido y significa "slide de info sin beat": es lo
# que se generaba antes de la escalera y el respaldo de cualquier beat desconocido.
_ROLES = ("portada", "contenido") + ROLES_BEAT
_MAX_PALABRAS_BLOQUE = 8
_MAX_PALABRAS_SECCION = 55
_MIN_CARACTERES = 400
_MAX_CARACTERES = 3000


class PromptInvalido(ValueError):
    """El prompt (o los datos de entrada) no cumplen las reglas del validador."""

    def __init__(self, errores: list[str]):
        self.errores = list(errores)
        super().__init__("; ".join(self.errores))


@dataclass
class ResultadoPrompt:
    """Prompt final + todo lo que hace falta para auditarlo y cobrarlo."""

    prompt: str
    secciones: dict[str, str] = field(default_factory=dict)
    bloques: list[str] = field(default_factory=list)     # titular (+ kicker)
    puntajes: dict[str, float] = field(default_factory=dict)
    iteraciones: int = 0                                  # reescrituras aplicadas
    usos: list[dict] = field(default_factory=list)        # usage_events del LLM
    avisos: list[str] = field(default_factory=list)
    fuente: str = "respaldo"                              # "llm" | "respaldo"


# ── Config ────────────────────────────────────────────────────────────────────

def _cfg_arch() -> dict:
    return prompt_config.architect()


def _secciones_cfg() -> list[dict]:
    secs = _cfg_arch().get("secciones")
    if isinstance(secs, list) and secs:
        return [s for s in secs if isinstance(s, dict) and s.get("clave") and s.get("etiqueta")]
    return _SECCIONES_FALLBACK


def _validacion_cfg() -> dict:
    v = _cfg_arch().get("validacion")
    return v if isinstance(v, dict) else {}


def _entero(cfg: dict, clave: str, defecto: int) -> int:
    try:
        return int(cfg.get(clave, defecto))
    except (TypeError, ValueError):
        return defecto


# ── Beats del carrusel ────────────────────────────────────────────────────────
#
# Un carrusel cuyos slides comparten pieza, banda, escala y composición no es un
# carrusel: son versiones de la misma imagen con otro objeto. La escalera de beats
# le da a cada posición una FUNCIÓN (tensión → desarrollo → prueba → remate) y hace
# que de ella dependan las tres cosas que la app escribe y el modelo no puede pisar:
# la escala del titular, la presencia del acento y el plano. Lo que NO cambia entre
# beats es el esqueleto del lockup —titular arriba, apoyo al pie, sujeto en la banda
# central—: unificarlo fue una corrección deliberada y es lo que hace que el set se
# lea como un sistema.

# Respaldos mínimos por si `architect.json` falta entero: como en el resto del
# módulo, sin config el prompt sale menos afinado pero igual de válido.
_COMPOSICION_BEAT_FALLBACK = {
    "tension": "SHOT — TENSION, the closest framing of the set: the subject fills the central "
               "band edge to edge, its own falloff and defocus forming the clear zones.",
    "desarrollo": "SHOT — DEVELOPMENT, a calm mid-distance view: the subject sits whole in the "
                  "central band, the set legible behind it, bare surface forming the clear zones.",
    "prueba": "SHOT — EVIDENCE, shot square-on: one object alone, centred on a bare field that "
              "forms the clear zones, nothing else competing in the frame.",
    "remate": "SHOT — PAYOFF, the widest and deepest frame of the set: the subject reads small "
              "in the central band, quiet space opening above and below it.",
}
_FUNCION_BEAT_FALLBACK = {
    "tension": "the tension — the problem, symptom or cost the reader recognises",
    "desarrollo": "the development — the explanation or step that moves the idea one notch forward",
    "prueba": "the evidence — the concrete figure, example or result that proves it",
    "remate": "the payoff — the line the reader would screenshot, still an idea from the source",
}
_RITMO_FALLBACK = {
    "tension": "Tight macro of a single worn surface or texture, hard raking light across it.",
    "desarrollo": "Mid-distance still life on a bare surface, shallow focus, camera at object height.",
    "prueba": "Flat overhead of one object centred on an empty field, single hard key light.",
    "remate": "Wide low three-quarter view with strong foreground depth, the subject small in a "
              "large space.",
}


def rol_base(rol: str) -> str:
    """El rol clásico del que hereda un beat: `"portada"` o `"contenido"`.

    Todo lo que no se declara por beat —la pieza, las bandas de texto— cae al bloque
    `contenido` de `architect.json`. Es también el rol que hay que pasarle al
    renderizador de la plantilla de respaldo, que solo conoce esos dos.
    """
    return "portada" if str(rol or "").strip().lower() == "portada" else "contenido"


def _escalera(n: int) -> list[str]:
    """La escalera canónica para `n` slides de info. La tensión abre, el remate
    cierra y lo que se estira en el medio es el desarrollo."""
    if n <= 0:
        return []
    if n == 1:
        return ["remate"]
    if n == 2:
        return ["tension", "remate"]
    if n == 3:
        return ["tension", "desarrollo", "remate"]
    if n == 4:
        return ["tension", "desarrollo", "prueba", "remate"]
    return ["tension"] + ["desarrollo"] * (n - 3) + ["prueba", "remate"]


def roles_carrusel(n_info: int) -> list[str]:
    """Qué beat le toca a cada slide de info, en orden.

    Fuente única de la secuencia: la usan el prompt de cada imagen, la regeneración
    de un slide suelto y el briefing del redactor. Si los tres no contaran lo mismo,
    el texto del slide 2 se escribiría para un beat y su imagen para otro.

    La tabla de `architect.json` manda; una entrada de longitud distinta a `n_info`
    se descarta entera (media escalera es peor que la escalera de siempre).
    """
    try:
        n = max(0, int(n_info or 0))
    except (TypeError, ValueError):
        return []
    tabla = _cfg_arch().get("secuencia_roles")
    if isinstance(tabla, dict):
        crudo = tabla.get(str(n))
        if isinstance(crudo, list) and len(crudo) == n:
            seq = [str(r).strip().lower() for r in crudo]
            if all(r in _ROLES for r in seq):
                return seq
    return _escalera(n)


def funcion_beat(rol: str) -> str:
    """Qué cuenta ese beat, en una frase. `""` si el rol no es un beat.

    Es lo que hace que el TEXTO impreso y la IMAGEN del slide i se encarguen desde el
    mismo sitio: `post_writer` se la pasa al redactor y `prompt_architect` al
    arquitecto de la imagen. Antes la secuencia se pedía solo en prosa ("cada slide
    avanza al anterior") sin nombrar posiciones, así que el texto del slide 2 no sabía
    que le tocaba desarrollar y su imagen no sabía que el texto lo haría.
    """
    if rol not in ROLES_BEAT:
        return ""
    cfg_rol = (_cfg_arch().get("roles") or {}).get(rol) or {}
    return str(cfg_rol.get("funcion") or _FUNCION_BEAT_FALLBACK[rol]).strip()


def _ritmo_beat(norm: dict, rol: str) -> str:
    """Cómo fotografía ESTA marca el beat `rol`.

    El beat es estructura (universal, `architect.json`); el ritmo es identidad: la
    misma escalera recorrida por una marca de macros y por una de naturalezas muertas
    abiertas no produce el mismo carrusel. La identidad lo declara en
    `ritmo_carrusel`, una lista ORDENADA por `ROLES_BEAT`; lo que no traiga cae al
    respaldo de `architect.json` beat a beat, así que una identidad sin el campo
    genera exactamente como antes de la escalera.
    """
    ritmo = norm.get("ritmo_carrusel") or []
    i = ROLES_BEAT.index(rol)
    propio = ritmo[i].strip() if i < len(ritmo) and isinstance(ritmo[i], str) else ""
    if not propio:
        cfg_rol = (_cfg_arch().get("roles") or {}).get(rol) or {}
        propio = str(cfg_rol.get("ritmo") or "").strip()
    return _recortar(propio or _RITMO_FALLBACK[rol],
                     _entero(_validacion_cfg(), "ritmo_palabras", 18))


def encuadre_beat(rol: str, identidad: dict | None = None) -> str:
    """El plano del beat, para el `prompt_base` que ve el LLM.

    El prompt base es lo único que el arquitecto le enseña al modelo como punto de
    partida, así que tiene que decir el MISMO plano que la cláusula determinista de
    la sección 3 — si dijeran cosas distintas, el brief se contradiría a sí mismo.
    """
    if rol not in ROLES_BEAT:
        return ""
    ident = identidad if isinstance(identidad, dict) else {}
    # La MISMA cadena de respaldo que `normalizar_spec`: identidad → `brand.json` →
    # el `ritmo` del beat en `architect.json`. Si esta se saltara la identidad de la
    # casa, un job sin identidad propia diría un plano en el prompt base y otro en la
    # sección 3 — el brief contradiciéndose a sí mismo.
    ritmo = ident.get("ritmo_carrusel") or prompt_config.brand().get("ritmo_carrusel") or []
    norm = {"ritmo_carrusel": [str(r) for r in ritmo] if isinstance(ritmo, (list, tuple)) else []}
    return _ritmo_beat(norm, rol)


# ── Entrada ───────────────────────────────────────────────────────────────────

def _texto_plano(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x).strip() for x in v if str(x).strip())
    return str(v or "").strip()


# Acento marcado a mano: **la palabra o frase** que el usuario quiere en color. Es la
# única forma que tiene de dirigir la jerarquía del titular; sin marcas, el modelo
# elige la palabra "más portante" por su cuenta (comportamiento de siempre). Los
# asteriscos son NOTACIÓN, no contenido: se quitan acá, antes de que el texto llegue
# ni al prompt ni al QA de visión, así que no pueden acabar impresos en la imagen.
_ACENTO_RE = re.compile(r"\*\*(.+?)\*\*", re.S)


def separar_acento(texto: str) -> tuple[str, str]:
    """`"Ecualizar **cambia** todo"` → `("Ecualizar cambia todo", "cambia")`.

    Devuelve `(texto_sin_marcas, acento)`. Si no hay marcas, `acento` es `""` y el
    texto vuelve igual (salvo asteriscos sueltos, que también se limpian: un `**`
    impar es un error de tipeo, no algo que deba imprimirse). Con varias marcas se
    toma la primera — un acento por pieza; varios spans en color son confeti.
    """
    t = str(texto or "")
    m = _ACENTO_RE.search(t)
    acento = " ".join(m.group(1).split()) if m else ""
    limpio = _ACENTO_RE.sub(lambda x: x.group(1), t).replace("**", "")
    return " ".join(limpio.split()), acento


def normalizar_spec(spec: dict) -> dict:
    """Valida y completa la entrada del arquitecto.

    Entrada esperada (las claves extra se ignoran):
        {"contenido": {"tema", "angulo", "texto_exacto_a_renderizar", "rol_slide", "idioma"?},
         "marca": {"paleta", "tipografia", "tono_visual", "aspect_ratio"},
         "prompt_base": str,
         "referencias": [str]?}

    Lo que falte en `marca`/`referencias` sale de `prompts/brand.json`. El texto a
    renderizar es obligatorio: sin él no hay pieza que construir (regla del módulo).
    """
    if not isinstance(spec, dict):
        raise PromptInvalido(["la spec debe ser un dict"])
    arch = _cfg_arch()
    marca_def = prompt_config.brand()
    contenido = spec.get("contenido") if isinstance(spec.get("contenido"), dict) else {}
    marca = spec.get("marca") if isinstance(spec.get("marca"), dict) else {}

    # El texto llega con las marcas de acento del usuario (**así**) o sin ellas. Se
    # separan ACÁ, en el único punto por el que pasan todos los caminos: lo que sigue
    # —prompt, validador, QA— ve siempre el texto limpio.
    texto, acento = separar_acento(contenido.get("texto_exacto_a_renderizar"))
    if not texto:
        raise PromptInvalido([
            "falta `contenido.texto_exacto_a_renderizar` — el texto nunca se omite"
        ])

    rol = str(contenido.get("rol_slide") or "portada").strip().lower()
    if rol not in _ROLES:
        rol = "portada"

    aspect = (str(marca.get("aspect_ratio") or "").strip()
              or str(marca_def.get("aspect_ratio") or "").strip()
              or str(arch.get("aspect_por_defecto") or "").strip()
              or "4:5")

    referencias = spec.get("referencias")
    if not referencias:
        referencias = marca_def.get("referencias") or []

    # Mismo trato que `referencias`: viaja al nivel de arriba de la spec (no dentro de
    # `marca`, donde `_texto_plano` la aplanaría a una frase con comas) y lo que no
    # llega cae a `brand.json`. Es una lista ORDENADA por `ROLES_BEAT`.
    ritmo = spec.get("ritmo_carrusel")
    if not ritmo:
        ritmo = marca_def.get("ritmo_carrusel") or []

    return {
        "contenido": {
            "tema": str(contenido.get("tema") or "").strip(),
            "angulo": str(contenido.get("angulo") or "").strip(),
            "texto_exacto_a_renderizar": texto,
            # Fragmento que va en color de acento. Vacío = lo elige el modelo.
            "acento": acento,
            # Escena de la portada, solo en los slides: es el ancla de continuidad
            # del set ahora que no se pasa la portada como imagen de entrada.
            "escena_portada": str(contenido.get("escena_portada") or "").strip(),
            "rol_slide": rol,
            "idioma": (str(contenido.get("idioma") or "").strip().lower()
                       or str(arch.get("idioma_por_defecto") or "es")),
        },
        "marca": {
            "paleta": _texto_plano(marca.get("paleta") or marca_def.get("paleta")),
            "paleta_nombres": _texto_plano(marca.get("paleta_nombres") or marca_def.get("paleta_nombres")),
            "color_texto": _texto_plano(marca.get("color_texto") or marca_def.get("color_texto")),
            "color_acento": _texto_plano(marca.get("color_acento") or marca_def.get("color_acento")),
            "tipografia": _texto_plano(marca.get("tipografia") or marca_def.get("tipografia")),
            "tipografia_secundaria": _texto_plano(
                marca.get("tipografia_secundaria") or marca_def.get("tipografia_secundaria")),
            "tono_visual": _texto_plano(marca.get("tono_visual") or marca_def.get("tono_visual")),
            # La luz viaja APARTE del tono visual aunque salgan del mismo campo, porque
            # ya no son lo mismo: `tono_visual` lo pisa el `image_style` que el LLM
            # escribe para ESTE post (tratamiento fotográfico, sección 7) y la luz tiene
            # que ser la de la identidad, idéntica en las N piezas del job (sección 6).
            # El respaldo es el de siempre, así que un job sin identidad se comporta
            # exactamente igual que antes: la luz sale del `tono_visual` de brand.json.
            "luz_identidad": _texto_plano(marca.get("luz_identidad")
                                          or marca_def.get("tono_visual")),
            "aspect_ratio": aspect,
        },
        "prompt_base": str(spec.get("prompt_base") or "").strip(),
        "referencias": [str(r).strip() for r in referencias if str(r).strip()],
        # Sin filtrar los vacíos: la posición ES el beat, así que una entrada en
        # blanco tiene que dejar su hueco y caer al respaldo de ESE beat, no correr
        # el siguiente a su sitio.
        "ritmo_carrusel": [str(r).strip() for r in ritmo] if isinstance(ritmo, (list, tuple)) else [],
    }


# ── Texto a renderizar ────────────────────────────────────────────────────────

# Corte explícito entre titular y segunda línea: una raya (o una barra) ESPACIADA.
# Espaciada a propósito — así un guion dentro de una palabra compuesta, un rango
# ("30-40%") o una raya pegada no se leen como el corte de la pieza.
_SEPARADOR_RE = re.compile(r"\s+(?:[—–]|\||--)\s+")


def dividir_texto(texto: str, max_palabras: int = 0) -> tuple[str, str]:
    """Parte el texto en (titular, kicker): por la raya explícita, o por longitud.

    Los modelos de imagen rinden bien un bloque corto y entrecomillado y se
    desarman con un párrafo, así que un texto largo no se recorta (perderíamos
    sentido) sino que se reparte en dos bloques con jerarquía distinta.

    El corte automático es el respaldo, no el mecanismo: partir por longitud cerca
    de la mitad decide la jerarquía de la pieza con un criterio —cuántas palabras
    caben— que no tiene nada que ver con lo que la frase quiere decir. Por eso
    quien escribe el texto (el LLM en `image_text.slides`, o el usuario en la
    compuerta previa) puede marcar el corte a mano con una **raya espaciada**:
    `Titular — apoyo`. La raya es notación, no contenido: se descarta acá, así que
    nunca puede acabar impresa. Un titular explícito más largo que el límite se
    ignora y se vuelve al corte por longitud — un bloque enorme arriba rompe el
    póster igual que un párrafo.
    """
    limite = max_palabras or _entero(_validacion_cfg(), "max_palabras_bloque", _MAX_PALABRAS_BLOQUE)
    texto = " ".join((texto or "").split())
    partes = _SEPARADOR_RE.split(texto, maxsplit=1)
    if len(partes) == 2 and partes[0].strip() and partes[1].strip():
        titular = partes[0].strip().rstrip(",;:—–")
        if len(titular.split()) <= limite:
            return titular, partes[1].strip()
        texto = " ".join(p.strip() for p in partes)  # sin la raya: corte por longitud
    palabras = texto.split()
    if len(palabras) <= limite:
        return texto.strip(), ""
    corte = min(limite, (len(palabras) + 1) // 2)
    # Un corte detrás de coma/punto/dos puntos lee mucho mejor que uno a mitad de
    # sintagma; se busca a ±2 palabras del corte teórico, sin pasarse del límite.
    for i in range(max(1, corte - 2), min(len(palabras) - 1, corte + 2) + 1):
        if i <= limite and palabras[i - 1][-1:] in (",", ".", ":", ";", "—", "–"):
            corte = i
            break
    titular = " ".join(palabras[:corte]).rstrip(",;:—–")
    kicker = " ".join(palabras[corte:])
    return titular, kicker


def _bloques(texto: str) -> list[str]:
    titular, kicker = dividir_texto(texto)
    return [b for b in (titular, kicker) if b]


# ── Secciones deterministas (las que la app nunca delega) ─────────────────────

def _seccion_pieza(norm: dict) -> str:
    piezas = _cfg_arch().get("piezas") or {}
    rol = norm["contenido"]["rol_slide"]
    plantilla = (piezas.get(rol) or piezas.get(rol_base(rol))
                 or "Editorial social image, {aspect}, print-quality single still.")
    return plantilla.format(aspect=norm["marca"]["aspect_ratio"])


def _zona(norm: dict) -> dict:
    """Bandas del lockup de póster para el rol del slide.

    `margen` es la corrección del defecto de titulares cortados: la v1 pedía
    "from the safe margin" sin cuantificarlo nunca, así que el modelo decidía —y a
    veces pegaba el tipo al borde. `zona_kicker` es la banda baja: la segunda línea
    no va debajo del titular (eso lee como caption) sino anclada al pie.
    """
    zonas = _cfg_arch().get("zonas_texto") or {}
    rol = norm["contenido"]["rol_slide"]
    # Las bandas NO cambian por beat a propósito: el esqueleto compartido es lo que
    # hace que el set se lea como un sistema. Lo que distingue a un slide de otro es
    # la escala del titular, el acento y el plano, no dónde se apoya el tipo.
    z = zonas.get(rol) or zonas.get(rol_base(rol)) or zonas.get("portada") or {}
    return {
        "zona": z.get("zona") or "the upper band, below an 8% top margin and above the 42% height line",
        "zona_kicker": z.get("zona_kicker") or "the bottom band, above an 8% bottom margin",
        "alineacion": z.get("alineacion") or "flush left from the 8% side margin",
        "ancho": z.get("ancho") or "up to 84% of the frame width",
        "margen": z.get("margen") or "an 8% safe margin on all four sides",
    }


def _tiene_acentos(texto: str) -> bool:
    return bool(re.search(r"[áéíóúüñÁÉÍÓÚÜÑàèìòùâêîôûçÇ]", texto))


def _seccion_texto(norm: dict, bloques: list[str], *, refuerzo: bool = False) -> str:
    """Sección 4: el bloque de texto. Es la razón de ser del módulo — se arma a mano."""
    cfg_txt = _cfg_arch().get("texto") or {}
    z = _zona(norm)
    idiomas = _cfg_arch().get("idiomas") or {}
    codigo = norm["contenido"]["idioma"]
    idioma = idiomas.get(codigo) or codigo.upper()
    titular = bloques[0]
    kicker = bloques[1] if len(bloques) > 1 else ""

    if kicker:
        base = (cfg_txt.get("instruccion_con_kicker")
                or 'render this exact text: "{titular}" as the headline, and directly under it, at '
                   'about half its size, this exact kicker: "{kicker}"')
        instruccion = base.format(titular=titular, kicker=kicker)
    else:
        base = cfg_txt.get("instruccion") or 'render this exact text: "{titular}"'
        instruccion = base.format(titular=titular)

    acentos = (cfg_txt.get("detalle_acentos")
               or "keep every accented character exactly as written") if _tiene_acentos(
        " ".join(bloques)) else (cfg_txt.get("detalle_sin_acentos") or "no accented characters in this string")
    detalle = (cfg_txt.get("detalle") or
               "Language: {idioma}. Reproduce every character literally ({acentos}). The headline sits "
               "in the reserved clear zone: {zona}, {alineacion}, spanning {ancho}. SAFE AREA: every "
               "glyph, accent and descender sits fully inside {margen} — nothing touches, overlaps or is "
               "cut by a frame edge. These are the ONLY words in the image.").format(
        idioma=idioma, acentos=acentos, **z)
    partes = [f"{instruccion}. {detalle}"]
    if kicker:
        # La banda baja se declara aparte: sin esto el modelo pega la segunda línea
        # debajo del titular y la pieza vuelve a leerse como foto + caption.
        partes.append((cfg_txt.get("detalle_kicker") or
                       "The second line locks into {zona_kicker}, {alineacion}, inside the same safe "
                       "area.").format(**z))
    if refuerzo:
        partes.append(cfg_txt.get("refuerzo") or
                      "TEXT ACCURACY IS THE PRIMARY REQUIREMENT: spell the quoted string character by "
                      "character and print no other word anywhere in the frame.")
    return " ".join(partes)


def _seccion_tipografia(norm: dict, bloques: list[str]) -> str:
    """Sección 5: la tipografía, escrita por la app.

    Dejó de ser creativa a propósito. Lo que la hacía verse genérica no era el
    modelo sino el brief: la familia salía del LLM (distinta en cada post) y nadie
    declaraba la ESCALA, así que el modelo ponía cuerpo de pie de foto. Acá la
    familia y el acento son marca (`brand.json`) y la escala es layout por rol
    (`architect.json`): un titular al 13-16% del alto es lo que separa un póster de
    un caption.
    """
    cfg_tip = _cfg_arch().get("tipografia") or {}
    m = norm["marca"]
    rol = norm["contenido"]["rol_slide"]
    escalas = cfg_tip.get("escala") or {}
    escala = (escalas.get(rol) or escalas.get(rol_base(rol)) or escalas.get("portada")
              or "Each headline line stands 13-16% of the frame height, broken over 2-3 lines.")
    # Un acento que aparece en TODOS los slides deja de ser un acento: el beat que lo
    # calla (la tensión) es lo que hace que el color se note en los que lo llevan. Solo
    # se calla la elección AUTOMÁTICA — un span marcado a mano por el usuario manda
    # siempre, que es la única palanca que tiene sobre la jerarquía del titular.
    omitido = cfg_tip.get("acento_omitido")
    sin_acento_auto = rol in [str(r).strip().lower() for r in omitido] if isinstance(omitido, list) else False
    acento = ""
    if m["color_acento"]:
        # El span elegido a mano en la revisión previa manda sobre la elección
        # automática: es la única palanca del usuario sobre la jerarquía del titular.
        elegido = norm["contenido"].get("acento") or ""
        if elegido:
            plantilla_ac = (cfg_tip.get("acento_explicito") or
                            'Exactly this fragment — "{acento}" — in {color_acento}; every other '
                            "word stays in the headline color.")
            acento = plantilla_ac.format(acento=elegido, color_acento=m["color_acento"])
        elif not sin_acento_auto:
            acento = (cfg_tip.get("acento") or
                      "Exactly one span — the single most load-bearing word of the quoted string — in "
                      "{color_acento}; every other word stays in the headline color.").format(
                color_acento=m["color_acento"])
    datos = {
        "display": m["tipografia"] or "ultra-condensed heavy display grotesque in ALL CAPS",
        "escala": escala,
        "color": m["color_texto"] or "light type over the dark areas of the frame",
        "acento": acento,
    }
    plantilla = (cfg_tip.get("plantilla") or
                 "{display}. {escala} Set solid, flush left, optical alignment, no hyphenation; "
                 "{color}. {acento}")
    try:
        seccion = plantilla.format(**datos)
    except (KeyError, IndexError):
        seccion = f"{datos['display']}. {datos['escala']} {datos['color']}. {acento}"
    # La familia de la segunda línea solo se declara si hay segunda línea.
    if len(bloques) > 1 and m["tipografia_secundaria"]:
        seccion += " " + (cfg_tip.get("secundaria") or "Second line: {secundaria}.").format(
            secundaria=m["tipografia_secundaria"])
    return " ".join(seccion.split())


def _seccion_negativos(_norm: dict) -> str:
    negs = _cfg_arch().get("negativos")
    if not isinstance(negs, list) or not negs:
        negs = [
            "no extra text beyond the quoted string", "no watermarks", "no lorem ipsum",
            "no deformed hands", "no fake logos",
        ]
    return "; ".join(str(n).strip() for n in negs if str(n).strip()) + "."


def _clausula_aire(norm: dict) -> str:
    """Lockup de póster que la app pega SIEMPRE a la composición (sección 3).

    La v1 decía "keep the subject out of it": reservaba una banda y echaba al sujeto
    de ella, que es exactamente la receta de "foto arriba, caption abajo". Después se
    declararon las dos bandas de tipo con el sujeto anclado en la central y el solape
    donde el cuadro cae a negro, que es lo que integra tipo e imagen.

    Lo que faltaba era decir de QUÉ está hecho el aire. Pedirlo "flat" hacía que el
    modelo lo entendiera como un rectángulo de color liso —un passe-partout, a veces
    pintado con el hueso de la propia paleta— en vez de una zona tranquila de la foto,
    y el mismo carrusel salía con unos slides a sangre y otros enmarcados. Ahora el
    sangrado se declara y el aire se nombra por sus medios fotográficos.

    En un slide con beat el lockup va en su versión CORTA: el beat ya nombra de qué
    está hecho el aire en ese plano concreto (el falloff de la propia masa, la
    superficie desnuda, el campo vacío), así que la versión larga lo repetiría en
    genérico y costaría ~180 caracteres del presupuesto del brief.
    """
    arch = _cfg_arch()
    es_beat = norm["contenido"]["rol_slide"] in ROLES_BEAT
    plantilla = ((arch.get("composicion_zona_slide") if es_beat else "") or
                 arch.get("composicion_zona") or
                 "Poster lockup, full bleed to all four edges: reserve two uncluttered clear zones for "
                 "the type — {zona} and {zona_kicker} — negative space made of the photograph itself "
                 "(shadow, defocus, bare surface), never panels of flat colour; anchor the subject in "
                 "the central band between them, lit so its silhouette separates from the type; they "
                 "overlap only where the frame falls to near-black.")
    partes = (plantilla.format(**_zona(norm)), _clausula_beat(norm), _clausula_set(norm))
    return " ".join(p for p in partes if p)


def _clausula_beat(norm: dict) -> str:
    """El plano del beat: la mitad determinista de lo que distingue un slide de otro.

    Vive acá, pegada al lockup, y no en el `prompt_base` — que es donde vivía la
    escalera de encuadres anterior. Esa diferencia lo es todo: el `prompt_base` solo
    le llega al arquitecto como "BASE PROMPT (weak, to rewrite)", así que con el LLM
    disponible el encuadre aparecía en el prompt final únicamente si el modelo lo
    repetía por su cuenta… mientras que la cláusula de lockup —que pide siempre el
    mismo cuadro— sí llegaba siempre. La variación estaba en la capa blanda y la
    uniformidad en la dura, y el carrusel salía como versiones de una misma imagen.

    El texto tiene dos mitades con dueños distintos: la FUNCIÓN del plano la fija
    `architect.json` (estructura, igual para todas las marcas) y su ejecución
    fotográfica sale del `ritmo_carrusel` de la identidad activa (marca).
    """
    rol = norm["contenido"]["rol_slide"]
    if rol not in ROLES_BEAT:
        return ""
    cfg_rol = (_cfg_arch().get("roles") or {}).get(rol) or {}
    composicion = str(cfg_rol.get("composicion") or _COMPOSICION_BEAT_FALLBACK[rol]).strip()
    return " ".join(p for p in (composicion, _ritmo_beat(norm, rol)) if p)


def _clausula_luz(norm: dict) -> str:
    """Bloqueo de luz que la app PREFIJA siempre a la sección 6.

    Es la corrección de un defecto estructural, no un refuerzo: la sección 6 la
    escribía entera el LLM, una vez por imagen y sin conocer a sus hermanas, así que
    un carrusel de cinco piezas salía con cinco esquemas de iluminación —y ninguna
    temperatura de color en todo el pipeline—. El esquema de iluminación es lo que
    hace que cinco fotos parezcan del mismo día: por definición no puede decidirse
    cinco veces.

    Matiza —no revierte— la regla de que `image_style` gana a `tono_visual`. El
    TRATAMIENTO fotográfico sigue siendo creatividad por pieza y vive en la sección 7;
    la luz pasa a ser propiedad de la identidad y la escribe la app. Por eso el texto
    sale de `luz_identidad` (la identidad activa, con respaldo en `brand.json`) y
    nunca del `image_style` del post: si saliera de ahí, volvería a cambiar por pieza.

    Tiene que ser **byte a byte idéntico** en la portada y en los cuatro beats, así
    que no depende del rol ni entra en la poda de `_ajustar_longitud`.
    """
    arch = _cfg_arch()
    plantilla = (arch.get("luz_bloqueada") or
                 "LIGHT LOCK — identical in every piece of this set: {clave} {temperatura} "
                 "Falloff to {fondo} at the edges, no ambient fill, one visible contact shadow.")
    clave = _recortar(norm["marca"].get("luz_identidad") or "",
                      _entero(_validacion_cfg(), "luz_palabras", 22))
    # `_recortar` solo pone el punto cuando recorta; sin él la frase de la marca se
    # pega a la de la temperatura y las dos se leen como una sola instrucción rota.
    if clave:
        clave = clave.rstrip(" .,;:") + "."
    temperatura = str(arch.get("luz_temperatura") or
                      "Colour temperature fixed at 5400K neutral: no warm or cool drift "
                      "between pieces.").strip()
    # El fondo es el PRIMER color de la paleta (la lista está ordenada [fondo, texto,
    # acento]): es contra él que muere la luz en los bordes.
    fondo = (norm["marca"].get("paleta") or "").split(",")[0].strip() or "near-black"
    try:
        return " ".join(plantilla.format(clave=clave, temperatura=temperatura,
                                         fondo=fondo).split())
    except (KeyError, IndexError):
        return ""


def _clausula_set(norm: dict) -> str:
    """Continuidad del carrusel, solo en los slides: mismo mundo, objeto distinto.

    Es el reemplazo textual de lo que antes se intentaba pasando la portada en
    `medias`. Aquello no era una referencia de estilo sino image-to-image, así que
    devolvía la portada re-encuadrada; esto pide explícitamente las dos mitades —lo
    que se comparte y lo que tiene que cambiar—, que es lo que un director de arte
    le diría a un fotógrafo. La escena de la portada se recorta: entra como ancla,
    no como un segundo brief que se coma el presupuesto de caracteres.
    """
    c = norm["contenido"]
    # La comparación va contra `rol_base`, NUNCA contra el literal "contenido": desde
    # la escalera de beats los slides llegan como `tension|desarrollo|prueba|remate` y
    # ninguno es "contenido", así que esta cláusula dejó de emitirse en TODOS los
    # slides de carrusel sin un solo error — y con ella se fue lo único que declaraba
    # el mundo compartido. Es el fallo que produjo carruseles con cinco localizaciones
    # distintas y, a la vez, el objeto de la portada repetido en tres piezas.
    if rol_base(c["rol_slide"]) != "contenido" or not c.get("escena_portada"):
        return ""
    arch = _cfg_arch()
    plantilla = (arch.get("continuidad_set") or
                 "SET CONTINUITY: same location, surfaces, light setup and palette as the carousel "
                 "cover ({escena_portada}), but a DIFFERENT hero object, camera position and framing "
                 "— never the cover restaged or re-cropped.")
    # La cita de la portada es lo único variable de esta cláusula, y solo la pagan los
    # slides: con 18 palabras el peor caso (slide con kicker) se pasaba del techo aun
    # podando las creativas al mínimo, y entonces el validador tira el prompt entero.
    # Basta con nombrar el sitio, la superficie y la luz — el resto es el brief de la
    # portada comiéndose el presupuesto del slide.
    tope = _entero(_validacion_cfg(), "continuidad_set_palabras", 12)
    try:
        return plantilla.format(escena_portada=_recortar(c["escena_portada"], tope).rstrip("."))
    except (KeyError, IndexError):
        return ""


# ── Secciones creativas: respaldo determinista ────────────────────────────────

def _respaldos(norm: dict) -> dict[str, str]:
    """Secciones creativas sin LLM. Deben bastar para pasar el validador."""
    r = _cfg_arch().get("respaldos") or {}
    c, m = norm["contenido"], norm["marca"]
    base = norm["prompt_base"] or (
        f"A single concrete object that embodies {c['tema'] or 'the topic'}, resting on a worn "
        "surface and casting a soft contact shadow."
    )
    datos = {
        "prompt_base": base,
        "tema": c["tema"],
        "angulo": c["angulo"],
        "paleta": m["paleta_nombres"] and f"{m['paleta_nombres']} ({m['paleta']})" or m["paleta"],
        "tipografia": m["tipografia"],
        "color_texto": m["color_texto"] or "white over dark areas",
        # Sin el punto final: la plantilla ya pone el suyo (el `image_style` del LLM
        # suele venir con punto y salían dos seguidos).
        "tono_visual": m["tono_visual"].rstrip(". "),
        "referencias": ", ".join(norm["referencias"]) or "editorial press photography",
    }

    def _fmt(clave: str, defecto: str) -> str:
        try:
            return str(r.get(clave) or defecto).format(**datos).strip()
        except (KeyError, IndexError):
            return defecto

    # `tipografia` no está: la escribe siempre la app (`_seccion_tipografia`).
    return {
        "sujeto": _fmt("sujeto", base),
        "composicion": _fmt("composicion", "One clear focal hierarchy: the subject centred in the "
                                           "middle band, secondary elements falling away in depth."),
        "luz": _fmt("luz", f"Single hard source raking from above, deep falloff into near-black, "
                           f"visible contact shadow; palette held to {m['paleta']}."),
        "estilo": _fmt("estilo", m["tono_visual"] or "cinematic poster still"),
        "camara": _fmt("camara", "50mm equivalent, f/4, natural micro-texture, fine grain."),
    }


# ── Ensamblado y validación ───────────────────────────────────────────────────

# Palabras que no pueden quedar al final de un recorte: cortar en "…texture and."
# deja la frase colgando y el modelo la lee como ruido.
_COLGANTES = {
    "and", "or", "with", "of", "the", "a", "an", "at", "in", "on", "for", "to",
    "from", "by", "its", "their", "that", "which", "plus", "into", "over", "as",
}


def _recortar(texto: str, max_palabras: int) -> str:
    """Acorta a `max_palabras` cortando en la última pausa y sin dejar cabos sueltos."""
    palabras = (texto or "").strip().split()
    if len(palabras) <= max_palabras:
        return " ".join(palabras)
    trozo = palabras[:max_palabras]
    for i in range(len(trozo) - 1, max_palabras // 2, -1):
        if trozo[i][-1:] in (",", ";", ".", ":"):
            trozo = trozo[:i + 1]
            break
    while trozo and trozo[-1].lower().strip(",;:.") in _COLGANTES:
        trozo.pop()
    # Un paréntesis abierto y sin cerrar es basura para el modelo: cortar la paleta
    # "…acid lime (#0B0C0E, #EDEAE0, #C9F227)" a mitad dejaba "…acid lime (#0B0C0E."
    # en el prompt. Si el recorte desbalanceó, se tira desde el paréntesis abierto.
    while trozo and " ".join(trozo).count("(") > " ".join(trozo).count(")"):
        corte = next((i for i in range(len(trozo) - 1, -1, -1) if "(" in trozo[i]), None)
        if corte is None:
            break
        trozo = trozo[:corte]
        while trozo and trozo[-1].lower().strip(",;:.") in _COLGANTES:
            trozo.pop()
    # Se quita también el punto: si la última palabra ya lo traía, concatenar el
    # nuestro dejaba ".." en el prompt.
    return " ".join(trozo).rstrip(",;:.") + "."


def _ajustar_longitud(creativas: dict[str, str], armar) -> tuple[str, dict[str, str], bool]:
    """Arma el prompt y, si se pasa de largo, acorta las secciones creativas.

    Pasarse de `max_caracteres` no puede costar el trabajo del LLM: antes de tirarlo
    entero se le van quitando palabras a las secciones creativas (las fijas —formato,
    texto y negativos— no se tocan nunca). Devuelve (prompt, secciones, se_recortó).
    """
    max_c = _entero(_validacion_cfg(), "max_caracteres", _MAX_CARACTERES)
    prompt, secciones = armar(creativas)
    if len(prompt) <= max_c:
        return prompt, secciones, False
    # El último escalón (10) existe por el peor caso de un slide: secciones creativas
    # largas MÁS la cláusula de continuidad de set. Pasarse del techo no es una opción
    # —el validador tira el prompt entero y la imagen sale con el prompt base— así que
    # se prefiere una sección creativa escueta a no tener brief.
    for tope in (26, 22, 18, 14, 10):
        podadas = {k: _recortar(v, tope) for k, v in creativas.items()}
        prompt, secciones = armar(podadas)
        if len(prompt) <= max_c:
            return prompt, secciones, True
    return prompt, secciones, True


def ensamblar(secciones: dict[str, str]) -> str:
    """Une las secciones en el prompt final numerado, en el orden obligatorio."""
    lineas = []
    for i, sec in enumerate(_secciones_cfg(), 1):
        valor = (secciones.get(sec["clave"]) or "").strip()
        lineas.append(f"{i}. {sec['etiqueta']}: {valor}")
    return "\n".join(lineas)


def validar(prompt: str, *, texto: str = "", bloques: list[str] | None = None,
            aspect_ratio: str = "") -> list[str]:
    """Devuelve la lista de errores del prompt ([] si es válido).

    Rechaza (paso 5 del encargo): que falte el texto exacto, que falte alguna de
    las 9 secciones o estén desordenadas, que no se declare el aspecto o la zona
    de aire negativo, y que la longitud se salga del rango razonable para
    Higgsfield (el MCP trunca el prompt a 2000 caracteres).
    """
    errores: list[str] = []
    p = prompt or ""
    v = _validacion_cfg()

    esperados = bloques if bloques else _bloques(texto)
    for bloque in esperados:
        if bloque and bloque not in p:
            errores.append(f'el prompt no contiene el texto literal a renderizar: "{bloque}"')

    pos = -1
    for i, sec in enumerate(_secciones_cfg(), 1):
        marca = f"{i}. {sec['etiqueta']}:"
        idx = p.find(marca)
        if idx < 0:
            errores.append(f"falta la sección {i} ({sec['etiqueta']})")
            continue
        if idx < pos:
            errores.append(f"la sección {i} ({sec['etiqueta']}) está fuera de orden")
        pos = idx
        # Una sección presente pero vacía es lo mismo que una sección ausente.
        resto = p[idx + len(marca):].split("\n", 1)[0].strip()
        if not resto:
            errores.append(f"la sección {i} ({sec['etiqueta']}) está vacía")

    if aspect_ratio and aspect_ratio not in p:
        errores.append(f"el prompt no declara el aspect ratio ({aspect_ratio})")

    marcas_aire = v.get("marcas_aire_negativo") or ["negative space", "clear zone", "reserved"]
    if not any(str(m).lower() in p.lower() for m in marcas_aire):
        errores.append("el prompt no declara una zona de aire negativo para el texto")

    # Sin área segura declarada el modelo pega el titular al borde y lo corta: es el
    # defecto que se veía en producción, así que es criterio de validación, no consejo.
    marcas_area = v.get("marcas_area_segura") or ["safe margin", "safe area"]
    if not any(str(m).lower() in p.lower() for m in marcas_area):
        errores.append("el prompt no declara el área segura del texto (margen cuantificado)")

    min_c = _entero(v, "min_caracteres", _MIN_CARACTERES)
    max_c = _entero(v, "max_caracteres", _MAX_CARACTERES)
    if len(p) < min_c:
        errores.append(f"prompt demasiado corto ({len(p)} < {min_c} caracteres)")
    elif len(p) > max_c:
        errores.append(f"prompt demasiado largo ({len(p)} > {max_c} caracteres)")

    return errores


def adjetivos_vacios(texto: str) -> list[str]:
    """Adjetivos prohibidos ("beautiful", "modern"...) presentes en el texto."""
    banned = _cfg_arch().get("adjetivos_prohibidos") or []
    bajo = (texto or "").lower()
    return [a for a in banned if re.search(rf"\b{re.escape(str(a).lower())}\b", bajo)]


# ── LLM: arquitectura y auto-crítica ──────────────────────────────────────────

def _linea_beat(rol: str) -> list[str]:
    """El beat, para el arquitecto: su función y la prohibición de repetir el plano.

    Sin la prohibición el modelo escribe su propio encuadre en `composicion` y choca
    con la cláusula determinista que la app le pega debajo — dos instrucciones de
    cámara contradictorias en la misma sección.
    """
    if rol not in ROLES_BEAT:
        return []
    cfg_rol = (_cfg_arch().get("roles") or {}).get(rol) or {}
    funcion = str(cfg_rol.get("funcion") or "").strip()
    linea = f"CAROUSEL BEAT: {rol}"
    if funcion:
        linea += f" — {funcion}"
    return [linea + ". The app appends this beat's shot scale and framing verbatim: describe "
                    "the subject and its scene, never the camera distance or the type zones."]


def _mensaje_arquitecto(norm: dict) -> str:
    arch = _cfg_arch()
    llm_cfg = arch.get("llm") or {}
    c, m = norm["contenido"], norm["marca"]
    instruccion = (llm_cfg.get("instruccion") or "Rewrite the base prompt into JSON sections.")
    try:
        instruccion = instruccion.format(
            adjetivos_prohibidos=", ".join(str(a) for a in (arch.get("adjetivos_prohibidos") or []))
        )
    except (KeyError, IndexError):
        pass
    datos = [
        f"TOPIC: {c['tema'] or '(not given)'}",
        f"ANGLE: {c['angulo'] or '(not given)'}",
        f"SLIDE ROLE: {c['rol_slide']}",
        *_linea_beat(c["rol_slide"]),
        f"ASPECT RATIO: {m['aspect_ratio']}",
        f"BRAND PALETTE: {m['paleta_nombres']} ({m['paleta']})" if m["paleta_nombres"]
        else f"BRAND PALETTE: {m['paleta']}",
        # La tipografía va como CONTEXTO (ayuda a componer para ese tipo), no como
        # encargo: la sección 5 la escribe la app.
        f"BRAND TYPE (context only, the app writes the typography section): {m['tipografia']}",
        f"BRAND VISUAL TONE: {m['tono_visual']}",
        f"VISUAL REFERENCES: {', '.join(norm['referencias']) or '(none)'}",
        f"BASE PROMPT (weak, to rewrite): {norm['prompt_base'] or '(empty)'}",
        # El texto va SOLO como contexto: la sección 4 la escribe la app.
        f"TEXT THAT WILL BE RENDERED (context only, never write it into a section): "
        f"\"{c['texto_exacto_a_renderizar']}\"",
    ]
    # En los slides, la portada entra como CONTEXTO DE SET con su prohibición al lado.
    # Sin la prohibición explícita el modelo toma la escena de la portada como el
    # sujeto a describir y los slides salen siendo la misma foto contada de nuevo.
    # Igual que en `_clausula_set`: contra `rol_base` y nunca contra el literal
    # "contenido", porque los slides llegan con el nombre de su beat.
    if rol_base(c["rol_slide"]) == "contenido" and c.get("escena_portada"):
        datos.append(
            "CAROUSEL COVER ALREADY SHOT (same set, same light, same palette — reuse the WORLD, "
            f"never its hero object or its framing): {c['escena_portada']}"
        )
    return instruccion + "\n\n" + "\n".join(datos)


def _mensaje_critico(prompt: str) -> str:
    rub = prompt_config.rubric()
    criterios = rub.get("criterios") or []
    escala = _entero(rub, "escala_max", 5)
    umbral = _entero(rub, "umbral", 4)
    instruccion = rub.get("instruccion") or "Score the prompt and rewrite it if weak. Return JSON."
    texto_criterios = "\n".join(
        f"- {c.get('clave')}: {c.get('descripcion', '')}" for c in criterios if isinstance(c, dict)
    )
    claves = ", ".join(f'"{c.get("clave")}": 0' for c in criterios if isinstance(c, dict))
    try:
        instruccion = (instruccion
                       .replace("{criterios}", texto_criterios)
                       .replace("{claves}", claves)
                       .replace("{escala_max}", str(escala))
                       .replace("{umbral}", str(umbral)))
    except Exception:
        pass
    return f"{instruccion}\n\nPROMPT TO REVIEW:\n{prompt}"


def _secciones_desde_llm(data: dict, norm: dict) -> dict[str, str]:
    """Toma del JSON del modelo solo las claves creativas, recortadas y limpias."""
    max_pal = _entero(_validacion_cfg(), "max_palabras_seccion", _MAX_PALABRAS_SECCION)
    texto = norm["contenido"]["texto_exacto_a_renderizar"]
    out: dict[str, str] = {}
    for clave in _CLAVES_CREATIVAS:
        valor = data.get(clave)
        if not isinstance(valor, str) or not valor.strip():
            continue
        limpio = " ".join(valor.split())
        # El modelo a veces cuela el texto de la pieza dentro de una sección creativa;
        # ahí duplicaría el bloque de la sección 4 y el render saca el texto dos veces.
        limpio = limpio.replace(f'"{texto}"', "").replace(texto, "").strip()
        if limpio:
            out[clave] = _recortar(limpio, max_pal)
    return out


# ── Construcción ──────────────────────────────────────────────────────────────

def construir(spec: dict, *, cfg=None, usar_llm: bool = True, autocritica: bool = True,
              refuerzo_texto: bool = False) -> ResultadoPrompt:
    """Construye el prompt final estructurado a partir de la spec.

    `cfg` es el Config del proyecto (para las keys del LLM); sin él —o sin keys—
    todo se resuelve con los respaldos deterministas y el prompt sale igual de
    válido. `refuerzo_texto` añade la instrucción de refuerzo del bloque de texto
    (la usa el reintento del QA de visión cuando el render salió mal escrito).

    Lanza `PromptInvalido` solo si ni siquiera el camino determinista produce un
    prompt válido (config rota) o si falta el texto a renderizar.
    """
    norm = normalizar_spec(spec)
    bloques = _bloques(norm["contenido"]["texto_exacto_a_renderizar"])
    avisos: list[str] = []
    usos: list[dict] = []
    if len(bloques) > 1:
        avisos.append("el texto se dividió en titular + subtítulo por longitud")

    fijas = {
        "pieza": _seccion_pieza(norm),
        "texto": _seccion_texto(norm, bloques, refuerzo=refuerzo_texto),
        "tipografia": _seccion_tipografia(norm, bloques),
        "negativos": _seccion_negativos(norm),
    }
    respaldo = _respaldos(norm)
    creativas = dict(respaldo)
    fuente = "respaldo"

    if usar_llm and cfg is not None and llm_json.disponible(cfg):
        llm_cfg = _cfg_arch().get("llm") or {}
        try:
            data, uso = llm_json.complete_json(
                llm_cfg.get("sistema") or "You are a senior art director. Answer with JSON only.",
                _mensaje_arquitecto(norm), cfg=cfg,
                max_tokens=_entero(llm_cfg, "modelo_max_tokens", 1200),
            )
            if uso:
                usos.append(uso)
            desde_llm = _secciones_desde_llm(data, norm)
            if desde_llm:
                creativas.update(desde_llm)
                fuente = "llm"
                faltantes = [k for k in _CLAVES_CREATIVAS if k not in desde_llm]
                if faltantes:
                    avisos.append(f"secciones con respaldo: {', '.join(faltantes)}")
            else:
                avisos.append("el LLM no devolvió secciones utilizables — respaldo determinista")
        except Exception as e:
            avisos.append(f"arquitectura de prompt sin LLM ({e})")

    def _armar(secs_creativas: dict[str, str]) -> tuple[str, dict[str, str]]:
        completas = dict(fijas)
        completas.update(secs_creativas)
        # La cláusula de aire negativo la pone SIEMPRE la app: es la que garantiza
        # el hueco limpio donde va el texto, y es criterio de validación.
        completas["composicion"] = f"{completas.get('composicion', '').strip()} {_clausula_aire(norm)}".strip()
        # El bloqueo de luz va PREFIJADO (la composición lo lleva detrás): es lo
        # invariante del set y tiene que leerse antes que el detalle de esta escena.
        # Al pegarse acá y no en las creativas, la poda no lo toca — que es el punto:
        # tiene que salir byte a byte igual en las N piezas del job.
        completas["luz"] = f"{_clausula_luz(norm)} {completas.get('luz', '').strip()}".strip()
        return ensamblar(completas), completas

    prompt, secciones, recortado = _ajustar_longitud(creativas, _armar)
    if recortado:
        avisos.append("secciones creativas acortadas para caber en el límite de Higgsfield")
    errores = validar(prompt, bloques=bloques, aspect_ratio=norm["marca"]["aspect_ratio"])
    if errores and fuente == "llm":
        # El texto del modelo rompió el prompt (demasiado largo, sección vacía…):
        # se vuelve al determinista, que sí es predecible.
        avisos.append(f"prompt del LLM rechazado por el validador ({'; '.join(errores)})")
        creativas, fuente = dict(respaldo), "respaldo"
        prompt, secciones, _ = _ajustar_longitud(creativas, _armar)
        errores = validar(prompt, bloques=bloques, aspect_ratio=norm["marca"]["aspect_ratio"])
    if errores:
        raise PromptInvalido(errores)

    vacios = adjetivos_vacios(" ".join(creativas.values()))
    if vacios:
        avisos.append(f"adjetivos vacíos en el prompt: {', '.join(vacios)}")

    resultado = ResultadoPrompt(prompt=prompt, secciones=secciones, bloques=bloques,
                                usos=usos, avisos=avisos, fuente=fuente)

    if autocritica and cfg is not None and llm_json.disponible(cfg):
        _autocritica(resultado, norm, bloques, _armar, cfg)
    return resultado


def _autocritica(res: ResultadoPrompt, norm: dict, bloques: list[str], armar, cfg) -> None:
    """Paso 4: puntuar contra el rubric y reescribir mientras algo quede por debajo.

    Máximo `max_iteraciones` vueltas. Best-effort de punta a punta: cualquier fallo
    deja el prompt que ya teníamos (que es válido) y anota el motivo.
    """
    rub = prompt_config.rubric()
    umbral = _entero(rub, "umbral", 4)
    max_iter = _entero(rub, "max_iteraciones", 2)

    for _ in range(max(0, max_iter)):
        try:
            data, uso = llm_json.complete_json(
                rub.get("sistema") or "You are a demanding art director. Answer with JSON only.",
                _mensaje_critico(res.prompt), cfg=cfg,
                max_tokens=_entero(rub, "modelo_max_tokens", 1500),
            )
            if uso:
                res.usos.append(uso)
        except Exception as e:
            res.avisos.append(f"auto-crítica no ejecutada ({e})")
            return

        puntajes = data.get("puntajes")
        if isinstance(puntajes, dict):
            limpios: dict[str, float] = {}
            for k, v in puntajes.items():
                try:
                    limpios[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            if limpios:
                res.puntajes = limpios
        bajos = [k for k, v in res.puntajes.items() if v < umbral]
        if not bajos:
            return

        nuevas = _secciones_desde_llm(data.get("secciones") or {}, norm)
        if not nuevas:
            res.avisos.append(f"criterios por debajo de {umbral} sin reescritura: {', '.join(bajos)}")
            return

        creativas = {k: res.secciones.get(k, "") for k in _CLAVES_CREATIVAS}
        # La composición guardada ya lleva la cláusula de aire pegada; se descarta y
        # se vuelve a pegar al armar, para no duplicarla en cada iteración.
        creativas["composicion"] = nuevas.get("composicion") or creativas["composicion"].split(
            _clausula_aire(norm))[0].strip()
        # Lo mismo con la luz, que va prefijada: se toma lo que hay DESPUÉS del bloqueo
        # (`[-1]`, no `[0]`). Sin esto cada iteración de la auto-crítica dejaría un
        # LIGHT LOCK más en la sección 6.
        creativas["luz"] = nuevas.get("luz") or creativas["luz"].split(
            _clausula_luz(norm))[-1].strip()
        creativas.update(nuevas)
        prompt, secciones, _ = _ajustar_longitud(creativas, armar)
        errores = validar(prompt, bloques=bloques, aspect_ratio=norm["marca"]["aspect_ratio"])
        if errores:
            res.avisos.append(f"reescritura descartada por el validador ({'; '.join(errores)})")
            return
        res.prompt, res.secciones = prompt, secciones
        res.iteraciones += 1
        res.fuente = "llm"
