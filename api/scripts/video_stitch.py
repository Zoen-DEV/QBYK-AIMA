"""
Concatenación de segmentos de video con ffmpeg (para los reels de ≥30s).

Un reel largo se arma generando varios segmentos cortos con Higgsfield (Kling
produce ~5-10s por generación) y uniéndolos en un solo MP4. Este módulo baja cada
segmento, lo normaliza a un formato/resolución uniforme y los concatena.

- Binario de ffmpeg: `imageio_ffmpeg.get_ffmpeg_exe()` (paquete `imageio-ffmpeg`,
  binario estático — no hace falta instalar ffmpeg en el sistema). Si el paquete
  no está, `ffmpeg_available()` devuelve False y el pipeline degrada con un aviso.
- Descarga con User-Agent de navegador (los assets de Higgsfield pueden estar tras
  Cloudflare, que banea el UA por defecto de urllib — mismo patrón que
  image_overlay._fetch_base y higgsfield_client).
- Sin audio: los segmentos generados son mudos; se descarta el stream de audio para
  evitar desajustes al concatenar (v=1:a=0).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DOWNLOAD_TIMEOUT = 120  # s por segmento

# Fuentes OFL embebidas (para los subtítulos quemados con libass — ver burn_subtitles).
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
# Familia del subtítulo: Montserrat (limpia, sans, look profesional/minimalista).
_SUBTITLE_FONT = "Montserrat"

# Resolución de salida por aspect (vertical 9:16 es el caso de los reels).
_ASPECT_SIZE = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "2:3": (1080, 1620),
    "3:2": (1620, 1080),
}
_FPS = 30


def ffmpeg_available() -> bool:
    """True si `imageio-ffmpeg` está instalado (binario de ffmpeg disponible)."""
    try:
        import imageio_ffmpeg  # noqa: F401
        return True
    except Exception:
        return False


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _is_local(src: str) -> bool:
    return not src.lower().startswith(("http://", "https://"))


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as r:
        dest.write_bytes(r.read())


def size_for_aspect(aspect: str) -> tuple[int, int]:
    """(ancho, alto) de salida para un aspect (mismo mapa que usa el concat)."""
    return _ASPECT_SIZE.get(aspect, _ASPECT_SIZE["9:16"])


def fetch_video_bytes(src: str) -> bytes:
    """Baja un video (URL, con el UA anti-Cloudflare) o lo lee de disco (path)."""
    if _is_local(src):
        return Path(src).read_bytes()
    req = urllib.request.Request(src, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as r:
        return r.read()


def concat_videos(sources: list[str], *, aspect: str = "9:16") -> bytes:
    """Descarga, normaliza y concatena `sources` (URLs o paths) en un MP4.

    Cada segmento se escala+padea a la resolución del `aspect` (letterbox negro si
    hace falta), se fija a 30fps y se re-encoda a H.264/yuv420p, luego se concatenan.
    Devuelve los bytes del MP4 final. Lanza RuntimeError si ffmpeg no está disponible
    o si la codificación falla (el llamador decide cómo degradar).
    """
    if not sources:
        raise RuntimeError("concat_videos: no hay segmentos que unir")
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg no disponible (falta el paquete imageio-ffmpeg). "
            "Instala api/requirements.txt para habilitar los reels de varios segmentos."
        )
    w, h = _ASPECT_SIZE.get(aspect, _ASPECT_SIZE["9:16"])

    with tempfile.TemporaryDirectory(prefix="aima-stitch-") as tmp:
        tmpdir = Path(tmp)
        inputs: list[Path] = []
        for i, src in enumerate(sources):
            if _is_local(src):
                inputs.append(Path(src))
            else:
                p = tmpdir / f"seg-{i}.mp4"
                _download(src, p)
                inputs.append(p)

        out_path = tmpdir / "out.mp4"
        cmd: list[str] = [_ffmpeg_exe(), "-y"]
        for p in inputs:
            cmd += ["-i", str(p)]

        # Normalizar cada input (escala con relación de aspecto preservada + pad a
        # WxH + sar 1:1 + fps fijo) y concatenar los streams de video (sin audio).
        n = len(inputs)
        parts = []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS}[v{i}]"
            )
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filter_complex = ";".join(parts) + f";{concat_inputs}concat=n={n}:v=1:a=0[outv]"

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out_path),
        ]

        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not out_path.exists():
            detail = proc.stderr.decode("utf-8", errors="replace")[-800:]
            raise RuntimeError(f"ffmpeg falló al concatenar los segmentos: {detail}")
        return out_path.read_bytes()


# ── Subtítulos quemados (estilo minimalista propio con libass) ────────────────────
#
# Reemplazan los subtítulos "etiqueta de papel" del server de Higgsfield por unos
# limpios y minimalistas: sans (Montserrat), blanco, contorno sutil, SIN caja,
# abajo-centro. El ensamblado con voz se pide SIN los subtítulos del server y luego
# se queman estos sobre el MP4. Best-effort en el llamador: si falla, el reel queda
# con voz pero sin subtítulos.

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _probe_duration(path: Path) -> float:
    """Duración del video en segundos (parseada del stderr de ffmpeg). 0.0 si falla."""
    try:
        proc = subprocess.run([_ffmpeg_exe(), "-i", str(path)], capture_output=True)
        m = _DUR_RE.search(proc.stderr.decode("utf-8", errors="replace"))
        if not m:
            return 0.0
        hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return hh * 3600 + mm * 60 + ss
    except Exception:
        return 0.0


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    hh = int(sec // 3600)
    mm = int((sec % 3600) // 60)
    ss = sec % 60
    return f"{hh}:{mm:02d}:{ss:05.2f}"


def _clean_ass_text(text: str) -> str:
    """Texto seguro para un Dialogue de ASS: sin llaves (tags), saltos → \\N."""
    t = (text or "").strip().replace("{", "(").replace("}", ")")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")
    return t


def _build_ass(cues: list[tuple[float, float, str]], *, w: int, h: int) -> str:
    """Arma un archivo .ass minimalista (estilo blanco, contorno sutil, abajo-centro).

    Tamaños proporcionales a la altura para que se vea igual en cualquier resolución.
    """
    fontsize = max(28, round(h * 0.040))
    outline = max(2, round(h * 0.0024))
    margin_v = round(h * 0.12)
    margin_lr = round(w * 0.08)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        # Blanco puro, contorno casi-negro fino, sin sombra ni caja (BorderStyle=1),
        # alineación 2 (abajo-centro). Look profesional y minimalista.
        f"Style: Sub,{_SUBTITLE_FONT},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00101010,"
        f"&H00000000,0,0,0,0,100,100,0,0,1,{outline},0,2,{margin_lr},{margin_lr},"
        f"{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text\n"
    )
    rows = []
    for start, end, text in cues:
        t = _clean_ass_text(text)
        if t:
            rows.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Sub,,0,0,,{t}")
    return header + "\n".join(rows) + "\n"


def burn_subtitles(video_bytes: bytes, lines: list[str], *, aspect: str = "9:16",
                   block_seconds: int = 10) -> bytes:
    """Quema subtítulos minimalistas sobre un MP4 (una línea por bloque de voz).

    `lines` es el guion hablado (una línea por segmento, en orden). Cada línea se
    muestra durante su ventana: se reparten uniformemente en la duración REAL del
    video (medida con ffmpeg) para no desfasar; si la medición falla, se usa
    `block_seconds` por línea. El audio se copia sin re-encodear. Lanza si ffmpeg
    no está o si la codificación falla (el llamador degrada a "sin subtítulos").
    """
    lines = [l for l in (lines or []) if l and l.strip()]
    if not video_bytes or not lines:
        return video_bytes
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg no disponible para quemar subtítulos")
    w, h = size_for_aspect(aspect)

    with tempfile.TemporaryDirectory(prefix="aima-subs-") as tmp:
        tmpdir = Path(tmp)
        in_path = tmpdir / "in.mp4"
        in_path.write_bytes(video_bytes)

        total = _probe_duration(in_path) or (len(lines) * block_seconds)
        per = total / len(lines)
        cues = [(i * per, (i + 1) * per, lines[i]) for i in range(len(lines))]

        (tmpdir / "subs.ass").write_text(_build_ass(cues, w=w, h=h), encoding="utf-8")

        # El parser del filtergraph de ffmpeg trata ':' como separador de opciones y en
        # Windows choca con el ':' de la unidad de una ruta absoluta. Se evita corriendo
        # ffmpeg con cwd = tmp y usando rutas RELATIVAS (sin ':') en el -vf: subs.ass y
        # una carpeta "fonts/" local (copiamos ahí los .ttf embebidos) para fontsdir.
        fonts_dst = tmpdir / "fonts"
        fonts_dst.mkdir(exist_ok=True)
        for ttf in _FONTS_DIR.glob("*.ttf"):
            (fonts_dst / ttf.name).write_bytes(ttf.read_bytes())

        cmd = [
            _ffmpeg_exe(), "-y", "-i", "in.mp4",
            "-vf", "ass=subs.ass:fontsdir=fonts",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", "out.mp4",
        ]
        proc = subprocess.run(cmd, capture_output=True, cwd=str(tmpdir))
        out_path = tmpdir / "out.mp4"
        if proc.returncode != 0 or not out_path.exists():
            detail = proc.stderr.decode("utf-8", errors="replace")[-800:]
            raise RuntimeError(f"ffmpeg falló al quemar subtítulos: {detail}")
        return out_path.read_bytes()
