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
  las dos bandas reservadas para el tipo y el sujeto entre ellas. La JERARQUÍA dentro
  de ese esqueleto no es la misma en las dos piezas: en la portada manda la imagen
  (el sujeto es el asunto y el titular lo acompaña) y en un slide de contenido manda
  el TEXTO (el titular es el elemento mayor del cuadro y el sujeto queda subordinado,
  bajo, pequeño). Una portada engancha con una imagen; un slide transmite, y lo que
  transmite es lo que está escrito. Se declara en las secciones 1, 3 y 5 a la vez —
  pieza, lockup y cuerpo— porque declararlo en una sola no alcanza: el modelo cumple
  el porcentaje del titular y aun así fotografía un objeto que se lleva más cuadro.
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

import hashlib
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
    bloques: list[str] = field(default_factory=list)     # los strings impresos, en orden
    # Los mismos strings pero con su clave de bloque (`titular`, `cuerpo`…), en el orden
    # del sistema. Lo necesita el QA por nivel: un cuerpo de 30 palabras no se puede
    # exigir carácter a carácter como un titular, así que hay que saber cuál es cuál.
    bloques_por_clave: dict[str, str] = field(default_factory=dict)
    sistema_texto: str = ""
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
# beats es el esqueleto del lockup —titular arriba, apoyo al pie—: unificarlo fue una
# corrección deliberada y es lo que hace que el set se lea como un sistema.
#
# Los cuatro planos subordinan el sujeto al tipo (va bajo y pequeño, ver `_clausula_aire`)
# pero conservan su ESCALERA DE DISTANCIAS: macro → media → cenital → wide. Las dos cosas
# van juntas y no es un detalle — subordinar el objeto sin conservar la escalera deja los
# cuatro slides sin nada que los distinga salvo el texto, que es el carrusel que se lee
# como una plantilla rellenada.

# Respaldos mínimos por si `architect.json` falta entero: como en el resto del
# módulo, sin config el prompt sale menos afinado pero igual de válido.
_COMPOSICION_BEAT_FALLBACK = {
    "tension": "SHOT — TENSION, the closest framing of the set: the subject crops hard against "
               "the bottom edge under the type, its own falloff and defocus forming the clear "
               "zone above.",
    "desarrollo": "SHOT — DEVELOPMENT, a calm mid-distance view: the subject sits whole along "
                  "the lower edge, the set legible behind it, bare surface forming the clear "
                  "zone above.",
    "prueba": "SHOT — EVIDENCE, shot square-on: one object alone in the lower band on a bare "
              "field, the rest of the frame an empty clear zone, nothing competing with the type.",
    "remate": "SHOT — PAYOFF, the widest and deepest frame of the set: the subject reads small "
              "at the foot of the frame, a large quiet space opening above it for the type.",
}
_FUNCION_BEAT_FALLBACK = {
    "tension": "the tension — the problem, symptom or cost the reader recognises",
    "desarrollo": "the development — the explanation or step that moves the idea one notch forward",
    "prueba": "the evidence — the concrete figure, example or result that proves it",
    "remate": "the payoff — the line the reader would screenshot, still an idea from the source",
}
# Los cuatro decían dónde estaba el sujeto además de a qué distancia ("still life on a
# bare surface", "flat overhead of one object on an empty field"), y eran una de las seis
# fuentes del carrusel de mesas: el ritmo es DISTANCIA, altura de cámara y qué llena el
# cuadro — el lugar lo declara el bloqueo de mundo, que además es igual en todas las piezas.
_RITMO_FALLBACK = {
    "tension": "The tightest shot of the set: one texture fills the lower frame, its edges "
               "cropped away.",
    "desarrollo": "Reading distance, camera at the subject's own height, the thing whole and "
                  "whatever holds it visible.",
    "prueba": "Square to the subject, one thing isolated against the plainest plane of the "
              "location, nothing else competing.",
    "remate": "The widest and deepest shot of the set, the subject small inside the space it "
              "belongs to.",
}


# ── Arco del carrusel y mundo de la pieza ─────────────────────────────────────
#
# Dos ejes que se eligen UNA vez por job y se aplican idénticos a las N piezas. Es el
# patrón que el proyecto ya validó con el bloqueo de luz, y por el mismo motivo: lo que
# es invariante dentro de un set no puede decidirse una vez por imagen, o el set deja de
# ser un set. Quien elige y congela es `job_runner.make_job` —el único sitio donde se
# construye el shape del job, así que los dos flujos lo heredan—; acá viven la elección
# determinista y las cláusulas que se emiten.
#
# EL MUNDO (`escenario`) es de la MARCA. Es el campo que faltaba: `ritmo_carrusel` solo
# dice a qué distancia y a qué altura se fotografía, nunca DÓNDE, así que dos identidades
# con paleta y tipografía completamente distintas producían la misma foto — un objeto
# sobre una mesa — porque el lugar estaba escrito en la capa dura y era siempre el mismo.
#
# EL ARCO (`arco_carrusel`) es ESTRUCTURA, como los beats: qué encadena las N imágenes
# entre sí. Sin él, `continuidad_set` pedía "mismo cuarto, objeto protagonista DISTINTO",
# que es una regla de CATÁLOGO y no de relato — y produce exactamente lo que se auditó:
# cinco props sin relación sobre la misma madera. El camino de video ya lo hacía bien
# ("keep a recurring anchor… chain the beats"); el de imagen se escribió al revés.
#
# LA FRONTERA ENTRE ARCO Y BEAT ES DURA: **el arco elige QUÉ hay delante de la cámara, el
# beat elige CÓMO se fotografía.** Un `enlace` que hablara de distancia, altura o encuadre
# colisionaría con la cláusula de plano del beat —que va pegada a ella en la misma
# sección— y ante dos instrucciones de cámara contradictorias el modelo elige una. Es la
# misma lección del paso 12.

ARCOS = ("transformacion", "cadena", "recorrido", "escala")

# Si el objeto protagonista VUELVE entre slides. Lo consultan el redactor (para escribir
# las escenas encadenadas o distintas) y el lint (que no puede avisar de «escena repetida»
# cuando la repetición es justo lo que el arco pide).
_ARCO_FALLBACK = {
    "transformacion": {
        "sujeto": "recurrente",
        "funcion": "the same hero object comes back in every slide with its STATE moved on — "
                   "whole to broken, full to empty, one to many. The object recurs; its "
                   "condition never does",
        "enlace": "SET ARC — TRANSFORMATION: the cover's own hero object at a later state; same "
                  "thing, different condition, and the change is visible.",
    },
    "cadena": {
        "sujeto": "encadenado",
        "funcion": "each slide opens on something the previous one left behind — what it "
                   "produced, what it became, what it fed into — and names it explicitly",
        "enlace": "SET ARC — CHAIN: the hero object is what the previous piece left behind — its "
                  "product, its residue, what it fed into.",
    },
    "recorrido": {
        "sujeto": "distinto",
        "funcion": "one place gone through part by part, each slide a different corner of the "
                   "SAME location",
        "enlace": "SET ARC — WALKTHROUGH: a different corner of the same location, with its own "
                  "hero object; the place is continuous, the part of it is new.",
    },
    "escala": {
        "sujeto": "distinto",
        "funcion": "pieces of one system: the loose part, the assembly it belongs to, the space "
                   "it is installed in — every slide a different rung of the same thing",
        "enlace": "SET ARC — SYSTEM: a different rung of the cover's own system — the loose part, "
                  "the assembly, the installation; never the same thing twice.",
    },
}
_SUJETO_ARCO_FALLBACK = "distinto"

# Respaldo del repertorio de mundos por si `architect.json` falta entero. Como el del
# archivo, NO es todo tabletop a propósito: el bodegón de mesa es una pieza legítima, pero
# como una opción entre varias y no como el default invisible que nadie eligió.
_ESCENARIOS_FALLBACK = (
    "A working workshop: concrete floor, steel racking, tools left where they were used.",
    "A lived-in interior: plaster walls, a deep window, worn board floor.",
    "The edge of a built place, outdoors: wet asphalt, brick, painted metal.",
    "A bare studio table under one lamp: the subject and its shadow, nothing else.",
)


def _indice_estable(valor: str, n: int) -> int:
    """Índice reproducible en `[0, n)` a partir de una cadena.

    Con `hash()` no serviría: para `str` está aleatorizado por proceso
    (PYTHONHASHSEED), así que un reinicio del servidor le daría otro arco al mismo job
    — y el prompt de una imagen tiene que poder reconstruirse igual al rehacerla desde
    la revisión, meses después.
    """
    if n <= 0:
        return 0
    digest = hashlib.blake2b(str(valor or "").encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % n


def arcos_disponibles() -> tuple[str, ...]:
    """Los arcos declarados en `architect.json`, o los de respaldo. Fuente única."""
    arcos = _cfg_arch().get("arcos")
    if isinstance(arcos, dict):
        validos = tuple(str(a).strip() for a in arcos if str(a).strip())
        if validos:
            return validos
    return ARCOS


def elegir_arco(semilla: str) -> str:
    """El arco de ESTE carrusel, elegido de forma determinista desde `semilla`.

    Rota entre los arcos declarados para que dos posts seguidos no cuenten igual, y es
    reproducible a propósito (ver `_indice_estable`). Quien llama pasa el id del job y
    **congela** el resultado en `params`: elegir dos veces sería elegir distinto.
    """
    disponibles = arcos_disponibles()
    return disponibles[_indice_estable(f"arco:{semilla}", len(disponibles))]


def escenarios_de(identidad: dict | None = None) -> list[str]:
    """El repertorio de mundos aplicable: identidad → `brand.json` → `architect.json`.

    La MISMA cadena de respaldo que `encuadre_beat`, y por el mismo motivo: si esta se
    saltara un escalón, un job sin identidad propia declararía un mundo por un lado y
    otro por el otro. Se cae de fuente en fuente, no campo a campo: un repertorio es una
    lista de alternativas y mezclar las de dos marcas no produce una tercera marca.
    """
    ident = identidad if isinstance(identidad, dict) else {}
    for fuente in (ident.get("escenarios"),
                   prompt_config.brand().get("escenarios"),
                   _cfg_arch().get("escenarios_respaldo")):
        if isinstance(fuente, (list, tuple)):
            limpios = [str(e).strip() for e in fuente if str(e).strip()]
            if limpios:
                return limpios
    return list(_ESCENARIOS_FALLBACK)


def elegir_escenario(semilla: str, identidad: dict | None = None) -> str:
    """El mundo de ESTE job. Determinista y reproducible, como `elegir_arco`.

    La semilla se sala distinto que la del arco: con la misma, un repertorio de cuatro
    mundos y cuatro arcos quedarían emparejados uno a uno para siempre, y el taller
    saldría siempre con transformación.
    """
    repertorio = escenarios_de(identidad)
    if not repertorio:
        return ""
    return repertorio[_indice_estable(f"mundo:{semilla}", len(repertorio))]


# ── Sistemas de texto: cuántos niveles lleva un slide ────────────────────────
#
# Mismo reparto que `ritmo_carrusel`, que es la frontera que este proyecto ya validó:
# QUÉ bloques existen y dónde van es LAYOUT (universal, `architect.json`); cuál de los
# sistemas usa esta marca es IDENTIDAD (repertorio en la identidad visual, el job
# congela uno). Lo que corrigen: un slide solo sabía imprimir un titular, así que un
# carrusel de cuatro tenía ~56 palabras para contar un video entero — con ese
# presupuesto no se narra, solo se titula.
#
# SOLO EN LOS SLIDES: la portada mantiene su lockup de siempre y `normalizar_spec` lo
# fuerza en un único sitio, para que ningún camino pueda pedirle otra cosa.

SISTEMAS = ("titular", "titular_cuerpo", "etiqueta_titular_cuerpo")
# Claves de bloque conocidas. El orden NO es el de emisión (ese lo dicta cada sistema):
# es el orden en el que se muestran los campos y en el que el redactor los escribe.
CLAVES_BLOQUE = ("etiqueta", "titular", "cuerpo", "apoyo")
_SISTEMA_BASE = "titular"

# Respaldo mínimo por si `architect.json` falta entero: reproduce el lockup histórico
# (titular arriba, apoyo al pie) para que el módulo siga produciendo una pieza válida.
_SISTEMA_FALLBACK = {
    "nombre": "Titular",
    "acento": "titular",
    "encargo": "ONE printed idea per slide",
    "bloques": [
        {"clave": "titular", "zona": "zona", "palabras": [3, 8],
         "cita": 'HEADLINE "{texto}" — the largest element in the frame, {alineacion} in {zona}',
         "escala": "The headline stands 15-18% of the frame height per line, over 2-3 lines."},
        {"clave": "apoyo", "zona": "zona_kicker", "palabras": [0, 8],
         "cita": 'SECOND LINE "{texto}" at ~40% of the headline size, {alineacion} in {zona}',
         "escala": "The second line runs at ~40% of the headline size, one line."},
    ],
}


def sistemas_disponibles() -> tuple[str, ...]:
    """Los sistemas declarados en `architect.json`, o los de respaldo. Fuente única.

    La importa `visual_identity` para validar el repertorio de una identidad: la lista
    se lee, nunca se copia (mismo criterio que `ROLES_RITMO = ROLES_BEAT`).
    """
    sistemas = _cfg_arch().get("sistemas_texto")
    if isinstance(sistemas, dict):
        validos = tuple(str(s).strip() for s in sistemas if str(s).strip())
        if validos:
            return validos
    return SISTEMAS


def _cfg_sistema(sistema: str) -> dict:
    cfg = (_cfg_arch().get("sistemas_texto") or {}).get(str(sistema or "").strip())
    if isinstance(cfg, dict) and isinstance(cfg.get("bloques"), list) and cfg["bloques"]:
        return cfg
    base = (_cfg_arch().get("sistemas_texto") or {}).get(_SISTEMA_BASE)
    if isinstance(base, dict) and isinstance(base.get("bloques"), list) and base["bloques"]:
        return base
    return _SISTEMA_FALLBACK


def bloques_sistema(sistema: str) -> list[dict]:
    """La spec ordenada de los bloques de ese sistema: `[{clave, zona, palabras, …}]`.

    Fuente única del contrato de un sistema: de acá salen los campos que pide el
    redactor, los que dibujan las dos compuertas previas, los que cita la sección 4 y
    los que compara el QA. Si cada uno lo dedujera por su cuenta, el slide se
    escribiría con tres bloques y se imprimiría con dos.
    """
    salida = []
    for b in _cfg_sistema(sistema).get("bloques") or []:
        if isinstance(b, dict) and str(b.get("clave") or "").strip() in CLAVES_BLOQUE:
            salida.append(dict(b, clave=str(b["clave"]).strip()))
    return salida or list(_SISTEMA_FALLBACK["bloques"])


def claves_sistema(sistema: str) -> list[str]:
    """Solo las claves de bloque, en orden de emisión."""
    return [b["clave"] for b in bloques_sistema(sistema)]


def bloques_requeridos(sistema: str) -> list[str]:
    """Los bloques que este sistema NECESITA para imprimir la pieza entera.

    Un bloque es opcional cuando su mínimo de palabras es 0 (el `apoyo`: una pieza sin
    segunda línea es una pieza legítima). Fuente única de «este slide está completo»,
    que consultan el redactor —para pedir la reparación—, el lint y el arquitecto.
    """
    return [b["clave"] for b in bloques_sistema(sistema)
            if _entero_lista(b.get("palabras"), 0, 0) > 0]


def bloques_de_slide(valor, sistema: str) -> dict[str, str]:
    """Lo que ESE slide trae, por clave, según el sistema. Acepta los dos contratos."""
    return {c: t for c, t in separar_bloques(valor, sistema)}


# A qué banda del renderizador de respaldo corresponde cada zona del brief. El overlay
# no sabe de sistemas ni relee `architect.json`: recibe los bloques ya resueltos, para
# que la plantilla y el prompt no puedan discrepar por leer la config dos veces.
_BANDA_OVERLAY = {"zona": "alta", "zona_kicker": "pie",
                  "zona_cuerpo": "cuerpo", "zona_etiqueta": "etiqueta"}


def lockup_bloques(valor, sistema: str, *, caja_alta: bool) -> list[dict]:
    """Los bloques de una pieza listos para `image_overlay`, en orden de emisión.

    El renderizador de respaldo tiene que imprimir lo MISMO que el prompt le pide al
    modelo —es la razón de ser de ese módulo—, así que la banda, el tamaño relativo y
    la caja de cada bloque se resuelven acá, en el sitio que ya los define, y viajan
    resueltos. Que el overlay volviera a deducirlos de `architect.json` es exactamente
    la clase de doble lectura que acaba desincronizándose.
    """
    specs = {b["clave"]: b for b in bloques_sistema(sistema)}
    salida = []
    for clave, texto in separar_bloques(valor, sistema):
        if not texto:
            continue
        spec = specs.get(clave) or {}
        alta = caja_alta and spec.get("caja_alta", True)
        salida.append({
            "clave": clave,
            "texto": texto.upper() if alta else texto,
            "banda": _BANDA_OVERLAY.get(str(spec.get("zona") or "zona"), "alta"),
            "escala_rel": float(spec.get("escala_rel") or 1.0),
            "max_lineas": int(spec.get("max_lineas") or 3),
        })
    return salida


def sistema_valido(sistema: str) -> str:
    """El nombre normalizado, o `""` si no es un sistema del catálogo."""
    s = str(sistema or "").strip().lower()
    return s if s in sistemas_disponibles() else ""


def datos_sistema(sistema: str) -> dict:
    """Lo que las dos UI y el redactor necesitan saber del sistema, sin releer el JSON."""
    cfg = _cfg_sistema(sistema)
    return {
        "sistema": sistema_valido(sistema) or _SISTEMA_BASE,
        "nombre": str(cfg.get("nombre") or "").strip(),
        "descripcion": str(cfg.get("descripcion") or "").strip(),
        "encargo": str(cfg.get("encargo") or "").strip(),
        "acento": str(cfg.get("acento") or "titular").strip(),
        "bloques": [
            {
                "clave": b["clave"],
                "palabras": [_entero_lista(b.get("palabras"), 0, 0),
                             _entero_lista(b.get("palabras"), 1, 0)],
            }
            for b in bloques_sistema(sistema)
        ],
    }


def _entero_lista(valor, i: int, defecto: int) -> int:
    try:
        return int(valor[i])
    except (TypeError, ValueError, IndexError, KeyError):
        return defecto


def sistemas_de(identidad: dict | None = None) -> list[str]:
    """El repertorio de sistemas aplicable: identidad → `brand.json` → `architect.json`.

    La MISMA cadena de respaldo que `escenarios_de`, y se cae de fuente en fuente y no
    entrada a entrada por el mismo motivo: un repertorio es una lista de alternativas y
    mezclar las de dos marcas no produce una tercera marca.
    """
    ident = identidad if isinstance(identidad, dict) else {}
    for fuente in (ident.get("sistemas_texto"),
                   prompt_config.brand().get("sistemas_texto"),
                   _cfg_arch().get("sistemas_respaldo")):
        if isinstance(fuente, (list, tuple)):
            limpios = [s for s in (sistema_valido(x) for x in fuente) if s]
            if limpios:
                return limpios
    return [_SISTEMA_BASE]


def elegir_sistema(semilla: str, identidad: dict | None = None) -> str:
    """El sistema de texto de ESTE job. Determinista y reproducible, como `elegir_arco`.

    La semilla se sala distinto que las del arco y el mundo: con la misma, tres
    repertorios del mismo tamaño quedarían emparejados uno a uno para siempre.
    """
    repertorio = sistemas_de(identidad)
    if not repertorio:
        return _SISTEMA_BASE
    return repertorio[_indice_estable(f"texto:{semilla}", len(repertorio))]


def _cfg_arco(arco: str) -> dict:
    cfg = (_cfg_arch().get("arcos") or {}).get(arco)
    if isinstance(cfg, dict) and cfg:
        return cfg
    return _ARCO_FALLBACK.get(arco) or {}


def funcion_arco(arco: str) -> str:
    """Qué encadena ese arco, en una frase. `""` si no es un arco conocido.

    Es lo que hace que las escenas de los slides y sus imágenes se encarguen desde el
    mismo sitio: `post_writer` se la pasa al redactor y `_clausula_arco` manda su
    `enlace` al prompt de imagen. Mismo reparto que `funcion_beat`.
    """
    return str(_cfg_arco(arco).get("funcion") or "").strip()


def sujeto_arco(arco: str) -> str:
    """`"recurrente"` | `"encadenado"` | `"distinto"`: si el objeto protagonista vuelve.

    Fuente única de la única regla del carrusel que este cambio invierte. El redactor
    tenía prohibido repetir el sujeto («the same device seen closer… does NOT count as
    different»), que es una regla de catálogo: en un arco de transformación el objeto
    que vuelve ES la historia. El lint lo lee por el mismo motivo, para no avisar de una
    repetición que se pidió a propósito.
    """
    valor = str(_cfg_arco(arco).get("sujeto") or "").strip().lower()
    return valor or (_SUJETO_ARCO_FALLBACK if arco else "")


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
        {"contenido": {"tema", "angulo", "texto_exacto_a_renderizar" | "bloques",
                       "rol_slide", "idioma"?},
         "marca": {"paleta", "tipografia", "tono_visual", "aspect_ratio"},
         "prompt_base": str,
         "referencias": [str]?,
         "ritmo_carrusel": [str]?,
         "sistema_texto": str?,      # el que el job congeló (ver `elegir_sistema`)
         "arco_carrusel": str?,      # el que el job congeló (ver `elegir_arco`)
         "escenario": str?}          # el mundo que el job congeló (`elegir_escenario`)

    Lo que falte en `marca`/`referencias` sale de `prompts/brand.json`. El texto a
    renderizar es obligatorio: sin él no hay pieza que construir (regla del módulo).
    """
    if not isinstance(spec, dict):
        raise PromptInvalido(["la spec debe ser un dict"])
    arch = _cfg_arch()
    marca_def = prompt_config.brand()
    contenido = spec.get("contenido") if isinstance(spec.get("contenido"), dict) else {}
    marca = spec.get("marca") if isinstance(spec.get("marca"), dict) else {}

    rol = str(contenido.get("rol_slide") or "portada").strip().lower()
    if rol not in _ROLES:
        rol = "portada"

    # La PORTADA siempre lleva el lockup de siempre, sea cual sea el sistema del job:
    # es la pieza que ya funcionaba y la que funda el set. Se decide acá, en el único
    # punto por el que pasan todos los caminos, para que ninguno pueda pedirle otra cosa.
    sistema = (_SISTEMA_BASE if rol_base(rol) == "portada"
               else sistema_valido(spec.get("sistema_texto")) or _SISTEMA_BASE)

    # Los bloques llegan por uno de los dos contratos (dict nuevo o str de siempre) y
    # `separar_bloques` es el único que sabe repartirlos. Traen las marcas de acento del
    # usuario (**así**) o no; se quitan ACÁ, así que lo que sigue —prompt, validador,
    # QA, plantilla— ve siempre el texto limpio y los asteriscos no pueden imprimirse.
    crudos = (contenido.get("bloques") if isinstance(contenido.get("bloques"), dict)
              else contenido.get("texto_exacto_a_renderizar"))
    bloques: dict[str, str] = {}
    acentos: dict[str, str] = {}
    for clave, crudo in separar_bloques(crudos, sistema):
        bloques[clave], acentos[clave] = separar_acento(crudo)
    if not any(bloques.values()):
        raise PromptInvalido([
            "falta `contenido.texto_exacto_a_renderizar` — el texto nunca se omite"
        ])

    # Un acento por pieza y vive donde el sistema dice (el titular). Si el usuario marcó
    # en otro bloque se respeta igual —marcar es una decisión suya y ya era el
    # comportamiento cuando la pieza era un solo string—, pero el bloque del sistema
    # manda cuando los dos traen marca.
    clave_acento = str(_cfg_sistema(sistema).get("acento") or "titular").strip()
    acento = acentos.get(clave_acento) or next((a for a in acentos.values() if a), "")
    texto = " ".join(t for t in bloques.values() if t)

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

    # Los dos ejes que el job congela al crearse. **Vacío significa "lo de siempre"**, no
    # "en blanco": sin escenario no se emite el bloqueo de mundo y sin arco la cláusula de
    # continuidad recupera su segunda mitad de antes (`continuidad_sin_arco`), así que un
    # job anterior a esta versión —o una llamada directa al arquitecto— genera igual que
    # generaba. No se eligen acá a propósito: elegir en el punto donde se construye el
    # prompt sería elegir una vez POR IMAGEN, que es justo el defecto que esto corrige.
    arco = str(spec.get("arco_carrusel") or "").strip().lower()
    if arco not in arcos_disponibles():
        arco = ""

    return {
        "contenido": {
            "tema": str(contenido.get("tema") or "").strip(),
            "angulo": str(contenido.get("angulo") or "").strip(),
            "texto_exacto_a_renderizar": texto,
            # Los bloques ya repartidos y limpios, en el orden de emisión del sistema.
            # Es el contrato con todo lo que hay aguas abajo (sección 4, sección 5, QA
            # por nivel, plantilla de respaldo): la clave dice QUÉ es cada string.
            "bloques": bloques,
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
        "sistema_texto": sistema,
        "arco_carrusel": arco,
        "escenario": str(spec.get("escenario") or "").strip(),
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


def separar_bloques(valor, sistema: str = "") -> list[tuple[str, str]]:
    """`str | dict` → `[(clave, texto)]` en el orden de emisión del sistema.

    Punto ÚNICO por el que pasan los dos contratos de entrada, y tiene que serlo: el
    prompt, la plantilla de respaldo, el QA y las dos compuertas previas cuentan los
    bloques de un slide, y si cada uno los dedujera por su cuenta el slide se
    escribiría con tres y se imprimiría con dos.

    - **dict** — el contrato nuevo (`{"etiqueta": …, "titular": …, "cuerpo": …}`).
      Se lee bloque a bloque y **los huecos se conservan**: un `cuerpo` vacío deja su
      hueco en vez de correr el siguiente a su sitio, igual que los campos indexados
      de la compuerta previa.
    - **str** — el contrato de siempre, y sigue valiendo: el texto va al `titular` y
      lo que quede detrás de la raya espaciada baja al bloque de texto secundario del
      sistema (su `cuerpo` si lo tiene, si no su `apoyo`). Un job anterior a los
      sistemas, o un texto escrito a mano en la revisión, se reparten como se
      repartían. La `etiqueta` nunca se rellena así: es un campo explícito y
      adivinarla desde una frase produciría un rótulo inventado.

    Devuelve los bloques **con sus marcas de acento intactas**: quitarlas es trabajo de
    `normalizar_spec`, que es quien sabe cuál de ellos las lleva.
    """
    specs = bloques_sistema(sistema)
    claves = [b["clave"] for b in specs]
    topes = {b["clave"]: _entero_lista(b.get("palabras"), 1, _MAX_PALABRAS_BLOQUE)
             for b in specs}

    if isinstance(valor, dict):
        return [(c, " ".join(str(valor.get(c) or "").split())) for c in claves]

    texto = " ".join(str(valor or "").split())
    secundario = next((c for c in ("cuerpo", "apoyo") if c in claves), "")
    titular, resto = dividir_texto(texto, topes.get("titular", _MAX_PALABRAS_BLOQUE))
    reparto = {"titular": titular}
    if secundario:
        reparto[secundario] = resto
    return [(c, reparto.get(c, "")) for c in claves]


def pide_caja_alta(tipografia: str) -> bool:
    """¿La familia de la identidad se compone en caja alta?

    Tanto `brand.json` como las identidades extraídas lo declaran con las mismas dos
    palabras ("ALL CAPS", "in caps"), que es también una de las `MARCAS_DISPLAY` que
    pide la puerta tipográfica. Si lo dice, el texto se cita ya en mayúsculas: pedirle
    al modelo una caja y darle la contraria en el mismo brief es la clase de
    contradicción que resuelve como quiere, y de ahí salía el slide en caja baja.
    """
    return bool(re.search(r"\bcaps\b", str(tipografia or ""), re.I))


# Una línea final de una sola palabra corta es una VIUDA: en la portada auditada
# dejaba "EN" solo en la tercera línea, al 14% del alto del cuadro. No es un detalle
# de tipógrafo — a ese cuerpo, una palabra suelta ocupa un tercio de la pieza.
_VIUDA_MAX = 3


# A cuerpo de titular (13-16% del alto, 84% del ancho) caben ~20 caracteres por línea.
# Por debajo de dos líneas de eso el titular entra en dos; por encima, en tres. Más de
# tres a ese cuerpo ya no es un titular, es un párrafo.
_LINEA_CARACTERES = 21
# Cuánto vale un corte detrás de una pausa, medido en la misma unidad que el
# desequilibrio (caracteres²). Inclina el reparto sin poder imponerlo: con 30, una
# pausa gana a un corte ~5 caracteres mejor equilibrado —que es cuando de verdad lee
# mejor— y pierde ante un reparto claramente desigual, que a cuerpo de póster se nota
# más que el sintagma partido.
_PREMIO_PAUSA = 30.0
_PAUSAS = (",", ".", ":", ";", "—", "–")


def lineas_titular(titular: str, max_lineas: int = 3) -> list[str]:
    """Reparte el titular en líneas, equilibradas y sin viuda al final.

    El modelo rompe el titular por su cuenta y decide dónde según lo que le quepa, no
    según lo que la frase dice. Acá se decide una vez, con criterio, y se le dicta.

    El reparto es una **partición equilibrada**, no un llenado greedy: greedy llena la
    primera línea hasta el tope y deja lo que sobre al final, que es justo como sale
    "…EN LA / LIGA". Con pocas palabras y como mucho tres líneas el espacio de cortes
    es diminuto, así que se prueban todos y gana el de menor desequilibrio, con una
    rebaja si la línea termina en pausa (el mismo criterio de `dividir_texto`).

    Después, la regla de la viuda: la última línea nunca queda con una sola palabra de
    `_VIUDA_MAX` caracteres o menos. Es el defecto "EN" de la portada auditada — a ese
    cuerpo, una palabra suelta ocupa un tercio de la pieza.
    """
    palabras = " ".join(str(titular or "").split()).split()
    tope = max(1, int(max_lineas or 1))
    if len(palabras) <= 1 or tope == 1:
        return [" ".join(palabras)] if palabras else []

    total = sum(len(p) for p in palabras) + len(palabras) - 1
    n = min(tope, len(palabras), 2 if total <= _LINEA_CARACTERES * 2 else 3)
    objetivo = total / n

    def _coste(corte: tuple[int, ...]) -> float:
        limites = (0,) + corte + (len(palabras),)
        c = 0.0
        for k in range(n):
            trozo = palabras[limites[k]:limites[k + 1]]
            largo = sum(len(p) for p in trozo) + len(trozo) - 1
            c += (largo - objetivo) ** 2
            if k < n - 1 and trozo[-1][-1:] in _PAUSAS:
                c -= _PREMIO_PAUSA
        return c

    from itertools import combinations
    mejor = min(combinations(range(1, len(palabras)), n - 1), key=_coste)
    lineas = [palabras[a:b] for a, b in zip((0,) + mejor, mejor + (len(palabras),))]

    # La viuda: se le baja una palabra de la línea anterior, salvo que eso la dejara
    # vacía (ahí se funden las dos, que es preferible a una línea sola).
    if len(lineas) > 1 and len(lineas[-1]) == 1 and len(lineas[-1][0]) <= _VIUDA_MAX:
        if len(lineas[-2]) > 1:
            lineas[-1].insert(0, lineas[-2].pop())
        else:
            lineas[-2].extend(lineas.pop())
    return [" ".join(l) for l in lineas]


# ── Secciones deterministas (las que la app nunca delega) ─────────────────────

def _seccion_pieza(norm: dict, *, refuerzo_sangrado: bool = False) -> str:
    piezas = _cfg_arch().get("piezas") or {}
    rol = norm["contenido"]["rol_slide"]
    plantilla = (piezas.get(rol) or piezas.get(rol_base(rol))
                 or "Editorial social image, {aspect}, print-quality single still.")
    seccion = plantilla.format(aspect=norm["marca"]["aspect_ratio"])
    # El reintento del detector de bandas: la tirada anterior salió con passe-partout
    # o letterbox. Va en la sección 1 —la más autoritativa del brief— por el mismo
    # motivo por el que ahí se declara el sangrado: el negativo solo no alcanzó nunca.
    if refuerzo_sangrado:
        seccion += " " + (piezas.get("refuerzo_sangrado") or
                          "THE PREVIOUS RENDER CAME BACK WITH A FLAT COLOUR BAND AT THE EDGE. The "
                          "photograph must reach every one of the four canvas edges: no letterbox, "
                          "no border, no matte, no strip of flat colour anywhere.")
    return seccion


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
        # Las dos bandas que estrenaron los sistemas de texto. Tienen que estar acá o el
        # bloque que las pide cae a la del titular y el cuerpo se imprime encima de él.
        "zona_etiqueta": z.get("zona_etiqueta") or "the very top of the upper band, on the 8% top margin",
        "zona_cuerpo": z.get("zona_cuerpo") or "directly under the headline, inside the same upper band",
        "alineacion": z.get("alineacion") or "flush left from the 8% side margin",
        "ancho": z.get("ancho") or "up to 84% of the frame width",
        "margen": z.get("margen") or "an 8% safe margin on all four sides",
    }


def _tiene_acentos(texto: str) -> bool:
    return bool(re.search(r"[áéíóúüñÁÉÍÓÚÜÑàèìòùâêîôûçÇ]", texto))


def _cita_bloque(spec_bloque: dict, texto: str, z: dict) -> str:
    """Una línea de la sección 4: el string literal con su tamaño y su banda.

    La posición y el tamaño viajan acá, DENTRO de la cita, y no en el detalle
    compartido: son lo único que de verdad cambia entre un bloque y otro.
    """
    plantilla = str(spec_bloque.get("cita") or
                    'TEXT "{texto}" — {alineacion} in {zona}')
    zona = z.get(str(spec_bloque.get("zona") or "zona")) or z.get("zona") or ""
    try:
        return plantilla.format(texto=texto, zona=zona, alineacion=z.get("alineacion", ""),
                                ancho=z.get("ancho", ""), margen=z.get("margen", ""))
    except (KeyError, IndexError):
        return f'TEXT "{texto}"'


def _seccion_texto(norm: dict, bloques: list[tuple[str, str]], *, refuerzo: bool = False,
                   cortes: list[str] | None = None) -> str:
    """Sección 4: el bloque de texto. Es la razón de ser del módulo — se arma a mano.

    `bloques` son los pares `(clave, texto)` NO vacíos del sistema, ya en su caja
    final. Se emiten como una lista con una cita por bloque más un detalle compartido:
    el idioma, las tildes, la copia carácter a carácter y el área segura valen para
    todos los strings, y repetirlos por bloque triplicaba la parte cara de la sección
    sin decir nada nuevo. Es lo que hace asequible el tercer nivel de texto.
    """
    cfg_txt = _cfg_arch().get("texto") or {}
    z = _zona(norm)
    idiomas = _cfg_arch().get("idiomas") or {}
    codigo = norm["contenido"]["idioma"]
    idioma = idiomas.get(codigo) or codigo.upper()
    specs = {b["clave"]: b for b in bloques_sistema(norm.get("sistema_texto") or _SISTEMA_BASE)}

    apertura = str(cfg_txt.get("apertura") or
                   "render these exact strings, and no other words anywhere in the image —")
    citas = [_cita_bloque(specs.get(clave) or {}, texto, z) for clave, texto in bloques]

    acentos = (cfg_txt.get("detalle_acentos")
               or "keep every accented character exactly as written") if _tiene_acentos(
        " ".join(t for _, t in bloques)) else (
        cfg_txt.get("detalle_sin_acentos") or "no accented characters in this string")
    detalle = (cfg_txt.get("detalle") or
               "Language: {idioma}; {acentos}. Copy each string character by character — never "
               "translate, abbreviate or hyphenate, and set every glyph in the case supplied. Each "
               "block spans {ancho}. SAFE AREA: every glyph, accent and descender sits whole inside "
               "{margen} — nothing touches or is cut by a frame edge.").format(
        idioma=idioma, acentos=acentos, **z)

    partes = [f"{apertura} {'; '.join(citas)}. {detalle}"]
    # Cortes de línea dictados: el modelo los decide según lo que le quepa, no según lo
    # que la frase dice, y de ahí salen las viudas ("EN" solo en la tercera línea, al
    # 14% del alto). Las etiquetas son instrucción, nunca contenido — hay que decirlo o
    # el modelo imprime "line 1".
    if cortes and len(cortes) > 1:
        citadas = ", ".join(f'line {i + 1} "{l}"' for i, l in enumerate(cortes))
        partes.append((cfg_txt.get("cortes") or
                       "Break the headline over exactly these lines: {lineas}. These line "
                       "labels are instructions, never printed.").format(lineas=citadas))
    if refuerzo:
        partes.append(cfg_txt.get("refuerzo") or
                      "TEXT ACCURACY IS THE PRIMARY REQUIREMENT: spell the quoted string character by "
                      "character and print no other word anywhere in the frame.")
    return " ".join(partes)


# ── Saneo de lo que la identidad escribe en la capa dura ──────────────────────
#
# La sección 5 pega `tipografia`, `tipografia_secundaria`, `color_texto` y
# `color_acento` VERBATIM. Eso convierte a la identidad en coautora de una sección
# determinista, y ahí está el problema: si esos strings traen vocabulario de LAYOUT,
# la identidad está escribiendo layout sin permiso y sin que nadie lo revise. El caso
# real: una identidad con `tipografia` = "…headline band" y `color_texto` = "…over the
# dark field" fabricó el letterbox del carrusel auditado — cuando la escena no era
# oscura, el modelo pintaba el "dark field" como un rectángulo.
#
# Corolario general, que vale para cualquier campo que se añada mañana: **una
# identidad puede escribir layout sin querer si sus campos entran verbatim en una
# sección determinista.** El fondo lo declaran las secciones 1, 3 y 6; la 5 habla solo
# de la TINTA.

_HEX_EN_TEXTO = re.compile(r"#[0-9a-fA-F]{6}")


def tinta(valor: str) -> str:
    """`"Soft bone white (#F5F3EE) for all caps over the dark field."` → `"Soft bone white (#F5F3EE)"`.

    El nombre del color y su hex, y nada más. Se recorta EN el hex porque el validador
    de `visual_identity` ya garantiza que está; sin hex se devuelve el valor tal cual
    (degradar, nunca romper: `brand.json` no pasa por ese validador).
    """
    v = " ".join(str(valor or "").split())
    m = _HEX_EN_TEXTO.search(v)
    if not m:
        return v
    fin = m.end()
    # El hex casi siempre viene entre paréntesis: cortar justo detrás dejaría uno
    # abierto, que para el modelo es ruido.
    if v[fin:fin + 1] == ")":
        fin += 1
    return v[:fin].strip()


def _palabras_layout() -> tuple[str, ...]:
    palabras = _cfg_arch().get("palabras_layout_prohibidas")
    if isinstance(palabras, list) and palabras:
        return tuple(str(p).strip().lower() for p in palabras if str(p).strip())
    return ("band", "panel", "block", "frame", "matte", "letterbox", "bar", "box",
            "backdrop", "background", "field")


def sin_layout(valor: str) -> str:
    """Quita de un campo de identidad los sintagmas que hablan de layout.

    Se elimina el **sintagma**, no la cadena entera: una tipografía se escribe por
    cláusulas separadas por comas ("condensed grotesque, ALL CAPS, tight tracking in a
    headline band") y solo una de ellas sobra. Si el filtro dejara el campo vacío se
    devuelve el original: quedarse sin familia tipográfica es peor que arrastrar la
    palabra, y el aviso del lint ya avisa de esto en la compuerta previa.
    """
    v = " ".join(str(valor or "").split())
    if not v:
        return v
    prohibidas = _palabras_layout()
    patron = re.compile(r"\b(" + "|".join(re.escape(p) for p in prohibidas) + r")\b", re.I)
    limpios = [t.strip() for t in v.split(",") if t.strip() and not patron.search(t)]
    return ", ".join(limpios) if limpios else v


def _escala_bloque(spec_bloque: dict, rol: str) -> str:
    """La escala de un bloque para este rol.

    Se declara como string (igual en todos los beats) o como tabla por rol: la
    variación por BEAT solo tiene sitio donde el titular está solo en la banda, y ese
    es el sistema `titular`. Con un cuerpo debajo, el titular no puede oscilar entre el
    15 y el 20% sin dejar al cuerpo fuera de su banda.
    """
    escala = spec_bloque.get("escala")
    if isinstance(escala, dict):
        return str(escala.get(rol) or escala.get(rol_base(rol)) or escala.get("portada") or "").strip()
    return str(escala or "").strip()


def _seccion_tipografia(norm: dict, bloques: list[tuple[str, str]]) -> str:
    """Sección 5: la tipografía, escrita por la app.

    Dejó de ser creativa a propósito. Lo que la hacía verse genérica no era el
    modelo sino el brief: la familia salía del LLM (distinta en cada post) y nadie
    declaraba la ESCALA, así que el modelo ponía cuerpo de pie de foto. Acá la
    familia y el acento son marca (`brand.json`) y la escala es layout por bloque
    y por rol (`architect.json`): un titular al 13-16% del alto es lo que separa un
    póster de un caption.
    """
    cfg_tip = _cfg_arch().get("tipografia") or {}
    m = norm["marca"]
    rol = norm["contenido"]["rol_slide"]
    specs = {b["clave"]: b for b in bloques_sistema(norm.get("sistema_texto") or _SISTEMA_BASE)}
    # Solo la de los bloques que ESTA pieza imprime: declarar la escala de un cuerpo que
    # no existe le pide al modelo un párrafo que nadie le dio.
    escala = " ".join(e for e in (_escala_bloque(specs.get(c) or {}, rol) for c, _ in bloques) if e)
    if not escala:
        escala = "The headline stands 13-16% of the frame height per line, over 2-3 lines."
    # Un acento que aparece en TODOS los slides deja de ser un acento: el beat que lo
    # calla (la tensión) es lo que hace que el color se note en los que lo llevan. Solo
    # se calla la elección AUTOMÁTICA — un span marcado a mano por el usuario manda
    # siempre, que es la única palanca que tiene sobre la jerarquía del titular.
    omitido = cfg_tip.get("acento_omitido")
    sin_acento_auto = rol in [str(r).strip().lower() for r in omitido] if isinstance(omitido, list) else False
    color = tinta(m["color_texto"]) or "light type over the dark areas of the frame"
    # El span elegido a mano en la revisión previa manda sobre la elección automática:
    # es la única palanca del usuario sobre la jerarquía del titular.
    elegido = norm["contenido"].get("acento") or ""
    if m["color_acento"] and elegido:
        # `tinta` también acá, y no es cosmético: la rama automática reducía el color a
        # nombre + hex y esta lo pegaba crudo, así que el MISMO acento se le describía
        # al modelo de dos maneras distintas según quién lo hubiera elegido — y dos
        # formulaciones de un color son dos colores.
        acento = (cfg_tip.get("acento_explicito") or
                  'Exactly this fragment — "{acento}" — in {color_acento}; every other '
                  "word stays in the headline color.").format(
            acento=elegido, color_acento=tinta(m["color_acento"]))
    elif m["color_acento"] and not sin_acento_auto:
        acento = (cfg_tip.get("acento") or
                  "Exactly one span — the single most load-bearing word of the quoted string — in "
                  "{color_acento}; every other word stays in the headline color.").format(
            color_acento=tinta(m["color_acento"]))
    else:
        # NO emitir nada no es lo mismo que prohibirlo. Mientras el beat omitido se
        # limitaba a callar la cláusula, el modelo pintaba igual una palabra y elegía el
        # color por su cuenta: es una de las tres causas del carrusel con un acento
        # distinto en cada slide. El silencio no es una prohibición.
        acento = (cfg_tip.get("acento_ninguno") or
                  "Every word in {color_texto}: no highlighted word and no second colour anywhere "
                  "in the type.").format(color_texto=color)
    datos = {
        # La familia pasa por el filtro de layout y el color se reduce a TINTA: esta
        # sección habla del tipo, no de sobre qué se apoya (ver el bloque de arriba).
        "display": sin_layout(m["tipografia"]) or "ultra-condensed heavy display grotesque in ALL CAPS",
        "escala": escala,
        "color": color,
        "acento": acento,
    }
    plantilla = (cfg_tip.get("plantilla") or
                 "{display}. {escala} Set solid, flush left, optical alignment, no hyphenation; "
                 "{color}. {acento}")
    try:
        seccion = plantilla.format(**datos)
    except (KeyError, IndexError):
        seccion = f"{datos['display']}. {datos['escala']} {datos['color']}. {acento}"
    # La familia secundaria solo se declara si la pieza imprime algún bloque que no sea
    # el titular: es la que compone el apoyo, el cuerpo y la etiqueta.
    if len(bloques) > 1 and m["tipografia_secundaria"]:
        seccion += " " + (cfg_tip.get("secundaria") or "Second line: {secundaria}.").format(
            secundaria=sin_layout(m["tipografia_secundaria"]))
    return " ".join(seccion.split())


def _seccion_negativos(norm: dict) -> str:
    arch = _cfg_arch()
    negs = arch.get("negativos")
    if not isinstance(negs, list) or not negs:
        negs = [
            "no extra text beyond the quoted string", "no watermarks", "no lorem ipsum",
            "no deformed hands", "no fake logos",
        ]
    negs = list(negs)
    # El atrezzo por defecto de estos modelos es estadounidense —en un post sobre
    # España salían billetes de dólar—, así que se prohíbe explícitamente cuando el
    # contenido NO está en inglés. Limitación conocida y deliberada: el pipeline
    # detecta idioma, no país, y `es` no distingue España de LatAm. Esto elimina el
    # DEFAULT, que es el fallo observado, y no pretende más.
    if norm.get("contenido", {}).get("idioma", "es") != "en":
        extra = arch.get("negativos_no_ingles")
        if isinstance(extra, list):
            negs += extra
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

    En un slide con beat el lockup NO es esta cláusula acortada sino su inversa: el
    titular se lleva los dos tercios altos y el sujeto queda subordinado debajo. La
    portada vende con una imagen; el slide transmite, y lo que transmite es el texto.
    El esqueleto (titular arriba, apoyo al pie, sangrado a los cuatro bordes) es el
    mismo en las dos — la inversión se declara como jerarquía de TAMAÑO, no moviendo
    las bandas de sitio, porque son las bandas compartidas lo que hace que el set se
    lea como un sistema.
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
    # Ojo con el respaldo de arriba: es el de la PORTADA. Un slide que caiga a él (config
    # rota) sale con la jerarquía vieja —sujeto en la banda central— en vez de con la
    # invertida. Es degradar, no romper: el prompt sigue siendo válido.
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


def _clausula_paleta(norm: dict) -> str:
    """Bloqueo de paleta que la app PREFIJA a la sección 6, detrás del de luz.

    Tercero de la familia (`_clausula_luz`, `_clausula_mundo`) y por el motivo de
    siempre: lo invariante dentro de un job no puede decidirse una vez por imagen. La
    sección 6 la escribe el LLM por pieza y su instrucción le pedía literalmente
    "Name the brand hex values for the palette", así que la paleta se REDACTABA N
    veces, cada vez con otras palabras. El defecto que producía es el que se reportó y
    se ve de un vistazo: en un mismo carrusel, cada slide con el acento de un color
    distinto.

    Es una de las tres causas de ese defecto, y la única que vive fuera de la sección 5
    (las otras dos: el beat que callaba el acento sin prohibirlo y la rama de acento
    explícito que se saltaba `tinta`). Como contrapartida el LLM tiene prohibido nombrar
    colores y el respaldo dejó de arrastrar la paleta: se declara una sola vez, acá.

    Sin paleta no se emite: degradar, nunca romper.
    """
    m = norm["marca"]
    paleta = (f"{m['paleta_nombres']} ({m['paleta']})" if m["paleta_nombres"] and m["paleta"]
              else m["paleta"] or m["paleta_nombres"])
    if not paleta:
        return ""
    plantilla = (_cfg_arch().get("paleta_bloqueada") or
                 "PALETTE LOCK — the same colours in every piece of this set, no other hue "
                 "anywhere: {paleta}.")
    try:
        return " ".join(plantilla.format(paleta=paleta).split())
    except (KeyError, IndexError):
        return ""


def _clausula_mundo(norm: dict) -> str:
    """Bloqueo de mundo que la app PREFIJA siempre a la sección 2 (sujeto).

    Hermano del bloqueo de luz y por exactamente el mismo motivo. La luz se arregló al
    ver que cinco piezas del mismo carrusel salían con cinco esquemas distintos; el
    LUGAR tenía el mismo defecto con otro traje —cada imagen inventaba el suyo y la
    continuidad se intentaba citando la escena de la portada, que es una copia y no un
    ancla—, más uno peor: como el lugar no lo podía declarar nadie, lo acababa poniendo
    el vocabulario de la capa dura, que decía «apoyado en una superficie» en todas
    partes. De ahí los carruseles de mesas, iguales con cualquier identidad.

    Va PREFIJADO y no dentro de las secciones creativas para que la poda de
    `_ajustar_longitud` no lo toque: tiene que salir byte a byte idéntico en la portada
    y en los N slides, o no es un bloqueo.

    Vacío = no se emite y el prompt sale como antes de que existieran los mundos.
    """
    escenario = _recortar(str(norm.get("escenario") or "").strip(),
                          _entero(_validacion_cfg(), "mundo_palabras", 22))
    if not escenario:
        return ""
    plantilla = (_cfg_arch().get("mundo_bloqueado") or
                 "WORLD LOCK — the same location in every piece of this set: {escenario} The "
                 "subject belongs to that place and is held by something in it.")
    try:
        return " ".join(plantilla.format(escenario=escenario).split())
    except (KeyError, IndexError):
        return ""


def _clausula_arco(norm: dict) -> str:
    """Qué encadena este slide con los demás. Solo en los slides, detrás de la continuidad.

    Es la segunda mitad de la continuidad de set, la que dice lo que tiene que CAMBIAR.
    Antes era una constante —«a DIFFERENT hero object, camera position and framing»— y
    esa constante es una regla de catálogo: garantiza que las piezas no se repitan y a
    la vez impide que se relacionen, que es la definición de cinco props sueltos sobre
    la misma mesa. Ahora sale del arco que el job congeló, y en dos de los cuatro el
    objeto protagonista VUELVE a propósito.

    Sin arco se emite la constante de antes (`continuidad_sin_arco`): un job anterior a
    esta versión tiene que generar como generaba, no peor.

    Ojo con lo que NO puede decir: nada de distancia, altura ni encuadre. Eso es del
    beat, cuya cláusula va pegada justo delante en la misma sección, y ante dos
    instrucciones de cámara contradictorias el modelo elige una.
    """
    if rol_base(norm["contenido"]["rol_slide"]) != "contenido":
        return ""
    arch = _cfg_arch()
    arco = str(norm.get("arco_carrusel") or "").strip()
    if not arco:
        return str(arch.get("continuidad_sin_arco") or
                   "A DIFFERENT hero object, camera position and framing from the cover.").strip()
    enlace = str(_cfg_arco(arco).get("enlace") or "").strip()
    if not enlace:
        return ""
    return _recortar(enlace, _entero(_validacion_cfg(), "arco_palabras", 26))


def _clausula_set(norm: dict) -> str:
    """Continuidad del carrusel, solo en los slides: lo que se comparte + lo que cambia.

    Es el reemplazo textual de lo que antes se intentaba pasando la portada en
    `medias`. Aquello no era una referencia de estilo sino image-to-image, así que
    devolvía la portada re-encuadrada; esto pide explícitamente las dos mitades, que
    es lo que un director de arte le diría a un fotógrafo. La primera la escribe esta
    cláusula; la segunda sale del arco (`_clausula_arco`).

    **Dejó de citar la escena de la portada**, y es una mejora antes que un ahorro:
    citarla era re-derivar el mundo compartido a partir de UNA pieza —una copia, no un
    ancla—. Ahora el mundo lo declara `_clausula_mundo` byte a byte idéntico en todas
    las piezas, portada incluida, que es exactamente lo que esto aproximaba. De paso
    libera el presupuesto que paga el bloqueo de mundo, y con él desaparece el único
    tope de palabras que esta cláusula necesitaba.
    """
    c = norm["contenido"]
    # La comparación va contra `rol_base`, NUNCA contra el literal "contenido": desde
    # la escalera de beats los slides llegan como `tension|desarrollo|prueba|remate` y
    # ninguno es "contenido", así que esta cláusula dejó de emitirse en TODOS los
    # slides de carrusel sin un solo error — y con ella se fue lo único que declaraba
    # el mundo compartido. Es el fallo que produjo carruseles con cinco localizaciones
    # distintas y, a la vez, el objeto de la portada repetido en tres piezas.
    if rol_base(c["rol_slide"]) != "contenido":
        return ""
    arch = _cfg_arch()
    compartido = str(arch.get("continuidad_set") or
                     "SET CONTINUITY: same location, surfaces, light and palette as the rest of "
                     "this carousel; never the cover restaged or re-cropped.").strip()
    return " ".join(p for p in (compartido, _clausula_arco(norm)) if p)


# ── Secciones creativas: respaldo determinista ────────────────────────────────

def _respaldos(norm: dict) -> dict[str, str]:
    """Secciones creativas sin LLM. Deben bastar para pasar el validador."""
    r = _cfg_arch().get("respaldos") or {}
    c, m = norm["contenido"], norm["marca"]
    # Sin decir «resting on a worn surface», que es lo que decía: el respaldo del sujeto
    # no puede elegir el mundo, porque el mundo lo declara `_clausula_mundo` y esta frase
    # llegaba antes. Era el escalón más silencioso de los seis que producían la mesa.
    base = norm["prompt_base"] or (
        f"A single concrete object that embodies {c['tema'] or 'the topic'}, held by whatever "
        "in this location would hold it and casting a contact shadow there."
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
        # Sin decir DÓNDE se apoya el sujeto: eso lo declara siempre `_clausula_aire`,
        # y con distinta jerarquía según sea portada o slide. Un respaldo que lo dijera
        # abriría la sección 3 contradiciendo a su propio lockup dos frases después.
        "composicion": _fmt("composicion", "One clear focal hierarchy: a single subject reading "
                                           "clean against depth that falls away behind it."),
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
                    "the subject and its scene, never the camera distance or the type zones.",
            # Sin esta línea el modelo escribe el sujeto como protagonista —es lo que se
            # le pide en la portada— y la sección 2 acaba contradiciendo a la 3, que lo
            # subordina. La 3 gana (es determinista), pero el brief se lee incoherente y
            # el modelo de imagen resuelve la contradicción como quiere.
            "On a content slide the TYPE is the subject of the piece: the photographed object "
            "is support, held low and small under the headline. Pick something that still reads "
            "at that size, and never describe it as filling or dominating the frame."]


def _linea_mundo(norm: dict) -> list[str]:
    """El mundo y el arco, para el arquitecto: dónde estamos y qué encadena esta pieza.

    Las dos cosas se emiten en el prompt final por su cuenta (`_clausula_mundo` y
    `_clausula_arco`), pero además hay que DECÍRSELAS al modelo que escribe la sección
    del sujeto: si no, escribe un objeto para un lugar que no conoce y el bloqueo de
    mundo —prefijado a esa misma sección— acaba contradiciéndolo dentro de la frase.
    Es la misma razón por la que el beat viaja en `_linea_beat` además de en la 3.
    """
    lineas: list[str] = []
    escenario = str(norm.get("escenario") or "").strip()
    if escenario:
        lineas.append(
            f"SET LOCATION (fixed for every piece of this set, the app states it verbatim — "
            f"put the subject where THIS place would keep it, and never restate the place "
            f"itself): {escenario}"
        )
    arco = str(norm.get("arco_carrusel") or "").strip()
    funcion = funcion_arco(arco) if arco else ""
    if funcion and rol_base(norm["contenido"]["rol_slide"]) == "contenido":
        lineas.append(f"CAROUSEL ARC: {arco} — {funcion}. The app appends this arc's link "
                      "clause verbatim; write a subject that obeys it.")
    return lineas


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
    idiomas = arch.get("idiomas") or {}
    idioma = idiomas.get(c["idioma"]) or c["idioma"].upper()
    datos = [
        f"TOPIC: {c['tema'] or '(not given)'}",
        f"ANGLE: {c['angulo'] or '(not given)'}",
        # El idioma llegaba solo a la sección de texto, nunca al brief del sujeto, así
        # que el atrezzo salía con el default del modelo: estadounidense. Va como línea
        # dura y no como sugerencia porque compite contra ese default.
        (arch.get("contexto_cultural") or
         "CULTURAL CONTEXT: the source is in {idioma} — props, currency, plugs, "
         "packaging and signage must match that context; never default to US props or "
         "US currency.").format(idioma=idioma),
        f"SLIDE ROLE: {c['rol_slide']}",
        *_linea_beat(c["rol_slide"]),
        # El mundo se le DICE al modelo además de emitirse en el prompt. Sin esto escribe
        # el sujeto para un lugar que no conoce y el WORLD LOCK, que va prefijado a su
        # propia sección, acaba contradiciéndolo dentro de la misma frase.
        *_linea_mundo(norm),
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
        # Qué se le permite tomar de la portada lo decide el ARCO, no una constante. Con
        # «never its hero object» pegado a todo slide, un arco de transformación —donde el
        # objeto que vuelve ES la historia— quedaba prohibido en el mismo brief que lo pide.
        arco = str(norm.get("arco_carrusel") or "")
        vuelve = sujeto_arco(arco) in ("recurrente", "encadenado") if arco else False
        regla = ("reuse the WORLD and carry its hero object forward as the set arc asks"
                 if vuelve else "reuse the WORLD, never its hero object or its framing")
        datos.append(
            f"CAROUSEL COVER ALREADY SHOT (same set, same light, same palette — {regla}): "
            f"{c['escena_portada']}"
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
    # La cadena entera Y bloque a bloque: desde los sistemas de texto el "texto de la
    # pieza" son 2-3 strings, y el modelo cuela unas veces el conjunto y otras uno solo.
    # Buscar solo una de las dos formas deja pasar la otra, y entonces el render saca el
    # texto dos veces. De más largo a más corto, para no dejar restos del grande.
    textos = sorted({norm["contenido"]["texto_exacto_a_renderizar"],
                     *(t for t in norm["contenido"].get("bloques", {}).values() if t)},
                    key=len, reverse=True)
    out: dict[str, str] = {}
    for clave in _CLAVES_CREATIVAS:
        valor = data.get(clave)
        if not isinstance(valor, str) or not valor.strip():
            continue
        limpio = " ".join(valor.split())
        # El modelo a veces cuela el texto de la pieza dentro de una sección creativa;
        # ahí duplicaría el bloque de la sección 4 y el render saca el texto dos veces.
        for texto in textos:
            if texto:
                limpio = re.sub(rf'"{re.escape(texto)}"', "", limpio, flags=re.I)
                limpio = re.sub(re.escape(texto), "", limpio, flags=re.I)
        limpio = " ".join(limpio.split()).strip()
        if limpio:
            out[clave] = _recortar(limpio, max_pal)
    return out


# ── Construcción ──────────────────────────────────────────────────────────────

def construir(spec: dict, *, cfg=None, usar_llm: bool = True, autocritica: bool = True,
              refuerzo_texto: bool = False, refuerzo_sangrado: bool = False) -> ResultadoPrompt:
    """Construye el prompt final estructurado a partir de la spec.

    `cfg` es el Config del proyecto (para las keys del LLM); sin él —o sin keys—
    todo se resuelve con los respaldos deterministas y el prompt sale igual de
    válido. `refuerzo_texto` añade la instrucción de refuerzo del bloque de texto
    (la usa el reintento del QA de visión cuando el render salió mal escrito) y
    `refuerzo_sangrado` la del sangrado en la sección 1 (la del detector de bandas).

    Lanza `PromptInvalido` solo si ni siquiera el camino determinista produce un
    prompt válido (config rota) o si falta el texto a renderizar.
    """
    norm = normalizar_spec(spec)
    sistema = norm["sistema_texto"]
    specs = {b["clave"]: b for b in bloques_sistema(sistema)}
    pares = [(c, t) for c, t in norm["contenido"]["bloques"].items() if t]
    avisos: list[str] = []
    usos: list[dict] = []
    faltan = [c for c in claves_sistema(sistema)
              if not norm["contenido"]["bloques"].get(c)
              and _entero_lista((specs.get(c) or {}).get("palabras"), 0, 0) > 0]
    if faltan:
        avisos.append(f"el sistema «{sistema}» pide bloques que llegaron vacíos: {', '.join(faltan)}")

    # Caja alta: si la identidad la declara, el texto se cita YA en mayúsculas. Se
    # transforma acá, en el único punto por el que pasan las tres cosas que tienen que
    # decir lo mismo —lo que se cita en la sección 4, lo que el validador exige que
    # esté en el prompt y lo que se devuelve en `ResultadoPrompt.bloques`—. Pedirle una
    # caja al modelo y darle la contraria entre comillas es la contradicción de la que
    # salía el slide en caja baja.
    #
    # NO se aplica al cuerpo: `pide_caja_alta` mira la familia de DISPLAY, que es la del
    # titular, y un párrafo de 30 palabras al 5% del alto en caja alta es ilegible. Cada
    # bloque declara si la respeta, y la plantilla de Pillow lee la misma bandera.
    if pide_caja_alta(norm["marca"]["tipografia"]):
        pares = [(c, t.upper() if (specs.get(c) or {}).get("caja_alta", True) else t)
                 for c, t in pares]
        # El acento se busca dentro del titular, así que tiene que ir en la misma caja.
        if (specs.get(str(_cfg_sistema(sistema).get("acento") or "titular"))
                or {}).get("caja_alta", True):
            norm["contenido"]["acento"] = norm["contenido"]["acento"].upper()
    # `norm["contenido"]["bloques"]` se queda con la caja ORIGINAL a propósito: es lo que
    # `_secciones_desde_llm` busca para sacar el texto de la pieza de una sección creativa,
    # y el modelo lo devuelve como se lo dimos, no en mayúsculas. Lo que se imprime son
    # `pares`.
    bloques = [t for _, t in pares]

    # Cortes de línea: se calculan una sola vez, sobre el TITULAR ya en su caja final.
    # Solo sobre él a propósito — es el bloque cuyas viudas se ven a esa escala; dictarle
    # las líneas a un cuerpo de cuatro renglones solo gastaría presupuesto.
    #
    # Y los sistemas CON CUERPO los apagan: la viuda que esta cláusula corrige —una
    # palabra sola en la tercera línea, al 14% del alto— es un defecto de titular largo a
    # tamaño de póster, y ahí el titular baja a 6 palabras sobre 1-2 líneas. Pagar 140
    # caracteres fijos por un defecto que ese sistema no puede tener es justo lo que hay
    # que recortar antes de tocar el techo del prompt.
    cortes: list[str] = []
    titular = next((t for c, t in pares if c == "titular"), "")
    if (titular and getattr(cfg, "image_line_breaks", True)
            and _cfg_sistema(sistema).get("cortes", True)):
        cortes = lineas_titular(titular)

    fijas = {
        "pieza": _seccion_pieza(norm, refuerzo_sangrado=refuerzo_sangrado),
        "texto": _seccion_texto(norm, pares, refuerzo=refuerzo_texto, cortes=cortes),
        "tipografia": _seccion_tipografia(norm, pares),
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
        # Los bloqueos de luz y de PALETA van PREFIJADOS (lo que escribió el LLM va
        # detrás): son lo invariante del set y tienen que leerse antes que el detalle de
        # esta escena. Al pegarse acá y no en las creativas, la poda no los toca — que es
        # el punto: tienen que salir byte a byte iguales en las N piezas del job.
        completas["luz"] = " ".join(p for p in (_clausula_luz(norm), _clausula_paleta(norm),
                                                completas.get("luz", "").strip()) if p)
        # El bloqueo de mundo va prefijado a la sección 2 por el mismo motivo que el de
        # luz al de la 6: es lo invariante del set, tiene que leerse antes que el detalle
        # de este sujeto y la poda no puede tocarlo. Las dos piezas más caras de la
        # coherencia —dónde estamos y cómo está iluminado— quedan así fuera del alcance
        # tanto del LLM como del recorte.
        completas["sujeto"] = f"{_clausula_mundo(norm)} {completas.get('sujeto', '').strip()}".strip()
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
                                bloques_por_clave=dict(pares), sistema_texto=sistema,
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
