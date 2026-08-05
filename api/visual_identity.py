"""Esquema de una identidad visual — el contrato que gobierna el look de las piezas.

Hasta ahora la identidad visual era UN archivo (`prompts/brand.json`) y por tanto un
único look para todos los posts. Este módulo la convierte en un dato con nombre y
dueño: el **mismo** esquema, pero validable y almacenable N veces.

`brand.json` sigue siendo la identidad **system** (la de la casa) y se sigue editando a
mano: NO se copia a la base. Copiarla habría creado dos fuentes para lo mismo y editar
`brand.json` —que es como está documentado cambiar el look de todos los posts— habría
dejado de tener efecto. Corolario útil: la identidad system existe aunque no haya Mongo,
que es justo el fallback que necesita la generación.

**El esquema es exactamente el de `brand.json`**, sin campos nuevos. Quedan fuera solo
las claves que describen el ARCHIVO y no la identidad: `_comment*` (documentación) y
`version` (revisión del archivo — que además hoy no la lee nadie; una identidad guardada
tiene sus propios `created_at`/`updated_at`).

**El esquema creció una vez**, con `ritmo_carrusel`: la lista ordenada de cómo esta marca
fotografía cada beat del carrusel (`tension`, `desarrollo`, `prueba`, `remate`). Es la
única parte del carrusel que sí es identidad: el beat —qué función cumple cada slide— es
estructura y vive en `architect.json`, pero recorrer esa escalera con macros de textura o
con naturalezas muertas abiertas produce dos carruseles que no se parecen en nada. Es
**opcional**: vacío significa "lo de siempre" y cada beat cae a su respaldo de
`architect.json`, así que una identidad guardada antes del campo genera exactamente igual.

Tres contratos que el validador hace explícitos porque hoy viven implícitos en el código
y romperlos **falla en silencio**:

1. **`paleta` está ORDENADA: `[fondo, texto, acento]`.** `job_runner._lockup_plantilla`
   usa `paleta[1]` y `paleta[2]` como respaldo de `color_texto`/`color_acento` al dibujar
   sobre una plantilla. Una paleta en otro orden pinta la plantilla con los colores
   cambiados y no hay un solo error en el log.
2. **`color_texto`/`color_acento` tienen que llevar su hex, y el de la paleta.**
   `image_overlay._color` busca un `#RRGGBB` dentro de la frase y, si no lo encuentra,
   cae a un hueso por defecto: una identidad que diga "warm off-white" a secas se dibuja
   con el color de OTRA marca, otra vez sin avisar.
3. **`ritmo_carrusel` está ORDENADO por `prompt_architect.ROLES_BEAT`.** La posición ES el
   beat: la primera entrada es el plano de la tensión, la última el del remate. Una lista
   en otro orden no da error en ningún sitio — le da a cada slide el plano de otro.

Los topes de longitud tampoco son cosméticos: todo esto se inyecta en el brief de nueve
secciones, que tiene un presupuesto duro de caracteres (`architect.json` →
`validacion.max_caracteres`). Un `tono_visual` de dos párrafos se come el prompt.

Aparte del esquema está el **criterio de diseño** (`revisar_diseno`): lo que separa una
identidad que valida de una que produce piezas profesionales —el titular se lee sobre su
fondo, el acento es un acento—. Son avisos, no errores, y por eso `validar` no los mira.

Módulo **puro**: la única lectura es `brand.json` para la identidad system.
"""

from __future__ import annotations

import re
from typing import Any

import prompt_architect
import prompt_config

# Id de la identidad de la casa. No es una fila de la base: se sirve desde
# `prompts/brand.json` (ver `identidad_system`), así que nunca colisiona con un uuid4.
SYSTEM_ID = "system"
NOMBRE_SYSTEM = "QBYK — identidad de la casa"

# Campos del esquema, en el orden de `brand.json` (que es también el de la UI).
CAMPOS_LISTA: tuple[str, ...] = ("paleta", "paleta_nombres", "referencias", "ritmo_carrusel")
CAMPOS_TEXTO: tuple[str, ...] = ("color_texto", "color_acento", "tipografia",
                                 "tipografia_secundaria", "tono_visual", "aspect_ratio")
CAMPOS: tuple[str, ...] = ("paleta", "paleta_nombres", "color_texto", "color_acento",
                           "tipografia", "tipografia_secundaria", "tono_visual",
                           "aspect_ratio", "referencias", "ritmo_carrusel")

# Los beats del carrusel se importan, no se copian: la POSICIÓN dentro de
# `ritmo_carrusel` es el beat, así que las dos listas tienen que moverse juntas.
ROLES_RITMO: tuple[str, ...] = prompt_architect.ROLES_BEAT

# Mínimo 3 colores porque `_lockup_plantilla` indexa hasta `paleta[2]`; el máximo evita
# que una identidad meta una carta de color entera en el prompt.
MIN_COLORES, MAX_COLORES = 3, 6
MIN_REFERENCIAS, MAX_REFERENCIAS = 1, 6
# Un plano por beat como mucho: más entradas no serían un ritmo más rico, serían
# entradas que no le tocan a ningún slide.
MAX_RITMO = len(ROLES_RITMO)
# Topes de longitud: presupuesto del brief, no estética (ver docstring del módulo).
MAX_TEXTO = 240
MAX_REFERENCIA = 160
# El ritmo se inyecta en la sección 3, que ya carga el lockup y la continuidad de set:
# `prompt_architect` lo recorta a `validacion.ritmo_palabras` y este tope es el techo duro.
MAX_RITMO_ITEM = 160
MAX_NOMBRE_COLOR = 40
NOMBRE_MIN, NOMBRE_MAX = 1, 60  # la única regla del mínimo es "no vacío"

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX_EN_TEXTO = re.compile(r"#[0-9a-fA-F]{6}")
_HEX_SUELTO = re.compile(r"^[0-9a-fA-F]{6}$")
_HEX_CORTO = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
_ASPECT = re.compile(r"^\d{1,2}:\d{1,2}$")


class IdentidadInvalida(ValueError):
    """La identidad no cumple el esquema. `errores` trae la lista completa."""

    def __init__(self, errores: list[str]):
        self.errores = list(errores)
        super().__init__("; ".join(self.errores))


# ── Normalización ─────────────────────────────────────────────────────────────

def _texto(v: Any) -> str:
    """Colapsa espacios y recorta. Un salto de línea dentro de un campo del brief
    rompe el ensamblado por líneas del prompt final."""
    return " ".join(str(v if v is not None else "").split())


def _color(v: Any) -> str:
    """Normaliza un color a `#RRGGBB` en mayúsculas.

    Acepta las dos formas que escriben los modelos cuando se despistan —sin `#` y en
    tres dígitos— porque corregirlas aquí ahorra un reintento entero del extractor.
    Lo que no se pueda arreglar se devuelve tal cual y lo rechaza el validador.
    """
    c = _texto(v)
    if _HEX_SUELTO.match(c):
        c = f"#{c}"
    corto = _HEX_CORTO.match(c)
    if corto:
        c = "#" + "".join(d * 2 for d in corto.groups())
    return c.upper() if _HEX.match(c) else c


def _lista(v: Any) -> list[str]:
    if isinstance(v, (list, tuple)):
        items = [_texto(x) for x in v]
    else:
        items = [_texto(v)]
    return [x for x in items if x]


def _ritmo(v: Any) -> list[str]:
    """Como `_lista`, pero **conservando los huecos interiores**.

    En `ritmo_carrusel` la posición es el beat, así que descartar una entrada vacía
    correría el resto de sitio y le daría a la prueba el plano del desarrollo. Un
    hueco es legítimo —"para este beat, el respaldo de la casa"— y solo se recortan
    los vacíos del final, que no son una decisión sino una lista más corta.
    """
    items = [_texto(x) for x in v] if isinstance(v, (list, tuple)) else [_texto(v)]
    while items and not items[-1]:
        items.pop()
    return items


def normalizar(data: Any) -> dict:
    """Cualquier dict → una identidad con los nueve campos del esquema.

    Tolerante a propósito (coacciona tipos, descarta claves desconocidas, rellena lo
    que falte con vacío): quien decide si la identidad sirve es `validar`, que así
    puede dar un error por campo en vez de reventar en el primer tipo raro. Sobre una
    identidad vacía el pipeline se comporta igual que hoy — `prompt_architect` cae a
    sus defaults campo a campo.
    """
    d = data if isinstance(data, dict) else {}
    ident = {
        "paleta": [_color(c) for c in _lista(d.get("paleta"))],
        "paleta_nombres": _lista(d.get("paleta_nombres")),
        "referencias": _lista(d.get("referencias")),
        "ritmo_carrusel": _ritmo(d.get("ritmo_carrusel")),
    }
    for campo in CAMPOS_TEXTO:
        ident[campo] = _texto(d.get(campo))
    # Se devuelve en el orden del esquema: es lo que ve el usuario al editar el JSON
    # a mano y lo que hace comparables dos identidades de un vistazo.
    return {campo: ident[campo] for campo in CAMPOS}


# ── Validación ────────────────────────────────────────────────────────────────

def validar(identidad: Any) -> list[str]:
    """Errores del esquema ([] si la identidad es válida).

    Espera una identidad ya pasada por `normalizar`. Los mensajes son el feedback que
    recibe el extractor en su reintento, así que dicen qué está mal **y con qué valor**:
    "usa #AABBCC, que no es el tercer color de la paleta (#C9F227)" se puede corregir;
    "color_acento inválido" no.
    """
    ident = identidad if isinstance(identidad, dict) else {}
    errores: list[str] = []

    paleta = ident.get("paleta") or []
    nombres = ident.get("paleta_nombres") or []

    if not MIN_COLORES <= len(paleta) <= MAX_COLORES:
        errores.append(
            f"`paleta` debe tener entre {MIN_COLORES} y {MAX_COLORES} colores "
            f"(tiene {len(paleta)}), ordenados [fondo, texto, acento]"
        )
    malos = [c for c in paleta if not _HEX.match(c)]
    if malos:
        errores.append(f"`paleta` solo admite colores #RRGGBB — no válidos: {', '.join(malos)}")
    if len(nombres) != len(paleta):
        errores.append(
            f"`paleta_nombres` necesita un nombre por color: {len(nombres)} nombres "
            f"para {len(paleta)} colores"
        )
    largos = [n for n in nombres if len(n) > MAX_NOMBRE_COLOR]
    if largos:
        errores.append(
            f"nombres de color de más de {MAX_NOMBRE_COLOR} caracteres: {', '.join(largos)}"
        )

    # El contrato del orden de la paleta, hecho comprobable: el color del texto es el
    # segundo y el del acento el tercero. Es lo que sostiene el respaldo de plantilla.
    for campo, idx, rol in (("color_texto", 1, "el segundo color de la paleta"),
                            ("color_acento", 2, "el tercer color de la paleta")):
        valor = ident.get(campo) or ""
        if not valor:
            errores.append(f"falta `{campo}`")
            continue
        if len(valor) > MAX_TEXTO:
            errores.append(f"`{campo}` supera los {MAX_TEXTO} caracteres")
        encontrado = _HEX_EN_TEXTO.search(valor)
        if not encontrado:
            errores.append(
                f"`{campo}` tiene que incluir su color en #RRGGBB (p. ej. "
                f'"bone white (#EDEAE0) on the near-black"); sin el hex la pieza de '
                f"respaldo se dibuja con un color que no es el de la identidad"
            )
            continue
        if len(paleta) > idx and encontrado.group(0).upper() != paleta[idx].upper():
            errores.append(
                f"`{campo}` usa {encontrado.group(0)}, que no es {rol} ({paleta[idx]})"
            )

    for campo in ("tipografia", "tipografia_secundaria", "tono_visual"):
        valor = ident.get(campo) or ""
        if not valor:
            errores.append(f"falta `{campo}`")
        elif len(valor) > MAX_TEXTO:
            errores.append(f"`{campo}` supera los {MAX_TEXTO} caracteres (tiene {len(valor)})")

    aspect = ident.get("aspect_ratio") or ""
    if not _ASPECT.match(aspect):
        errores.append(f'`aspect_ratio` debe ser "ancho:alto" (p. ej. "4:5"); llegó "{aspect}"')

    referencias = ident.get("referencias") or []
    if not MIN_REFERENCIAS <= len(referencias) <= MAX_REFERENCIAS:
        errores.append(
            f"`referencias` debe tener entre {MIN_REFERENCIAS} y {MAX_REFERENCIAS} entradas "
            f"(tiene {len(referencias)})"
        )
    if any(len(r) > MAX_REFERENCIA for r in referencias):
        errores.append(f"hay referencias de más de {MAX_REFERENCIA} caracteres")

    # `ritmo_carrusel` es OPCIONAL: vacío = cada beat cae a su respaldo de
    # `architect.json`, que es como se generaba antes del campo. Tampoco se exige
    # completo — definir solo el remate y dejar el resto de la casa es una decisión
    # válida, y rechazarla convertiría un ajuste fino en un error.
    ritmo = ident.get("ritmo_carrusel") or []
    if len(ritmo) > MAX_RITMO:
        errores.append(
            f"`ritmo_carrusel` admite como mucho {MAX_RITMO} planos, uno por beat del "
            f"carrusel ({', '.join(ROLES_RITMO)}); llegaron {len(ritmo)}"
        )
    if any(len(r) > MAX_RITMO_ITEM for r in ritmo):
        errores.append(f"hay planos de `ritmo_carrusel` de más de {MAX_RITMO_ITEM} caracteres")

    return errores


# ── Criterio de diseño ────────────────────────────────────────────────────────
#
# `validar` comprueba que la identidad es del TIPO correcto; esto comprueba que
# **sirve**. Una identidad puede validar entera y aun así producir piezas de
# aficionado: un texto que no se lee sobre su fondo, un "acento" que es otro gris.
# Son defectos de diseño, no de esquema, así que viven aparte y `validar` NO los mira:
# rechazar por ellos convertiría una identidad discutible en una imposible de guardar.
# Quien extrae los usa como feedback del reintento y, si sobreviven, como aviso al
# lado del editor.

# El titular va sobre una FOTOGRAFÍA, no sobre un plano de color. Aunque el tipo es
# enorme (9-16% del alto) y el mínimo de WCAG para texto grande sería 3:1, el grano, el
# falloff y la textura de la base se comen parte del contraste: 4.5:1 es lo que aguanta.
CONTRASTE_MIN = 4.5
# Saturación HSV por debajo de la cual el tercer color no es un acento: es un neutro.
SATURACION_ACENTO_MIN = 0.25
# Distancia euclídea en RGB a partir de la cual dos colores se leen como distintos.
DISTANCIA_MIN = 60.0


def _rgb(color: str) -> tuple[int, int, int] | None:
    c = _color(color)
    if not _HEX.match(c):
        return None
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def _luminancia(rgb: tuple[int, int, int]) -> float:
    """Luminancia relativa WCAG (0 = negro, 1 = blanco)."""
    canales = []
    for v in rgb:
        s = v / 255
        canales.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canales[0] + 0.7152 * canales[1] + 0.0722 * canales[2]


def contraste(a: str, b: str) -> float:
    """Ratio de contraste WCAG entre dos colores (1 = el mismo, 21 = negro sobre blanco).

    0.0 si alguno no es un hex válido: de eso ya se queja `validar`, y devolver 0 deja
    que quien llame ignore el caso sin duplicar el error.
    """
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return 0.0
    la, lb = _luminancia(ra), _luminancia(rb)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def saturacion(color: str) -> float:
    """Saturación HSV del color (0 = gris, 1 = color puro)."""
    rgb = _rgb(color)
    if rgb is None:
        return 0.0
    alto, bajo = max(rgb), min(rgb)
    return (alto - bajo) / alto if alto else 0.0


def distancia(a: str, b: str) -> float:
    """Distancia euclídea en RGB: "¿se ven como dos colores distintos?".

    Para esto NO se usa el contraste, a propósito: dos colores pueden tener casi la
    misma luminancia y ser inconfundibles —hueso `#EDEAE0` y lima ácido `#C9F227`
    contrastan 1.03:1 y nadie los mezcla—, porque lo que hace visible a un acento es el
    croma, no el brillo.
    """
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return 0.0
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def revisar_diseno(identidad: Any) -> list[str]:
    """Reparos de diseño de una identidad ([] si la pieza se va a sostener).

    Espera una identidad ya pasada por `normalizar`. Los mensajes dicen el defecto **y
    con qué valores**, porque son a la vez el feedback del reintento del extractor y lo
    que lee el usuario en el editor.
    """
    paleta = (identidad if isinstance(identidad, dict) else {}).get("paleta") or []
    if len(paleta) < 3:
        return []  # el esquema ya se está quejando de eso: no se duplica el ruido
    fondo, texto, acento = paleta[0], paleta[1], paleta[2]
    reparos: list[str] = []

    ratio = contraste(texto, fondo)
    if ratio and ratio < CONTRASTE_MIN:
        reparos.append(
            f"el texto ({texto}) sobre el fondo ({fondo}) contrasta {ratio:.1f}:1 y un "
            f"titular necesita {CONTRASTE_MIN}:1 — el segundo color tiene que ser mucho "
            f"más claro o mucho más oscuro que el primero"
        )

    sat = saturacion(acento)
    if sat < SATURACION_ACENTO_MIN:
        reparos.append(
            f"el acento ({acento}) es prácticamente un neutro (saturación "
            f"{sat * 100:.0f}%): el acento es el color que aparece UNA vez en la pieza y "
            f"tiene que leerse como una decisión, no como un tercer gris"
        )

    for otro, rol in ((texto, "el texto"), (fondo, "el fondo")):
        if distancia(acento, otro) < DISTANCIA_MIN:
            reparos.append(
                f"el acento ({acento}) es casi el mismo color que {rol} ({otro}): la "
                f"palabra acentuada no se distinguiría en el titular"
            )
    return reparos


def exigir_valida(identidad: Any) -> dict:
    """Normaliza y valida de una vez. Devuelve la identidad o lanza `IdentidadInvalida`."""
    ident = normalizar(identidad)
    errores = validar(ident)
    if errores:
        raise IdentidadInvalida(errores)
    return ident


def validar_nombre(nombre: Any) -> list[str]:
    """Errores del nombre de una identidad ([] si sirve)."""
    n = _texto(nombre)
    if len(n) < NOMBRE_MIN:
        return ["el nombre no puede estar vacío"]
    if len(n) > NOMBRE_MAX:
        return [f"el nombre no puede pasar de {NOMBRE_MAX} caracteres (tiene {len(n)})"]
    return []


def normalizar_nombre(nombre: Any) -> str:
    return _texto(nombre)[:NOMBRE_MAX].strip()


def nombre_sugerido(identidad: Any) -> str:
    """Nombre por defecto cuando el usuario deja el campo vacío.

    Sale de los nombres de color porque es lo que se reconoce de un vistazo en la lista
    ("Acid lime · near-black"), no de un uuid ni de un "Identidad 3" que no distingue
    nada. Se nombra por acento + fondo: los dos colores que más cambian entre marcas.
    """
    nombres = [n for n in (normalizar(identidad).get("paleta_nombres") or []) if n]
    if len(nombres) >= 3:
        base = f"{nombres[2]} · {nombres[0]}"
    elif len(nombres) == 2:
        base = f"{nombres[1]} · {nombres[0]}"
    elif nombres:
        base = nombres[0]
    else:
        base = "Identidad visual"
    base = base[:NOMBRE_MAX].strip()
    return base[:1].upper() + base[1:]


# ── Identidad system (la casa) ────────────────────────────────────────────────

def identidad_system() -> dict:
    """La identidad de la casa: `prompts/brand.json`, normalizado.

    No se valida a propósito. `prompt_config.load` devuelve `{}` ante un archivo
    ausente o roto y todo el pipeline está construido para caer a sus defaults campo a
    campo; hacer que esto lance convertiría un `brand.json` mal editado en una caída de
    la generación, que es exactamente lo que hoy no pasa.
    """
    return normalizar(prompt_config.brand())


def fila_system() -> dict:
    """La identidad system con el mismo shape que una fila de la base.

    La UI y la API tratan a las dos por igual; lo único que las distingue es
    `is_system`, que apaga el botón de eliminar y el de editar. `is_default` lo decide
    el store: es system quien está activa cuando el usuario no marcó ninguna propia.
    """
    return {
        "id": SYSTEM_ID,
        "user_id": None,
        "name": NOMBRE_SYSTEM,
        "identity_json": identidad_system(),
        "is_default": False,
        "is_system": True,
        "created_at": None,
        "updated_at": None,
    }
