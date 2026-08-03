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

Dos contratos que el validador hace explícitos porque hoy viven implícitos en el código
y romperlos **falla en silencio**:

1. **`paleta` está ORDENADA: `[fondo, texto, acento]`.** `job_runner._lockup_plantilla`
   usa `paleta[1]` y `paleta[2]` como respaldo de `color_texto`/`color_acento` al dibujar
   sobre una plantilla. Una paleta en otro orden pinta la plantilla con los colores
   cambiados y no hay un solo error en el log.
2. **`color_texto`/`color_acento` tienen que llevar su hex, y el de la paleta.**
   `image_overlay._color` busca un `#RRGGBB` dentro de la frase y, si no lo encuentra,
   cae a un hueso por defecto: una identidad que diga "warm off-white" a secas se dibuja
   con el color de OTRA marca, otra vez sin avisar.

Los topes de longitud tampoco son cosméticos: todo esto se inyecta en el brief de nueve
secciones, que tiene un presupuesto duro de caracteres (`architect.json` →
`validacion.max_caracteres`). Un `tono_visual` de dos párrafos se come el prompt.

Módulo **puro**: la única lectura es `brand.json` para la identidad system.
"""

from __future__ import annotations

import re
from typing import Any

import prompt_config

# Id de la identidad de la casa. No es una fila de la base: se sirve desde
# `prompts/brand.json` (ver `identidad_system`), así que nunca colisiona con un uuid4.
SYSTEM_ID = "system"
NOMBRE_SYSTEM = "QBYK — identidad de la casa"

# Campos del esquema, en el orden de `brand.json` (que es también el de la UI).
CAMPOS_LISTA: tuple[str, ...] = ("paleta", "paleta_nombres", "referencias")
CAMPOS_TEXTO: tuple[str, ...] = ("color_texto", "color_acento", "tipografia",
                                 "tipografia_secundaria", "tono_visual", "aspect_ratio")
CAMPOS: tuple[str, ...] = ("paleta", "paleta_nombres", "color_texto", "color_acento",
                           "tipografia", "tipografia_secundaria", "tono_visual",
                           "aspect_ratio", "referencias")

# Mínimo 3 colores porque `_lockup_plantilla` indexa hasta `paleta[2]`; el máximo evita
# que una identidad meta una carta de color entera en el prompt.
MIN_COLORES, MAX_COLORES = 3, 6
MIN_REFERENCIAS, MAX_REFERENCIAS = 1, 6
# Topes de longitud: presupuesto del brief, no estética (ver docstring del módulo).
MAX_TEXTO = 240
MAX_REFERENCIA = 160
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

    return errores


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
