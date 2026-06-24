"""Plantilla .xlsx y parseo del sheet para la creación de posts en lote.

Una fila del sheet = un post. Las columnas mapean al dict `params` que consume
`run_pipeline` (ver `create_job` en app.py). Las cuentas de Blotato y el dry-run
NO van en el sheet: se eligen una sola vez en la UI de lote y el endpoint las
inyecta por fila. El máximo es MAX_ROWS filas (las demás se ignoran con aviso).
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

import networks

# Orden de columnas = orden en la plantilla. Los encabezados deben coincidir con
# lo que lee parse_sheet (la lectura es case-insensitive).
COLUMNS = [
    "youtube_url",
    "texto",
    "tono",
    "objetivo",
    "tipo_medio",
    "fuente_imagen",
    "formato_instagram",
    "carrusel_slides",
    "idioma",
    "linkedin",
    "instagram",
    "facebook",
    "fecha_hora",
]

MAX_ROWS = 12

# Valores permitidos por columna enum (el vacío siempre vale = default/auto).
ALLOWED = {
    "tono": {"", "educativo", "inspiracional", "personal"},
    "objetivo": {"", "engagement", "awareness", "trafico"},
    "tipo_medio": {"imagen", "video"},
    "fuente_imagen": {"higgsfield", "template"},
    "formato_instagram": {"imagen-unica", "carrusel"},
    "idioma": {"auto", "es", "en"},
    # linkedin/instagram/facebook son sí/no por red → se parsean aparte (_net_yes).
}

# Valores que se interpretan como "sí" / "no" en las columnas de red (case-insensitive).
NET_YES = {"sí", "si", "s", "yes", "y", "true", "1", "x", "✓"}
NET_NO = {"no", "n", "false", "0"}

# Opciones (ordenadas) que se muestran como lista desplegable en la plantilla.
# Deben mantenerse en sincronía con ALLOWED (sin el vacío: ese se deja en blanco).
# carrusel_slides también es una lista para que el usuario elija un número válido.
DROPDOWN_OPTIONS = {
    "tono": ["educativo", "inspiracional", "personal"],
    "objetivo": ["engagement", "awareness", "trafico"],
    "tipo_medio": ["imagen", "video"],
    "fuente_imagen": ["higgsfield", "template"],
    "formato_instagram": ["imagen-unica", "carrusel"],
    "idioma": ["auto", "es", "en"],
    "carrusel_slides": ["3", "4", "5", "6"],
    "linkedin": ["sí", "no"],
    "instagram": ["sí", "no"],
    "facebook": ["sí", "no"],
}

DEFAULTS = {
    "tono": "",
    "objetivo": "",
    "tipo_medio": "imagen",
    "fuente_imagen": "higgsfield",
    "formato_instagram": "imagen-unica",
    "idioma": "auto",
    "carrusel_slides": 3,
    "linkedin": "sí",
    "instagram": "sí",
    "facebook": "sí",
}

# Descripción legible de valores válidos: va en los comentarios de celda y en la
# hoja "Instrucciones" de la plantilla.
COLUMN_HELP = {
    "youtube_url": "URL de YouTube. Llena ESTA o 'texto' (una sola fuente por fila).",
    "texto": "Texto libre (guion, notas). Llena ESTA o 'youtube_url' (una sola por fila).",
    "tono": "Vacío (auto) | educativo | inspiracional | personal",
    "objetivo": "Vacío (auto) | engagement | awareness | trafico",
    "tipo_medio": "imagen | video",
    "fuente_imagen": "higgsfield (IA, con respaldo en plantillas) | template (solo plantillas). Solo aplica si tipo_medio = imagen.",
    "formato_instagram": "imagen-unica | carrusel",
    "carrusel_slides": "Número de 3 a 6 (solo aplica si formato_instagram = carrusel)",
    "idioma": "auto | es | en",
    "linkedin": "¿Publicar en LinkedIn? sí | no (vacío = sí)",
    "instagram": "¿Publicar en Instagram? sí | no (vacío = sí)",
    "facebook": "¿Publicar en Facebook? sí | no (vacío = sí)",
    "fecha_hora": "Programar el post, formato AAAA-MM-DD HH:MM. Vacío = publicar ahora.",
}

# Ancho de columna (en caracteres) para que la plantilla respire y se lea mejor.
COLUMN_WIDTHS = {
    "youtube_url": 44,
    "texto": 52,
    "tono": 18,
    "objetivo": 18,
    "tipo_medio": 16,
    "fuente_imagen": 18,
    "formato_instagram": 22,
    "carrusel_slides": 18,
    "idioma": 14,
    "linkedin": 12,
    "instagram": 12,
    "facebook": 12,
    "fecha_hora": 22,
}

EXAMPLE_ROWS = [
    {
        "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "texto": "",
        "tono": "educativo",
        "objetivo": "engagement",
        "tipo_medio": "imagen",
        "fuente_imagen": "higgsfield",
        "formato_instagram": "carrusel",
        "carrusel_slides": 4,
        "idioma": "auto",
        "linkedin": "sí",
        "instagram": "sí",
        "facebook": "sí",
        "fecha_hora": "2026-06-20 09:00",
    },
    {
        "youtube_url": "",
        "texto": "Hoy quiero compartir 3 aprendizajes sobre productividad que cambiaron mi forma de trabajar...",
        "tono": "personal",
        "objetivo": "awareness",
        "tipo_medio": "imagen",
        "fuente_imagen": "template",
        "formato_instagram": "imagen-unica",
        "carrusel_slides": 3,
        "idioma": "es",
        "linkedin": "sí",
        "instagram": "no",
        "facebook": "sí",
        "fecha_hora": "",
    },
]

# Formatos de fecha aceptados cuando la celda viene como texto (no como fecha de Excel).
_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y",
]


# ── Plantilla ────────────────────────────────────────────────────────────────────

def build_template_xlsx() -> bytes:
    """Genera la plantilla .xlsx descargable (encabezados + ejemplos + instrucciones)."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Posts"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="7C3AED")  # violeta marca
    example_font = Font(italic=True, color="6B7280")       # gris: filas de ejemplo
    example_fill = PatternFill("solid", fgColor="F3EFFF")  # violeta muy claro
    thin = Side(style="thin", color="D1C7EC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Las columnas de texto libre se alinean a la izquierda; el resto al centro.
    left_cols = {"youtube_url", "texto"}
    # La lista desplegable cubre exactamente las filas que el lote procesa.
    last_row = 1 + MAX_ROWS

    # Encabezados.
    for ci, col in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=ci, value=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        cell.comment = Comment(COLUMN_HELP[col], "AIMA")
        ws.column_dimensions[get_column_letter(ci)].width = COLUMN_WIDTHS.get(col, 18)
    ws.row_dimensions[1].height = 30

    # Filas de ejemplo (estilo tenue para que se note que se pueden borrar).
    for ri, ex in enumerate(EXAMPLE_ROWS, start=2):
        for ci, col in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=ri, column=ci, value=ex.get(col, ""))
            cell.font = example_font
            cell.fill = example_fill
            cell.border = border
            align = "left" if col in left_cols else "center"
            cell.alignment = Alignment(horizontal=align, vertical="center")
        ws.row_dimensions[ri].height = 22

    # Filas vacías restantes: solo borde y alineación, listas para escribir.
    for ri in range(2 + len(EXAMPLE_ROWS), last_row + 1):
        for ci, col in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=ri, column=ci)
            cell.border = border
            align = "left" if col in left_cols else "center"
            cell.alignment = Alignment(horizontal=align, vertical="center")
        ws.row_dimensions[ri].height = 22

    # Listas desplegables (selects) en las columnas de opciones fijas: evitan typos.
    for col, options in DROPDOWN_OPTIONS.items():
        letter = get_column_letter(COLUMNS.index(col) + 1)
        dv = DataValidation(
            type="list",
            formula1='"' + ",".join(options) + '"',
            allow_blank=True,          # el vacío = default/auto sigue siendo válido
            showErrorMessage=True,
        )
        dv.errorTitle = "Valor no válido"
        dv.error = f"Elige una opción de la lista: {', '.join(options)}."
        dv.add(f"{letter}2:{letter}{last_row}")
        ws.add_data_validation(dv)

    ws.freeze_panes = "A2"

    # Hoja de instrucciones
    ws2 = wb.create_sheet("Instrucciones")
    intro = [
        ("Cómo usar esta plantilla", True),
        ("", False),
        (f"• Cada fila = un post. Máximo {MAX_ROWS} filas (las demás se ignoran).", False),
        ("• Por fila llena 'youtube_url' O 'texto' (una sola fuente).", False),
        ("• 'fecha_hora' programa el post (AAAA-MM-DD HH:MM). Vacío = publicar ahora.", False),
        ("• Redes: pon sí/no en las columnas linkedin, instagram y facebook. Vacío = sí (se publica en las tres por defecto).", False),
        ("• Las columnas con lista desplegable solo aceptan los valores de la lista.", False),
        ("• No borres ni renombres la fila de encabezados (fila 1).", False),
        ("• Las cuentas de LinkedIn/Instagram/Facebook se eligen en la app, no en el sheet.", False),
        ("• Las filas de ejemplo (en gris) son de muestra: puedes borrarlas antes de cargar.", False),
        ("", False),
        ("Valores válidos por columna", True),
        ("", False),
    ]
    for ri, (text, bold) in enumerate(intro, start=1):
        c = ws2.cell(row=ri, column=1, value=text)
        if bold:
            c.font = Font(bold=True, size=13)
    base = len(intro) + 1
    for i, col in enumerate(COLUMNS):
        ws2.cell(row=base + i, column=1, value=f"{col}: {COLUMN_HELP[col]}")
    ws2.column_dimensions["A"].width = 95

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Parseo ───────────────────────────────────────────────────────────────────────

def parse_sheet(data: bytes, filename: str = "") -> tuple[list[dict], list[str]]:
    """Lee un .xlsx/.csv y devuelve (specs, warnings).

    Cada spec es un dict con: params (normalizado, sin cuentas/dry-run), source,
    label, schedule_dt (naive o None), upload_bytes, upload_filename.
    Lanza ValueError si el archivo no se puede leer.
    """
    warnings: list[str] = []
    fmt = _detect_format(data, filename)
    try:
        raw_rows = _rows_from_csv(data) if fmt == "csv" else _rows_from_xlsx(data)
    except Exception as e:  # noqa: BLE001 - se reporta al usuario
        raise ValueError(f"No se pudo leer el archivo ({fmt}): {e}")

    # Descarta filas totalmente vacías.
    raw_rows = [r for r in raw_rows if any(_clean(v) for v in r.values())]

    if len(raw_rows) > MAX_ROWS:
        warnings.append(
            f"El archivo tiene {len(raw_rows)} filas; se procesan solo las primeras {MAX_ROWS}."
        )
        raw_rows = raw_rows[:MAX_ROWS]

    specs: list[dict] = []
    for idx, r in enumerate(raw_rows, start=1):
        spec, row_warnings = _row_to_spec(r, idx)
        warnings.extend(row_warnings)
        if spec is not None:
            specs.append(spec)

    return specs, warnings


def _detect_format(data: bytes, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv":
        return "csv"
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    # Sin extensión confiable: los .xlsx son contenedores ZIP (PK\x03\x04).
    return "xlsx" if data[:4] == b"PK\x03\x04" else "csv"


def _rows_from_csv(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for row in reader:
        rows.append({(k or "").strip(): v for k, v in row.items()})
    return rows


def _rows_from_xlsx(data: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []
    headers = [str(h).strip() if h is not None else "" for h in header]
    out: list[dict] = []
    for row in rows_iter:
        d: dict = {}
        for i, h in enumerate(headers):
            if h:
                d[h] = row[i] if i < len(row) else None
        out.append(d)
    return out


def _row_to_spec(r: dict, idx: int) -> tuple[dict | None, list[str]]:
    w: list[str] = []
    g = {(k or "").strip().lower(): v for k, v in r.items()}

    youtube_url = _clean(g.get("youtube_url"))
    texto = _clean(g.get("texto"))

    if youtube_url and texto:
        w.append(f"Fila {idx}: tiene 'youtube_url' y 'texto'; se usa 'youtube_url'.")
        texto = ""
    if not youtube_url and not texto:
        w.append(f"Fila {idx}: sin 'youtube_url' ni 'texto'; se omite.")
        return None, w

    source = "youtube" if youtube_url else "texto"
    upload_bytes = b""
    upload_filename = ""
    if source == "texto":
        upload_bytes = texto.encode("utf-8")
        upload_filename = "texto.txt"

    params = {
        "source_type": source,
        "youtube_url": youtube_url,
        "upload_filename": upload_filename,
        "tono": _enum(g.get("tono"), "tono", w, idx),
        "tono_linkedin": "",
        "tono_instagram": "",
        "tono_facebook": "",
        "objetivo": _enum(g.get("objetivo"), "objetivo", w, idx),
        "objetivo_linkedin": "",
        "objetivo_instagram": "",
        "objetivo_facebook": "",
        "formato_instagram": _enum(g.get("formato_instagram"), "formato_instagram", w, idx),
        "carrusel_slides": _parse_slides(g.get("carrusel_slides"), w, idx),
        "tipo_medio": _enum(g.get("tipo_medio"), "tipo_medio", w, idx),
        "fuente_imagen": _enum(g.get("fuente_imagen"), "fuente_imagen", w, idx),
        "idioma": _enum(g.get("idioma"), "idioma", w, idx),
        "modelo_perplexity": "sonar-pro",
        "redes": _parse_net_flags(g, w, idx),
        "publicar": "",
    }

    schedule_dt = _parse_datetime(g.get("fecha_hora"), w, idx)
    label = youtube_url if source == "youtube" else (texto[:80] + ("…" if len(texto) > 80 else ""))

    spec = {
        "params": params,
        "source": source,
        "label": label,
        "schedule_dt": schedule_dt,
        "upload_bytes": upload_bytes,
        "upload_filename": upload_filename,
    }
    return spec, w


# ── Helpers de normalización ──────────────────────────────────────────────────────

def _clean(v) -> str:
    """Valor de celda → string limpio (sin convertir fechas, que se manejan aparte)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _enum(value, col: str, w: list[str], idx: int) -> str:
    v = _clean(value).lower()
    if v in ALLOWED[col]:
        return v
    # Celda vacía = usar el default en silencio (no es un error del usuario).
    if v == "":
        return DEFAULTS[col]
    w.append(f"Fila {idx}: '{col}' inválido ('{_clean(value)}'); se usa el valor por defecto.")
    return DEFAULTS[col]


def _net_yes(value, net: str, w: list[str], idx: int) -> bool:
    """Celda sí/no de una columna de red → bool. Vacío = sí (publicar por defecto)."""
    v = _clean(value).lower()
    if v == "" or v in NET_YES:
        return True
    if v in NET_NO:
        return False
    w.append(f"Fila {idx}: valor de '{net}' no reconocido ('{_clean(value)}'); se incluye la red.")
    return True


def _parse_net_flags(g: dict, w: list[str], idx: int) -> list[str]:
    """Columnas linkedin/instagram/facebook (sí/no) → lista canónica de redes.

    Por defecto (todas vacías) se publican las tres. Si el usuario pone 'no' en las
    tres, se avisa y se vuelve al default para no dejar el post sin destino.
    """
    chosen = [net for net in networks.NETWORKS if _net_yes(g.get(net), net, w, idx)]
    if not chosen:
        w.append(f"Fila {idx}: no se eligió ninguna red (todas en 'no'); se publican las tres.")
        return list(networks.NETWORKS)
    return chosen


def _parse_slides(value, w: list[str], idx: int) -> int:
    s = _clean(value)
    if not s:
        return DEFAULTS["carrusel_slides"]
    try:
        n = int(float(s))
    except (ValueError, TypeError):
        w.append(f"Fila {idx}: 'carrusel_slides' inválido ('{s}'); se usa 3.")
        return DEFAULTS["carrusel_slides"]
    clamped = max(3, min(6, n))
    if clamped != n:
        w.append(f"Fila {idx}: 'carrusel_slides' {n} fuera de rango; se ajusta a {clamped}.")
    return clamped


def _parse_datetime(value, w: list[str], idx: int) -> datetime | None:
    """Celda fecha/hora → datetime naive (hora local del usuario) o None si vacía."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    w.append(f"Fila {idx}: 'fecha_hora' no reconocida ('{s}'); ese post se publicará de inmediato.")
    return None
