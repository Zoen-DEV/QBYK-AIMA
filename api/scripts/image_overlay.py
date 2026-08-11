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
   "caja_alta": bool,                          # opcional, default True (la caja es de la identidad)
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
# Cuerpo del titular como fracción del alto. Queda algo por debajo de lo que el brief
# le pide al modelo (13-16% en portada, 15-20% en slide) porque acá el texto cae sobre
# una foto genérica que no reservó aire para él: pasarse de cuerpo lo empuja contra el
# sujeto de la plantilla.
# El slide va POR ENCIMA de la portada, igual que en el brief: en un slide de contenido
# el tipo es el elemento principal y la imagen lo soporta. Si acá se quedara la
# jerarquía vieja (portada 0.135 / contenido 0.105), la pieza diría una cosa cuando la
# genera el modelo y la contraria cuando cae a plantilla.
_CUERPO = {"portada": 0.135, "contenido": 0.155}
_CUERPO_MIN = 0.050     # por debajo de esto ya no es un póster, es un pie de foto
_CUERPO_KICKER = 0.45   # la segunda línea, ~la mitad del titular (igual que en el prompt)
_MAX_LINEAS = 3
# Aire entre dos bloques apilados (etiqueta→titular, titular→cuerpo), en fracción del
# alto. Los bloques que van al pie se anclan al margen inferior y no lo usan.
_AIRE = 0.025
_PESO_CUERPO = 400      # el cuerpo va en peso de lectura, no de display
_INTERLINEA = 0.92      # "set solid": el display se compone con la interlínea cerrada
# Hasta dónde puede bajar el titular: la banda alta del lockup, por rol. Los dos valores
# son los mismos que `zonas_texto` declara en el brief (`prompts/architect.json`), y el
# del slide es el mayor porque ahí el titular manda: 3 líneas al 0.155 del alto no caben
# en el 38% viejo, así que `_encajar` lo habría bajado de cuerpo hasta deshacer el
# cambio en silencio.
_BANDA_ALTA = {"portada": 0.42, "contenido": 0.68}

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


def _bloques_de(texto: dict) -> list[dict]:
    """Los bloques a dibujar. Acepta el contrato nuevo y el de siempre.

    El nuevo (`bloques`) llega ya resuelto desde `prompt_architect.lockup_bloques`:
    banda, tamaño relativo y caja. El viejo (`titular` + `kicker`) se sigue aceptando
    porque es lo que produce cualquier llamada que no conozca los sistemas de texto, y
    porque una portada no lleva sistema.
    """
    bloques = texto.get("bloques")
    if isinstance(bloques, list) and bloques:
        return [b for b in bloques if isinstance(b, dict) and str(b.get("texto") or "").strip()]
    caja = (lambda s: s.upper()) if texto.get("caja_alta", True) else (lambda s: s)
    salida = []
    for clave, banda, rel, lineas in (("titular", "alta", 1.0, _MAX_LINEAS),
                                      ("kicker", "pie", _CUERPO_KICKER, 2)):
        crudo = " ".join(str(texto.get(clave) or "").split())
        if crudo:
            salida.append({"clave": clave, "texto": caja(crudo), "banda": banda,
                           "escala_rel": rel, "max_lineas": lineas})
    # Sin titular, el que haya sube a la banda alta: un bloque suelto al pie se lee
    # como un pie de foto y la pieza se queda sin nada arriba.
    if salida and salida[0]["banda"] != "alta":
        salida[0] = dict(salida[0], banda="alta", escala_rel=1.0, max_lineas=_MAX_LINEAS)
    return salida


def _dibujar_texto(img: Image.Image, texto: dict) -> Image.Image:
    """Compone el lockup de marca sobre la plantilla ya recortada.

    Dibuja los bloques que declare el sistema de texto de la pieza —etiqueta, titular,
    cuerpo, apoyo— en sus bandas: los apilados van desde el margen superior hacia abajo
    y los del pie anclados al margen inferior. Que el cuerpo se ancle al pie o cuelgue
    del titular no lo decide este módulo: viene resuelto en `banda`, del mismo sitio que
    lo declara el prompt.

    Best-effort de punta a punta: cualquier fallo devuelve la imagen tal cual. Una
    plantilla sin texto es un post pobre; una excepción acá es un post perdido.
    """
    try:
        bloques = _bloques_de(texto)
        if not bloques:
            return img

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
        aire = int(h * _AIRE)
        # Todo lo que se apila arriba comparte la banda alta del lockup, así que su alto
        # disponible se va gastando: el titular es el que manda y los demás se ajustan a
        # lo que quede. Sin esto, una etiqueta y un cuerpo empujaban el titular fuera de
        # su banda y `_bloque` lo bajaba de cuerpo hasta deshacer la jerarquía.
        disponible = int(h * _BANDA_ALTA[rol]) - my
        arriba, pie = [], []
        for b in bloques:
            # El tamaño sale de `escala_rel`, que lo declara el SISTEMA: el titular no
            # mide lo mismo solo por ser titular — con un cuerpo debajo baja al 82% para
            # dejarle su banda, igual que en el brief. El peso va por CLAVE y no por
            # tamaño: un titular pequeño sigue siendo display, y un cuerpo grande sigue
            # siendo texto de lectura.
            rel = float(b.get("escala_rel") or 1.0)
            clave = b.get("clave") or "titular"
            es_titular = clave in ("titular", "text")
            peso = (_PESO_TITULAR if es_titular
                    else _PESO_CUERPO if clave == "cuerpo" else _PESO_KICKER)
            al_pie = b.get("banda") == "pie"
            lineas, font, alto = _bloque(
                b["texto"], font_maker=lambda s, _p=peso: _fuente(s, peso=_p),
                cuerpo=max(cuerpo_min if es_titular else 12, int(cuerpo * rel)),
                cuerpo_min=cuerpo_min if es_titular else max(12, cuerpo_min // 2),
                ancho=ancho,
                alto_max=int(h * 0.28) if al_pie else max(aire, disponible),
                max_lineas=int(b.get("max_lineas") or _MAX_LINEAS), draw=draw,
            )
            destino = pie if al_pie else arriba
            destino.append((lineas, font, alto))
            if not al_pie:
                disponible = max(aire, disponible - alto - aire)

        # Posiciones: los apilados bajan desde el margen; los del pie suben desde abajo.
        colocados, bandas = [], []
        y = my
        for lineas, font, alto in arriba:
            colocados.append((lineas, font, y))
            bandas.append((y, y + alto))
            y += alto + aire
        y = h - my
        for lineas, font, alto in reversed(pie):
            y -= alto
            colocados.append((lineas, font, y))
            bandas.append((y, y + alto))
            y -= aire

        img = _scrim(img, bandas)
        draw = ImageDraw.Draw(img)
        # El acento se busca en TODOS los bloques: el reparto puede haber dejado la
        # palabra marcada en cualquiera de ellos, y ahí también es la que manda.
        marcado = texto.get("acento") or ""
        for lineas, font, y0 in colocados:
            _dibujar_bloque(draw, lineas, font, x=mx, y=y0,
                            avance=int(_alto_linea(font) * _INTERLINEA), color=color,
                            color_acento=acento,
                            indices=_indices_acento([p for l in lineas for p in l], marcado))
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


# ── Detector de bandas planas y marcos ─────────────────────────────────────────
#
# El passe-partout y el letterbox se atacan en tres frentes porque el defecto tiene
# tres orígenes y ya volvió dos veces por atacar solo uno. Los otros dos son prompt
# —el sangrado declarado en positivo en las secciones 1 y 3, y el saneo de lo que la
# identidad escribe en la sección 5—; este es el único que convierte la regla en algo
# COMPROBABLE. El prompt ya falló dos veces; una comprobación sobre el píxel, no.
#
# La idea que hace que esto funcione y no genere falsos positivos: **un letterbox no
# es "una zona oscura", es un ESCALÓN**. Una escena nocturna legítima tiene una banda
# alta oscura y de baja varianza —y es correcta, es justo el aire donde se apoya el
# titular—. Lo que delata a la banda pintada es que termina de golpe: hasta la fila k
# no pasa nada y en la k+1 aparece la fotografía entera. Sin escalón no hay banda.

# Varianza por fila/columna por debajo de la cual la línea se considera PLANA. Se
# calibró contra las imágenes reales de `api/outputs/` (carruseles y portadas ya
# generados, con y sin passe-partout): las líneas de una fotografía real —incluso un
# cielo nocturno o un fondo desenfocado— quedan muy por encima, porque siempre traen
# grano y viñeteo.
_VARIANZA_PLANA = 12.0
# Salto de media (de 255) contra la línea anterior que marca el borde de la banda.
_SALTO_MIN = 14.0
# ...o un salto de varianza: una barra negra sobre una escena nocturna puede no mover
# la media y sí disparar la textura. Es el caso que la media sola no ve.
_SALTO_VARIANZA = 8.0
# Hasta dónde se busca la banda: el primer 25% del lado. Más adentro ya no es un
# marco, es composición.
_FRANJA = 0.25
# Una banda tiene que ser un elemento de diseño, no dos píxeles de compresión.
_BANDA_MIN = 0.015
# Diferencia de medias por debajo de la cual los cuatro bordes son "el mismo color",
# que es lo que distingue un marco de cuatro casualidades.
_TOLERANCIA_MARCO = 12.0

_BORDES = ("arriba", "abajo", "izquierda", "derecha")


def _lineas(img: "Image.Image", *, vertical: bool) -> tuple[list[float], list[float]]:
    """Medias y varianzas por fila (`vertical=True`) o por columna.

    Se reduce la imagen a una tira de 1 px de ancho/alto usando el propio remuestreo de
    Pillow para las medias, y se calcula la varianza sobre una miniatura: es O(píxeles)
    una sola vez y evita traer numpy solo para esto.
    """
    gris = img.convert("L")
    if vertical:
        largo = gris.height
        lado = min(gris.width, 128)
        chico = gris.resize((lado, largo), Image.BILINEAR)
    else:
        largo = gris.width
        lado = min(gris.height, 128)
        chico = gris.resize((largo, lado), Image.BILINEAR)
    # `tobytes()` sobre una imagen "L" ya devuelve los píxeles en orden de filas, y a
    # diferencia de `getdata()` no está deprecado.
    px = chico.tobytes()
    medias: list[float] = []
    varianzas: list[float] = []
    for i in range(largo):
        if vertical:
            linea = px[i * lado:(i + 1) * lado]
        else:
            linea = px[i::largo]
        n = len(linea) or 1
        media = sum(linea) / n
        medias.append(media)
        varianzas.append(sum((v - media) ** 2 for v in linea) / n)
    return medias, varianzas


def _banda(medias: list[float], varianzas: list[float], *, desde_el_final: bool) -> float:
    """Media de la banda plana de ese borde, o `-1.0` si no hay banda.

    Devuelve la media (y no un booleano) porque el marco necesita comparar los cuatro
    bordes entre sí: cuatro bandas del mismo color son un passe-partout; cuatro bandas
    de colores distintos son cuatro escenas que casualmente empiezan planas.
    """
    n = len(medias)
    if n < 8:
        return -1.0
    orden = range(n - 1, -1, -1) if desde_el_final else range(n)
    idx = list(orden)
    limite = max(2, int(n * _FRANJA))
    corrido = 0
    while corrido < limite and varianzas[idx[corrido]] < _VARIANZA_PLANA:
        corrido += 1
    if corrido < max(2, int(n * _BANDA_MIN)) or corrido >= limite:
        # Sin banda, o plana hasta tan adentro que ya no es un borde: en los dos casos
        # falta el escalón, que es lo único que distingue una banda pintada de una
        # zona tranquila legítima de la fotografía.
        return -1.0
    dentro, ultima = idx[corrido], idx[corrido - 1]
    salto_media = abs(medias[dentro] - medias[ultima])
    salto_var = varianzas[dentro] > _VARIANZA_PLANA * _SALTO_VARIANZA
    if salto_media < _SALTO_MIN and not salto_var:
        return -1.0
    return sum(medias[i] for i in idx[:corrido]) / corrido


def bytes_crudos(src: str) -> bytes:
    """Los bytes tal como los devolvió el proveedor (o la plantilla local).

    Sin recorte, sin overlay y sin grade: es lo que hace falta para juzgar lo que hizo
    el MODELO. `render_feed` no sirve para esto — recorta al 4:5 y puede dibujar texto.
    """
    if _is_local_path(src):
        return Path(src).read_bytes()
    req = urllib.request.Request(src, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECS) as r:
        return r.read()


def bordes_planos(png: bytes) -> list[str]:
    """Qué bordes de la imagen son una banda de color plano.

    Devuelve `["arriba", "abajo"]` (letterbox), `["marco"]` (passe-partout en los
    cuatro lados) o `[]`. Se mide sobre la imagen **cruda del proveedor**, antes del
    overlay y del grade: así se juzga lo que hizo el modelo, no lo que hizo Pillow.

    Best-effort: cualquier problema devuelve `[]`. Este detector no puede interrumpir
    una generación — como mucho deja de avisar.
    """
    try:
        img = Image.open(io.BytesIO(png))
        medias_v, var_v = _lineas(img, vertical=True)
        medias_h, var_h = _lineas(img, vertical=False)
        bandas = {
            "arriba": _banda(medias_v, var_v, desde_el_final=False),
            "abajo": _banda(medias_v, var_v, desde_el_final=True),
            "izquierda": _banda(medias_h, var_h, desde_el_final=False),
            "derecha": _banda(medias_h, var_h, desde_el_final=True),
        }
    except Exception as e:  # noqa: BLE001
        print(f"   [aviso] No se pudo revisar los bordes de la imagen: {e}")
        return []

    presentes = [b for b in _BORDES if bandas[b] >= 0]
    if len(presentes) == 4:
        valores = [bandas[b] for b in _BORDES]
        if max(valores) - min(valores) <= _TOLERANCIA_MARCO:
            return ["marco"]
    return presentes


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
