"""Usuarios de la app — hoy una lista fija, mañana lo que traiga la sesión.

AIMA no tiene autenticación: no hay sesiones, ni cookies, ni middleware, y la API
está abierta en localhost. Las identidades visuales sí necesitan un dueño, así que
este módulo introduce el concepto de usuario **sin** introducir un sistema de auth:
tres usuarios fijos y un selector en la barra de navegación.

La deuda se acota en un solo punto: **`current_user_id` es el único lugar de todo el
proyecto que decide quién está pidiendo**. La base guarda `user_id` y los endpoints
lo exigen exactamente igual que lo harían con auth real, así que cambiar a login de
verdad es reescribir el cuerpo de esta función —leer la sesión en vez del header— sin
tocar el esquema, los endpoints ni la UI.

Que el id viaje en una cabecera que el cliente elige significa que **esto no autentica
nada**: cualquiera puede decir que es cualquiera. Es correcto para lo que es —un
selector de perfil en una app de escritorio local— y es justo lo que deja de ser cierto
el día que `current_user_id` lea una sesión firmada.
"""

from __future__ import annotations

from typing import Any

# Cabecera con la que el frontend dice qué perfil está activo (la pone `lib/api.ts`
# y el proxy de Astro la reenvía tal cual).
HEADER = "X-User-Id"

# Usuarios fijos. `id` es lo que se guarda en `visual_identities.user_id`: cambiarlo
# deja huérfanas las identidades de ese usuario, así que se trata como una clave.
USERS: tuple[dict[str, str], ...] = (
    {"id": "qbyk", "nombre": "QBYK"},
    {"id": "cliente-1", "nombre": "Cliente 1"},
    {"id": "cliente-2", "nombre": "Cliente 2"},
)

DEFAULT_USER_ID = USERS[0]["id"]

_IDS = frozenset(u["id"] for u in USERS)


def listar() -> list[dict[str, str]]:
    """Los usuarios disponibles, en orden, para pintar el selector."""
    return [dict(u) for u in USERS]


def existe(user_id: Any) -> bool:
    return str(user_id or "").strip() in _IDS


def nombre(user_id: Any) -> str:
    uid = str(user_id or "").strip()
    for u in USERS:
        if u["id"] == uid:
            return u["nombre"]
    return ""


def resolver(valor: Any) -> str:
    """Id de usuario a partir del valor crudo de la cabecera.

    Un id desconocido (o vacío) cae al usuario por defecto en vez de dar 401: sin auth
    real, rechazar la petición solo produciría una app rota tras limpiar el navegador.
    La parte pura de `current_user_id`, para poder testearla sin un `Request`.
    """
    uid = str(valor or "").strip()
    return uid if uid in _IDS else DEFAULT_USER_ID


def current_user_id(request) -> str:
    """**El único punto del proyecto que decide quién está pidiendo.**

    Sustituir el cuerpo por la lectura de una sesión es todo lo que hace falta para
    migrar a auth real (ver el docstring del módulo).
    """
    return resolver(request.headers.get(HEADER, ""))
