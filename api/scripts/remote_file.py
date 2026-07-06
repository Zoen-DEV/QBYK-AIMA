"""Descarga de archivos remotos para el trigger `archivo_url` del bulk.

El sheet no puede contener archivos (solo texto), así que los triggers de audio y
documento del flujo individual se expresan en el bulk como una URL pública por
fila. Aquí se descarga el archivo (urllib puro, sin SDKs) y se clasifica en
"audio" o "texto" (documento) para que `run_pipeline` lo enrute por el mismo
camino que un archivo subido en el form.

Soporta URLs directas y los links de compartir más comunes (Google Drive,
Dropbox), que se transforman a su variante de descarga directa.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from pathlib import Path

# Límite de descarga: las notas de voz y documentos reales pesan mucho menos;
# esto solo evita que una URL equivocada (p. ej. un video) agote la memoria.
MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# Cloudflare y varios CDNs bloquean el User-Agent por defecto de urllib.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

AUDIO_EXTS = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".aac", ".flac"}
DOC_EXTS = {".pdf", ".docx", ".txt", ".md"}


def _direct_url(url: str) -> str:
    """Transforma links de compartir conocidos a su URL de descarga directa."""
    u = url.strip()
    # Google Drive: https://drive.google.com/file/d/<ID>/view?... → uc?export=download
    m = re.search(r"drive\.google\.com/file/d/([^/?#]+)", u)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"drive\.google\.com/open\?id=([^&#]+)", u)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    # Dropbox: ?dl=0 (página de preview) → dl=1 (descarga directa)
    if "dropbox.com" in u:
        u = re.sub(r"[?&]dl=0", "", u)
        u += ("&" if "?" in u else "?") + "dl=1"
    return u


def _filename_from_response(resp, url: str) -> str:
    """Nombre del archivo: Content-Disposition si viene; si no, el path de la URL."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;]+)", cd) or re.search(
        r'filename="?([^";]+)"?', cd
    )
    if m:
        name = urllib.parse.unquote(m.group(1).strip())
        if name:
            return Path(name).name
    path_name = Path(urllib.parse.urlparse(url).path).name
    return urllib.parse.unquote(path_name) if path_name else ""


def fetch_remote_file(url: str, *, max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """Descarga `url` y devuelve `(bytes, filename)`.

    Lanza RuntimeError con mensaje claro (de cara al usuario del sheet) si la URL
    no responde, exige login (HTML en vez del archivo) o excede el límite.
    """
    target = _direct_url(url)
    req = urllib.request.Request(target, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(max_bytes + 1)
            filename = _filename_from_response(resp, target)
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except Exception as e:
        raise RuntimeError(f"No se pudo descargar el archivo de la URL ({e}).")
    if len(data) > max_bytes:
        raise RuntimeError(
            f"El archivo supera el límite de {max_bytes // (1024 * 1024)} MB."
        )
    if not data:
        raise RuntimeError("La URL devolvió un archivo vacío.")
    # Un HTML suele significar página de login/preview (link no público).
    if "text/html" in ctype and not data[:5].lstrip().startswith(b"%PDF"):
        head = data[:256].lstrip().lower()
        if head.startswith((b"<!doctype", b"<html")):
            raise RuntimeError(
                "La URL devolvió una página web, no un archivo. Verifica que el "
                "link sea público y de descarga directa (en Google Drive: "
                "'Cualquier persona con el enlace')."
            )
    return data, filename


def classify_source(filename: str, data: bytes) -> str:
    """Clasifica el archivo descargado como fuente "audio" o "texto" (documento).

    Primero por extensión; si no es concluyente, por magic bytes. Lanza
    RuntimeError si no se reconoce (mensaje pensado para el warning de la fila).
    """
    ext = Path(filename or "").suffix.lower()
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in DOC_EXTS:
        return "texto"

    head = data[:12]
    if head.startswith(b"%PDF"):
        return "texto"
    if head.startswith(b"PK\x03\x04"):  # .docx (zip) — un .xlsx aquí sería un error del usuario
        return "texto"
    if head.startswith(b"OggS"):  # .ogg / .opus
        return "audio"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):  # .mp3
        return "audio"
    if head.startswith(b"RIFF"):  # .wav
        return "audio"
    if head[4:8] == b"ftyp":  # .m4a / contenedores MP4
        return "audio"
    if head.startswith(b"fLaC"):
        return "audio"
    # Texto plano legible → documento (decodificable Y mayormente imprimible;
    # los bytes de control también son UTF-8 válido, no basta con decodificar).
    try:
        sample = data[:2048].decode("utf-8")
    except UnicodeDecodeError:
        sample = ""
    if sample and "\x00" not in sample:
        printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
        if printable / len(sample) > 0.9:
            return "texto"
    raise RuntimeError(
        "No se reconoce el tipo del archivo descargado. Usa un audio "
        "(.ogg/.opus/.m4a/.mp3/.wav) o un documento (.pdf/.docx/.txt/.md)."
    )
