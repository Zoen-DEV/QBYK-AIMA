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

# Formatos de publicación (columna `formato` del sheet / campo del form). El formato
# elegido aplica a TODAS las redes del post; una red que no soporta el formato se
# omite (se publica solo en las demás). Matriz según lo que Blotato permite:
#   - imagen-unica: post normal con una imagen → las tres redes.
#   - carrusel: IG carousel nativo; LinkedIn document-carousel (2-10 imágenes);
#     Facebook post multi-foto.
#   - historia / reel: solo Instagram y Facebook (target.mediaType de Blotato);
#     LinkedIn no tiene historias ni reels.
FORMATS: tuple[str, ...] = ("imagen-unica", "carrusel", "historia", "reel")

FORMAT_NETWORKS: dict[str, tuple[str, ...]] = {
    "imagen-unica": NETWORKS,
    "carrusel": NETWORKS,
    "historia": ("instagram", "facebook"),
    "reel": ("instagram", "facebook"),
}


def networks_for_format(formato: str, nets: list[str] | tuple[str, ...]) -> list[str]:
    """Filtra las redes elegidas a las que soportan el formato (orden canónico).

    Un formato desconocido/vacío no filtra nada. Puede devolver una lista vacía:
    el llamador decide si eso es un error (individual) o un warning (fila del bulk).
    """
    allowed = FORMAT_NETWORKS.get((formato or "").strip().lower(), NETWORKS)
    return [n for n in NETWORKS if n in nets and n in allowed]


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
