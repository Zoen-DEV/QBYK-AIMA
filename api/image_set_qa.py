"""QA de CONJUNTO: ¿las N piezas del carrusel se leen como un set?

Es la pieza que faltaba en la arquitectura, y la única de este plan que evita que todo
lo demás vuelva a degradarse sin que nadie se entere. Hasta acá cada imagen se
generaba, se validaba y se aceptaba **aislada**: `rubric.json` puntúa un prompt sin
conocer a sus hermanos y `image_text_qa` solo mira ortografía y recorte. Ningún control
por imagen puede detectar el defecto que el lector nota primero —que las cinco piezas
no se parecen entre sí—, porque para verlo hay que verlas juntas. Esto es esa llamada.

Espejo deliberado de `image_text_qa`, hasta en las decisiones incómodas:

- **Anthropic a propósito**, no `vision_disponible`. Perplexity también lee imágenes,
  pero encenderlo aquí añadiría una llamada por carrusel a quien hoy tiene el QA
  apagado sin saberlo. Es un cambio que se decide, no un efecto secundario.
- **Reducción en memoria**: las piezas van al modelo escaladas y en JPEG. Son N y no
  una, así que el peso importa más que en el QA de texto.
- **`try/except` de punta a punta.** Nunca lanza. Sin key, sin Pillow, con una imagen
  rota o ante cualquier fallo de red devuelve "no verificado" y el pipeline sigue.

Veredictos **binarios con motivo**, no puntuaciones: una nota de "coherencia" no dice
qué slide rehacer. Un binario por slide sí, y la acción que le corresponde —rehacer esa
imagen— ya existe en las dos compuertas de revisión.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import llm_json
import prompt_config

# Lado máximo por pieza. Más bajo que el del QA de texto (1568) porque acá van N
# imágenes en la MISMA llamada y lo que se juzga —mundo, tipografía, grade, bordes— se
# ve entero a esta resolución; leer letra por letra es trabajo del otro QA.
_MAX_LADO = 900
_CALIDAD_JPEG = 80
_MAX_BYTES_TOTAL = 12 * 1024 * 1024
_VEREDICTOS = ("mismo_mundo", "mismo_sistema_tipografico", "mismo_grade",
               "sin_marco_ni_bandas")


@dataclass
class PiezaSet:
    """Veredicto de UNA pieza dentro del conjunto."""

    indice: int
    ok: bool
    fallos: list[str] = field(default_factory=list)   # veredictos en `False`
    motivo: str = ""


@dataclass
class ResultadoSet:
    """Veredicto del conjunto (`verificado=False` = no se pudo comprobar)."""

    ok: bool
    verificado: bool = False
    piezas: list[PiezaSet] = field(default_factory=list)
    peor: int = -1               # índice del outlier, o -1
    motivo: str = ""
    uso: dict | None = None


def _cfg() -> dict:
    return prompt_config.qa_set()


def _entero(clave: str, defecto: int) -> int:
    try:
        return int(_cfg().get(clave, defecto))
    except (TypeError, ValueError):
        return defecto


def veredictos() -> tuple[str, ...]:
    """Los cuatro veredictos, del archivo de prompt (con respaldo).

    Fuente única: con ellos se arma el JSON que se pide Y se lee el que llega, así que
    no pueden separarse.
    """
    v = _cfg().get("veredictos")
    if isinstance(v, list) and v:
        return tuple(str(x).strip() for x in v if str(x).strip())
    return _VEREDICTOS


def max_reintentos() -> int:
    """Rondas de regeneración permitidas. **Una**, y no es un número redondo.

    Regenerar cuesta créditos por imagen y el veredicto es una opinión, no una medida:
    encadenar rondas convierte un carrusel caro en uno carísimo sin garantía de que la
    segunda tirada se parezca más al set que la primera.
    """
    return _entero("max_reintentos", 1)


def disponible(cfg) -> bool:
    """True si este QA puede correr. Anthropic a propósito (ver el docstring del módulo)."""
    return bool(getattr(cfg, "anthropic_api_key", ""))


def activo(cfg) -> bool:
    """El flag del proyecto + que haya con qué correrlo."""
    return bool(getattr(cfg, "image_set_qa", True)) and disponible(cfg)


# ── Preparación ───────────────────────────────────────────────────────────────

def _preparar(png: bytes) -> tuple[bytes, str]:
    """Reduce una pieza a lo que hace falta para juzgar el conjunto.

    Sin Pillow se manda tal cual: quien llama corta por peso total si se pasa.
    """
    try:
        from PIL import Image
    except ImportError:
        return png, "image/png"
    try:
        img = Image.open(io.BytesIO(png)).convert("RGB")
        if max(img.size) > _MAX_LADO:
            img.thumbnail((_MAX_LADO, _MAX_LADO))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_CALIDAD_JPEG)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return png, "image/png"


def _instruccion(n: int) -> str:
    plantilla = (_cfg().get("instruccion") or
                 "These {n} images are one carousel. Return JSON with one entry per "
                 "image: {claves} and a short reason. Also return `peor`, the index of "
                 "the image that most breaks the set, or -1.")
    claves = ", ".join(f'"{v}": true' for v in veredictos())
    return plantilla.replace("{n}", str(n)).replace("{claves}", claves)


# ── Lectura del veredicto ─────────────────────────────────────────────────────

def _piezas(data: dict, n: int) -> list[PiezaSet]:
    """El JSON del modelo → una `PiezaSet` por imagen, en orden y sin huecos.

    Tolerante como el resto del proyecto: una entrada que falte o venga con el índice
    cambiado se trata como "no dice nada de esa pieza" (ok), nunca como un fallo. Un
    QA que inventa fallos por un JSON mal formado es peor que no tenerlo: dispara
    regeneraciones que cuestan créditos.
    """
    crudas = data.get("imagenes")
    por_indice: dict[int, dict] = {}
    if isinstance(crudas, list):
        for i, item in enumerate(crudas):
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("indice", i))
            except (TypeError, ValueError):
                idx = i
            if 0 <= idx < n:
                por_indice.setdefault(idx, item)

    piezas: list[PiezaSet] = []
    for i in range(n):
        item = por_indice.get(i) or {}
        fallos = [v for v in veredictos() if item.get(v) is False]
        piezas.append(PiezaSet(indice=i, ok=not fallos, fallos=fallos,
                               motivo=str(item.get("motivo") or "").strip()))
    return piezas


def _peor(data: dict, piezas: list[PiezaSet]) -> int:
    """El outlier: el que dice el modelo si es válido y falla de verdad; si no, el que
    más veredictos rompe. `-1` cuando el set está bien."""
    malas = [p for p in piezas if not p.ok]
    if not malas:
        return -1
    try:
        dicho = int(data.get("peor", -1))
    except (TypeError, ValueError):
        dicho = -1
    if any(p.indice == dicho for p in malas):
        return dicho
    return max(malas, key=lambda p: len(p.fallos)).indice


# ── Revisión ──────────────────────────────────────────────────────────────────

def revisar(imagenes: list[bytes], *, cfg) -> ResultadoSet:
    """Mira las N piezas juntas y dice cuáles rompen el set. Nunca lanza.

    `imagenes` son los bytes **ya publicables** (overlay y grade aplicados): es lo que
    va a ver el lector, y por tanto lo único que tiene sentido juzgar.
    """
    piezas = [p for p in (imagenes or []) if p]
    # Con menos de tres piezas no hay "conjunto" que juzgar: dos imágenes distintas
    # son dos imágenes, no un set incoherente.
    if len(piezas) < 3:
        return ResultadoSet(ok=True, motivo="menos de tres piezas: no hay conjunto")
    if not activo(cfg):
        return ResultadoSet(ok=True, motivo="QA de conjunto desactivado o sin modelo de visión")

    conf = _cfg()
    try:
        preparadas = [_preparar(p) for p in piezas]
        if sum(len(b) for b, _ in preparadas) > _MAX_BYTES_TOTAL:
            return ResultadoSet(ok=True, motivo="el juego pesa demasiado para verificarlo")
        data, uso = llm_json.complete_json_vision_multi(
            conf.get("sistema") or "You are an art director signing off a carousel. JSON only.",
            _instruccion(len(preparadas)), preparadas, cfg=cfg,
            max_tokens=_entero("modelo_max_tokens", 1200),
        )
    except Exception as e:  # noqa: BLE001
        return ResultadoSet(ok=True, motivo=f"no se pudo verificar el conjunto ({e})")

    resultado = _piezas(data, len(piezas))
    peor = _peor(data, resultado)
    malas = [p for p in resultado if not p.ok]
    return ResultadoSet(
        ok=not malas, verificado=True, piezas=resultado, peor=peor, uso=uso,
        motivo=("el conjunto se lee como un set" if not malas else
                f"{len(malas)} pieza(s) rompen el set: " +
                "; ".join(f"#{p.indice + 1} ({', '.join(p.fallos)})" for p in malas)),
    )
