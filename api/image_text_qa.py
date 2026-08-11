"""QA post-generación: ¿el texto impreso en la imagen es EXACTAMENTE el esperado?

Desde que el texto lo renderiza el propio modelo (y no Pillow), nadie garantiza que
lo que sale escrito sea lo que se pidió: los generadores de imagen se comen tildes,
cambian letras y a veces inventan una palabra. Este módulo cierra el lazo: descarga
la imagen, se la pasa a un modelo de visión y compara carácter a carácter con lo
esperado. `job_runner` decide qué hacer con el veredicto (reintentar reforzando la
instrucción de texto, hasta el máximo configurado).

Reglas de comparación: se ignoran mayúsculas, espacios y puntuación (el diseño puede
poner el titular en caja alta), pero **NO los acentos** — "IA" y "Iá" son cosas
distintas y el fallo típico del modelo es justo ese.

**La exigencia va POR NIVEL**, y no es una concesión sino la diferencia entre un QA
útil y uno que quema créditos. Un titular son 3-6 palabras a tamaño de póster: ahí una
letra mal es un defecto que se ve desde el otro lado de la sala, y se exige exacto. Un
cuerpo son 30 palabras al 5% del alto: ningún generador las clava carácter a carácter,
y exigirle lo mismo convertiría cada errata en una regeneración pagada de la imagen
entera. Por eso el cuerpo se mide por SIMILITUD (`similitud_cuerpo` en
`prompts/qa_vision.json`) — lo que se persigue ahí es que diga lo que tenía que decir,
no que lo diga letra por letra.

El otro fallo que se veía en producción no es de ortografía sino de encuadre: el
titular sale bien escrito pero **cortado por el borde**. La comparación de strings no
lo detecta (el modelo de visión lee la palabra igual aunque le falte la mitad de
arriba), así que se le pregunta aparte (`recortado`) y también cuenta como fallo.

Best-effort de punta a punta: sin key de visión, con una plantilla local, o si la
llamada falla, se devuelve "no verificado" y el pipeline sigue. Nunca lanza.
"""

from __future__ import annotations

import difflib
import io
import re
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import llm_json
import prompt_config

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_BYTES = 4 * 1024 * 1024
_MAX_LADO = 1568          # lado máximo que aprovecha el modelo de visión
_TIMEOUT = 60


@dataclass
class ResultadoQA:
    """Veredicto de una verificación (`verificado=False` = no se pudo comprobar)."""

    ok: bool
    verificado: bool = False
    texto_visto: str = ""
    motivo: str = ""
    uso: dict | None = None
    recortado: bool = False   # el texto está bien escrito pero el borde lo corta


def _cfg() -> dict:
    return prompt_config.qa_vision()


def _entero(clave: str, defecto: int) -> int:
    try:
        return int(_cfg().get(clave, defecto))
    except (TypeError, ValueError):
        return defecto


def max_intentos() -> int:
    """Reintentos de generación permitidos cuando el texto sale mal (config)."""
    return _entero("max_intentos", 2)


def disponible(cfg) -> bool:
    """True si este QA puede correr. **Anthropic a propósito**, no `vision_disponible`.

    Perplexity también lee imágenes, así que técnicamente podría verificar el texto
    impreso. Se deja fuera porque encenderlo cambiaría el pipeline por su cuenta: una
    llamada de visión más por imagen generada, en todos los posts, para quien hoy tiene
    el QA apagado sin saberlo. Es un cambio que se decide, no un efecto secundario de
    haber añadido el extractor de identidades. Para activarlo, `llm_json.vision_disponible(cfg)`.
    """
    return bool(getattr(cfg, "anthropic_api_key", ""))


# ── Comparación ───────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    """Minúsculas y sin puntuación ni espacios, PERO conservando los acentos."""
    t = unicodedata.normalize("NFC", texto or "")
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[^0-9a-zà-öø-ÿñÑ]+", "", t.casefold())
    return t


def coincide(esperado: str, visto: str) -> bool:
    """True si `visto` contiene el texto esperado con sus acentos intactos."""
    e, v = _normalizar(esperado), _normalizar(visto)
    return bool(e) and e in v


# Bloques que se miden por similitud en vez de por coincidencia exacta. Es la lista
# de los que se imprimen a tamaño de LECTURA: a 30 palabras y al 5% del alto, ningún
# generador clava el string, y exigirlo convierte cada errata en una regeneración
# pagada. Los de display (etiqueta, titular, apoyo) siguen siendo exactos.
_BLOQUES_APROXIMADOS = ("cuerpo",)
_SIMILITUD_CUERPO = 0.90


def similitud(esperado: str, visto: str) -> float:
    """Parecido [0,1] del bloque esperado con lo mejor que se le parezca en lo leído.

    Se busca el mejor tramo dentro de lo visto y no se comparan las dos cadenas
    enteras: el modelo de visión devuelve TODO el texto de la imagen —titular
    incluido—, así que comparar contra el conjunto castigaría a un cuerpo correcto.
    """
    e, v = _normalizar(esperado), _normalizar(visto)
    if not e:
        return 1.0
    if not v:
        return 0.0
    if e in v:
        return 1.0
    m = difflib.SequenceMatcher(None, e, v).find_longest_match(0, len(e), 0, len(v))
    tramo = v[max(0, m.b - m.a):][:len(e) + 8]
    return difflib.SequenceMatcher(None, e, tramo).ratio()


def _revisar_bloques(bloques: list[tuple[str, str]], visto: str, umbral: float) -> str:
    """`""` si todos los bloques están bien; si no, el motivo del primero que falla."""
    for clave, esperado in bloques:
        if not esperado:
            continue
        if clave in _BLOQUES_APROXIMADOS:
            ratio = similitud(esperado, visto)
            if ratio < umbral:
                return (f'el {clave} no coincide ({int(ratio * 100)}% de parecido): '
                        f'esperaba "{esperado}"')
        elif not coincide(esperado, visto):
            return f'el {clave} no coincide: esperaba "{esperado}"'
    return ""


# ── Descarga y preparación de la imagen ───────────────────────────────────────

def _es_local(src: str) -> bool:
    return not (src or "").lower().startswith(("http://", "https://"))


def _tipo(datos: bytes) -> str:
    if datos[:8].startswith(b"\x89PNG"):
        return "image/png"
    if datos[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    if datos[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def _descargar(src: str) -> bytes:
    if _es_local(src):
        return Path(src).read_bytes()
    req = urllib.request.Request(src, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_entero("timeout_descarga", _TIMEOUT)) as r:
        return r.read()


def _preparar(datos: bytes) -> tuple[bytes, str]:
    """Reduce la imagen a lo que aprovecha el modelo de visión (si hay Pillow).

    Sin Pillow se manda tal cual; si además pesa más del límite, quien llama lo
    trata como "no verificable" en vez de reventar la request.
    """
    try:
        from PIL import Image
    except ImportError:
        return datos, _tipo(datos)
    try:
        img = Image.open(io.BytesIO(datos))
        img = img.convert("RGB")
        lado = max(img.size)
        if lado > _MAX_LADO:
            escala = _MAX_LADO / lado
            img = img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return datos, _tipo(datos)


# ── Verificación ──────────────────────────────────────────────────────────────

def _texto_leido(data: dict) -> str:
    """Une lo que el modelo de visión dice haber leído (titular + resto)."""
    partes = [str(data.get("texto") or "")]
    otros = data.get("otros")
    if isinstance(otros, (list, tuple)):
        partes.extend(str(o) for o in otros)
    elif isinstance(otros, str):
        partes.append(otros)
    return " ".join(p.strip() for p in partes if p and p.strip())


def verificar(src: str, esperado, *, cfg) -> ResultadoQA:
    """Lee el texto impreso en `src` y lo compara con `esperado`. Nunca lanza.

    `esperado` es un string (contrato de siempre) o una lista `[(clave, texto)]` con
    los bloques de la pieza: la clave decide con qué exigencia se mide cada uno.
    """
    bloques = ([(str(c), " ".join(str(t).split())) for c, t in esperado]
               if isinstance(esperado, (list, tuple))
               else [("texto", " ".join(str(esperado or "").split()))])
    bloques = [(c, t) for c, t in bloques if t]
    esperado = " ".join(t for _, t in bloques)
    if not esperado:
        return ResultadoQA(ok=True, motivo="sin texto esperado")
    if not src:
        return ResultadoQA(ok=True, motivo="sin imagen que verificar")
    if _es_local(src):
        # Plantilla local de respaldo: no lleva texto renderizado por el modelo,
        # verificarla solo gastaría tokens para confirmar lo que ya sabemos.
        return ResultadoQA(ok=True, motivo="plantilla local (sin texto generado)")
    if not disponible(cfg):
        return ResultadoQA(ok=True, motivo="sin modelo de visión configurado")

    conf = _cfg()
    try:
        datos = _descargar(src)
        imagen, media_type = _preparar(datos)
        if len(imagen) > _entero("max_bytes", _MAX_BYTES):
            return ResultadoQA(ok=True, motivo="imagen demasiado grande para verificar")
        data, uso = llm_json.complete_json_vision(
            conf.get("sistema") or "You transcribe text from images. Answer with JSON only.",
            conf.get("instruccion") or "Transcribe every word printed in this image as JSON.",
            imagen, media_type=media_type, cfg=cfg,
            max_tokens=_entero("modelo_max_tokens", 400),
        )
    except Exception as e:
        return ResultadoQA(ok=True, motivo=f"no se pudo verificar ({e})")

    visto = _texto_leido(data)
    if data.get("legible") is False and not visto:
        return ResultadoQA(ok=False, verificado=True, texto_visto="",
                           motivo="el texto de la imagen no es legible", uso=uso)
    # Un titular bien escrito pero cortado por el borde antes pasaba el QA: la
    # comparación de strings no lo ve (el modelo de visión lee "Conecta MCP" igual
    # aunque le falte la mitad de arriba a las letras). Es el defecto que se veía en
    # producción, así que cuenta como fallo y dispara el reintento reforzado.
    if data.get("recortado") is True:
        return ResultadoQA(ok=False, verificado=True, texto_visto=visto, recortado=True,
                           motivo=f'el borde del cuadro corta el texto ("{visto or "?"}")', uso=uso)
    fallo = _revisar_bloques(bloques, visto, _umbral_cuerpo())
    if not fallo:
        return ResultadoQA(ok=True, verificado=True, texto_visto=visto,
                           motivo="texto correcto", uso=uso)
    return ResultadoQA(
        ok=False, verificado=True, texto_visto=visto,
        motivo=f'{fallo} y la imagen dice "{visto or "(nada)"}"', uso=uso,
    )


def _umbral_cuerpo() -> float:
    try:
        return float(_cfg().get("similitud_cuerpo", _SIMILITUD_CUERPO))
    except (TypeError, ValueError):
        return _SIMILITUD_CUERPO
