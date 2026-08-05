import asyncio
import io
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import blotato_client as bc
import image_provider as improv
import higgsfield_client as hf   # Cloud API (retirado; se conserva por rollback)
import higgsfield_mcp as hfmcp   # MCP oficial (OAuth) — backend activo de video/imagen
import video_stitch as vstitch   # concat de segmentos de video con ffmpeg
import transcribe as tr
import transcribe_local as trl
import document_text as doc
import remote_file as rf

import cost_calc
import lang_detect as ld
import prompt_architect as parch
import prompt_config
import image_text_qa as iqa
import image_set_qa as sqa

try:
    import image_overlay as ov
    _HAS_OVERLAY = True
except ImportError:
    _HAS_OVERLAY = False


def _text_in_prompt(cfg) -> bool:
    """True si la imagen va a llevar texto. En la imagen generada lo renderiza el
    propio modelo desde el prompt (`prompt_architect`); ese es el camino normal.

    La plantilla de respaldo es la excepción y no la contradice: como no pasa por
    ningún modelo, llega muda y el texto se lo dibuja `image_overlay` después
    (`_lockup_plantilla`). El interruptor es el mismo para los dos caminos —apagado,
    la pieza no lleva texto salga por donde salga—, gobierna la composición que se le
    pide al modelo (reservar un área calma vs. llenar el cuadro) y es lo que el
    preview usa para saber si el copy de los visuales se va a ver.
    """
    return bool(getattr(cfg, "image_text_in_prompt", True))

from post_writer import write_posts, rewrite_posts, _segments_needed, _wants_video
from networks import active_networks
import cost_tracker

_loop_executor = None
_OUTPUTS_DIR = Path(__file__).parent / "outputs"


def _event_context(job: dict) -> dict:
    """Contexto común de un usage_event extraído del job (flow, ids, plataformas…)."""
    params = job.get("params", {})
    return {
        "flow": job.get("flow", "individual"),
        "job_id": job.get("id"),
        "batch_id": job.get("batch_id"),
        "source_type": params.get("source_type"),
        "platforms": active_networks(params),
        "dry_run": bool(params.get("dry_run", False)),
    }


async def _track(job: dict, *, service: str, operation: str, units: dict,
                 model: str | None = None, status: str = "success") -> None:
    """Registra un usage_event para este job. Best-effort: nunca rompe el pipeline.

    Punto único de instrumentación (regla de los dos flujos): tanto el post
    individual como cada fila del bulk pasan por aquí y heredan el tracking gratis.
    """
    try:
        await cost_tracker.record_event(
            service=service, operation=operation, units=units, model=model,
            status=status, **_event_context(job),
        )
    except Exception:
        pass  # record_event ya es best-effort; este guard cubre el armado del contexto


def make_job(cfg, params: dict, *, upload_bytes: bytes = b"", upload_filename: str = "",
             final_media_bytes: bytes = b"", final_media_filename: str = "",
             photo_files: list | None = None,
             flow: str = "individual", batch_id: str | None = None) -> dict:
    """Build a fresh in-memory job from an already-normalized `params` dict.

    Shared by the single-post endpoint (`create_job`) and the bulk batch runner so
    both produce the exact same job shape the pipeline expects. `params` must already
    be normalized by the caller (clamped slide count, stripped account ids, etc.).
    For non-YouTube sources, pass the source bytes via `upload_bytes`/`upload_filename`.
    Para reel/historia en modo "subir", el medio final (video/imagen ya hecho) va en
    `final_media_bytes`/`final_media_filename`: se publica tal cual, sin generación.
    `flow`/`batch_id` etiquetan el origen del job para el tracking de costos
    ("individual" por defecto; el bulk pasa "bulk" + el id del batch).

    `params["identidad_visual"]` es la identidad visual **congelada** al crear el job
    (la resuelve quien llama; ver `_identidad`). Ausente o vacía = la de la casa, y el
    job se comporta exactamente igual que antes de que existieran las identidades.
    """
    return {
        "id": str(uuid.uuid4()),
        "status": "running",
        "flow": flow,
        "batch_id": batch_id,
        "params": params,
        "content": {},
        "accounts": {},
        "posts": {},
        # Avisos sobre el ORIGEN del job (sin transcripción, escritura degradada): lo
        # que explica POR QUÉ los prompts salieron como salieron. Viajan junto al lint
        # en `/jobs/{id}` para que las dos compuertas previas los pinten igual.
        "avisos": [],
        # text_overlay: si la imagen lleva texto (lo ponga el modelo desde el prompt o
        # Pillow por encima). Viaja en el job para que el preview no invite a editar un
        # texto que no se va a ver. text_in_prompt distingue quién lo dibuja.
        # prompts/qa: el prompt final y el registro del QA de visión por subkey — es la
        # traza que permite auditar por qué salió la imagen que salió.
        # raw_urls/reference: lo que necesita rehacer UNA imagen desde la revisión —
        # el origen de cada subkey (respaldo de subida) y el job_id de la portada que
        # los slides usan como referencia visual. Sobreviven a la fase de imágenes
        # porque la regeneración crea su propio provider y no hereda sus locales.
        "images": {"bytes": {}, "raw_urls": {}, "reference": "", "blotato_urls": {"linkedin": "", "instagram": [], "facebook": "", "tiktok": ""}, "base_urls": {"linkedin": [], "instagram": [], "facebook": []}, "provider": "", "notice": "", "text_overlay": _text_in_prompt(cfg), "text_in_prompt": _text_in_prompt(cfg), "prompts": {}, "qa": {}, "bandas": {}, "qa_set": []},
        "video": {"url": "", "provider": "", "notice": "", "cost": None},
        "result": {},
        "error_msg": None,
        "_queue": asyncio.Queue(),
        "_cfg": cfg,
        "_li_media_urls": [],
        "_ig_media_urls": [],
        "_fb_media_urls": [],
        "_tk_media_urls": [],
        "_upload_bytes": upload_bytes,
        "_upload_filename": upload_filename,
        # Medio final subido por el usuario (modo "subir" de reel/historia): se publica
        # tal cual, sin generación. Vacío en el modo "generar" y en el flujo normal.
        "_final_media_bytes": final_media_bytes,
        "_final_media_filename": final_media_filename,
        # Fotos subidas para el recorrido image-to-video (modo "fotos", solo-individual):
        # lista de (bytes, filename). Vacío en el resto de los flujos.
        "_photo_files": photo_files or [],
    }


def _avisar(job: dict, campo: str, nivel: str, mensaje: str) -> None:
    """Anota un aviso de origen en el job (mismo shape que los de `prompt_lint`)."""
    job.setdefault("avisos", []).append({"campo": campo, "nivel": nivel, "mensaje": mensaje})


def _save_image(job_id: str, key: str, png: bytes) -> None:
    try:
        out = _OUTPUTS_DIR / job_id
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{key}.png").write_bytes(png)
    except Exception as e:
        print(f"   [aviso] No se pudo guardar imagen en disco ({key}): {e}")


async def _run(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def _job_model(job: dict, param: str, default: str) -> str:
    """Modelo de generación elegido para ESTE job, o el default del .env.

    `param` ∈ {modelo_imagen, modelo_video, modelo_voz}. Los valores llegan ya
    validados contra model_catalog en la entrada (create_job / sheets); vacío =
    usar el modelo por defecto de la config.
    """
    return (job["params"].get(param) or "").strip() or default


# Ajuste fino de la voz (ratios, 1.0 = normal). El MCP acepta params extra, así que
# un modelo que no los soporte los ignora (no rompe). Valores conservadores.
_PITCH_RATES = {"grave": 0.92, "agudo": 1.08}
_SPEED_RATES = {"lenta": 0.9, "rapida": 1.1}


def _wants_preview(job: dict) -> bool:
    """True si el job debe PAUSAR en el preview editable antes de generar el medio.

    Pausan los DOS flujos: el usuario revisa/edita los prompts y textos antes de
    gastar créditos en imágenes/video. En el individual la compuerta es por post; en
    el bulk, `run_batch` la agrupa y el lote entero espera en estado "preview" (ver
    batch_runner). `preview_step` en params permite desactivarlo (default: activado).
    """
    return bool(job["params"].get("preview_step", True))


def _template_set(params: dict) -> int:
    """Set de estilo de plantillas elegido (1-3); default/invalid → 1."""
    try:
        n = int(params.get("template_set", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return n if n in (1, 2, 3) else 1


def _voice_params(job: dict, cfg) -> dict:
    """Selección de voz para ESTE job (precedencia: form > .env).

    Devuelve {voice_type, voice_id, speech_rate, pitch_rate} listo para pasar como
    kwargs a hfmcp.generate_audio / audio_cost. `voz_id`/`voz_tipo` vienen del form
    (una voz de list_voices); vacío = la voz por defecto del .env (o la del server).
    `voz_pitch`/`voz_velocidad` (grave/agudo, lenta/rapida) → ratios; None = normal.
    """
    p = job["params"]
    return {
        "voice_type": (p.get("voz_tipo") or cfg.higgsfield_tts_voice_type or ""),
        "voice_id": (p.get("voz_id") or cfg.higgsfield_tts_voice_id or ""),
        "speech_rate": _SPEED_RATES.get((p.get("voz_velocidad") or "").strip().lower()),
        "pitch_rate": _PITCH_RATES.get((p.get("voz_pitch") or "").strip().lower()),
    }


def _media_mime(filename: str) -> str:
    """MIME type para subir un medio a Blotato según la extensión del archivo.

    Cubre los formatos comunes de video/imagen que el usuario puede subir para un
    reel o una historia. Default: video/mp4 (el caso más común en reels/historias).
    """
    ext = Path(filename or "").suffix.lower()
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".m4v": "video/x-m4v", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    }.get(ext, "video/mp4")


def _is_local_src(src: str) -> bool:
    """True if `src` is a local file path (a bundled template) rather than a URL.

    Provider sources are either http(s) URLs (Higgsfield) or local template
    paths (the fallback). Local paths can't be published directly — they must be
    uploaded to Blotato first.
    """
    return not src.lower().startswith(("http://", "https://"))


def _publishable_media(src: str, filename: str, *, api_key: str) -> str:
    """Turn a provider source into a Blotato-hosted, publishable media URL.

    URLs pass through unchanged. A local template path is read from disk and
    uploaded to Blotato so the raw template can still be published when Pillow is
    unavailable or el recorte falló. Es el único camino por el que una plantilla
    llega a publicarse **sin** el texto dibujado: sin Pillow no hay con qué.
    """
    if not _is_local_src(src):
        return src
    file_bytes = Path(src).read_bytes()
    return bc.upload_media_local(file_bytes, filename, api_key=api_key)


async def _push(queue: asyncio.Queue, event: dict):
    await queue.put(event)


# Campos de la identidad visual que viajan como `marca` al arquitecto. `aspect_ratio` y
# `referencias` van por su cuenta: el aspecto lo fija el job (no la identidad) y las
# referencias `normalizar_spec` las lee del nivel de arriba de la spec, no de `marca`.
_CAMPOS_MARCA = ("paleta", "paleta_nombres", "color_texto", "color_acento",
                 "tipografia", "tipografia_secundaria", "tono_visual")


def _identidad(job: dict) -> dict:
    """La identidad visual congelada en el job al crearlo (`{}` = la de la casa).

    Se congela en `create_job` / `run_batch` y no se vuelve a consultar: cambiar la
    identidad activa a mitad de una generación no puede alterar un job en vuelo, y un
    lote entero sale con la que estaba activa cuando se subió el sheet.

    **Vacío significa "lo de siempre"**: `prompt_architect` y `_lockup_plantilla` leen
    `prompts/brand.json` como han hecho hasta ahora. Es lo que hace que un job sin
    identidad sea idéntico —no parecido— a uno de antes de esta feature.
    """
    ident = (job.get("params") or {}).get("identidad_visual")
    return ident if isinstance(ident, dict) else {}


def _lockup_plantilla(cfg, src: str, *, texto: str, rol: str,
                      identidad: dict | None = None) -> dict | None:
    """Texto que hay que DIBUJAR sobre `src`, o None si no hay que dibujar nada.

    Solo lo llevan las plantillas de respaldo. Cuando la imagen sale del modelo, el
    texto ya viene impreso desde el prompt y volver a dibujarlo encima lo duplicaría;
    cuando cae a plantilla —sin token OAuth, generación fallida, o `fuente_imagen=
    template`— el PNG llega mudo y el post se publicaba con una foto sin titular.
    Es el mismo texto, partido igual (`dividir_texto`) y con la misma notación de
    acento (`separar_acento`) que se le pide al modelo, así que la pieza dice lo
    mismo salga por donde salga.

    Respeta el interruptor de siempre: con `IMAGE_TEXT_IN_PROMPT` apagado la pieza
    no lleva texto por ningún camino.
    """
    if not _text_in_prompt(cfg) or not improv.es_plantilla(src):
        return None
    limpio, acento = parch.separar_acento(texto or "")
    if not limpio.strip():
        return None
    titular, kicker = parch.dividir_texto(limpio)
    # La identidad activa del job manda; sin ella, la de la casa. Los colores tienen que
    # ser los MISMOS que gobiernan las imágenes generadas o la pieza de respaldo se
    # leería de otra marca.
    marca = (identidad if isinstance(identidad, dict) and identidad
             else prompt_config.brand())
    paleta = marca.get("paleta") if isinstance(marca.get("paleta"), list) else []
    return {
        "titular": titular,
        "kicker": kicker,
        "acento": acento,
        # El renderizador de plantilla solo conoce los dos roles de siempre: un beat
        # se le pasa como `contenido`, que es el que tiene su escala de cuerpo. Sin
        # esto, `image_overlay` trata cualquier nombre desconocido como portada y la
        # pieza de respaldo saldría con el titular a escala de portada.
        "rol": parch.rol_base(rol),
        # Los colores son marca: salen de brand.json, el mismo archivo que gobierna
        # el look de las imágenes generadas.
        "color_texto": marca.get("color_texto") or (paleta[1] if len(paleta) > 1 else ""),
        "color_acento": marca.get("color_acento") or (paleta[2] if len(paleta) > 2 else ""),
    }


async def _render_imagen(src: str, *, cfg, texto: str, rol: str,
                         historia: bool = False, identidad: dict | None = None) -> bytes:
    """Prepara la imagen publicable: recorte al aspecto de la red (+ texto si es plantilla).

    Punto único del recorte para los dos flujos y para la regeneración de una imagen
    suelta: quien llama pasa siempre el copy de esa imagen y aquí se decide si hay
    que dibujarlo (`_lockup_plantilla`) o si ya viene impreso por el modelo.
    """
    lockup = _lockup_plantilla(cfg, src, texto=texto, rol=rol, identidad=identidad)
    return await _run(ov.render_story if historia else ov.render_feed, src, lockup)


async def _match_cover_grade(png: bytes, cover: bytes | None, cfg) -> bytes:
    """Iguala el color de un slide al de la portada. Best-effort: nunca interrumpe.

    Si falta la portada, el grade está apagado o Pillow falla, devuelve el slide tal
    cual — un carrusel con deriva de color es infinitamente mejor que uno sin slide.
    """
    if not cover or not getattr(cfg, "image_grade_match", True) or not _HAS_OVERLAY:
        return png
    try:
        return await _run(ov.match_grade, png, cover)
    except Exception as e:
        print(f"   [aviso] No se pudo igualar el color del slide: {e}")
        return png


async def _media_fallback(q: asyncio.Queue, raw_urls: dict, key: str, filename: str, cfg) -> list[str]:
    """Best-effort publishable media for `key` from raw_urls (URL or template path).

    Returns [url] when a publishable URL is obtained, else [] (and warns). A local
    template path is uploaded to Blotato first so it can be published raw.
    """
    if key not in raw_urls:
        return []
    try:
        url = await _run(_publishable_media, raw_urls[key], filename, api_key=cfg.blotato_api_key)
        return [url] if url else []
    except Exception as e:
        await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"No se pudo subir respaldo: {e}"})
        return []


# ── Video por segmentos (text-to-video storyboard / image-to-video de fotos) ──────

# Se anexa siempre a cada prompt de segmento: garantiza clips limpios sin texto.
_NO_TEXT_SUFFIX = "No text, no captions, no typography, no logos, no watermarks."
# También se anexa a cada segmento: los fallos que más delatan que el video es de IA
# (objetos flotando sin apoyo, escala incoherente, formas que se derriten a mitad de
# toma) se atacan pidiendo la física correcta EN POSITIVO. Los modelos atienden a los
# sustantivos del prompt, así que nombrar el defecto ("sin dedos de más") tiende a
# invocarlo; describir el acierto, no. La plantilla del storyboard pide lo mismo por
# beat (ver PHYSICAL PLAUSIBILITY en post_writer): esto es la red por si el beat que
# llega —editado a mano en el preview, o del fallback genérico— se olvidó de pedirlo.
_GROUNDING_SUFFIX = (
    "Physically grounded: every object rests on a real surface and casts a soft "
    "contact shadow, consistent scale and gravity, solid forms that keep their "
    "shape and proportions throughout the shot."
)
# Estilo visual de respaldo cuando el LLM no entrega `video_style`: mantiene una
# base de calidad y de coherencia entre segmentos aunque el guion venga incompleto.
_DEFAULT_VIDEO_STYLE = (
    "Cinematic footage, filmic color grade, shallow depth of field, "
    "smooth stable camera, natural coherent motion."
)
# Tope de segmentos por job para acotar el costo aunque el LLM/entrada pidan de más.
_MAX_VIDEO_SEGMENTS = 8
# Reintentos por segmento (y por línea de voz). Un clip perdido rompe el reel entero
# —queda corto y sin voz— y los fallos del proveedor son casi siempre transitorios
# (job failed puntual, timeout de la espera, corte de red), así que reintentar sale
# mucho más barato que degradar: lo caro es tirar los segmentos que SÍ salieron.
_SEGMENT_ATTEMPTS = 3
_SEGMENT_RETRY_SLEEP = 6.0
# Deadline por INTENTO de espera (el duro del cliente es 600s). Un clip de 5-10s
# tarda ~60-180s; cortar antes deja lugar a reintentar dentro del mismo presupuesto.
_SEGMENT_POLL_DEADLINE = 300
# Ventana FIJA por bloque de explainer_video (confirmado empíricamente: 2 clips de
# 5s + voz → 20.0s). Con voz, los segmentos se generan de este largo para que la
# duración final del reel coincida con la pedida.
_VOICE_BLOCK_SECONDS = 10

# Movimiento de cámara del recorrido inmobiliario (por par de fotos). El modelo
# interpola de start_image a end_image; el prompt fija el estilo del movimiento.
_WALKTHROUGH_STYLES = {
    "dolly": "Gimbal-smooth cinematic dolly that glides forward from the first framing to the second at a calm walking pace, as if entering the space.",
    "orbit": "Gimbal-smooth cinematic orbit that arcs gently from the first framing to the second, revealing the space with steady parallax.",
    "pan": "Gimbal-smooth cinematic pan with a slight forward push that carries the first framing into the second.",
}
_WALKTHROUGH_DEFAULT = "dolly"


def _walkthrough_prompt(style: str) -> str:
    base = _WALKTHROUGH_STYLES.get(style, _WALKTHROUGH_STYLES[_WALKTHROUGH_DEFAULT])
    return (
        f"Professional real-estate walkthrough transition between two photos of the same property. "
        f"{base} Photorealistic interior videography: straight architectural lines, consistent "
        f"exposure and white balance, soft natural window light, no warping or morphing of walls "
        f"and furniture. {_NO_TEXT_SUFFIX}"
    )


def _photo_to_vertical(pbytes: bytes, fname: str) -> tuple[bytes, str]:
    """Recorta la foto al centro a 9:16 para el recorrido image-to-video.

    En image-to-video Kling interpola entre las fotos tal cual: con fotos
    horizontales el clip sale horizontal aunque se pida aspect_ratio 9:16, y el
    reel termina letterboxeado (barras negras). Recortar la entrada garantiza
    segmentos nativamente verticales que llenan el cuadro del reel. Best-effort:
    sin Pillow o con un formato ilegible se devuelven los bytes originales.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return pbytes, fname
    try:
        img = Image.open(io.BytesIO(pbytes))
        img = ImageOps.exif_transpose(img)
        w, h = img.size
        target = 9 / 16
        if abs(w / h - target) < 0.01:
            return pbytes, fname
        if w / h > target:  # demasiado ancha → recorte lateral centrado
            nw = round(h * target)
            x0 = (w - nw) // 2
            img = img.crop((x0, 0, x0 + nw, h))
        else:  # demasiado alta → recorte superior/inferior centrado
            nh = round(w / target)
            y0 = (h - nh) // 2
            img = img.crop((0, y0, w, y0 + nh))
        if img.height > 1920:
            img = img.resize((1080, 1920), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=92)
        stem = fname.rsplit(".", 1)[0] or "foto"
        return buf.getvalue(), f"{stem}-9x16.jpg"
    except Exception as e:
        print(f"   [aviso] No se pudo recortar la foto a 9:16 ({fname}): {e}")
        return pbytes, fname


def _generic_scene(content: dict) -> str:
    topic = content.get("title", "professional topic")
    return (
        f"Cinematic video evoking: {topic}. One concrete symbolic object in a real, "
        "lived-in setting; slow push-in from a medium framing to a close-up; soft "
        "directional window light, muted editorial palette, calm confident mood."
    )


# ── Prompts de imagen (portada + slides del carrusel) ─────────────────────────
# Igual que en video, la escena la escribe el LLM anclada a la transcripción
# (`image_prompt` / `image_slide_prompts`): antes el prompt se armaba solo con el
# TÍTULO del video, así que la imagen no tenía nada que ver con lo que se decía
# adentro. Estos helpers le agregan lo que el modelo no debe decidir: el acabado
# editorial, el espacio libre para el overlay y el "sin texto".
# Acabado de respaldo: solo se usa cuando el LLM no entrega `image_style`. La
# dirección de arte real (paleta concreta, luz, óptica, materia) la escribe el
# modelo por post — una constante global igual para todos los posts no puede dar
# coherencia dentro del carrusel ni identidad entre posts.
_IMAGE_LOOK = (
    "Cinematic poster still: one spotlit subject on a near-black field, hard rim "
    "light, deep shadow falloff, heavy vignette, photorealistic detail."
)
# Espacio para el copy: solo tiene sentido si el copy se imprime. Con el texto
# apagado, pedir media imagen vacía produce composiciones desbalanceadas sin nada
# que llene el hueco, así que se pide lo contrario: llenar el cuadro.
# El esqueleto es el mismo lockup de póster que declara `prompt_architect` (tipo en la
# banda alta y en la baja, sujeto en la central): este texto es el que llega al modelo
# cuando el arquitecto está apagado, así que decir otra cosa daría dos composiciones
# distintas según el interruptor.
# Las bandas se piden "calm", nunca "flat": para un modelo de imagen una banda plana es
# un rectángulo de color liso, y así salían slides con passe-partout mientras otros
# salían a sangre. Además este texto es el `prompt_base` que el arquitecto le enseña al
# LLM como "BASE PROMPT (weak, to rewrite)", así que la palabra se propagaba a las
# secciones creativas. El aire se nombra por sus medios fotográficos.
_IMAGE_SPACE_FEED = (
    "Full-bleed photograph to all four edges: keep the top and bottom bands calm and "
    "uncluttered for poster type — quiet photograph there (shadow falloff, defocus, "
    "bare surface), never a band of flat colour — subject anchored in the central band."
)
_IMAGE_SPACE_VERTICAL = (
    "Vertical 9:16 framing, full-bleed photograph to all four edges: top and bottom "
    "bands calm and uncluttered for poster type — quiet photograph there (shadow "
    "falloff, defocus, bare surface), never a band of flat colour — subject anchored "
    "in the central band."
)
_IMAGE_FULL_FRAME = (
    "Compose the full frame edge to edge with deliberate balance: no empty band "
    "reserved for text, no dead space."
)
_IMAGE_FULL_FRAME_VERTICAL = (
    "Vertical 9:16 framing composed full-bleed edge to edge with deliberate "
    "balance: no empty band reserved for text, no dead space."
)
# El encuadre de cada slide lo fija su BEAT (`prompt_architect.roles_carrusel`), no el
# LLM ni una escalera posicional suelta. La tupla `_SLIDE_FRAMINGS` que vivía acá era
# lo único que variaba entre slides y era también lo más débil de la cadena: solo
# entraba en el `prompt_base`, que el arquitecto le enseña al modelo como "BASE PROMPT
# (weak, to rewrite)", así que con el LLM disponible casi nunca llegaba al prompt final
# — mientras que la cláusula de lockup, que pide siempre el mismo cuadro, sí llegaba
# siempre. Ahora el plano se declara en la sección 3 (determinista) y acá solo se
# repite, literal, para que el prompt base no contradiga al brief.


def _image_space_clause(vertical: bool, con_texto: bool = False) -> str:
    """Qué pedirle a la composición según la imagen vaya a llevar texto o no.

    `con_texto` lo pasa el llamador cuando hay texto que renderizar: la pieza
    necesita entonces sus bandas calmas para el tipo. Sin texto se pide el cuadro
    lleno, sin área reservada esperando algo que no va a llegar.
    """
    if con_texto:
        return _IMAGE_SPACE_VERTICAL if vertical else _IMAGE_SPACE_FEED
    return _IMAGE_FULL_FRAME_VERTICAL if vertical else _IMAGE_FULL_FRAME


def _image_style(posts: dict) -> str:
    """Dirección de arte del post (`image_style` del LLM) o el acabado de respaldo.

    Es el equivalente en imagen de `video_style`: el MISMO texto en la portada y en
    todos los slides es lo que hace que imágenes generadas por separado se lean como
    un set diseñado.
    """
    return (posts.get("image_style") or "").strip() or _IMAGE_LOOK


def _compose_image_prompt(scene: str, *, style: str = "", framing: str = "",
                          vertical: bool = False, con_texto: bool = False) -> str:
    """Escena + encuadre + dirección de arte + composición + anclaje físico + sin texto.

    Con `con_texto=True` el resultado NO lleva el "sin texto": la imagen va a llevar
    texto renderizado por el modelo, y este prompt es el `prompt_base` que recibe
    `prompt_architect` para convertirlo en el brief de 9 secciones.
    """
    parts = [
        (scene or "").strip(),
        (framing or "").strip(),
        (style or "").strip() or _IMAGE_LOOK,
        _image_space_clause(vertical, con_texto),
        _GROUNDING_SUFFIX,
    ]
    if not con_texto:
        parts.append(_NO_TEXT_SUFFIX)
    return " ".join(p for p in parts if p)


def _cover_image_prompt(posts: dict, content: dict, *, vertical: bool = False,
                        con_texto: bool = False) -> str:
    """Prompt de la imagen base/portada (LinkedIn, IG, Facebook, slide 0, historia).

    Usa el `image_prompt` del LLM; si falta (modelo viejo, JSON incompleto o el
    usuario lo vació en el preview) cae al prompt histórico basado en el título.
    """
    scene = (posts.get("image_prompt") or "").strip()
    if not scene:
        scene = f"Editorial photography about: {content.get('title', 'professional topic')}."
    return _compose_image_prompt(scene, style=_image_style(posts), vertical=vertical,
                                 con_texto=con_texto)


def _rol_slide(i: int, n_info: int) -> str:
    """El beat del slide de info `i` (0-based). Fuente única para los dos flujos.

    La generación, la regeneración de un slide suelto y el briefing del redactor
    tienen que contar la MISMA secuencia: si no, rehacer un slide le cambia el plano
    y el texto del slide 2 se escribe para un beat que su imagen no tiene.
    """
    roles = parch.roles_carrusel(n_info)
    return roles[i] if 0 <= i < len(roles) else "contenido"


def _slide_image_prompts(posts: dict, content: dict, n_info: int,
                         con_texto: bool = False, identidad: dict | None = None) -> list[str]:
    """Prompts de los slides extra del carrusel: n_info slides de info.

    Cada slide toma su escena del LLM (un detalle concreto distinto de la fuente) y
    su plano del BEAT que le toca por posición —el mismo que `prompt_architect` vuelve
    a declarar, literal, en la sección 3—; los que falten caen a la variación genérica
    del título. Todos comparten la misma dirección de arte que la portada.
    """
    topic = content.get("title", "engaging topic")
    style = _image_style(posts)
    llm = [s.strip() for s in (posts.get("image_slide_prompts") or []) if isinstance(s, str) and s.strip()]
    prompts: list[str] = []
    for i in range(n_info):
        scene = llm[i] if i < len(llm) else (
            f"Conceptual editorial visual about: {topic}. Lateral composition or texture, variation {i + 1}."
        )
        prompts.append(_compose_image_prompt(
            scene, style=style,
            framing=parch.encuadre_beat(_rol_slide(i, n_info), identidad),
            con_texto=con_texto,
        ))
    return prompts


def _n_slides(params: dict) -> int:
    """Slides del carrusel (3-6). Fuente única: la fase de imágenes y la regeneración
    de una imagen suelta tienen que contar lo mismo o los índices no calzan."""
    try:
        n = int(params.get("carrusel_slides", 3) or 3)
    except (TypeError, ValueError):
        n = 3
    return max(3, min(6, n))


def _texto_historia(posts: dict) -> str:
    """Hook impreso en la historia: el del modelo, o la primera línea del caption."""
    image_text = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else None
    return ((image_text or {}).get("hook", "").strip()
            or _extract_hook(posts.get("instagram_text", ""), max_words=12))


def _copy_de_imagenes(posts: dict, cfg, *, n_info: int, is_carousel: bool,
                      hay_redes: bool = True) -> dict:
    """Copy que va DENTRO de las imágenes: hook de portada + una idea por slide.

    Se resuelve ANTES de generar, porque el texto viaja en el prompt y no se dibuja
    después. Es la fuente única de la fase de imágenes y de la regeneración de una
    imagen suelta: rehacer un slide tiene que imprimir exactamente lo mismo que la
    primera vez, o el carrusel deja de contar lo que decía.

    Se prefiere el bloque `image_text` del LLM (una frase de portada cerrada + una
    idea por slide) y se degrada a las heurísticas de siempre cuando falta, dejando
    dicho en `avisos` por qué. Devuelve {li, ig, fb, portada, slides, avisos}.
    """
    image_text = posts.get("image_text") if isinstance(posts.get("image_text"), dict) else None
    llm_hook = (image_text or {}).get("hook", "").strip()
    llm_slides = [s for s in (image_text or {}).get("slides", []) if s.strip()]
    avisos: list[str] = []

    # Hook: el image_text del LLM para todas las redes; si falta, la 1ª línea del caption.
    if llm_hook:
        li = ig = fb = llm_hook
    else:
        li = _extract_hook(posts.get("linkedin_text", ""), max_words=12)
        ig = _extract_hook(posts.get("instagram_text", ""), max_words=10)
        fb = _extract_hook(posts.get("facebook_text", ""), max_words=12)
        if hay_redes and _text_in_prompt(cfg):
            avisos.append("sin texto de portada del modelo")

    # Slides de info: exactamente una idea cerrada por slide. Lo que falte se rellena
    # con las líneas del caption (NUNCA con un "mira el video" genérico).
    slides: list[str] = []
    if is_carousel:
        heur = _extract_body_lines(posts.get("instagram_text", ""), max_lines=n_info)
        for i in range(n_info):
            if i < len(llm_slides):
                slides.append(llm_slides[i])
            elif i < len(heur):
                slides.append(heur[i])
            else:
                slides.append("")  # el renderer rellena con vacío
        if len(llm_slides) < n_info and _text_in_prompt(cfg):
            avisos.append(f"el modelo dio {len(llm_slides)} de {n_info} frases para el carrusel")

    return {
        # La imagen base es UNA sola y la comparten las tres redes: con el texto
        # dentro del prompt solo puede decir una cosa, así que hay un único texto de
        # portada (el del LLM, con respaldo en la 1ª línea de cualquier caption).
        "portada": llm_hook or ig or li or fb,
        "slides": slides,
        "avisos": avisos,
    }


# ── Arquitectura del prompt de imagen (texto renderizado por el modelo) ────────
#
# El texto de la pieza ya no se dibuja después con Pillow: viaja DENTRO del prompt
# y lo renderiza Higgsfield. Eso exige un prompt mucho más explícito que la frase
# de antes, así que el prompt compuesto arriba pasa a ser el `prompt_base` de
# `prompt_architect`, que lo convierte en un brief de 9 secciones con el string
# exacto entrecomillado, su idioma, su jerarquía y su zona de aire negativo.
# Todo esto vive en el núcleo compartido: individual y bulk lo heredan igual.


def _marca_post(posts: dict, *, aspect: str, identidad: dict | None = None) -> dict:
    """Datos de marca para el arquitecto: los de la identidad activa (o los de
    `prompts/brand.json` si no hay), con el `image_style` del post pisando el tono
    visual (es la dirección de arte que el LLM escribió para ESTE post) y el aspecto
    realmente pedido al modelo.

    Lo que la identidad deja vacío no se pasa: `normalizar_spec` resuelve cada campo
    con `marca.get(x) or marca_def.get(x)`, así que un campo ausente cae solo a
    `brand.json` en vez de imponer un blanco.

    `image_style` sigue ganando sobre `tono_visual` a propósito: la identidad fija la
    paleta, la tipografía y las referencias —lo que hace reconocible a la marca— y el
    tratamiento fotográfico lo sigue eligiendo el LLM por post, como hasta ahora.

    **Pero la LUZ ya no viaja ahí.** Son dos cosas distintas que salían del mismo
    campo: el tratamiento fotográfico es creatividad por pieza (sección 7) y el
    esquema de iluminación es lo que hace que las N piezas de un job parezcan del
    mismo día, así que no puede decidirse una vez por imagen. Por eso el `tono_visual`
    de la identidad se manda ADEMÁS como `luz_identidad`, intacto y sin que el
    `image_style` lo pise: `prompt_architect` lo usa para el bloqueo de luz de la
    sección 6. Los dos viajan juntos y significan cosas distintas.
    """
    base = identidad if isinstance(identidad, dict) else {}
    marca = {k: v for k, v in base.items() if k in _CAMPOS_MARCA and v}
    marca["aspect_ratio"] = aspect
    # Antes de que `image_style` lo pise. Vacío = `normalizar_spec` cae al
    # `tono_visual` de brand.json, que es como se generaba antes de la identidad.
    if marca.get("tono_visual"):
        marca["luz_identidad"] = marca["tono_visual"]
    estilo = (posts.get("image_style") or "").strip()
    if estilo:
        marca["tono_visual"] = estilo
    return marca


def _prompt_imagen(cfg, *, prompt_base: str, posts: dict, content: dict, texto: str,
                   rol: str, aspect: str, lang: str = "es", refuerzo: bool = False,
                   refuerzo_sangrado: bool = False, identidad: dict | None = None):
    """Prompt final de UNA imagen. Devuelve `(prompt, resultado_del_arquitecto|None)`.

    Sin texto que renderizar —o con la capa de arquitectura apagada— devuelve el
    prompt base tal cual: el camino clásico sigue vivo y es el que se usa para las
    imágenes que no llevan copy. Si el arquitecto falla por lo que sea, también se
    devuelve el base: generar nunca se interrumpe por esto.
    """
    texto = (texto or "").strip()
    if not texto or not _text_in_prompt(cfg) or not getattr(cfg, "prompt_architect", True):
        return prompt_base, None
    # `angulo` es el enfoque de ESTA imagen. En los slides era la escena de la PORTADA,
    # así que a cada slide se le pedía —sin querer— el sujeto de la portada: una segunda
    # fuente de carruseles con la misma foto repetida, independiente del image-to-image.
    # Ahora la portada viaja como `escena_portada`, que es continuidad de set, no encargo.
    escena_portada = (posts.get("image_prompt") or "").strip()
    es_slide = parch.rol_base(rol) == "contenido"
    spec = {
        "contenido": {
            "tema": content.get("title", ""),
            "angulo": "" if es_slide else escena_portada,
            "escena_portada": escena_portada if es_slide else "",
            "texto_exacto_a_renderizar": texto,
            "rol_slide": rol,
            "idioma": lang,
        },
        "marca": _marca_post(posts, aspect=aspect, identidad=identidad),
        "prompt_base": prompt_base,
    }
    # Las referencias de dirección de arte NO viajan en `marca`: `normalizar_spec` las
    # lee del nivel de arriba de la spec. Vacío = las de `brand.json`, como siempre.
    referencias = identidad.get("referencias") if isinstance(identidad, dict) else None
    if referencias:
        spec["referencias"] = list(referencias)
    # El ritmo del carrusel viaja igual que las referencias (nivel de arriba, no dentro
    # de `marca`): es una lista ORDENADA por beat y `_texto_plano` la aplanaría a una
    # frase con comas. Vacío = el respaldo de `architect.json`, beat a beat.
    ritmo = identidad.get("ritmo_carrusel") if isinstance(identidad, dict) else None
    if ritmo:
        spec["ritmo_carrusel"] = list(ritmo)
    try:
        res = parch.construir(
            spec, cfg=cfg,
            autocritica=bool(getattr(cfg, "prompt_architect_critique", True)),
            refuerzo_texto=refuerzo, refuerzo_sangrado=refuerzo_sangrado,
        )
        return res.prompt, res
    except Exception as e:
        print(f"   [aviso] PromptArchitect no pudo construir el prompt: {e}. Se usa el prompt base.")
        return prompt_base, None


async def _prompt_para(job: dict, cfg, *, subkey: str, prompt_base: str, posts: dict,
                       content: dict, texto: str, rol: str, aspect: str, lang: str = "es",
                       refuerzo: bool = False, refuerzo_sangrado: bool = False) -> str:
    """`_prompt_imagen` + traza: guarda el prompt final en el job, lo loguea y cobra el LLM.

    El prompt de cada imagen queda en `job["images"]["prompts"][subkey]` (lo sirve
    `/jobs/{id}`) además de en el log del servidor: sin eso, una imagen rara no se
    puede auditar después.
    """
    prompt, res = await _run(
        _prompt_imagen, cfg, prompt_base=prompt_base, posts=posts, content=content,
        texto=texto, rol=rol, aspect=aspect, lang=lang, refuerzo=refuerzo,
        refuerzo_sangrado=refuerzo_sangrado, identidad=_identidad(job),
    )
    job["images"]["prompts"][subkey] = prompt
    print(f"   [prompt {subkey}]\n{prompt}")
    if res is not None:
        for aviso in res.avisos:
            print(f"   [aviso prompt {subkey}] {aviso}")
        for uso in res.usos:
            await _track(job, service=uso["service"], operation="prompt_architect",
                         units=uso["units"], model=uso.get("model"))
    return prompt


async def _verificar_texto(job: dict, q: asyncio.Queue, cfg, *, subkey: str, src: str,
                           texto: str, rehacer) -> str:
    """QA post-generación: ¿la imagen dice exactamente lo que tenía que decir?

    Un modelo de visión lee el texto impreso y lo compara con el esperado (acentos
    incluidos). Si no coincide, `rehacer()` vuelve a generar la imagen con la
    instrucción de texto reforzada, hasta el máximo de `prompts/qa_vision.json`.
    Cada intento queda registrado en `job["images"]["qa"][subkey]`.

    Best-effort: sin modelo de visión, con plantilla local o ante cualquier fallo se
    devuelve la imagen que ya había. Nunca interrumpe la generación.
    """
    # Las marcas de acento (**así**) son notación del usuario, no parte del copy: el
    # modelo imprime el texto sin ellas, así que el QA tiene que comparar contra el
    # texto limpio o toda imagen con acento marcado se leería como error de render.
    texto = parch.separar_acento(texto)[0].strip()
    if not src or not texto or not _text_in_prompt(cfg) or not getattr(cfg, "image_text_qa", True):
        return src
    if not iqa.disponible(cfg):
        return src

    registro: list[dict] = []
    maximo = iqa.max_intentos()
    intento = 0
    while True:
        res = await _run(iqa.verificar, src, texto, cfg=cfg)
        registro.append({
            "intento": intento + 1, "ok": res.ok, "verificado": res.verificado,
            "texto_visto": res.texto_visto, "motivo": res.motivo, "recortado": res.recortado,
        })
        print(f"   [QA texto {subkey}] intento {intento + 1}: "
              f"{'ok' if res.ok else 'NO COINCIDE'} — {res.motivo}")
        if res.uso:
            await _track(job, service=res.uso["service"], operation="image_text_qa",
                         units=res.uso["units"], model=res.uso.get("model"))
        if res.ok or not res.verificado:
            break
        if intento >= maximo:
            await _push(q, {"step": "images", "status": "warn", "subkey": subkey,
                            "msg": f"El texto de la imagen no coincide tras {maximo + 1} intentos: {res.motivo}"})
            break
        intento += 1
        motivo_corto = "Texto cortado por el borde" if res.recortado else "Texto mal renderizado"
        await _push(q, {"step": "images", "status": "running",
                        "msg": f"{motivo_corto} en {subkey} — reintento {intento} de {maximo}..."})
        try:
            nuevo = await rehacer()
        except Exception as e:
            print(f"   [aviso] Reintento de {subkey} falló: {e}")
            break
        if not nuevo:
            break
        src = nuevo
    job["images"]["qa"][subkey] = registro
    return src


# Un solo reintento, a diferencia del QA de texto (que permite dos): el defecto es
# binario —o hay banda o no la hay— y regenerar cuesta créditos de verdad.
_BANDAS_REINTENTOS = 1


async def _verificar_bandas(job: dict, q: asyncio.Queue, cfg, *, subkey: str, src: str,
                            rehacer) -> str:
    """QA post-generación: ¿el modelo pintó un passe-partout o un letterbox?

    Es el tercer frente contra ese defecto y el único que no es prompt. Los otros dos
    —el sangrado declarado en positivo en las secciones 1 y 3, y el saneo de lo que la
    identidad escribe en la 5— ya fallaron dos veces; una comprobación sobre el píxel
    no depende de cómo el modelo resuelva una ambigüedad.

    Se mide sobre la imagen **cruda del proveedor**: antes del recorte, del texto de
    la plantilla y del `match_grade`. Así se juzga lo que hizo el modelo y no lo que
    hizo Pillow — que además dibuja sus propias bandas oscuras sobre las plantillas de
    respaldo y daría positivo siempre.

    Best-effort: sin Pillow, con plantilla local o ante cualquier fallo se devuelve la
    imagen que ya había. Nunca interrumpe la generación.
    """
    if not src or not _HAS_OVERLAY or not getattr(cfg, "image_band_qa", True):
        return src
    # Una plantilla local no la pintó ningún modelo: reintentarla daría exactamente la
    # misma imagen y el aviso no tendría acción posible detrás.
    if improv.es_plantilla(src):
        return src

    registro: list[dict] = []
    intento = 0
    while True:
        try:
            crudo = await _run(ov.bytes_crudos, src)
            bordes = await _run(ov.bordes_planos, crudo)
        except Exception as e:
            print(f"   [aviso] QA de bandas de {subkey} no ejecutado: {e}")
            break
        registro.append({"intento": intento + 1, "bordes": bordes})
        print(f"   [QA bandas {subkey}] intento {intento + 1}: "
              f"{'ok' if not bordes else 'BANDA ' + ', '.join(bordes)}")
        if not bordes:
            break
        if intento >= _BANDAS_REINTENTOS:
            await _push(q, {"step": "images", "status": "warn", "subkey": subkey,
                            "msg": f"La imagen sigue saliendo con banda de color liso "
                                   f"({', '.join(bordes)}) tras {_BANDAS_REINTENTOS + 1} intentos."})
            break
        intento += 1
        await _push(q, {"step": "images", "status": "running",
                        "msg": f"Banda de color liso en {subkey} — reintento con el "
                               f"sangrado reforzado..."})
        try:
            nuevo = await rehacer()
        except Exception as e:
            print(f"   [aviso] Reintento por bandas de {subkey} falló: {e}")
            break
        if not nuevo:
            break
        src = nuevo
    if registro:
        job["images"]["bandas"][subkey] = registro
    return src


async def _verificar_conjunto(job: dict, q: asyncio.Queue, cfg, *, claves: list[str],
                              rehacer) -> None:
    """QA de conjunto: ¿las N piezas del carrusel se leen como un set?

    Corre **después** del bucle de slides y **antes** de subir, sobre los bytes que se
    van a publicar (overlay y grade ya aplicados): es lo que va a ver el lector.

    Los slides marcados como outlier se rehacen por el camino que ya existe, **una
    sola ronda**: regenerar cuesta créditos por imagen y el veredicto es una opinión,
    no una medida — encadenar rondas convierte un carrusel caro en uno carísimo sin
    garantía de que la segunda tirada se parezca más al set.

    Best-effort de punta a punta. Nunca interrumpe la generación.
    """
    if not sqa.activo(cfg):
        return
    image_bytes: dict[str, bytes] = job["images"]["bytes"]
    presentes = [k for k in claves if k in image_bytes]
    if len(presentes) < 3:
        return

    registro: list[dict] = []
    rondas = sqa.max_reintentos()
    ronda = 0
    while True:
        res = await _run(sqa.revisar, [image_bytes[k] for k in presentes], cfg=cfg)
        registro.append({
            "ronda": ronda + 1, "ok": res.ok, "verificado": res.verificado,
            "motivo": res.motivo,
            "peor": presentes[res.peor] if 0 <= res.peor < len(presentes) else "",
            "piezas": [{"subkey": presentes[p.indice], "ok": p.ok, "fallos": p.fallos,
                        "motivo": p.motivo}
                       for p in res.piezas if 0 <= p.indice < len(presentes)],
        })
        print(f"   [QA conjunto] ronda {ronda + 1}: "
              f"{'ok' if res.ok else 'ROMPE EL SET'} — {res.motivo}")
        if res.uso:
            await _track(job, service=res.uso["service"], operation="image_set_qa",
                         units=res.uso["units"], model=res.uso.get("model"))
        if res.ok or not res.verificado:
            break
        if ronda >= rondas or not (0 <= res.peor < len(presentes)):
            await _push(q, {"step": "images", "status": "warn",
                            "msg": f"El carrusel no se lee como un set: {res.motivo}"})
            break
        ronda += 1
        outlier = presentes[res.peor]
        await _push(q, {"step": "images", "status": "running",
                        "msg": f"{outlier} rompe el set — rehaciéndolo..."})
        try:
            if not await rehacer(outlier):
                break
        except Exception as e:
            print(f"   [aviso] Rehacer {outlier} por el QA de conjunto falló: {e}")
            break
    job["images"]["qa_set"] = registro


def _segment_prompt(beat: str, style: str) -> str:
    """Prompt final de un segmento text-to-video: shot + look compartido + sin texto.

    El `style` (el `video_style` del LLM, idéntico en TODOS los segmentos) es lo
    que hace que clips generados por separado corten como un solo video; si falta,
    el estilo por defecto garantiza la misma coherencia mínima.
    """
    parts = [(beat or "").strip(), (style or "").strip() or _DEFAULT_VIDEO_STYLE,
             _GROUNDING_SUFFIX, _NO_TEXT_SUFFIX]
    return " ".join(p for p in parts if p)


def _usd_per_credit() -> float:
    """USD por crédito de la suscripción de Higgsfield (de pricing.json). 0 si falta."""
    try:
        pricing = cost_calc.load_pricing()
        return float((pricing.get("higgsfield_mcp") or {}).get("usd_per_credit") or 0.0)
    except Exception:
        return 0.0


def _save_video(job_id: str, mp4: bytes) -> None:
    try:
        out = _OUTPUTS_DIR / job_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "reel.mp4").write_bytes(mp4)
    except Exception as e:
        print(f"   [aviso] No se pudo guardar el video en disco: {e}")


def _subtitle_font(cfg) -> str:
    """Fuente de los subtítulos quemados, o "" si están desactivados por config."""
    font = (getattr(cfg, "higgsfield_subtitle_font", "") or "").strip().lower()
    return "" if font in ("none", "off", "no", "0") else font


def _video_warn(job: dict, msg: str) -> None:
    """Acumula un aviso en job["video"]["notice"] (el banner ámbar de la revisión).

    Con una sola asignación, el último error pisaba a los anteriores y el usuario
    veía "falló el segmento 4" sin enterarse de que también habían fallado el 1 y
    el 2 — ni de por qué el reel salió mudo.
    """
    prev = (job["video"].get("notice") or "").strip()
    job["video"]["notice"] = f"{prev} {msg}".strip() if prev else msg


def _is_fatal_gen_error(e: BaseException) -> bool:
    """True si reintentar no puede cambiar el resultado (sesión muerta / sin créditos).

    Con estos dos errores se corta la tanda de una: reintentar cada segmento con el
    mismo fallo solo alarga el job (cada espera cuesta minutos) y tapa el motivo real.
    """
    if isinstance(e, hfmcp.ReauthRequired):
        return True
    return "not_enough_credits" in str(e).lower()


def _is_poll_timeout(e: BaseException) -> bool:
    """True si el fallo fue solo la espera (el job encolado sigue vivo del lado del server)."""
    return "poll timeout" in str(e).lower()


async def _await_segment(q: asyncio.Queue, seg: dict, handle: dict | None, *,
                         idx: int, total: int, aspect: str, seg_seconds: int,
                         model: str, attempts: int = _SEGMENT_ATTEMPTS) -> dict:
    """Espera UN segmento reintentando hasta `attempts` veces; lanza el último error.

    Un timeout de espera NO invalida el job encolado (el server sigue generando), así
    que se vuelve a esperar el mismo handle: no se paga otra generación. Cualquier
    otro fallo sí descarta el job y el reintento encola uno nuevo.
    """
    last: BaseException = RuntimeError("no se pudo generar el segmento")
    for attempt in range(1, attempts + 1):
        try:
            if handle is None:
                handle = await _run(hfmcp.submit_video, seg["prompt"], aspect_ratio=aspect,
                                    duration=(seg_seconds or None), model=model,
                                    medias=seg.get("medias"))
            res = await _run(hfmcp.poll_video, handle, deadline=_SEGMENT_POLL_DEADLINE)
            if res.get("url"):
                return res
            last = RuntimeError("el proveedor no devolvió la URL del clip")
        except Exception as e:
            if _is_fatal_gen_error(e):
                raise
            last = e
        # Log del motivo EXACTO del proveedor. Los warn del SSE se pisan en la UI
        # (la fila del video muestra solo el último evento), así que sin esto un
        # segmento caído se investiga a ciegas.
        print(f"   [video] segmento {idx + 1}/{total} intento {attempt}/{attempts} falló: {last}")
        if not _is_poll_timeout(last):
            handle = None
        if attempt < attempts:
            await _push(q, {"step": "video", "status": "running",
                            "msg": f"Reintentando el segmento {idx + 1}/{total} "
                                   f"({attempt}/{attempts - 1})..."})
            await asyncio.sleep(_SEGMENT_RETRY_SLEEP)
    raise last


async def _generate_segments(job: dict, q: asyncio.Queue, segments: list[dict], *,
                             aspect: str, seg_seconds: int, model: str) -> list[dict | None]:
    """Genera los N segmentos y devuelve los resultados ALINEADOS por índice (None = falló).

    Los submits se encolan todos primero: el server genera en paralelo, así que un
    reel de 6 clips tarda lo que el más lento y no la suma de los seis (menos tiempo
    de pared = menos ventana para que algo se caiga a mitad de camino). Después se
    espera cada uno en orden, con reintentos. El índice se conserva porque el guion
    de voz está alineado 1:1 con el storyboard: perder la posición desfasa la
    narración.
    """
    n = len(segments)
    handles: list[dict | None] = [None] * n
    results: list[dict | None] = [None] * n

    fatal = ""
    await _push(q, {"step": "video", "status": "running", "msg": f"Encolando {n} clip(s) de video..."})
    for i, seg in enumerate(segments):
        try:
            handles[i] = await _run(hfmcp.submit_video, seg["prompt"], aspect_ratio=aspect,
                                    duration=(seg_seconds or None), model=model,
                                    medias=seg.get("medias"))
        except Exception as e:
            print(f"   [video] no se pudo encolar el segmento {i + 1}/{n} ({model}, {seg_seconds}s): {e}")
            if _is_fatal_gen_error(e):
                fatal = str(e)
                _video_warn(job, f"Generación interrumpida: {e}")
                await _push(q, {"step": "video", "status": "warn", "msg": fatal})
                break
            # Fallo puntual del encolado: se reintenta al esperar este segmento.

    # Con un error fatal en el encolado no se reintenta nada, pero sí se esperan los
    # clips que YA se encolaron: están pagados y con dos alcanza para armar el reel.
    for i, seg in enumerate(segments):
        if fatal and handles[i] is None:
            continue
        await _push(q, {"step": "video", "status": "running", "msg": f"Generando segmento {i + 1}/{n}..."})
        try:
            results[i] = await _await_segment(q, seg, handles[i], idx=i, total=n, aspect=aspect,
                                              seg_seconds=seg_seconds, model=model,
                                              attempts=1 if fatal else _SEGMENT_ATTEMPTS)
        except Exception as e:
            if _is_fatal_gen_error(e):
                _video_warn(job, f"Generación interrumpida en el segmento {i + 1} de {n}: {e}")
                await _push(q, {"step": "video", "status": "warn", "msg": str(e)})
                return results
            print(f"   [video] segmento {i + 1}/{n} DESCARTADO tras {_SEGMENT_ATTEMPTS} intentos: {e}")
            _video_warn(job, f"El segmento {i + 1} de {n} no se pudo generar: {e}")
            await _push(q, {"step": "video", "status": "warn",
                            "msg": f"Segmento {i + 1}/{n} descartado: {e}"})
    ok = sum(1 for r in results if r)
    print(f"   [video] {ok}/{n} segmentos generados (modelo={model}, {seg_seconds}s, aspect={aspect})")
    return results


async def _tts_job_id(q: asyncio.Queue, line: str, *, model: str, voice: dict,
                      idx: int, total: int) -> str:
    """job_id del TTS de una línea, o "" si falló tras los reintentos.

    Mismo criterio que los segmentos: un fallo puntual del TTS no debería costar la
    voz del reel entero (antes tumbaba toda la rama y el reel salía mudo).
    """
    for attempt in range(1, _SEGMENT_ATTEMPTS + 1):
        await _push(q, {"step": "video", "status": "running",
                        "msg": f"Generando voz {idx + 1}/{total}..."})
        try:
            res = await _run(hfmcp.generate_audio, line, model=model, **voice)
            if res.get("job_id"):
                return res["job_id"]
        except Exception as e:
            if _is_fatal_gen_error(e):
                raise
            if attempt == _SEGMENT_ATTEMPTS:
                await _push(q, {"step": "video", "status": "warn",
                                "msg": f"No se pudo generar la voz {idx + 1}/{total}: {e}"})
        if attempt < _SEGMENT_ATTEMPTS:
            await asyncio.sleep(_SEGMENT_RETRY_SLEEP)
    return ""


async def _voiceover_assembly(job: dict, q: asyncio.Queue, cfg, seg_jobs: list[dict],
                              voice_lines: list[str], *, aspect: str) -> tuple[bytes, str]:
    """Voz en off + subtítulos: TTS por segmento y ensamblaje con explainer_video.

    `seg_jobs` ([{"job_id", "url"}]) y `voice_lines` llegan ALINEADOS 1:1: cada
    línea narra su propio clip. Si el TTS de una línea falla tras los reintentos, se
    descarta ese bloque entero (clip incluido) en vez de mandarlo sin audio: un
    bloque mudo deja el join `in_progress` para siempre en el server (verificado).
    Devuelve (bytes_mp4, url_del_explainer) — bytes vacíos si la descarga falló pero
    la URL sirve. Si cualquier paso rompe, lanza: el llamador degrada al stitching
    mudo local (los clips ya están generados, no se pierde nada).
    """
    n = len(seg_jobs)
    tts_model = _job_model(job, "modelo_voz", cfg.higgsfield_mcp_tts_model)
    voice = _voice_params(job, cfg)  # voz + ajuste elegidos en el form (o el .env)

    # TTS por línea → un bloque (clip + voz) por línea que salió bien.
    blocks: list[tuple[dict, str, str]] = []  # (segmento, audio_job_id, línea)
    for i, (sj, line) in enumerate(zip(seg_jobs, voice_lines)):
        audio_id = await _tts_job_id(q, line, model=tts_model, voice=voice, idx=i, total=n)
        if audio_id:
            blocks.append((sj, audio_id, line))
        else:
            _video_warn(job, f"La voz del segmento {i + 1} falló: ese clip se omite del reel.")
    if len(blocks) < 2:
        raise RuntimeError("no quedaron bloques con voz suficientes para el ensamblaje")
    lines = [b[2] for b in blocks]

    # Tracking del TTS: créditos exactos vía preflight (lineal por carácter);
    # si el preflight falla, cost_calc estima con la tarifa por carácter.
    tts_chars = sum(len(l) for l in lines)
    tts_cost = await _run(hfmcp.audio_cost, " ".join(lines), model=tts_model, **voice)
    tts_units = {"generations": len(blocks), "characters": tts_chars}
    if tts_cost and tts_cost.get("credits_exact") is not None:
        tts_units["credits"] = float(tts_cost["credits_exact"])
    await _track(job, service="higgsfield_mcp", operation="tts", units=tts_units,
                 model=tts_model)

    # Ensamblaje server-side: ventanas fijas por bloque + voz TTS por bloque. Se pide
    # SIN los subtítulos "etiqueta de papel" del server: quemamos los nuestros
    # (minimalistas) sobre el MP4 resultante. El ensamblaje es gratis.
    want_subs = bool(_subtitle_font(cfg))  # HIGGSFIELD_SUBTITLE_FONT=none|off los apaga
    await _push(q, {"step": "video", "status": "running", "msg": "Ensamblando el reel con voz..."})
    items = [{"video": sj["job_id"], "audio": audio_id} for sj, audio_id, _ in blocks]
    w, h = vstitch.size_for_aspect(aspect)
    url = await _run(hfmcp.assemble_explainer, items, width=w, height=h, subtitle_font="")
    await _track(job, service="higgsfield_mcp", operation="video_assembly",
                 units={"blocks": len(blocks), "voiced_blocks": len(blocks), "credits": 0.0},
                 model="explainer_video")

    try:
        final_bytes = await _run(vstitch.fetch_video_bytes, url)
    except Exception as e:
        await _push(q, {"step": "video", "status": "warn",
                        "msg": f"No se pudo descargar el MP4 final ({e}); se re-hospeda desde la URL."})
        final_bytes = b""

    # Subtítulos minimalistas propios (una línea por bloque, repartidas en la duración
    # real). Best-effort: si falla, el reel queda con voz pero sin subtítulos.
    if want_subs and final_bytes:
        await _push(q, {"step": "video", "status": "running", "msg": "Añadiendo subtítulos..."})
        try:
            final_bytes = await _run(vstitch.burn_subtitles, final_bytes, lines,
                                     aspect=aspect, block_seconds=_VOICE_BLOCK_SECONDS)
        except Exception as e:
            await _push(q, {"step": "video", "status": "warn",
                            "msg": f"No se pudieron quemar los subtítulos ({e}); el reel queda con voz sin subtítulos."})
    return final_bytes, url


async def _run_video_segments(job: dict, q: asyncio.Queue, cfg, segments: list[dict], *,
                              aspect: str, seg_seconds: int,
                              do_linkedin: bool, do_instagram: bool, do_facebook: bool,
                              do_tiktok: bool = False,
                              voiceover: list[str] | None = None,
                              default_model: str = "") -> None:
    """Genera N segmentos, los une (con voz si hay guion) y deja el medio en el job.

    `segments`: lista de {"prompt": str, "medias": list|None}. Un segmento = una
    generación con Higgsfield (text-to-video si medias es None; image-to-video si
    trae start_image/end_image). Cada uno se reintenta (`_generate_segments`); los
    que aun así fallen se descartan y el reel se arma con los que sobrevivieron,
    avisando cuántos faltaron. Sin fallback gratis: si no sobrevive ninguno, la
    publicación queda sin medio (las plantillas son imágenes, no son un clip).

    `voiceover`: líneas habladas del LLM (una por segmento). Si hay y REEL_VOICEOVER
    no está apagado, la unión la hace `explainer_video` en el server (voz TTS por
    bloque + subtítulos quemados) en vez del concat mudo de ffmpeg; cualquier fallo
    de esa rama degrada al stitching mudo local — los clips ya están generados.
    """
    # `default_model` deja que la rama de fotos (image-to-video) use su propio default
    # sin arrastrar el de text-to-video; el modelo elegido por post siempre gana.
    model = _job_model(job, "modelo_video", default_model or cfg.higgsfield_mcp_video_model)
    n = len(segments)
    job["video"]["provider"] = "higgsfield-mcp"
    voice_lines = [l.strip() for l in (voiceover or []) if l and l.strip()]
    # La voz necesita el switch encendido, ≥2 segmentos (explainer_video exige
    # mínimo 2 bloques) y el guion COMPLETO (una línea por segmento): un bloque sin
    # audio deja el join in_progress indefinidamente en el server (verificado:
    # timeout a los 600s). Un reel de 1 segmento sale mudo, como siempre.
    want_voice = getattr(cfg, "reel_voiceover", True) and n >= 2 and len(voice_lines) >= n
    if getattr(cfg, "reel_voiceover", True) and n >= 2 and 0 < len(voice_lines) < n:
        # Caso típico tras editar el preview: se borró una línea y el guion dejó de
        # calzar con los shots. Antes salía mudo sin explicación.
        _video_warn(job, f"El guion de voz trae {len(voice_lines)} línea(s) para {n} "
                         f"segmento(s): el reel se genera sin voz. Dejá una línea por shot en el preview.")

    # ── Preflight de costo (get_cost — no encola ni cobra). Informativo. ──
    per = await _run(hfmcp.video_cost, aspect_ratio=aspect, duration=(seg_seconds or None),
                     model=model, medias=(segments[0].get("medias") if segments else None))
    per_credits = float(per["credits"]) if (per and per.get("credits") is not None) else 0.0
    voice_credits = 0.0
    if per and per.get("credits") is not None:
        total_credits = per_credits * n
        extras = ""
        if want_voice:
            tts_est = await _run(hfmcp.audio_cost, " ".join(voice_lines[:n]),
                                 model=_job_model(job, "modelo_voz", cfg.higgsfield_mcp_tts_model),
                                 **_voice_params(job, cfg))
            voice_credits = float((tts_est or {}).get("credits_exact") or 0.0)
            if _subtitle_font(cfg):
                voice_credits += 0.05 * min(len(voice_lines), n)
            total_credits += voice_credits
            extras = " · con voz y subtítulos" if _subtitle_font(cfg) else " · con voz"
        usd = total_credits * _usd_per_credit()
        job["video"]["cost"] = {
            "credits": round(total_credits, 2), "usd": round(usd, 4),
            "segments": n, "seconds": (seg_seconds or 0) * n, "voice": want_voice,
        }
        await _push(q, {"step": "video", "status": "running",
                        "msg": f"Costo estimado: {total_credits:.1f} créditos (~${usd:.2f}) · {n} segmento(s) · ~{(seg_seconds or 0) * n}s{extras}"})

    # ── Generar los segmentos (con reintentos; se conserva el job_id porque
    #    explainer_video referencia los clips por id, no por URL) ──
    results = await _generate_segments(job, q, segments, aspect=aspect,
                                       seg_seconds=seg_seconds, model=model)
    # Solo los que salieron, conservando su índice: la línea i de la voz narra el
    # shot i, así que el guion se recorta a los MISMOS índices que sobrevivieron.
    kept = [(i, r) for i, r in enumerate(results) if r]
    seg_jobs = [r for _, r in kept]
    seg_urls = [r["url"] for r in seg_jobs]
    if seg_jobs and len(seg_jobs) < n:
        _video_warn(job, f"Se generaron {len(seg_jobs)} de {n} segmentos: el video dura "
                         f"~{(seg_seconds or 0) * len(seg_jobs)}s en vez de ~{(seg_seconds or 0) * n}s.")

    play_url = ""
    if seg_urls:
        final_bytes = b""
        voiced_url = ""
        # ── Voz en off + subtítulos (explainer_video). Degrada al concat mudo. ──
        # Con segmentos caídos se sigue adelante usando solo las líneas de los clips
        # que existen (≥2 bloques, el mínimo de explainer_video): perder un clip ya
        # es bastante castigo como para además quedarse sin voz ni subtítulos.
        kept_lines = [voice_lines[i] for i, _ in kept] if want_voice else []
        if want_voice and len(seg_jobs) >= 2:
            try:
                final_bytes, voiced_url = await _voiceover_assembly(
                    job, q, cfg, seg_jobs, kept_lines, aspect=aspect)
            except Exception as e:
                _video_warn(job, f"No se pudo añadir la voz al reel: {e}. Se publica sin audio.")
                await _push(q, {"step": "video", "status": "warn",
                                "msg": f"No se pudo añadir la voz al reel: {e}. Se publica sin audio."})
                final_bytes, voiced_url = b"", ""
        elif want_voice:
            _video_warn(job, "Quedó un solo clip: el reel se publica sin voz (el ensamblaje con voz necesita al menos dos).")
            await _push(q, {"step": "video", "status": "warn",
                            "msg": "Quedó un solo clip: el reel se publica sin voz."})
        if job["video"].get("cost"):
            job["video"]["cost"]["voice"] = bool(final_bytes or voiced_url)

        # ── Concatenar mudo (solo si no hubo voz y hay más de uno) ──
        if not final_bytes and not voiced_url and len(seg_urls) > 1:
            try:
                final_bytes = await _run(vstitch.concat_videos, seg_urls, aspect=aspect)
            except Exception as e:
                await _push(q, {"step": "video", "status": "warn",
                                "msg": f"No se pudieron unir los {len(seg_urls)} segmentos ({e}). Se publica el primero."})
                final_bytes = b""

        # ── Guardar en disco y re-hospedar en Blotato ──
        try:
            if final_bytes:
                _save_video(job["id"], final_bytes)
                play_url = await _run(bc.upload_media_local, final_bytes, "reel.mp4",
                                      api_key=cfg.blotato_api_key, mime="video/mp4")
            else:
                src = voiced_url or seg_urls[0]
                hosted = await _run(bc.upload_media_from_url, src, api_key=cfg.blotato_api_key)
                play_url = hosted or src
        except Exception as e:
            play_url = voiced_url or seg_urls[0]
            await _push(q, {"step": "video", "status": "warn",
                            "msg": f"No se pudo re-hospedar el video en Blotato: {e}. Se usa la URL del proveedor."})

        # ── Tracking de costo real: segundos totales generados (el MCP cobra por seg) ──
        await _track(job, service="higgsfield_mcp", operation="video_generation",
                     units={"generations": len(seg_urls), "seconds": (seg_seconds or 0) * len(seg_urls)},
                     model=model)
        # El costo mostrado en la revisión se ajusta a lo que realmente se generó
        # (el preflight asumía los N segmentos pedidos).
        if job["video"].get("cost") and len(seg_urls) != n:
            real = per_credits * len(seg_urls) + (voice_credits if job["video"]["cost"]["voice"] else 0.0)
            job["video"]["cost"].update({
                "credits": round(real, 2), "usd": round(real * _usd_per_credit(), 4),
                "segments": len(seg_urls), "seconds": (seg_seconds or 0) * len(seg_urls),
            })

    if play_url:
        job["video"]["url"] = play_url
        job["_li_media_urls"] = [play_url] if do_linkedin else []
        job["_ig_media_urls"] = [play_url] if do_instagram else []
        job["_fb_media_urls"] = [play_url] if do_facebook else []
        job["_tk_media_urls"] = [play_url] if do_tiktok else []
        job["images"]["blotato_urls"] = {
            "linkedin": play_url if do_linkedin else "",
            "instagram": [play_url] if do_instagram else [],
            "facebook": play_url if do_facebook else "",
            "tiktok": play_url if do_tiktok else "",
        }
        await _push(q, {"step": "video", "status": "done", "msg": "Video listo"})
    else:
        # Ningún clip sobrevivió: sin fallback gratis, la publicación queda sin medio.
        if not job["video"].get("notice"):
            _video_warn(job, "No se pudo generar ningún clip del video.")
        job["_li_media_urls"] = []
        job["_ig_media_urls"] = []
        job["_fb_media_urls"] = []
        job["_tk_media_urls"] = []


async def run_pipeline(job: dict):
    q: asyncio.Queue = job["_queue"]
    params: dict = job["params"]
    cfg = job["_cfg"]

    source_type: str = params.get("source_type", "youtube")
    url: str = params.get("youtube_url", "")
    dry_run: bool = params.get("dry_run", False)
    formato_ig: str = params.get("formato_instagram", "imagen-unica")

    # Segundos por segmento de video (config): lo necesita post_writer para calcular
    # cuántos shots pedirle al storyboard (y el presupuesto de palabras de la voz).
    # Se fija antes de escribir los posts. Con voz en off, cada bloque de
    # explainer_video es una ventana FIJA de ~10s (confirmado empíricamente:
    # 2 clips de 5s + voz → 20.0s), así que los segmentos se generan de 10s para
    # que la duración final coincida con la pedida — mismo costo total (Kling
    # cobra por segundo) y el doble de presupuesto de palabras por línea.
    # `_wants_video` es la fuente única de "este job necesita guion de video": la
    # comparte post_writer para pedir el storyboard y alinear la voz.
    wants_voiced_video = cfg.reel_voiceover and _wants_video(params)
    params["video_segment_seconds"] = (
        _VOICE_BLOCK_SECONDS if wants_voiced_video
        else max(1, int(cfg.higgsfield_video_segment_seconds or 5))
    )

    # Redes destino elegidas en el form/sheet (default: las tres). Fuente única: networks.
    nets = active_networks(params)
    do_linkedin = "linkedin" in nets
    do_instagram = "instagram" in nets
    do_facebook = "facebook" in nets
    do_tiktok = "tiktok" in nets  # solo reels (video vertical); ver networks.py

    try:
        # ── Step 1: Extract ──────────────────────────────────────────────
        # The pipeline downstream only needs a `content` dict (title/transcript/…)
        # and a `clean_url` (the LinkedIn "watch the video" CTA target, empty for
        # non-YouTube sources). Each source builds those two, then everything
        # after this step is source-agnostic.
        forced_lang = params.get("idioma", "auto")
        # Pista para el motor de transcripción: solo cuando el usuario forzó el
        # idioma (en "auto" el propio Whisper detecta mejor que cualquier pista).
        lang_hint = ld.normalize_lang(forced_lang)

        # Trigger "archivo" (solo bulk): audio o documento referenciado por URL en el
        # sheet. Se descarga, se clasifica (audio | texto) y sigue por el mismo camino
        # que un archivo subido en el flujo individual.
        if source_type == "archivo":
            await _push(q, {"step": "extract", "status": "running", "msg": "Descargando archivo de la URL..."})
            data, fname = await _run(rf.fetch_remote_file, params.get("archivo_url", ""))
            source_type = rf.classify_source(fname, data)
            job["_upload_bytes"] = data
            job["_upload_filename"] = fname or ("audio.ogg" if source_type == "audio" else "documento.txt")
            params["source_type"] = source_type

        if source_type == "audio":
            await _push(q, {"step": "extract", "status": "running", "msg": "Transcribiendo audio..."})
            if not cfg.transcription_available:
                raise RuntimeError(
                    "Transcripción de audio no configurada. Define OPENAI_API_KEY "
                    "(o GROQ_API_KEY + TRANSCRIPTION_BASE_URL), o usa "
                    "TRANSCRIPTION_ENGINE=local, en .env."
                )
            if cfg.transcription_engine == "local":
                transcript, audio_seconds = await _run(
                    trl.transcribe_audio_local,
                    job["_upload_bytes"], job["_upload_filename"],
                    model_size=cfg.transcription_local_model,
                    device=cfg.transcription_local_device,
                    compute_type=cfg.transcription_local_compute,
                    language=lang_hint,
                )
                whisper_model = "local"
            else:
                transcript, audio_seconds = await _run(
                    tr.transcribe_audio,
                    job["_upload_bytes"], job["_upload_filename"],
                    api_key=cfg.transcription_api_key,
                    base_url=cfg.transcription_base_url,
                    model=cfg.transcription_model,
                    language=lang_hint,
                )
                whisper_model = cfg.transcription_model
            if not transcript.strip():
                raise RuntimeError("La transcripción quedó vacía — revisa el audio o las credenciales.")
            # Tracking de costos: minutos transcritos (motor local = $0, igual se registra).
            await _track(job, service="whisper", operation="transcription",
                         units={"minutes": (audio_seconds or 0.0) / 60.0}, model=whisper_model)
            content = _content_from_text(transcript, default_title="Nota de voz")
            clean_url = ""

        elif source_type == "manual":
            await _push(q, {"step": "extract", "status": "running", "msg": "Leyendo texto..."})
            # Texto plano escrito directamente en el form (solo flujo individual).
            text = (params.get("manual_text") or "").strip()
            if not text:
                raise RuntimeError("El texto manual está vacío.")
            content = _content_from_text(text, default_title="Texto manual")
            clean_url = ""

        elif source_type == "texto":
            await _push(q, {"step": "extract", "status": "running", "msg": "Leyendo documento..."})
            # Accepts .txt/.md, PDF and Word (.docx); the extractor picks the parser
            # by extension/magic bytes and raises a user-facing message on failure.
            text = (await _run(doc.extract_document_text, job["_upload_bytes"], job["_upload_filename"])).strip()
            if not text:
                raise RuntimeError("El documento está vacío o no se pudo extraer texto.")
            content = _content_from_text(
                text, default_title=Path(job["_upload_filename"] or "Documento").stem
            )
            clean_url = ""

        else:  # youtube
            await _push(q, {"step": "extract", "status": "running", "msg": "Extrayendo video de YouTube..."})
            clean_url = re.sub(r'[&?]t=\d+s?', '', url)
            try:
                content = await _run(bc.extract_youtube_local, clean_url)
            except Exception:
                try:
                    vid_match = re.search(r'[?&]v=([^&]+)', url)
                    if vid_match:
                        content = await _run(bc.extract_youtube_local, f"https://www.youtube.com/watch?v={vid_match.group(1)}")
                    else:
                        raise
                except Exception as e:
                    content = {"title": url, "description": "", "transcript": "", "tags": [], "chapters": [], "channel": ""}
                    await _push(q, {"step": "extract", "status": "warn", "msg": f"No se pudo extraer transcript: {e}. Continuando con el título."})

        # Idioma del contenido (gobierna posts, overlay y voz en off). La lógica
        # vive en lang_detect: metadatos del video/subtítulos + heurística sobre el
        # texto, con la heurística vetando metadatos mal etiquetados.
        lang, lang_source = ld.resolve_lang(forced_lang, content)

        content["lang"] = lang
        job["content"] = content
        params["lang"] = lang

        # La transcripción puede venir vacía sin que nada falle: el extractor se traga
        # el fallo de subtítulos y devuelve el video con título y descripción. Sin ella
        # los posts y TODOS los prompts visuales salen del título, así que se dice acá
        # en vez de que se descubra al mirar la imagen generada.
        if not (content.get("transcript") or "").strip():
            motivo = (content.get("transcript_error") or "").strip()
            detalle = f" ({motivo})" if motivo else ""
            _avisar(job, "content", "alto",
                    f"El video no dejó transcripción{detalle}: los textos y todos los prompts "
                    "visuales se escriben solo con el título y la descripción. Revísalos con "
                    "cuidado, o usa otra fuente.")
            await _push(q, {"step": "extract", "status": "warn",
                            "msg": f"Sin transcripción{detalle}: se escribe solo con título y descripción."})

        await _push(q, {"step": "extract", "status": "done",
                        "msg": f"Idioma detectado: {lang} ({lang_source}) | {content.get('title', '')[:60]}"})

        # ── Step 2: Accounts ─────────────────────────────────────────────
        await _push(q, {"step": "accounts", "status": "running", "msg": "Verificando cuentas..."})

        # Precedence: account picked in the UI form > .env default > first listed account.
        li_account_id = params.get("linkedin_account_id") or cfg.linkedin_account_id
        ig_account_id = params.get("instagram_account_id") or cfg.instagram_account_id
        fb_account_id = params.get("facebook_account_id") or cfg.facebook_account_id
        tk_account_id = params.get("tiktok_account_id") or cfg.tiktok_account_id

        if do_linkedin and not li_account_id:
            try:
                accounts = await _run(bc.get_accounts, "linkedin", api_key=cfg.blotato_api_key)
                if accounts:
                    li_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta LinkedIn: {e}"})

        if do_instagram and not ig_account_id:
            try:
                accounts = await _run(bc.get_accounts, "instagram", api_key=cfg.blotato_api_key)
                if accounts:
                    ig_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta Instagram: {e}"})

        if do_facebook and not fb_account_id:
            try:
                accounts = await _run(bc.get_accounts, "facebook", api_key=cfg.blotato_api_key)
                if accounts:
                    fb_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta Facebook: {e}"})

        if do_tiktok and not tk_account_id:
            try:
                accounts = await _run(bc.get_accounts, "tiktok", api_key=cfg.blotato_api_key)
                if accounts:
                    tk_account_id = str(accounts[0]["id"])
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener cuenta TikTok: {e}"})

        # LinkedIn page id (a "subaccount") is optional — empty means personal profile.
        # Facebook posts always target a Page (its pageId is a subaccount too): Blotato
        # rechaza el post sin pageId ("body.post.target must have required property
        # 'pageId'"). Si el form/sheet no eligió una Página, auto-resolvemos la primera
        # de la cuenta (igual que con los account_id de arriba).
        li_page_id = params.get("linkedin_page_id") or ""
        fb_page_id = params.get("facebook_page_id") or ""
        if do_facebook and fb_account_id and not fb_page_id:
            try:
                subs = await _run(bc.get_subaccounts, fb_account_id, api_key=cfg.blotato_api_key)
                if subs:
                    fb_page_id = str(subs[0].get("id", "")) or fb_page_id
            except Exception as e:
                await _push(q, {"step": "accounts", "status": "warn", "msg": f"No se pudo obtener la Página de Facebook: {e}"})
        job["accounts"] = {
            "linkedin_id": li_account_id, "linkedin_page_id": li_page_id,
            "instagram_id": ig_account_id,
            "facebook_id": fb_account_id, "facebook_page_id": fb_page_id,
            "tiktok_id": tk_account_id,  # TikTok no tiene páginas/subcuentas
        }
        await _push(q, {"step": "accounts", "status": "done", "msg": "Cuentas configuradas"})

        # ── Step 3: Resolve tone/objective ───────────────────────────────
        tono_li = params.get("tono_linkedin") or params.get("tono") or "educativo"
        tono_ig = params.get("tono_instagram") or params.get("tono") or "inspiracional"
        tono_fb = params.get("tono_facebook") or params.get("tono") or "personal"
        obj_li = params.get("objetivo_linkedin") or params.get("objetivo") or "engagement"
        obj_ig = params.get("objetivo_instagram") or params.get("objetivo") or "engagement"
        obj_fb = params.get("objetivo_facebook") or params.get("objetivo") or "engagement"

        params.update({
            "tono_linkedin": tono_li,
            "tono_instagram": tono_ig,
            "tono_facebook": tono_fb,
            "objetivo_linkedin": obj_li,
            "objetivo_instagram": obj_ig,
            "objetivo_facebook": obj_fb,
            "formato_instagram": formato_ig,
        })

        # ── Step 4/4.5: Write + humanize posts ───────────────────────────
        if cfg.llm_provider == "perplexity":
            writer_label = params.get("modelo_perplexity") or "sonar-pro"
        else:
            writer_label = "Claude"
        await _push(q, {"step": "writing", "status": "running", "msg": f"Escribiendo posts con {writer_label}..."})

        # La URL limpia se guarda en el job porque el reintento manual de la escritura
        # (`rewrite_job_posts`, desde la compuerta previa) la necesita fuera de aquí.
        job["_clean_url"] = clean_url
        posts, writer_usage, writer_avisos = await write_posts(content, params, clean_url, q, cfg)
        job["posts"] = posts
        # El escritor avisa cuando no entregó los campos visuales del contrato (JSON
        # roto o campos vacíos, ya con una reparación dirigida encima). Sin esto el
        # preview aparecía sin prompts y nada decía por qué.
        for aviso in writer_avisos:
            _avisar(job, aviso["campo"], aviso["nivel"], aviso["mensaje"])
            await _push(q, {"step": "writing", "status": "warn", "msg": aviso["mensaje"]})
        await _push(q, {"step": "writing", "status": "done", "msg": "Posts escritos y humanizados"})

        # Tracking de costos del LLM (tokens de entrada/salida + caché en Claude).
        if writer_usage:
            await _track(job, service=writer_usage["service"], operation="post_writing",
                         units=writer_usage["units"], model=writer_usage["model"])
    except Exception as e:
        job["status"] = "error"
        job["error_msg"] = str(e)
        await _push(q, {"step": "error", "msg": str(e)})
        return

    # ── Preview gate (los DOS flujos): pausa para revisión editable de los prompts y
    # textos ANTES de gastar créditos generando imágenes/video. En bulk, `run_batch`
    # recoge esta pausa y deja el lote entero esperando en estado "preview".
    if _wants_preview(job):
        job["status"] = "preview"
        await _push(q, {"step": "preview", "redirect": f"/jobs/{job['id']}/preview"})
        return

    await _run_media_phase(job)


async def rewrite_job_posts(job: dict) -> list[dict]:
    """Reintento MANUAL de la escritura desde la compuerta previa (los dos flujos).

    Vuelve a pedirle al LLM SOLO los campos que falten —captions incluidos: una red
    destino sin texto publicaría un post vacío— y los funde sobre lo que ya hay, así
    lo editado a mano y lo que sí llegó no se tocan. Es lo que permite recuperarse
    del aviso «la escritura no entregó N campos» sin relanzar el post entero
    (individual) ni la fila (lote). Devuelve los avisos que queden.
    """
    posts, usage, avisos = await rewrite_posts(
        job["content"], job["params"], job["posts"], job["_cfg"],
        job.get("_clean_url", ""),
    )
    job["posts"] = posts
    # El aviso de escritura se reemplaza entero: el viejo describe un estado que este
    # reintento acaba de cambiar, y dejarlo pegado diría que faltan campos que ya están.
    job["avisos"] = [a for a in job.get("avisos", []) if a.get("campo") != "escritura"]
    for aviso in avisos:
        _avisar(job, aviso["campo"], aviso["nivel"], aviso["mensaje"])
    if usage:
        await _track(job, service=usage["service"], operation="post_writing",
                     units=usage["units"], model=usage["model"])
    return avisos


async def resume_media(job: dict):
    """Reanuda tras el preview editable: corre la fase de media (endpoint /generate)."""
    job["status"] = "running"
    # La fase A ya corrió (extract/accounts/writing). Re-emitir su "done" para que la
    # barra de progreso del segundo tramo no los muestre pendientes (los eventos se
    # bufferean en la cola hasta que el nuevo stream SSE se conecta).
    q: asyncio.Queue = job["_queue"]
    for step in ("extract", "accounts", "writing"):
        await _push(q, {"step": step, "status": "done"})
    await _run_media_phase(job)


async def _run_media_phase(job: dict):
    """Fase 2 del pipeline: genera el medio (imágenes/video) y deja el job en review.

    Recalcula sus locales desde el job (params/content/posts) para poder correrse
    tanto de corrido (bulk) como reanudada tras el preview editable (individual).
    """
    q: asyncio.Queue = job["_queue"]
    params: dict = job["params"]
    cfg = job["_cfg"]

    source_type = params.get("source_type", "youtube")
    dry_run = params.get("dry_run", False)
    formato_ig = params.get("formato_instagram", "imagen-unica")
    tipo_post = params.get("tipo_post", "post")
    media_origin = params.get("media_origin", "generar")
    historia_formato = params.get("historia_formato", "imagen")

    # Redes destino (misma fuente única que la fase A).
    nets = active_networks(params)
    do_linkedin = "linkedin" in nets
    do_instagram = "instagram" in nets
    do_facebook = "facebook" in nets
    do_tiktok = "tiktok" in nets

    content = job["content"]
    posts = job["posts"]
    # El tono de cada red vive en los textos, que ya escribió la fase A: la fase de
    # medios no lo necesita desde que el copy de la imagen lo renderiza el modelo.
    lang = params.get("lang") or content.get("lang", "es")

    try:
        # ── Media: medio final subido por el usuario (reel/historia, modo "subir") ──
        # El video/imagen ya está hecho: se sube tal cual a Blotato y se publica sin
        # generación. Reel/historia aplican a Instagram y Facebook (LinkedIn se filtró).
        if media_origin == "subir":
            await _push(q, {"step": "images", "status": "running", "msg": "Subiendo tu archivo a Blotato..."})
            fbytes = job.get("_final_media_bytes") or b""
            fname = job.get("_final_media_filename") or "media"
            if not fbytes:
                raise RuntimeError("No se recibió el archivo a publicar (modo subir).")
            mime = _media_mime(fname)
            try:
                url_media = await _run(bc.upload_media_local, fbytes, fname, api_key=cfg.blotato_api_key, mime=mime)
            except Exception as e:
                raise RuntimeError(f"No se pudo subir el archivo a Blotato: {e}")
            job["_ig_media_urls"] = [url_media] if do_instagram else []
            job["_fb_media_urls"] = [url_media] if do_facebook else []
            job["_tk_media_urls"] = [url_media] if do_tiktok else []
            job["images"]["blotato_urls"]["instagram"] = [url_media] if do_instagram else []
            job["images"]["blotato_urls"]["facebook"] = url_media if do_facebook else ""
            job["images"]["blotato_urls"]["tiktok"] = url_media if do_tiktok else ""
            # Si es video, refléjalo también en job["video"] para que la revisión lo muestre.
            if mime.startswith("video/"):
                job["video"]["provider"] = "subido"
                job["video"]["url"] = url_media
            await _push(q, {"step": "images", "status": "done", "msg": "Archivo listo"})
            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Media decision: video por segmentos (fotos o text-to-video) OR images ──
        tipo_medio = params.get("tipo_medio", "imagen")
        # Reel y historia-video siempre son video vertical 9:16.
        is_reel = tipo_post == "reel"
        is_historia_video = tipo_post == "historia" and historia_formato == "video"
        is_historia_imagen = tipo_post == "historia" and historia_formato == "imagen"
        # Segundos por segmento generado (la duración total se logra uniendo N).
        # Se lee de params: run_pipeline lo fijó arriba (10s si el job lleva voz,
        # para calzar con la ventana fija de explainer_video; si no, la config).
        seg_seconds = max(1, int(params.get("video_segment_seconds") or 5))

        # ── Recorrido image-to-video a partir de fotos (modo "fotos", solo-individual) ──
        # Cada par de fotos consecutivas es un segmento de transición (start_image →
        # end_image); se concatenan en un solo reel. Sin fallback: requiere Higgsfield.
        if media_origin == "fotos":
            if not cfg.video_available:
                raise RuntimeError(
                    "El recorrido a partir de fotos requiere Higgsfield (MCP) configurado. "
                    "Conéctalo desde la página Conexiones."
                )
            photos = job.get("_photo_files") or []
            if len(photos) < 2:
                raise RuntimeError("Sube al menos 2 fotos para armar el recorrido.")
            await _push(q, {"step": "video", "status": "running",
                            "msg": f"Preparando {len(photos)} fotos (recorte vertical 9:16)..."})
            # Recortar cada foto a 9:16, hospedarla en Blotato (→ URL pública) e
            # importarla al MCP (→ media_id).
            photo_ids: list[str] = []
            for idx, (pbytes, pfname) in enumerate(photos):
                try:
                    fname = pfname or f"foto-{idx}.jpg"
                    pbytes, fname = await _run(_photo_to_vertical, pbytes, fname)
                    purl = await _run(bc.upload_media_local, pbytes, fname,
                                      api_key=cfg.blotato_api_key, mime=_media_mime(fname))
                    mid = await _run(hfmcp.import_media_url, purl)
                    photo_ids.append(mid)
                except Exception as e:
                    await _push(q, {"step": "video", "status": "warn", "msg": f"No se pudo procesar la foto {idx + 1}: {e}"})
            if len(photo_ids) < 2:
                job["video"]["notice"] = "No se pudieron preparar suficientes fotos para el recorrido."
                await _push(q, {"step": "video", "status": "warn", "msg": job["video"]["notice"]})
                job["_li_media_urls"] = []
                job["_ig_media_urls"] = []
                job["_fb_media_urls"] = []
            else:
                estilo = params.get("camara_estilo", _WALKTHROUGH_DEFAULT)
                prompt = _walkthrough_prompt(estilo)
                segments = [
                    {"prompt": prompt, "medias": [
                        {"value": photo_ids[i], "role": "start_image"},
                        {"value": photo_ids[i + 1], "role": "end_image"},
                    ]}
                    for i in range(min(len(photo_ids) - 1, _MAX_VIDEO_SEGMENTS))
                ]
                await _run_video_segments(job, q, cfg, segments, aspect="9:16", seg_seconds=seg_seconds,
                                          do_linkedin=do_linkedin, do_instagram=do_instagram, do_facebook=do_facebook,
                                          do_tiktok=do_tiktok,
                                          default_model=cfg.higgsfield_mcp_walkthrough_model)
            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Video text-to-video por segmentos (reel/historia-video o tipo_medio=video) ──
        want_video = tipo_medio == "video" or is_reel or is_historia_video
        # Aspect del clip: 9:16 para reel/historia-video; el default de feed en lo demás.
        video_aspect = "9:16" if (is_reel or is_historia_video) else cfg.higgsfield_video_aspect
        if want_video and not cfg.video_available:
            if is_reel or is_historia_video:
                # En reel/historia-video el video es obligatorio: no hay fallback a imagen.
                raise RuntimeError(
                    "Este formato requiere video, pero Higgsfield no está configurado. "
                    "Define las credenciales de Higgsfield o sube tu propio video."
                )
            want_video = False
            await _push(q, {"step": "video", "status": "warn",
                            "msg": "Video solicitado pero Higgsfield no está configurado — se generan imágenes."})

        if want_video:
            await _push(q, {"step": "video", "status": "running", "msg": "Preparando el guion del video..."})
            # Storyboard del LLM (N shots concretos anclados a la transcripción) → N
            # segmentos. Si falta, 1 segmento con el video_prompt (o una escena genérica
            # del título como última red). El "sin texto" se anexa por segmento.
            storyboard = [s for s in (posts.get("video_storyboard") or []) if s.strip()]
            if storyboard:
                beats = storyboard[:_MAX_VIDEO_SEGMENTS]
            else:
                scene = (posts.get("video_prompt") or "").strip() or _generic_scene(content)
                beats = [scene]
            # Un guion más corto que la duración pedida da un reel corto sin que nadie
            # lo diga (el LLM devolvió menos shots, o se borraron líneas en el preview).
            wanted = min(_segments_needed(params), _MAX_VIDEO_SEGMENTS)
            if len(beats) < wanted:
                _video_warn(job, f"El guion trae {len(beats)} shot(s) para los {wanted} que "
                                 f"pedía la duración: el video dura ~{len(beats) * seg_seconds}s.")
            # El look compartido (video_style) se anexa a CADA beat: es lo que hace
            # que los segmentos, generados por separado, corten como un solo video.
            video_style = (posts.get("video_style") or "").strip()
            segments = [{"prompt": _segment_prompt(b, video_style), "medias": None} for b in beats]
            # Guion de voz del LLM (una línea hablada por shot, en el idioma de los
            # posts). Si falta o REEL_VOICEOVER está apagado, el reel sale mudo.
            voiceover = [v for v in (posts.get("video_voiceover") or []) if v.strip()][:len(beats)]
            await _run_video_segments(job, q, cfg, segments, aspect=video_aspect, seg_seconds=seg_seconds,
                                      do_linkedin=do_linkedin, do_instagram=do_instagram, do_facebook=do_facebook,
                                      do_tiktok=do_tiktok, voiceover=voiceover)
            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Media: historia-imagen (una sola imagen vertical 9:16 con overlay) ──
        # La misma imagen se comparte entre Instagram y Facebook (una Story acepta
        # una imagen en ambas redes; LinkedIn quedó filtrado por el formato).
        if is_historia_imagen:
            await _push(q, {"step": "images", "status": "init", "subkeys": ["ig-story"]})
            await _push(q, {"step": "images", "status": "running", "msg": "Generando imagen vertical para la historia..."})
            force_template = params.get("fuente_imagen", "higgsfield") == "template"
            img_model = _job_model(job, "modelo_imagen", cfg.higgsfield_mcp_image_model)
            provider = improv.make_provider(
                force_template=force_template, mcp_image_model=img_model,
            )
            # Hook de portada: el image_text del modelo si vino; si no, la 1ª línea del
            # caption. Mismo helper que usa la regeneración de esta imagen.
            story_hook = _texto_historia(posts)
            con_texto = bool(story_hook) and _text_in_prompt(cfg)
            # Escena anclada a la transcripción (image_prompt del LLM), en vertical.
            base_prompt = _cover_image_prompt(posts, content, vertical=True, con_texto=con_texto)
            aspect_story = hfmcp.image_aspect("9:16", model=img_model)

            async def _prompt_story(refuerzo: bool = False) -> str:
                return await _prompt_para(job, cfg, subkey="ig-story", prompt_base=base_prompt,
                                          posts=posts, content=content, texto=story_hook,
                                          rol="portada", aspect=aspect_story, lang=lang,
                                          refuerzo=refuerzo)

            async def _rehacer_story() -> str:
                return await _run(provider.generate_base, await _prompt_story(True), aspect_ratio="9:16")

            story_url = ""
            try:
                base_url = await _run(provider.generate_base, await _prompt_story(),
                                      aspect_ratio="9:16")
                base_url = await _verificar_texto(job, q, cfg, subkey="ig-story", src=base_url,
                                                  texto=story_hook, rehacer=_rehacer_story)
                if _HAS_OVERLAY:
                    png = await _render_imagen(base_url, cfg=cfg, texto=story_hook,
                                               rol="portada", historia=True,
                                               identidad=_identidad(job))
                    _save_image(job["id"], "ig-story", png)
                    job["images"]["bytes"]["ig-story"] = png
                    story_url = await _run(bc.upload_media_local, png, "ig-story.png", api_key=cfg.blotato_api_key)
                else:
                    # Sin Pillow: publica la base tal cual (URL o plantilla local subida).
                    story_url = await _run(_publishable_media, base_url, "ig-story.png", api_key=cfg.blotato_api_key)
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "subkey": "ig-story", "msg": str(e)})
            # Tracking de costos: generación HF real (si cayó a plantilla local, es gratis).
            job["images"]["provider"] = provider.name
            hf_gens = getattr(provider, "hf_generations", 0)
            if hf_gens:
                await _track(job, service="higgsfield_mcp", operation="image_generation",
                             units={"generations": hf_gens}, model=img_model)
            if story_url:
                job["_ig_media_urls"] = [story_url] if do_instagram else []
                job["_fb_media_urls"] = [story_url] if do_facebook else []
                job["images"]["blotato_urls"]["instagram"] = [story_url] if do_instagram else []
                job["images"]["blotato_urls"]["facebook"] = story_url if do_facebook else ""
                await _push(q, {"step": "images", "status": "done", "subkey": "ig-story"})
                await _push(q, {"step": "images", "status": "done", "msg": "Imagen lista"})
            else:
                await _push(q, {"step": "images", "status": "warn", "msg": "No se pudo generar la imagen de la historia. Puedes reintentar."})
            job["status"] = "review"
            await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})
            return

        # ── Steps 5-7: Images (generate + overlay + upload) ──────────────────

        # Carousel slide count from the form (3–6); slide 0 = hook and every slide
        # after it is an info/argument slide (there is no credits slide any more).
        n_slides = _n_slides(params)

        # El formato aplica a TODAS las redes: en carrusel se genera UN solo juego de
        # slides (subkeys ig-N, nombre histórico) y se comparte con LinkedIn (document
        # carousel de Blotato) y Facebook (post multi-foto). En imagen única cada red
        # recibe su hook con overlay propio (li-hook 4:5, fb-hook 4:5, ig-single 1:1).
        is_carousel = formato_ig == "carrusel"

        expected_subkeys: list[str] = []
        if is_carousel:
            expected_subkeys.extend(f"ig-{i}" for i in range(n_slides))
        else:
            if do_linkedin:
                expected_subkeys.append("li-hook")
            if do_facebook:
                expected_subkeys.append("fb-hook")
            if do_instagram:
                expected_subkeys.append("ig-single")

        # El usuario puede forzar plantillas locales (sin llamar a Higgsfield) o usar
        # el flujo normal (Higgsfield con fallback a plantilla). Default: higgsfield.
        force_template = params.get("fuente_imagen", "higgsfield") == "template"
        img_model = _job_model(job, "modelo_imagen", cfg.higgsfield_mcp_image_model)
        provider = improv.make_provider(
            force_template=force_template, mcp_image_model=img_model,
            template_set=_template_set(params),
        )

        await _push(q, {"step": "images", "status": "init", "subkeys": expected_subkeys})
        await _push(q, {"step": "images", "status": "running", "msg": f"Generando imágenes con {provider.label}..."})

        # image_bytes is mutable — /image/{key} can serve mid-pipeline as soon as a key is set
        image_bytes: dict[str, bytes] = job["images"]["bytes"]
        # raw_urls: provider image source per subkey (URL or local template path),
        # used as upload fallback when overlay/upload fails. Vive en el job (no en un
        # local) porque la regeneración de una imagen suelta vuelve a subir el juego.
        raw_urls: dict[str, str] = job["images"]["raw_urls"]
        # image_warnings: reasons Higgsfield fell back to local templates (empty when not applicable)
        image_warnings: list[str] = []
        # overlay_text_warnings: reasons the overlay copy fell back to heuristics (missing image_text)
        overlay_text_warnings: list[str] = []

        # ── Copy de los visuales (se resuelve ANTES de generar) ───────────────────
        # Antes esto vivía después de la generación, porque el texto se dibujaba
        # encima al final. Ahora el texto viaja DENTRO del prompt, así que hay que
        # saber qué dice cada imagen antes de pedirla.
        # Se prefiere el bloque `image_text` del LLM (una frase de portada cerrada +
        # una idea por slide) y se degrada a las heurísticas de siempre cuando falta.
        # Number of info (argument) slides after the hook: TODOS los slides que
        # siguen a la portada, incluido el último.
        n_info = (n_slides - 1) if is_carousel else 1

        copy_img = _copy_de_imagenes(
            posts, cfg, n_info=n_info, is_carousel=is_carousel,
            hay_redes=do_linkedin or do_instagram or do_facebook,
        )
        slide_texts: list[str] = copy_img["slides"]
        cover_text = copy_img["portada"]
        overlay_text_warnings.extend(copy_img["avisos"])
        texto_en_prompt = _text_in_prompt(cfg)
        aspect_feed = hfmcp.image_aspect(hfmcp.FEED_IMAGE_ASPECT, model=img_model)

        # ── 5a: Base image (shared by LinkedIn, Facebook, IG single, carousel slide 0) ──
        base_url: str | None = None
        if do_linkedin or do_instagram or do_facebook:
            # La escena la escribe el LLM desde la transcripción (image_prompt); acá se
            # le suma la dirección de arte y la composición, y `prompt_architect` lo
            # convierte en el brief de 9 secciones con el texto de portada dentro. El
            # aspecto se pide NATIVO (4:5, el vertical de feed).
            con_texto_portada = bool(cover_text) and texto_en_prompt
            base_scene = _cover_image_prompt(posts, content, con_texto=con_texto_portada)

            async def _prompt_portada(refuerzo: bool = False, sangrado: bool = False) -> str:
                return await _prompt_para(job, cfg, subkey="cover", prompt_base=base_scene,
                                          posts=posts, content=content, texto=cover_text,
                                          rol="portada", aspect=aspect_feed, lang=lang,
                                          refuerzo=refuerzo, refuerzo_sangrado=sangrado)

            async def _rehacer_portada() -> str:
                # Se regenera con `generate_base` (no `generate_one`) a propósito: así el
                # job_id de referencia que heredan los slides es el de la portada BUENA.
                return await _run(provider.generate_base, await _prompt_portada(True),
                                  aspect_ratio=hfmcp.FEED_IMAGE_ASPECT)

            async def _rehacer_portada_sangrado() -> str:
                return await _run(provider.generate_base,
                                  await _prompt_portada(sangrado=True),
                                  aspect_ratio=hfmcp.FEED_IMAGE_ASPECT)

            try:
                base_url = await _run(provider.generate_base, await _prompt_portada(),
                                      aspect_ratio=hfmcp.FEED_IMAGE_ASPECT)
            except Exception as e:
                await _push(q, {"step": "images", "status": "warn", "msg": f"Error generando imagen base: {e}"})
            image_warnings.extend(provider.pop_warnings())
            if base_url:
                base_url = await _verificar_texto(job, q, cfg, subkey="cover", src=base_url,
                                                  texto=cover_text, rehacer=_rehacer_portada)
                # Después del QA de texto: si el texto se tuvo que rehacer, lo que hay
                # que revisar por bandas es la imagen FINAL, no la descartada.
                base_url = await _verificar_bandas(job, q, cfg, subkey="cover", src=base_url,
                                                   rehacer=_rehacer_portada_sangrado)
                image_warnings.extend(provider.pop_warnings())
            # Referencia visual de la portada (su job_id en el MCP). Se guarda en el
            # job porque rehacer un slide desde la revisión crea su propio provider y
            # necesita mirar la MISMA portada que miraron los demás slides.
            job["images"]["reference"] = getattr(provider, "base_reference", "")

        # ── 5b: Pre-warm carousel extra slides immediately in background ──────────
        # The provider starts generating slides 1 & 2 while LinkedIn/IG-0 overlays run.
        extra_prompts: list[str] = []
        extra_handles: list = []
        reference = ""
        # Texto impreso en cada slide extra: una idea del carrusel por slide.
        # Índice paralelo a `extra_prompts` / `extra_handles`.
        extra_texts: list[str] = []
        if is_carousel and base_url:
            # Slides 1..n-1: (n_slides - 1) slides de info. Cada uno sale de su propio
            # prompt del LLM (un detalle distinto de la fuente), dentro del mismo mundo
            # visual que la portada; el último no es la excepción.
            extra_texts = [(slide_texts[i] if i < len(slide_texts) else "") for i in range(n_info)]
            bases = _slide_image_prompts(posts, content, n_info,
                                         con_texto=texto_en_prompt and any(extra_texts),
                                         identidad=_identidad(job))
            for i, base_prompt_slide in enumerate(bases):
                extra_prompts.append(await _prompt_para(
                    job, cfg, subkey=f"ig-{i + 1}", prompt_base=base_prompt_slide, posts=posts,
                    content=content, texto=extra_texts[i] if i < len(extra_texts) else "",
                    rol=_rol_slide(i, n_info), aspect=aspect_feed, lang=lang,
                ))
            # Referencia visual: los slides se generan MIRANDO la portada (su job_id va
            # en `medias`), que es lo que hace que compartan paleta y luz de verdad. Si
            # el modelo no acepta referencias o la portada cayó a plantilla local, queda
            # vacía y se genera como siempre — la coherencia la sostienen entonces la
            # dirección de arte compartida y el grade de más abajo.
            reference = job["images"]["reference"] if cfg.image_reference_slides else ""
            # Start generating slides 1..n-1 now (Higgsfield submits the jobs; the template
            # provider returns immediate handles) so they render while LinkedIn/IG-0 overlays run.
            # raw_urls for these slides are filled in at resolve time, once we have a real src.
            extra_handles = await _run(provider.prewarm_extras, extra_prompts,
                                       aspect_ratio=hfmcp.FEED_IMAGE_ASPECT, reference=reference)

        if not _HAS_OVERLAY:
            await _push(q, {"step": "images", "status": "warn",
                            "msg": "Pillow no instalado — las imágenes van sin recorte por red"})

        # ── 5c: LinkedIn overlay (uses base_url — emits done immediately) ────────
        # En carrusel no hay hook propio por red: LinkedIn/Facebook comparten los
        # slides del carrusel (se suben una sola vez más abajo).
        # Las imágenes que salen de la base son las MISMAS para todas las redes: con
        # el texto dentro del prompt, lo único que hacía distinta a la de LinkedIn de
        # la de Instagram era el copy que se les dibujaba encima. Hoy solo queda el
        # recorte al aspecto del feed, que es común, así que se prepara una vez y se
        # comparte. Se conserva un subkey por red porque cada una publica su medio.
        derivadas = ["ig-0"] if is_carousel else (
            (["li-hook"] if do_linkedin else [])
            + (["fb-hook"] if do_facebook else [])
            + (["ig-single"] if do_instagram else [])
        )
        if derivadas and not base_url:
            for key in derivadas:
                await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": "Sin imagen base"})
        elif derivadas:
            png_base: bytes | None = None
            if _HAS_OVERLAY:
                try:
                    png_base = await _render_imagen(base_url, cfg=cfg, texto=cover_text,
                                                    rol="portada", identidad=_identidad(job))
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": derivadas[0],
                                    "msg": f"No se pudo preparar la imagen: {e}"})
            for key in derivadas:
                raw_urls[key] = base_url
                if png_base is not None:
                    image_bytes[key] = png_base
                    _save_image(job["id"], key, png_base)
                await _push(q, {"step": "images", "status": "done", "subkey": key})

        # Cómo rehacer CADA slide, por subkey. Se va llenando en el bucle de abajo y lo
        # consume el QA de conjunto, que corre cuando el bucle ya terminó y no puede
        # alcanzar sus locales. Cada entrada regenera, vuelve a recortar/igualar y deja
        # el resultado en `image_bytes` — es decir, hace lo mismo que el bucle.
        rehacedores: dict[str, Any] = {}

        if is_carousel:
            # Carousel slides 1..n-1: (n_info) info slides, el último incluido.
            for i, fname in enumerate(f"ig-{s + 1}" for s in range(n_info)):
                if i >= len(extra_handles):
                    await _push(q, {"step": "images", "status": "warn", "subkey": fname, "msg": "Sin imagen base"})
                    continue
                try:
                    slide_url = await _run(provider.resolve, extra_handles[i])
                    image_warnings.extend(provider.pop_warnings())
                    # QA del texto renderizado en ESTE slide. El reintento vuelve a
                    # generar solo este (mismo aspecto y misma referencia a la portada),
                    # con la instrucción de texto reforzada.
                    texto_slide = extra_texts[i] if i < len(extra_texts) else ""
                    # El prompt base del slide se resuelve fuera del `if`: el QA de
                    # bandas también necesita poder rehacerlo, y ese corre lleve o no
                    # texto impreso la pieza.
                    base_slide = _slide_image_prompts(
                        posts, content, n_info, con_texto=bool(texto_slide),
                        identidad=_identidad(job))[i]

                    async def _rehacer_slide(_base=base_slide, _texto=texto_slide,
                                             _key=fname, _rol=_rol_slide(i, n_info),
                                             _texto_ok: bool = False,
                                             _sangrado: bool = False) -> str:
                        prompt = await _prompt_para(
                            job, cfg, subkey=_key, prompt_base=_base, posts=posts,
                            content=content, texto=_texto, rol=_rol, aspect=aspect_feed,
                            lang=lang, refuerzo=_texto_ok, refuerzo_sangrado=_sangrado,
                        )
                        return await _run(provider.generate_one, prompt,
                                          aspect_ratio=hfmcp.FEED_IMAGE_ASPECT,
                                          reference=reference)

                    if texto_slide:
                        slide_url = await _verificar_texto(
                            job, q, cfg, subkey=fname, src=slide_url, texto=texto_slide,
                            rehacer=lambda _r=_rehacer_slide: _r(_texto_ok=True))
                        image_warnings.extend(provider.pop_warnings())
                    slide_url = await _verificar_bandas(
                        job, q, cfg, subkey=fname, src=slide_url,
                        rehacer=lambda _r=_rehacer_slide: _r(_sangrado=True))
                    image_warnings.extend(provider.pop_warnings())
                    raw_urls[fname] = slide_url

                    async def _colocar(src: str, _key=fname, _texto=texto_slide) -> None:
                        """Recorte + grade + guardado de un slide ya generado."""
                        raw_urls[_key] = src
                        if not _HAS_OVERLAY:
                            return
                        png = await _render_imagen(src, cfg=cfg, texto=_texto,
                                                   rol="contenido", identidad=_identidad(job))
                        png = await _match_cover_grade(png, image_bytes.get("ig-0"), cfg)
                        image_bytes[_key] = png
                        _save_image(job["id"], _key, png)

                    await _colocar(slide_url)

                    async def _rehacer_para_el_set(_r=_rehacer_slide, _c=_colocar) -> bool:
                        # Sin refuerzos: el slide no está mal escrito ni tiene banda —
                        # rompe el SET, y eso se corrige con otra tirada del mismo brief.
                        nuevo = await _r()
                        if not nuevo:
                            return False
                        await _c(nuevo)
                        return True

                    rehacedores[fname] = _rehacer_para_el_set
                    await _push(q, {"step": "images", "status": "done", "subkey": fname})
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": fname, "msg": str(e)})

            # ── QA de conjunto: las N piezas, juntas ──────────────────────────
            # Ningún QA por imagen puede ver que cinco piezas no se parecen entre sí.
            # Corre acá —después del bucle y ANTES de subir— sobre los bytes que se van
            # a publicar. La portada NO se rehace aunque salga marcada: funda el set y
            # es la referencia de los slides ya generados, así que rehacerla dejaría a
            # los demás persiguiendo una imagen que ya no existe.
            async def _rehacer_outlier(subkey: str) -> bool:
                fn = rehacedores.get(subkey)
                if fn is None:
                    await _push(q, {"step": "images", "status": "warn", "subkey": subkey,
                                    "msg": "Esta pieza rompe el set pero no se rehace sola "
                                           "(la portada funda el set): rehazla desde la revisión."})
                    return False
                return await fn()

            await _verificar_conjunto(job, q, cfg,
                                      claves=[f"ig-{i}" for i in range(n_slides)],
                                      rehacer=_rehacer_outlier)

        # Catch-all: warn any expected subkey that never received a status event
        for key in expected_subkeys:
            if key not in image_bytes and key not in raw_urls:
                await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": "No se pudo generar"})

        # ── 5e: Upload ────────────────────────────────────────────────────────────
        await _subir_imagenes(job, q, cfg, is_carousel=is_carousel, n_slides=n_slides,
                              do_linkedin=do_linkedin, do_instagram=do_instagram,
                              do_facebook=do_facebook)

        # Surface warnings — live in the progress step and durably (stored on the
        # job → shown on the review screen). Two independent kinds can co-occur:
        # (a) Higgsfield fell back to local templates; (b) the overlay copy fell back
        # to heuristics because the LLM's image_text was missing/short. Accumulate both.
        # Count what actually got produced (overlaid bytes) — a local template that never
        # got an overlay rendered onto it isn't publishable to Blotato on its own.
        job["images"]["provider"] = provider.name
        # Tracking de costos: solo las generaciones HF reales (las que cayeron a
        # plantilla local son gratis y no las cuenta el provider).
        hf_gens = getattr(provider, "hf_generations", 0)
        if hf_gens:
            await _track(job, service="higgsfield_mcp", operation="image_generation",
                         units={"generations": hf_gens}, model=img_model)
        produced = len({k for k in image_bytes} | {k for k in raw_urls})
        notices: list[str] = []
        if produced == 0:
            notices.append(
                "No se pudo generar ninguna imagen — Higgsfield no respondió y no se "
                "pudo aplicar la plantilla de respaldo. Revisa los créditos de Higgsfield "
                "y que las plantillas estén en api/assets/templates/. "
                "Puedes publicar sin imagen o reintentar."
            )
        elif image_warnings:
            reasons = list(dict.fromkeys(image_warnings))  # dedupe, preserve order
            notices.append(
                f"Higgsfield no disponible ({'; '.join(reasons)}) — "
                f"{produced} imagen(es) generada(s) con plantillas de respaldo."
            )
        if overlay_text_warnings:
            reasons = list(dict.fromkeys(overlay_text_warnings))  # dedupe, preserve order
            notices.append(
                f"El texto sobre las imágenes usó el método de respaldo ({'; '.join(reasons)}) — "
                "revisa que el copy de los visuales se lea bien."
            )
        if notices:
            notice = " ".join(notices)
            job["images"]["notice"] = notice
            await _push(q, {"step": "images", "status": "warn", "msg": notice})
        else:
            await _push(q, {"step": "images", "status": "done", "msg": "Imágenes listas"})

        # ── Done ─────────────────────────────────────────────────────────
        job["status"] = "review"
        await _push(q, {"step": "done", "redirect": f"/jobs/{job['id']}/review"})

    except Exception as e:
        job["status"] = "error"
        job["error_msg"] = str(e)
        await _push(q, {"step": "error", "msg": str(e)})


async def _subir_imagenes(job: dict, q: asyncio.Queue, cfg, *, is_carousel: bool,
                          n_slides: int, do_linkedin: bool, do_instagram: bool,
                          do_facebook: bool) -> None:
    """Sube el juego de imágenes a Blotato y deja las URLs publicables en el job.

    Fuente única de la subida: la corre la fase de imágenes al terminar y la vuelve
    a correr la regeneración de una imagen suelta, para que rehacer un slide deje el
    juego publicable exactamente igual de armado (mismo orden, mismos respaldos).
    Lee `images.bytes` (lo ya renderizado) con respaldo en `images.raw_urls`.
    """
    image_bytes: dict[str, bytes] = job["images"]["bytes"]
    raw_urls: dict[str, str] = job["images"]["raw_urls"]
    li_media_urls: list[str] = []
    ig_media_urls: list[str] = []
    fb_media_urls: list[str] = []

    if is_carousel:
        # Un solo juego de slides subido una vez y compartido por las redes
        # activas (LinkedIn document carousel / IG carousel / FB multi-foto).
        carousel_urls: list[str] = []
        for key in [f"ig-{i}" for i in range(n_slides)]:
            if key in image_bytes:
                try:
                    u = await _run(bc.upload_media_local, image_bytes[key], f"{key}.png", api_key=cfg.blotato_api_key)
                    carousel_urls.append(u)
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    carousel_urls.extend(await _media_fallback(q, raw_urls, key, f"{key}.png", cfg))
            else:
                carousel_urls.extend(await _media_fallback(q, raw_urls, key, f"{key}.png", cfg))

        li_media_urls = list(carousel_urls) if do_linkedin else []
        ig_media_urls = list(carousel_urls) if do_instagram else []
        fb_media_urls = list(carousel_urls) if do_facebook else []
        if do_linkedin and carousel_urls:
            job["images"]["blotato_urls"]["linkedin"] = carousel_urls[0]
        if do_facebook and carousel_urls:
            job["images"]["blotato_urls"]["facebook"] = carousel_urls[0]
        if do_instagram:
            job["images"]["blotato_urls"]["instagram"] = ig_media_urls
    else:
        if do_linkedin:
            key = "li-hook"
            if key in image_bytes:
                try:
                    url_li = await _run(bc.upload_media_local, image_bytes[key], "linkedin-hook.png", api_key=cfg.blotato_api_key)
                    li_media_urls = [url_li]
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    li_media_urls = await _media_fallback(q, raw_urls, key, "linkedin-hook.png", cfg)
            else:
                li_media_urls = await _media_fallback(q, raw_urls, key, "linkedin-hook.png", cfg)
            if li_media_urls:
                job["images"]["blotato_urls"]["linkedin"] = li_media_urls[0]

        if do_facebook:
            key = "fb-hook"
            if key in image_bytes:
                try:
                    url_fb = await _run(bc.upload_media_local, image_bytes[key], "facebook-hook.png", api_key=cfg.blotato_api_key)
                    fb_media_urls = [url_fb]
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    fb_media_urls = await _media_fallback(q, raw_urls, key, "facebook-hook.png", cfg)
            else:
                fb_media_urls = await _media_fallback(q, raw_urls, key, "facebook-hook.png", cfg)
            if fb_media_urls:
                job["images"]["blotato_urls"]["facebook"] = fb_media_urls[0]

        if do_instagram:
            key = "ig-single"
            if key in image_bytes:
                try:
                    u = await _run(bc.upload_media_local, image_bytes[key], "ig-single.png", api_key=cfg.blotato_api_key)
                    ig_media_urls = [u]
                except Exception as e:
                    await _push(q, {"step": "images", "status": "warn", "subkey": key, "msg": f"Upload falló: {e}"})
                    ig_media_urls = await _media_fallback(q, raw_urls, key, "ig-single.png", cfg)
            else:
                ig_media_urls = await _media_fallback(q, raw_urls, key, "ig-single.png", cfg)

            job["images"]["blotato_urls"]["instagram"] = ig_media_urls

    job["_li_media_urls"] = li_media_urls
    job["_ig_media_urls"] = ig_media_urls
    job["_fb_media_urls"] = fb_media_urls


# ── Rehacer UNA imagen desde la revisión ────────────────────────────────────────
#
# Hasta acá, un slide que salía mal costaba rehacer el post entero: la unidad de
# reintento era el job. Esto rehace UNA imagen —mismo prompt, mismo texto y misma
# referencia visual que la primera vez—, vuelve a subir el juego a Blotato y deja
# el resto del set intacto. Vive en el núcleo compartido, así que la revisión del
# individual y la del lote usan el MISMO endpoint (`POST /jobs/{id}/regenerate`).

# Subkeys que salen de la MISMA imagen base: rehacer cualquiera de ellas rehace la
# base, y por lo tanto cambia la imagen de todas las redes del post.
_SUBKEYS_PORTADA = ("ig-0", "li-hook", "fb-hook", "ig-single")


def subkeys_regenerables(job: dict) -> list[str]:
    """Imágenes de este job que la revisión puede rehacer de a una.

    Vacía cuando la unidad de reintento no es una imagen: video (reel), medio
    subido por el usuario o recorrido de fotos. El orden es el de la pieza
    (portada primero), que es como los muestra la revisión.
    """
    params = job["params"]
    if params.get("media_origin", "generar") != "generar":
        return []
    tipo_post = params.get("tipo_post", "post")
    if tipo_post == "reel":
        return []
    if tipo_post == "historia":
        return ["ig-story"] if params.get("historia_formato", "imagen") == "imagen" else []
    if params.get("formato_instagram", "imagen-unica") == "carrusel":
        return [f"ig-{i}" for i in range(_n_slides(params))]
    nets = active_networks(params)
    return [k for k, red in (("li-hook", "linkedin"), ("fb-hook", "facebook"),
                             ("ig-single", "instagram")) if red in nets]


async def regenerate_image(job: dict, subkey: str) -> dict:
    """Rehace UNA imagen del set ya generado y vuelve a subir el juego a Blotato.

    Cuesta una generación (2 créditos con el modelo por defecto). Devuelve
    `{"subkeys": [...], "aviso": str}`: las imágenes que cambiaron y el aviso del
    proveedor si hubo que degradar a plantilla local.

    Rehacer la portada de un post de imagen única cambia la de las TRES redes: las
    tres comparten la misma base y solo se diferencian en el recorte. En carrusel,
    la portada nueva pasa a ser la referencia visual de los slides que se rehagan
    después (los ya generados siguen mirando la anterior, que es la que los hizo).
    """
    params = job["params"]
    cfg = job["_cfg"]
    q: asyncio.Queue = job["_queue"]
    posts = job["posts"]
    content = job["content"]

    if subkey not in subkeys_regenerables(job):
        raise ValueError(f"No se puede rehacer «{subkey}» en este post.")

    lang = params.get("lang") or content.get("lang", "es")
    nets = active_networks(params)
    is_carousel = params.get("formato_instagram", "imagen-unica") == "carrusel"
    n_slides = _n_slides(params)
    n_info = (n_slides - 1) if is_carousel else 1
    texto_en_prompt = _text_in_prompt(cfg)

    copy_img = _copy_de_imagenes(posts, cfg, n_info=n_info, is_carousel=is_carousel)
    img_model = _job_model(job, "modelo_imagen", cfg.higgsfield_mcp_image_model)
    provider = improv.make_provider(
        force_template=params.get("fuente_imagen", "higgsfield") == "template",
        mcp_image_model=img_model, template_set=_template_set(params),
    )
    image_bytes: dict[str, bytes] = job["images"]["bytes"]
    raw_urls: dict[str, str] = job["images"]["raw_urls"]

    es_historia = subkey == "ig-story"
    es_portada = subkey in _SUBKEYS_PORTADA
    pedido = "9:16" if es_historia else hfmcp.FEED_IMAGE_ASPECT
    aspect = hfmcp.image_aspect(pedido, model=img_model)

    await _push(q, {"step": "images", "status": "running", "msg": f"Rehaciendo {subkey}..."})

    # ── Qué dice esta imagen y con qué prompt se pide ─────────────────────────
    # El texto y la escena salen de las MISMAS funciones que la primera generación:
    # rehacer un slide no puede cambiar lo que el slide dice ni su encuadre en la
    # escalera, solo la tirada del modelo.
    if es_historia:
        texto = _texto_historia(posts)
        base_prompt = _cover_image_prompt(posts, content, vertical=True,
                                          con_texto=bool(texto) and texto_en_prompt)
        rol = "portada"
    elif es_portada:
        texto = copy_img["portada"]
        base_prompt = _cover_image_prompt(posts, content,
                                          con_texto=bool(texto) and texto_en_prompt)
        rol = "portada"
    else:
        i = int(subkey.rsplit("-", 1)[1]) - 1
        slides = copy_img["slides"]
        texto = slides[i] if i < len(slides) else ""
        base_prompt = _slide_image_prompts(
            posts, content, n_info, con_texto=texto_en_prompt and any(slides),
            identidad=_identidad(job),
        )[i]
        # El beat sale de la MISMA secuencia que en la primera tirada: rehacer un slide
        # solo puede cambiar la tirada del modelo, nunca su función en el carrusel.
        rol = _rol_slide(i, n_info)

    async def _prompt(refuerzo: bool = False, sangrado: bool = False) -> str:
        return await _prompt_para(job, cfg, subkey=subkey, prompt_base=base_prompt,
                                  posts=posts, content=content, texto=texto, rol=rol,
                                  aspect=aspect, lang=lang, refuerzo=refuerzo,
                                  refuerzo_sangrado=sangrado)

    referencia = job["images"].get("reference", "") if cfg.image_reference_slides else ""

    if es_portada or es_historia:
        # `generate_base` (no `generate_one`) a propósito: el job_id de la portada
        # nueva es el que van a heredar como referencia los slides que se rehagan.
        async def _rehacer(refuerzo: bool = True, sangrado: bool = False) -> str:
            return await _run(provider.generate_base, await _prompt(refuerzo, sangrado),
                              aspect_ratio=pedido)

        src = await _run(provider.generate_base, await _prompt(), aspect_ratio=pedido)
    else:
        async def _rehacer(refuerzo: bool = True, sangrado: bool = False) -> str:
            return await _run(provider.generate_one, await _prompt(refuerzo, sangrado),
                              aspect_ratio=pedido, reference=referencia)

        src = await _run(provider.generate_one, await _prompt(),
                         aspect_ratio=pedido, reference=referencia)

    # Mismos QA que en la generación normal: primero el texto impreso y su recorte,
    # después las bandas sobre la imagen que haya quedado.
    src = await _verificar_texto(job, q, cfg, subkey=subkey, src=src, texto=texto,
                                 rehacer=_rehacer)
    src = await _verificar_bandas(job, q, cfg, subkey=subkey, src=src,
                                  rehacer=lambda: _rehacer(refuerzo=False, sangrado=True))
    avisos = provider.pop_warnings()
    if (es_portada or es_historia) and getattr(provider, "base_reference", ""):
        job["images"]["reference"] = provider.base_reference

    # ── Recorte al aspecto de destino ─────────────────────────────────────────
    # Rehacer la portada de un post de imagen única cambia la de las tres redes:
    # las tres son la misma base con el mismo recorte de feed.
    if es_historia:
        destinos = ["ig-story"]
    elif es_portada and is_carousel:
        destinos = ["ig-0"]
    elif es_portada:
        destinos = [k for k, red in (("li-hook", "linkedin"), ("fb-hook", "facebook"),
                                     ("ig-single", "instagram")) if red in nets]
    else:
        destinos = [subkey]

    png = None
    if _HAS_OVERLAY:
        png = await _render_imagen(src, cfg=cfg, texto=texto, rol=rol, historia=es_historia,
                                   identidad=_identidad(job))
        if not (es_portada or es_historia):
            # El slide nuevo se iguala a la portada, igual que en la generación.
            png = await _match_cover_grade(png, image_bytes.get("ig-0"), cfg)

    cambiados: list[str] = []
    for key in destinos:
        raw_urls[key] = src
        if png is not None:
            image_bytes[key] = png
            _save_image(job["id"], key, png)
        cambiados.append(key)
        await _push(q, {"step": "images", "status": "done", "subkey": key})

    # ── Volver a dejar el juego publicable ────────────────────────────────────
    if es_historia:
        png = image_bytes.get("ig-story")
        if png:
            url = await _run(bc.upload_media_local, png, "ig-story.png", api_key=cfg.blotato_api_key)
        else:
            url = await _run(_publishable_media, src, "ig-story.png", api_key=cfg.blotato_api_key)
        do_instagram, do_facebook = "instagram" in nets, "facebook" in nets
        job["_ig_media_urls"] = [url] if do_instagram else []
        job["_fb_media_urls"] = [url] if do_facebook else []
        job["images"]["blotato_urls"]["instagram"] = [url] if do_instagram else []
        job["images"]["blotato_urls"]["facebook"] = url if do_facebook else ""
    else:
        await _subir_imagenes(job, q, cfg, is_carousel=is_carousel, n_slides=n_slides,
                              do_linkedin="linkedin" in nets, do_instagram="instagram" in nets,
                              do_facebook="facebook" in nets)

    job["images"]["provider"] = provider.name
    hf_gens = getattr(provider, "hf_generations", 0)
    if hf_gens:
        await _track(job, service="higgsfield_mcp", operation="image_generation",
                     units={"generations": hf_gens}, model=img_model)

    aviso = ""
    if avisos:
        aviso = (f"Higgsfield no disponible ({'; '.join(dict.fromkeys(avisos))}) — "
                 "se usó la plantilla de respaldo.")
        await _push(q, {"step": "images", "status": "warn", "subkey": subkey, "msg": aviso})
    return {"subkeys": cambiados, "aviso": aviso}


# ── Publishing ──────────────────────────────────────────────────────────────────

def _post_url(status: dict) -> str | None:
    """Extract the published-post permalink from a Blotato post-status response.

    Blotato exposes the link as `publicUrl` on a published post (per the API
    reference). Some shapes nest the state under `state` and/or use `postUrl`;
    we check the known variants so the "Ver publicación" link is robust.
    """
    if not isinstance(status, dict):
        return None
    for key in ("publicUrl", "postUrl", "url"):
        val = status.get(key)
        if val:
            return val
    state = status.get("state")
    if isinstance(state, dict):
        for key in ("publicUrl", "postUrl", "url"):
            val = state.get(key)
            if val:
                return val
    return None


async def publish_job_posts(job: dict, schedule_time: str | None = "") -> dict:
    """Publish (or schedule) a job's already-generated posts to Blotato.

    Shared by the single-post publish endpoint and the bulk batch runner. Respects
    `params.redes` (the set of target networks) and `params.dry_run` (generate but
    don't publish). `schedule_time` is an ISO-8601 string for deferred publishing,
    or empty/None to publish now. Sets `job["result"]`/`job["status"]` and returns
    the result dict.
    """
    cfg = job["_cfg"]
    posts = job["posts"]
    accounts = job["accounts"]
    params = job["params"]
    dry_run = params.get("dry_run", False)

    # Redes destino (default: las tres). Fuente única compartida con run_pipeline.
    nets = active_networks(params)
    do_linkedin = "linkedin" in nets
    do_instagram = "instagram" in nets
    do_facebook = "facebook" in nets
    do_tiktok = "tiktok" in nets

    li_text = posts.get("linkedin_text", "")
    ig_text = posts.get("instagram_text", "")
    fb_text = posts.get("facebook_text", "")
    # TikTok reutiliza el caption corto de reel: el de Instagram si existe, si no el de
    # otra red (post_writer escribe instagram_text cuando TikTok está activo — ver
    # _user_message). Evita un post de TikTok sin texto si solo se eligió TikTok.
    tk_text = posts.get("instagram_text", "") or posts.get("facebook_text", "") or posts.get("linkedin_text", "")
    li_media = job.get("_li_media_urls", [])
    ig_media = job.get("_ig_media_urls", [])
    fb_media = job.get("_fb_media_urls", [])
    tk_media = job.get("_tk_media_urls", [])

    result: dict = {}
    scheduled_at = (schedule_time or "").strip() or None

    if not dry_run and do_linkedin and accounts.get("linkedin_id") and li_text:
        try:
            resp = await _run(bc.publish_post, accounts["linkedin_id"], "linkedin", li_text, li_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                              page_id=accounts.get("linkedin_page_id") or None)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["linkedin"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["linkedin"] = {"error": str(e)}

    if not dry_run and do_instagram and accounts.get("instagram_id") and ig_text:
        # tipo_post → mediaType de IG: reel/historia publican como Reel/Story; post = feed.
        ig_media_type = {"reel": "reel", "historia": "story"}.get(params.get("tipo_post", "post"))
        try:
            resp = await _run(bc.publish_post, accounts["instagram_id"], "instagram", ig_text, ig_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at, share_to_feed=True,
                              media_type=ig_media_type)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["instagram"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["instagram"] = {"error": str(e)}

    if not dry_run and do_facebook and accounts.get("facebook_id") and fb_text:
        # tipo_post → mediaType de FB: reel/historia publican como Reel/Story. Además,
        # un video de feed también va como "reel": Blotato/Facebook ya no aceptan
        # videos de feed normales ("regular feed videos no longer supported").
        fb_media_type = {"reel": "reel", "historia": "story"}.get(params.get("tipo_post", "post"))
        if fb_media_type is None and fb_media and fb_media[0] == (job.get("video") or {}).get("url"):
            fb_media_type = "reel"
        try:
            resp = await _run(bc.publish_post, accounts["facebook_id"], "facebook", fb_text, fb_media,
                              api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                              page_id=accounts.get("facebook_page_id") or None,
                              media_type=fb_media_type)
            status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
            result["facebook"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
        except Exception as e:
            result["facebook"] = {"error": str(e)}

    # TikTok solo publica video (reels). Su `target` exige privacidad + flags de
    # disclosure completos (ver TIKTOK_TARGET_DEFAULTS en blotato_client). El único
    # que depende del job es `isAiGenerated`: solo el video que sube el usuario
    # ("subir") no lo genera la app.
    if not dry_run and do_tiktok and accounts.get("tiktok_id") and tk_text:
        if not tk_media:
            result["tiktok"] = {"error": "TikTok necesita un video y este post no tiene medio."}
        else:
            try:
                resp = await _run(bc.publish_post, accounts["tiktok_id"], "tiktok", tk_text, tk_media,
                                  api_key=cfg.blotato_api_key, schedule_time=scheduled_at,
                                  tiktok_options={"isAiGenerated": params.get("media_origin", "generar") != "subir"})
                status = await _run(bc.poll_post_status, resp["postSubmissionId"], api_key=cfg.blotato_api_key)
                result["tiktok"] = {"submission_id": resp["postSubmissionId"], "status": status.get("status"), "url": _post_url(status)}
            except Exception as e:
                result["tiktok"] = {"error": str(e)}

    if dry_run:
        result["dry_run"] = True
        result["linkedin"] = {"status": "dry-run"}
        result["instagram"] = {"status": "dry-run"}
        result["facebook"] = {"status": "dry-run"}
        if do_tiktok:
            result["tiktok"] = {"status": "dry-run"}

    job["result"] = result
    job["status"] = "done"
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_from_text(text: str, *, default_title: str) -> dict:
    """Build the pipeline `content` dict from a plain transcript/document.

    Shared by the audio (transcription) and text-file sources. The title is the
    first non-empty line (trimmed); the rest is treated as the transcript so the
    post writer has the full material. No tags/chapters/channel are available.
    """
    text = (text or "").strip()
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    title = (first_line[:120] or default_title).strip()
    return {
        "title": title,
        "description": "",
        "transcript": text,
        "summary": "",
        "keyPoints": [],
        "tags": [],
        "chapters": [],
        "channel": "",
    }


def _extract_hook(text: str, max_words: int = 12) -> str:
    if not text:
        return ""
    first_line = text.strip().split("\n")[0].strip()
    words = first_line.split()
    return " ".join(words[:max_words])


def _extract_body_lines(text: str, max_lines: int = 3) -> list[str]:
    """Heuristic fallback for info-slide copy: real caption lines, one idea each.

    Returns up to `max_lines` lines (possibly fewer / empty). No generic filler —
    the renderer pads missing slides with empty text instead of a canned phrase.
    """
    lines = [l.strip().lstrip("•→-* ") for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    # Skip the first line (hook) and grab up to max_lines body lines
    body = [l for l in lines[1:] if not l.startswith("▶") and not l.startswith("#") and len(l) > 10]
    return body[:max_lines]
