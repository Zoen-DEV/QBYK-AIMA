"""
Preparación de la imagen para cada red: recorte al aspecto de destino, texto de la
pieza cuando toca dibujarlo, y grade común.

El texto (hook de portada, idea de cada slide) lo renderiza **el propio modelo** al
generar la imagen: viaja dentro del prompt que arma `prompt_architect`. Ese es el
camino normal y este módulo no lo toca. Pero cuando la imagen NO sale del modelo
—sin token OAuth, la generación falló, o el usuario eligió "plantillas"— la pieza es
un PNG de respaldo que no dice nada, y el post se publicaba con una foto muda donde
tenía que ir el titular. Para ese caso —y solo para ese— vuelve una capa de texto
con Pillow: quien llama (`job_runner`) pasa el lockup ya resuelto **únicamente**
cuando la fuente es una plantilla nuestra, así una imagen generada nunca se
sobreimprime dos veces.

Lo que hace el módulo, en orden:

  1. traer la imagen base (URL del proveedor o plantilla local),
  2. recortarla centrada al aspecto que espera cada red (feed 4:5, historia 9:16),
  3. dibujar el lockup de marca si el llamador lo pidió (solo plantillas),
  4. igualar el color de los slides al de la portada (`match_grade`).

Dependencias:
  Pillow  ->  python -m pip install Pillow

API pública:
  render_feed(src, texto=None)  -> bytes   imagen de feed 1080x1350 (4:5): portada,
                                slides y las imágenes únicas de LinkedIn / IG / Facebook
  render_story(src, texto=None) -> bytes   historia vertical 1080x1920 (9:16)
  match_grade(png, reference)   -> bytes

`texto` es el lockup a dibujar (o None = no dibujar nada):

  {"titular": str, "kicker": str, "acento": str,
   "rol": "portada" | "contenido",
   "color_texto": str, "color_acento": str}    # admiten "#RRGGBB" o una frase que lo contenga

`src` puede ser una URL http(s) (salida del proveedor) o una ruta local (plantilla de
respaldo). Todo devuelve PNG; se sube con `bc.upload_media_local()` para obtener la
URL pública de Blotato que va en `mediaUrls`.
"""

import io
import os
import re
import sys
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat
except ImportError as e:
    raise RuntimeError(
        "[error] Pillow no está instalado. Ejecuta: python -m pip install Pillow"
    ) from e


# ── Lienzos ────────────────────────────────────────────────────────────────────
#
# El lienzo del feed es 4:5 (el formato vertical que aceptan Instagram —imagen única
# y carrusel—, LinkedIn y Facebook, y el que más pantalla ocupa en el scroll). Antes
# era 1:1 y el 4:5 de LinkedIn se fabricaba escalando ese cuadrado; ahora la imagen
# se pide ya en 4:5 y el lienzo es el mismo para todas las redes de feed.

_FEED_W, _FEED_H = 1080, 1350
_STORY_W, _STORY_H = 1080, 1920


# ── Helpers ────────────────────────────────────────────────────────────────────

_TIMEOUT_SECS = 30

# Browser User-Agent: some image hosts (e.g. Higgsfield behind Cloudflare) reject
# urllib's default UA. Only used for remote URLs; local template paths skip it.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _is_local_path(src: str) -> bool:
    """True if `src` is a local filesystem path rather than an http(s) URL.

    Template fallbacks pass a local .png path; Higgsfield passes a URL.
    """
    return not src.lower().startswith(("http://", "https://"))


def _fetch_base(url: str, target_size: tuple[int, int] = (_FEED_W, _FEED_H)) -> Image.Image:
    """Load the base image and return it as an RGB Pillow image of `target_size`.

    `url` may be an http(s) URL (provider output) or a local filesystem path
    (template fallback). Center-crops to preserve composition.
    """
    if _is_local_path(url):
        img = Image.open(url).convert("RGB")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECS) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = target_size
    w, h = img.size
    if (w, h) != (tw, th):
        scale = max(tw / w, th / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - tw) // 2
        top = (nh - th) // 2
        img = img.crop((left, top, left + tw, top + th))
    return img


def _to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Texto sobre las plantillas de respaldo ─────────────────────────────────────
#
# El layout imita el mismo lockup de póster que el prompt le pide al modelo
# (`prompts/architect.json`): titular en la banda alta, segunda línea anclada al pie,
# caja alta, alineado a la izquierda, todo dentro del área segura del 8%. Así una
# tirada que cayó a plantilla se lee como el resto del set y no como otra cosa.
#
# Los colores son los de la marca y llegan de fuera (`brand.json` vía job_runner):
# editar ese archivo tiene que seguir cambiando el look de TODOS los posts, también
# el de los que caen a plantilla.

_MARGEN = 0.08          # área segura: el mismo 8% por lado que declara el brief
_ANCHO = 0.84           # ancho máximo del bloque de texto (84% del cuadro)
# Cuerpo del titular como fracción del alto. Queda algo por debajo del 13-16% que el
# brief le pide al modelo porque acá el texto cae sobre una foto genérica que no
# reservó aire para él: pasarse de cuerpo lo empuja contra el sujeto de la plantilla.
_CUERPO = {"portada": 0.135, "contenido": 0.105}
_CUERPO_MIN = 0.050     # por debajo de esto ya no es un póster, es un pie de foto
_CUERPO_KICKER = 0.45   # la segunda línea, ~la mitad del titular (igual que en el prompt)
_MAX_LINEAS = 3
_INTERLINEA = 0.92      # "set solid": el display se compone con la interlínea cerrada
# Hasta dónde puede bajar el titular: la banda alta del lockup, por rol.
_BANDA_ALTA = {"portada": 0.42, "contenido": 0.38}

_COLOR_TEXTO = (237, 234, 224)   # respaldo si la marca no trae un hex legible
_HEX_RE = re.compile(r"#([0-9a-fA-F]{6})")

# Fuente: la embebida en assets/fonts (la misma que quema los subtítulos del reel).
# La marca pide una grotesca condensada pesada; Montserrat no es condensada, así que
# se pesa al máximo (900) y se compone en caja alta. `OVERLAY_FONT_PATH` permite
# apuntar a la fuente real de la marca sin tocar código.
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FUENTE_EMBEBIDA = _FONTS_DIR / "Montserrat-Variable.ttf"
_PESO_TITULAR = 900
_PESO_KICKER = 700
_FONT_CACHE: dict[tuple[int, int], "ImageFont.ImageFont"] = {}
_AVISO_FUENTE = False


def _paths_fuente() -> list[Path]:
    """Candidatas en orden de prioridad: override del usuario, embebida, sistema."""
    paths: list[Path] = []
    override = os.environ.get("OVERLAY_FONT_PATH", "").strip()
    if override:
        paths.append(Path(override))
    paths.append(_FUENTE_EMBEBIDA)
    if sys.platform.startswith("win"):
        winfonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        paths += [winfonts / "arialbd.ttf", winfonts / "segoeuib.ttf"]
    elif sys.platform == "darwin":
        paths.append(Path("/System/Library/Fonts/Helvetica.ttc"))
    else:
        paths += [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                  Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")]
    return paths


def _fuente(size: int, *, peso: int):
    """Fuente de `size` px con el peso pedido (si el archivo es variable).

    Sin ninguna fuente utilizable cae a la bitmap de Pillow: fea, pero el texto sale.
    Una plantilla muda es peor que una plantilla con el titular en Arial.
    """
    global _AVISO_FUENTE
    key = (size, peso)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for path in _paths_fuente():
        if not path.exists():
            continue
        try:
            font = ImageFont.truetype(str(path), size=size)
        except (OSError, ValueError):
            continue
        try:
            font.set_variation_by_axes([peso])
        except (OSError, ValueError, AttributeError):
            pass   # no es variable (o no tiene eje de peso): se usa tal cual
        _FONT_CACHE[key] = font
        return font
    if not _AVISO_FUENTE:
        print("[aviso] Sin fuentes utilizables — el texto de las plantillas sale con la "
              "fuente bitmap por defecto (calidad reducida).")
        _AVISO_FUENTE = True
    return ImageFont.load_default()


def _color(valor, defecto: tuple[int, int, int]) -> tuple[int, int, int]:
    """`"#C9F227"` o `"acid lime (#C9F227)"` → (201, 242, 39). Sin hex: el defecto."""
    m = _HEX_RE.search(str(valor or ""))
    if not m:
        return defecto
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _wrap(palabras: list[str], font, ancho: int, draw) -> list[list[str]]:
    """Reparte `palabras` en líneas que quepan en `ancho` px (devuelve listas de palabras).

    Se devuelven las palabras y no la línea armada para poder colorear el acento
    palabra por palabra sin volver a partir el texto.
    """
    if not palabras:
        return []
    lineas: list[list[str]] = [[palabras[0]]]
    for palabra in palabras[1:]:
        prueba = " ".join(lineas[-1] + [palabra])
        if draw.textlength(prueba, font=font) <= ancho:
            lineas[-1].append(palabra)
        else:
            lineas.append([palabra])
    return lineas


def _indices_acento(palabras: list[str], acento: str) -> set[int]:
    """Índices de las palabras que van en color de acento (la primera coincidencia).

    Se compara sin acentos gráficos ni puntuación para que el fragmento marcado por
    el usuario (`**así**`) coincida aunque el titular haya cambiado de caja.
    """
    objetivo = [_clave(p) for p in (acento or "").split() if _clave(p)]
    if not objetivo:
        return set()
    claves = [_clave(p) for p in palabras]
    for i in range(len(claves) - len(objetivo) + 1):
        if claves[i:i + len(objetivo)] == objetivo:
            return set(range(i, i + len(objetivo)))
    return set()


def _clave(palabra: str) -> str:
    return re.sub(r"[^0-9a-zà-öø-ÿñ]+", "", (palabra or "").casefold())


def _cuerpo_de(font, defecto: int) -> int:
    """Cuerpo en px de una fuente (la bitmap de respaldo no expone `size`)."""
    return int(getattr(font, "size", 0) or defecto)


def _alto_linea(font) -> int:
    try:
        ascent, descent = font.getmetrics()
    except AttributeError:      # fuente bitmap
        cuerpo = _cuerpo_de(font, 16)
        ascent, descent = cuerpo, max(2, cuerpo // 4)
    return ascent + descent


def _bloque(texto: str, *, font_maker, cuerpo: int, cuerpo_min: int, ancho: int,
            alto_max: int, max_lineas: int, draw) -> tuple[list[list[str]], object, int]:
    """Ajusta el cuerpo hasta que el texto entre en `max_lineas` y en `alto_max`.

    Devuelve (líneas, fuente, alto del bloque). Si ni con el cuerpo mínimo entra, se
    devuelve el mínimo: un titular apretado sigue siendo mejor que ningún titular.
    """
    palabras = texto.split()
    size = cuerpo
    while True:
        font = font_maker(size)
        lineas = _wrap(palabras, font, ancho, draw)
        alto = int(_alto_linea(font) * _INTERLINEA * len(lineas))
        if (len(lineas) <= max_lineas and alto <= alto_max) or size <= cuerpo_min:
            return lineas, font, alto
        size = max(cuerpo_min, int(size * 0.92))


def _dibujar_bloque(draw, lineas: list[list[str]], font, *, x: int, y: int, avance: int,
                    color, color_acento, indices: set[int]) -> None:
    i = 0
    for linea in lineas:
        dx = x
        for palabra in linea:
            draw.text((dx, y), palabra, font=font,
                      fill=color_acento if i in indices else color)
            dx += draw.textlength(palabra + " ", font=font)
            i += 1
        y += avance


def _scrim(img: Image.Image, bandas: list[tuple[int, int]], *, fuerza: int = 170) -> Image.Image:
    """Oscurece las bandas donde va el texto, con un desvanecido a los lados.

    La plantilla es una foto cualquiera: no reservó aire para el tipo como sí hace
    el modelo cuando el prompt se lo pide, así que sin esto el titular se pierde
    sobre las zonas claras. Se oscurece solo la banda del texto (no el cuadro
    entero) para no aplanar la imagen.
    """
    if not bandas:
        return img
    w, h = img.size
    fade = max(1, int(h * 0.10))
    mascara = Image.new("L", (1, h), 0)
    px = mascara.load()
    for y in range(h):
        alpha = 0
        for y0, y1 in bandas:
            if y0 <= y <= y1:
                alpha = max(alpha, fuerza)
            else:
                d = (y0 - y) if y < y0 else (y - y1)
                if d < fade:
                    alpha = max(alpha, int(fuerza * (1 - d / fade)))
        px[0, y] = alpha
    mascara = mascara.resize((w, h))
    out = img.copy()
    out.paste(Image.new("RGB", (w, h), (0, 0, 0)), (0, 0), mascara)
    return out


def _dibujar_texto(img: Image.Image, texto: dict) -> Image.Image:
    """Compone el lockup de marca sobre la plantilla ya recortada.

    Best-effort de punta a punta: cualquier fallo devuelve la imagen tal cual. Una
    plantilla sin texto es un post pobre; una excepción acá es un post perdido.
    """
    try:
        titular = " ".join((texto.get("titular") or "").split()).upper()
        kicker = " ".join((texto.get("kicker") or "").split()).upper()
        if not titular and not kicker:
            return img
        if not titular:                      # sin titular, el kicker sube a titular
            titular, kicker = kicker, ""

        w, h = img.size
        mx, my = round(w * _MARGEN), round(h * _MARGEN)
        ancho = round(w * _ANCHO)
        rol = "contenido" if (texto.get("rol") or "portada") == "contenido" else "portada"
        color = _color(texto.get("color_texto"), _COLOR_TEXTO)
        # Sin color de acento declarado, el titular va entero en el color del texto.
        acento = _color(texto.get("color_acento"), color)

        draw = ImageDraw.Draw(img)
        cuerpo = int(h * _CUERPO[rol])
        cuerpo_min = int(h * _CUERPO_MIN)
        lineas, font, alto = _bloque(
            titular, font_maker=lambda s: _fuente(s, peso=_PESO_TITULAR), cuerpo=cuerpo,
            cuerpo_min=cuerpo_min, ancho=ancho, alto_max=int(h * _BANDA_ALTA[rol]) - my,
            max_lineas=_MAX_LINEAS, draw=draw,
        )
        avance = int(_alto_linea(font) * _INTERLINEA)

        bandas = [(my, my + alto)]
        lineas_k: list[list[str]] = []
        font_k = None
        alto_k = 0
        if kicker:
            # La segunda línea se ancla al pie, no debajo del titular: eso es lo que
            # separa el póster de una foto con caption (misma regla que el prompt).
            lineas_k, font_k, alto_k = _bloque(
                kicker, font_maker=lambda s: _fuente(s, peso=_PESO_KICKER),
                cuerpo=max(cuerpo_min, int(_cuerpo_de(font, cuerpo) * _CUERPO_KICKER)),
                cuerpo_min=max(12, cuerpo_min // 2), ancho=ancho,
                alto_max=int(h * 0.22), max_lineas=2, draw=draw,
            )
            bandas.append((h - my - alto_k, h - my))

        img = _scrim(img, bandas)
        draw = ImageDraw.Draw(img)
        # El acento se busca en los dos bloques: `dividir_texto` puede haber dejado la
        # palabra marcada en la segunda línea, y ahí también es la que manda.
        marcado = texto.get("acento") or ""
        _dibujar_bloque(draw, lineas, font, x=mx, y=my, avance=avance, color=color,
                        color_acento=acento,
                        indices=_indices_acento([p for l in lineas for p in l], marcado))
        if lineas_k and font_k is not None:
            _dibujar_bloque(draw, lineas_k, font_k, x=mx, y=h - my - alto_k,
                            avance=int(_alto_linea(font_k) * _INTERLINEA), color=color,
                            color_acento=acento,
                            indices=_indices_acento([p for l in lineas_k for p in l], marcado))
        return img
    except Exception as e:
        print(f"   [aviso] No se pudo dibujar el texto sobre la plantilla: {e}")
        return img


# ── Renderers ──────────────────────────────────────────────────────────────────
#
# Dos, no cinco: desde que el texto lo pone el modelo, lo único que distingue a una
# imagen de LinkedIn de una de Instagram o de un slide del carrusel es el aspecto de
# destino, y el del feed es el mismo para las tres redes.


def render_feed(src: str, texto: dict | None = None) -> bytes:
    """Imagen de feed 1080x1350 (4:5): portada, slide del carrusel o imagen única.

    `texto` (opcional) es el lockup a dibujar: lo pasa `job_runner` SOLO cuando la
    fuente es una plantilla de respaldo, que llega muda.
    """
    img = _fetch_base(src, target_size=(_FEED_W, _FEED_H))
    if texto:
        img = _dibujar_texto(img, texto)
    return _to_png_bytes(img)


def render_story(src: str, texto: dict | None = None) -> bytes:
    """Historia vertical 1080x1920 (9:16). `texto`: ver `render_feed`."""
    img = _fetch_base(src, target_size=(_STORY_W, _STORY_H))
    if texto:
        img = _dibujar_texto(img, texto)
    return _to_png_bytes(img)


# ── Grade común del carrusel ───────────────────────────────────────────────────
#
# Red de seguridad estética: aunque los slides se generen con la portada como
# referencia y con la misma dirección de arte, el modelo puede derivar en exposición
# o temperatura. Igualar la media y el contraste por canal a los de la portada une el
# set sin gastar créditos. Los topes son deliberadamente conservadores: esto corrige
# una deriva, no reinterpreta la imagen — un slide legítimamente más oscuro debe
# seguir siéndolo.

_GRADE_MAX_GAIN = 0.18   # ±18% de contraste por canal
_GRADE_MAX_SHIFT = 18.0  # ±18 niveles (de 255) de desplazamiento de la media


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def match_grade(png: bytes, reference: bytes) -> bytes:
    """Acerca el color de `png` al de `reference` (media y contraste por canal).

    Devuelve PNG. Correcciones acotadas por `_GRADE_MAX_GAIN`/`_GRADE_MAX_SHIFT`.
    """
    img = Image.open(io.BytesIO(png)).convert("RGB")
    ref = Image.open(io.BytesIO(reference)).convert("RGB")
    st_img = ImageStat.Stat(img)
    st_ref = ImageStat.Stat(ref)

    lut: list[int] = []
    for c in range(3):
        mean_i, mean_r = st_img.mean[c], st_ref.mean[c]
        std_i, std_r = st_img.stddev[c], st_ref.stddev[c]
        # Un canal plano (std ~0) no tiene contraste que igualar: solo se desplaza.
        gain = _clamp(std_r / std_i, 1 - _GRADE_MAX_GAIN, 1 + _GRADE_MAX_GAIN) if std_i > 1.0 else 1.0
        target = mean_i + _clamp(mean_r - mean_i, -_GRADE_MAX_SHIFT, _GRADE_MAX_SHIFT)
        lut.extend(int(round(_clamp((v - mean_i) * gain + target, 0, 255))) for v in range(256))
    return _to_png_bytes(img.point(lut))
