"""Redes destino de un post — fuente única para los dos flujos.

Tanto el post individual como cada fila del bulk eligen en qué redes publicar
(LinkedIn, Instagram, Facebook). Aquí vive la normalización canónica para que
el pipeline (`job_runner`), la redacción (`post_writer`) y el tracking de costos
lean exactamente la misma lista y en el mismo orden.

`redes` es el campo nuevo (lista de redes activas, default: las tres). Se mantiene
compatibilidad con el campo legacy `solo` (una sola red) para que tests y filas
antiguas sigan funcionando.
"""
from __future__ import annotations

from typing import Any

# Orden canónico (también el orden en que se muestran en la UI y en la revisión).
NETWORKS: tuple[str, ...] = ("linkedin", "instagram", "facebook")


def normalize_networks(value: Any) -> list[str]:
    """Normaliza una selección de redes a la lista canónica (orden fijo, sin duplicados).

    Acepta una lista/iterable o un string separado por comas (o ';'). Los tokens
    desconocidos se ignoran. Vacío / `None` / sin tokens válidos → todas las redes.
    """
    if value is None:
        items: list[str] = []
    elif isinstance(value, str):
        items = [v.strip().lower() for v in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(v).strip().lower() for v in value]
    else:
        items = []
    picked = [n for n in NETWORKS if n in items]
    return picked or list(NETWORKS)


def active_networks(params: dict) -> list[str]:
    """Redes destino del job a partir de sus `params`.

    Precedencia: el campo `redes` (lista o string) manda; si falta, se respeta el
    `solo` legacy (una sola red). Vacío en ambos → todas las redes.
    """
    if params.get("redes"):
        return normalize_networks(params.get("redes"))
    solo = (params.get("solo") or "").strip().lower()
    if solo in NETWORKS:
        return [solo]
    return list(NETWORKS)
