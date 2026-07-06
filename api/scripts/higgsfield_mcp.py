"""
Cliente de generación vía el MCP oficial de Higgsfield (https://mcp.higgsfield.ai/mcp).

A diferencia de `higgsfield_client.py` (Cloud API, key+secret, pool de créditos
propio y vacío), este cliente autentica por **OAuth contra la cuenta del usuario**,
así que la generación consume los créditos de la **suscripción** (App). Confirmado
empíricamente: 1 imagen con `nano_banana_pro` gasta 2 créditos del plan Plus.

Interfaz espejo de `higgsfield_client.py` para que el provider/pipeline casi no
cambien (todas SÍNCRONAS y bloqueantes, como las del cliente viejo):

    generate_image(prompt) -> [url]     submit + poll, bloquea hasta la URL
    submit_image(prompt)   -> handle    solo submit (para el prewarm concurrente)
    poll_image(handle)     -> url        espera un handle enviado
    generate_video(prompt) -> url        submit + poll de video, bloquea

Patrón del MCP (descubierto en el spike):
    generate_image/generate_video  ->  {"results": [{"id", "status": "pending", ...}]}
    job_status {"jobId", "sync": true}  ->  {"generation": {"status", "results": {"rawUrl"}}}
      (sync=true hace que el server espere hasta ~25s por estado terminal)
    Sin créditos  ->  {"recovery_tool": "show_plans_and_credits"} (no rompe, lo tratamos como error)
    Prompt parecido a un preset -> sin "results", con {"notice": {"type": "preset_recommendation",
      "data": {"preset": ...}}} — se rechaza solo (declined_preset_id) y se reintenta literal

Puente sync↔async: el SDK `mcp` es asíncrono; corremos un event loop asyncio en un
thread daemon y marshaleamos cada operación con run_coroutine_threadsafe. job_runner
llama estas funciones dentro de su executor (`_run`), igual que al cliente viejo.
Un único OAuthClientProvider (con lock interno) refresca el token solo; si durante
la generación hace falta re-consentir, se lanza un error claro y se cae a las
plantillas. El consentimiento se hace desde la página Conexiones de la web
(start_web_auth/finish_web_auth, abajo) o por terminal con `scripts/mcp_bootstrap.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

# ── Configuración ─────────────────────────────────────────────────────────────

MCP_URL = "https://mcp.higgsfield.ai/mcp"
# Raíz del host: solo para armar los endpoints OAuth (issuer/authorize/token).
# Al provider se le pasa MCP_URL COMPLETA (con /mcp): el SDK valida por RFC 8707
# que el `resource` de la metadata protegida (https://.../mcp) matchee el
# server_url — con la raíz pelada el flujo muere en "Protected resource ...
# does not match expected ..." y la re-autenticación nunca puede completarse.
SERVER_URL = "https://mcp.higgsfield.ai"
REDIRECT_URI = "http://localhost:3030/callback"

# Endpoints OAuth reales (según {SERVER_URL}/.well-known/oauth-authorization-server).
# Se siembran en el context del provider porque el discovery del SDK solo corre en
# el flujo interactivo (tras un 401): sin metadata, el refresh proactivo caería al
# fallback urljoin(base, "/token"), que en Higgsfield no existe.
_OAUTH_ENDPOINTS = {
    "issuer": SERVER_URL,
    "authorization_endpoint": f"{SERVER_URL}/oauth2/authorize",
    "token_endpoint": f"{SERVER_URL}/oauth2/token",
}

# Modelos por defecto (IDs reales del catálogo del MCP). Sobrescribibles por env.
#   nano_banana_pro: confirmado (2 créditos/imagen). Otras opciones: z_image (rápido/
#   barato), soul_location (fondos/escenas), recraft_v4_1 (marca/producto, soporta 4:5).
DEFAULT_IMAGE_MODEL = "nano_banana_pro"
DEFAULT_IMAGE_ASPECT = "1:1"
# TODO: confirmar con `mcp_bootstrap.py --test-video`. kling3_0_turbo es el
# text-to-video rápido citado en la descripción de generate_video.
DEFAULT_VIDEO_MODEL = "kling3_0_turbo"
DEFAULT_VIDEO_ASPECT = "9:16"

# Token store: web/api/.hf_oauth.json (lo genera mcp_bootstrap.py). Configurable.
_DEFAULT_TOKEN_STORE = Path(__file__).resolve().parent.parent / ".hf_oauth.json"

_POLL_DEADLINE = 240       # imagen ~10-40s; margen holgado
_VIDEO_POLL_DEADLINE = 600  # video ~60-180s
# Estados terminales de fallo (el resto se considera "en progreso").
_TERMINAL_FAIL = {"failed", "canceled", "cancelled", "nsfw", "ip_detected", "ip_detect"}

_HTTP_TIMEOUT = 60          # por request no-SSE
_SSE_READ_TIMEOUT = 900     # el job_status sync puede colgar hasta ~25s server-side


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def image_model() -> str:
    return _env("HIGGSFIELD_MCP_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)


def video_model() -> str:
    return _env("HIGGSFIELD_MCP_VIDEO_MODEL", DEFAULT_VIDEO_MODEL)


def token_store_path() -> Path:
    return Path(_env("HIGGSFIELD_MCP_TOKEN_STORE", str(_DEFAULT_TOKEN_STORE)))


def is_configured() -> bool:
    """True si existe un token store — condición para usar el backend MCP.

    El consentimiento OAuth se hace una vez con scripts/mcp_bootstrap.py; sin ese
    archivo no hay sesión y no tiene sentido elegir este backend.
    """
    return token_store_path().exists()


# ── Token store persistente (escritura atómica) ───────────────────────────────

class FileTokenStorage(TokenStorage):
    """Persiste tokens + client_info en un JSON de disco (mismo formato que el spike).

    Escritura atómica (tmp + replace): el refresh token rota y es de un solo uso.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        d = self._read()
        d["tokens"] = tokens.model_dump(mode="json")
        # expires_in es relativo: sin el instante de emisión, un proceso nuevo no
        # puede saber si el token guardado sigue vivo (ver _seed_token_expiry).
        d["issued_at"] = time.time()
        self._write(d)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        d = self._read()
        d["client_info"] = client_info.model_dump(mode="json")
        self._write(d)

    def token_info(self) -> tuple[float, int] | None:
        """(issued_at, expires_in) del token guardado, o None si no hay/faltan datos.

        Para stores escritos antes de que guardáramos issued_at, cae al mtime del
        archivo (la escritura atómica lo hace un proxy fiel del momento de emisión).
        """
        d = self._read()
        tok = d.get("tokens") or {}
        expires_in = tok.get("expires_in")
        if not tok.get("access_token") or not expires_in:
            return None
        issued = d.get("issued_at")
        if not issued:
            try:
                issued = self._path.stat().st_mtime
            except OSError:
                return None
        return float(issued), int(expires_in)

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return {}

    def _write(self, d: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2), "utf-8")
        tmp.replace(self._path)


class ReauthRequired(RuntimeError):
    """El token expiró/rotó y no se pudo refrescar: hace falta re-consentir."""


_REAUTH_HINT = "Reconectá desde la página Conexiones de la web (o corré: python scripts/mcp_bootstrap.py)"


async def _reauth_redirect(url: str) -> None:  # pragma: no cover - camino de error
    raise ReauthRequired(f"Higgsfield MCP: reautenticación requerida. {_REAUTH_HINT}")


async def _reauth_callback() -> tuple[str, str | None]:  # pragma: no cover
    raise ReauthRequired(f"Higgsfield MCP: reautenticación requerida. {_REAUTH_HINT}")


def _build_oauth() -> OAuthClientProvider:
    metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": "AIMA web",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid email offline_access",
        }
    )
    # En runtime NO hay consentimiento interactivo: si el refresh falla, los handlers
    # lanzan ReauthRequired en vez de intentar abrir un navegador en el server.
    storage = FileTokenStorage(token_store_path())
    provider = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_reauth_redirect,
        callback_handler=_reauth_callback,
    )
    _seed_token_expiry(provider, storage)
    return provider


def _seed_token_expiry(provider: OAuthClientProvider, storage: FileTokenStorage) -> None:
    """Siembra en el context del SDK la expiración real del token guardado.

    El SDK solo conoce la expiración de tokens que él mismo obtuvo en este proceso:
    al recargar del disco deja token_expiry_time=None y eso cuenta como "válido para
    siempre". Como Higgsfield reporta el token vencido como error in-band (HTTP 200,
    no 401), el refresh reactivo tampoco corre, y el proceso queda mandando un token
    muerto eternamente ("Invalid or expired token" / "Something went wrong").
    Con la expiración sembrada, el primer request ya dispara el refresh (que persiste
    los tokens rotados vía set_tokens). El margen de 60s evita vencer en vuelo.
    """
    provider.context.oauth_metadata = OAuthMetadata.model_validate(_OAUTH_ENDPOINTS)
    info = storage.token_info()
    if info:
        issued_at, expires_in = info
        provider.context.token_expiry_time = issued_at + expires_in - 60


# ── Event loop de fondo (thread daemon) ───────────────────────────────────────

_loop: asyncio.AbstractEventLoop | None = None
_oauth: OAuthClientProvider | None = None
_start_lock = threading.Lock()


def _ensure_loop() -> None:
    """Arranca (una vez) el loop de fondo y construye el OAuthClientProvider en él.

    Construir el provider DENTRO del loop asegura que su anyio.Lock interno quede
    ligado al loop correcto (todas las sesiones corren en este mismo loop, así que
    el refresh de token queda serializado por ese lock — sin carreras).
    """
    global _loop, _oauth
    if _loop is not None:
        return
    with _start_lock:
        if _loop is not None:
            return
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True, name="hf-mcp-loop").start()

        async def _make() -> OAuthClientProvider:
            return _build_oauth()

        _oauth = asyncio.run_coroutine_threadsafe(_make(), loop).result()
        _loop = loop


def _unwrap(exc: BaseException) -> BaseException:
    """Devuelve la excepción hoja de un árbol de ExceptionGroups.

    anyio envuelve cualquier excepción del cuerpo de la sesión en TaskGroups
    anidados ("unhandled errors in a TaskGroup"), enterrando el motivo real —
    incluso nuestros RuntimeError limpios ("sin créditos", ReauthRequired…).
    """
    while isinstance(exc, BaseExceptionGroup):
        subs = list(exc.exceptions)
        real = [e for e in subs if not isinstance(e, asyncio.CancelledError)]
        exc = (real or subs)[0]
    return exc


def _run_coro(coro):
    """Marshalea una coroutine al loop de fondo y bloquea hasta el resultado."""
    _ensure_loop()
    assert _loop is not None
    try:
        return asyncio.run_coroutine_threadsafe(coro, _loop).result()
    except BaseExceptionGroup as eg:
        raise _unwrap(eg) from eg


@asynccontextmanager
async def _open_session():
    """Abre una sesión MCP (transporte streamable-http + OAuth) e inicializa."""
    async with streamablehttp_client(
        MCP_URL, auth=_oauth, timeout=_HTTP_TIMEOUT, sse_read_timeout=_SSE_READ_TIMEOUT
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ── Parseo de resultados del MCP ──────────────────────────────────────────────

def _structured(result) -> dict:
    """Output estructurado de un CallToolResult (o parseo del texto como fallback)."""
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                pass
    return {}


def _submit_error(data: dict) -> str:
    """Traduce una respuesta de generación fallida a un motivo corto.

    Sin créditos, el MCP devuelve recovery_tool=show_plans_and_credits (no rompe).
    Devolvemos un texto con 'not_enough_credits' para que image_provider._short_reason
    lo muestre como 'sin créditos'.
    """
    if data.get("recovery_tool") == "show_plans_and_credits":
        return "not_enough_credits (sin créditos en la suscripción de Higgsfield)"
    return data.get("error") or f"generación fallida (recovery_tool={data.get('recovery_tool')})"


def _is_auth_error(msg: str) -> bool:
    """True si el error in-band del MCP indica sesión OAuth muerta.

    Higgsfield responde el token vencido con HTTP 200 y el error dentro del
    resultado ("Invalid or expired token"), nunca con 401 — hay que detectarlo
    por texto para dar el aviso de reautenticación en vez de un error genérico.
    """
    m = (msg or "").lower()
    return "token" in m and ("expired" in m or "invalid" in m or "unauthorized" in m)


def _preset_notice_id(data: dict) -> str:
    """Id del preset recomendado en un notice `preset_recommendation`, o "".

    Si el prompt se parece a un preset del catálogo, el MCP no encola el job:
    responde sin `results` y con notice={"type": "preset_recommendation", ...,
    "data": {"preset": ...}} esperando que un agente conversacional le pregunte
    al humano. Esta app es headless, así que el preset se rechaza siempre
    (params.declined_preset_id) y se reintenta generando literal.
    """
    notice = data.get("notice") or {}
    if notice.get("type") != "preset_recommendation":
        return ""
    nd = notice.get("data") or {}
    preset = nd.get("preset")
    if isinstance(preset, dict):
        return str(preset.get("id") or preset.get("preset_id") or "")
    if isinstance(preset, str):
        return preset
    return str(nd.get("preset_id") or "")


async def _submit_in(session: ClientSession, tool: str, params: dict) -> str:
    for attempt in (0, 1):
        data = _structured(await session.call_tool(tool, {"params": params}))
        if data.get("recovery_tool") or data.get("error"):
            if _is_auth_error(data.get("error") or ""):
                raise ReauthRequired(
                    f"Higgsfield MCP: sesión OAuth inválida ({data['error']}). {_REAUTH_HINT}"
                )
            raise RuntimeError(_submit_error(data))
        results = data.get("results") or []
        if results and results[0].get("id"):
            return results[0]["id"]
        preset_id = _preset_notice_id(data)
        if attempt == 0 and preset_id:
            params = dict(params, declined_preset_id=preset_id)
            continue
        message = (data.get("notice") or {}).get("message") or ""
        if message:
            raise RuntimeError(f"MCP {tool}: el server no encoló el job: {message[:200]}")
        raise RuntimeError(f"MCP {tool}: respuesta sin job id: {str(data)[:200]}")
    raise AssertionError("unreachable")


async def _poll_in(session: ClientSession, job_id: str, deadline: int) -> str:
    started = time.time()
    last = ""
    while True:
        js = _structured(await session.call_tool("job_status", {"jobId": job_id, "sync": True}))
        if _is_auth_error(js.get("error") or ""):
            # Sesión muerta a mitad del poll: irrecuperable, no seguir hasta el deadline.
            raise ReauthRequired(
                f"Higgsfield MCP: sesión OAuth inválida ({js['error']}). {_REAUTH_HINT}"
            )
        gen = js.get("generation") or {}
        status = (gen.get("status") or "").lower()
        last = status or last
        if status == "completed":
            url = (gen.get("results") or {}).get("rawUrl")
            if url:
                return url
            raise RuntimeError("MCP job completed pero sin rawUrl")
        if status in _TERMINAL_FAIL:
            raise RuntimeError(f"MCP job status={status}")
        if time.time() - started > deadline:
            raise RuntimeError(f"MCP poll timeout tras {deadline}s (último estado={last or 'desconocido'})")
        # sync=true ya esperó server-side; un respiro corto respetando poll_after_seconds.
        await asyncio.sleep(float(js.get("poll_after_seconds") or 2))


async def _generate(tool: str, params: dict, deadline: int) -> str:
    async with _open_session() as session:
        job_id = await _submit_in(session, tool, params)
        return await _poll_in(session, job_id, deadline)


async def _submit_only(tool: str, params: dict) -> str:
    async with _open_session() as session:
        return await _submit_in(session, tool, params)


async def _poll_only(job_id: str, deadline: int) -> str:
    async with _open_session() as session:
        return await _poll_in(session, job_id, deadline)


# ── API pública (síncrona, espejo de higgsfield_client) ───────────────────────

def submit_image(prompt: str, *, aspect_ratio: str = DEFAULT_IMAGE_ASPECT, model: str = "") -> dict:
    """Envía una generación de imagen. Devuelve un handle para pollear luego."""
    params = {"model": model or image_model(), "prompt": (prompt or "")[:2000], "aspect_ratio": aspect_ratio}
    job_id = _run_coro(_submit_only("generate_image", params))
    return {"job_id": job_id, "prompt": prompt}


def poll_image(handle: dict, *, deadline: int = _POLL_DEADLINE) -> str:
    """Espera un handle enviado con submit_image; devuelve la URL de la imagen."""
    return _run_coro(_poll_only(handle["job_id"], deadline))


def generate_image(prompt: str, *, aspect_ratio: str = DEFAULT_IMAGE_ASPECT, model: str = "") -> list[str]:
    """Envía y bloquea hasta que la imagen esté lista. Devuelve [url]."""
    params = {"model": model or image_model(), "prompt": (prompt or "")[:2000], "aspect_ratio": aspect_ratio}
    return [_run_coro(_generate("generate_image", params, _POLL_DEADLINE))]


def generate_video(prompt: str, *, aspect_ratio: str = DEFAULT_VIDEO_ASPECT,
                   duration: int | None = None, model: str = "") -> str:
    """Envía y bloquea hasta que el video esté listo. Devuelve la URL del video."""
    params: dict = {"model": model or video_model(), "prompt": (prompt or "")[:2000], "aspect_ratio": aspect_ratio}
    if duration:
        params["duration"] = duration
    return _run_coro(_generate("generate_video", params, _VIDEO_POLL_DEADLINE))


def credits_balance() -> float | None:
    """Créditos disponibles en la suscripción (o None si falla). Best-effort."""
    async def _bal():
        async with _open_session() as session:
            data = _structured(await session.call_tool("balance", {}))
            return data.get("credits")
    try:
        return _run_coro(_bal())
    except Exception:
        return None


def connection_status(live: bool = True) -> dict:
    """Estado de la sesión OAuth con el MCP, para la vista Conexiones de la web.

    Con `live=True` verifica con una llamada real (`balance`, gratis): es la única
    forma de detectar el token muerto, porque Higgsfield lo reporta in-band con
    HTTP 200 ("Invalid or expired token" / "Something went wrong"), nunca con 401.
    De paso, si el token venció pero el refresh token sigue vivo, esta llamada
    dispara el refresh proactivo y la sesión se recupera sola.
    """
    path = token_store_path()
    out: dict = {
        "configured": path.exists(),
        "alive": None,       # True/False solo si se verificó en vivo
        "expires_at": None,  # epoch seconds del vencimiento del access token
        "credits": None,
        "plan": None,
        "error": "",
    }
    if not out["configured"]:
        return out
    info = FileTokenStorage(path).token_info()
    if info:
        issued_at, expires_in = info
        out["expires_at"] = issued_at + expires_in
    if not live:
        return out

    async def _bal() -> dict:
        async with _open_session() as session:
            return _structured(await session.call_tool("balance", {}))

    try:
        data = _run_coro(_bal())
        err = data.get("error") or ""
        if err:
            out["alive"] = False
            out["error"] = err
        else:
            out["alive"] = True
            out["credits"] = data.get("credits")
            out["plan"] = data.get("subscription_plan_type")
            # El refresh proactivo pudo rotar el token durante la llamada.
            refreshed = FileTokenStorage(path).token_info()
            if refreshed:
                out["expires_at"] = refreshed[0] + refreshed[1]
    except ReauthRequired as e:
        out["alive"] = False
        out["error"] = str(e)
    except Exception as e:
        out["alive"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ── Flujo OAuth iniciado desde la web (página Conexiones) ─────────────────────
#
# El consentimiento ya no exige la terminal: la API arranca el flujo con
# start_web_auth() —que devuelve la URL de autorización para redirigir el
# navegador del usuario— y lo cierra con finish_web_auth() cuando Higgsfield
# vuelve al callback HTTP con el `code`. Hay a lo sumo UN flujo pendiente por
# proceso (igual que el job store en memoria): re-clickear "Conectar" cancela
# el anterior.

_WEB_FLOW_DEADLINE = 600  # s máx entre "Conectar" y el callback (consentimiento humano)
_FINISH_TIMEOUT = 120     # s máx para el intercambio de tokens tras el callback

_web_flow: dict = {}      # {"task", "auth_url", "code"} — solo se toca DENTRO del loop


class _FreshStorage(TokenStorage):
    """Storage que fuerza un consentimiento completo pero persiste el resultado.

    Oculta al SDK los tokens/client_info guardados —así hace DCR + authorize sí
    o sí, incluso con un store muerto en disco— y escribe lo nuevo en el store
    real, de donde lo levanta el provider del runtime.
    """

    def __init__(self, real: FileTokenStorage):
        self._real = real

    async def get_tokens(self) -> OAuthToken | None:
        return None

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._real.set_tokens(tokens)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._real.set_client_info(client_info)


def _reload_runtime_oauth() -> None:
    """Reconstruye el provider del runtime para que levante los tokens nuevos.

    Debe correr EN el loop de fondo (el anyio.Lock interno del provider queda
    ligado al loop donde se construye — misma razón que en _ensure_loop).
    """
    global _oauth
    _oauth = _build_oauth()


async def _web_consent(redirect_uri: str) -> dict:
    """Cuerpo del flujo web: 401 → discovery → DCR → authorize → tokens → prueba."""
    metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": "AIMA web",
            # Se registran ambos callbacks para que el mismo client sirva también
            # al flujo por terminal (mcp_bootstrap.py) sin re-registrar.
            "redirect_uris": [redirect_uri, REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid email offline_access",
        }
    )

    async def _redirect(url: str) -> None:
        fut = _web_flow.get("auth_url")
        if fut is not None and not fut.done():
            fut.set_result(url)

    async def _callback() -> tuple[str, str | None]:
        return await asyncio.wait_for(_web_flow["code"], timeout=_WEB_FLOW_DEADLINE)

    provider = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=metadata,
        storage=_FreshStorage(FileTokenStorage(token_store_path())),
        redirect_handler=_redirect,
        callback_handler=_callback,
    )
    async with streamablehttp_client(
        MCP_URL, auth=provider, timeout=_HTTP_TIMEOUT, sse_read_timeout=_SSE_READ_TIMEOUT
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # initialize pasa incluso con sesión muerta (el MCP no valida el token
            # ahí); balance sí lo exige — es la prueba real de que quedó viva.
            data = _structured(await session.call_tool("balance", {}))
    if data.get("error"):
        raise RuntimeError(f"la sesión quedó abierta pero la prueba falló: {data['error']}")
    _reload_runtime_oauth()
    return {"credits": data.get("credits"), "plan": data.get("subscription_plan_type")}


def start_web_auth(redirect_uri: str) -> str:
    """Arranca el flujo OAuth para la web y devuelve la URL de autorización.

    Bloquea unos pocos requests (discovery + registro del client). El flujo queda
    esperando el `code` que entregará finish_web_auth(); si nadie vuelve en
    _WEB_FLOW_DEADLINE se cancela solo.
    """
    _ensure_loop()
    assert _loop is not None

    async def _begin() -> str:
        old = _web_flow.get("task")
        if old is not None and not old.done():
            old.cancel()
        loop = asyncio.get_running_loop()
        _web_flow["auth_url"] = loop.create_future()
        _web_flow["code"] = loop.create_future()
        _web_flow["task"] = loop.create_task(_web_consent(redirect_uri))

        url_fut, task = _web_flow["auth_url"], _web_flow["task"]
        done, _pending = await asyncio.wait(
            {url_fut, task}, timeout=60, return_when=asyncio.FIRST_COMPLETED
        )
        if url_fut.done():
            return url_fut.result()
        if task.done():
            exc = task.exception()
            detail = _unwrap(exc) if exc else "terminó sin entregar la URL"
            raise RuntimeError(f"el flujo OAuth falló antes de dar la URL: {detail}")
        task.cancel()
        raise RuntimeError("timeout esperando la URL de autorización de Higgsfield")

    try:
        return asyncio.run_coroutine_threadsafe(_begin(), _loop).result()
    except BaseExceptionGroup as eg:
        raise _unwrap(eg) from eg


def finish_web_auth(code: str, state: str | None) -> dict:
    """Entrega el `code` del callback OAuth y espera a que el flujo web cierre.

    Devuelve {"credits", "plan"} de la prueba post-conexión. Lanza si no había
    flujo pendiente o si el intercambio/prueba falla.
    """
    _ensure_loop()
    assert _loop is not None

    async def _finish() -> dict:
        fut, task = _web_flow.get("code"), _web_flow.get("task")
        if fut is None or task is None or task.done():
            raise RuntimeError(
                "no hay un flujo de conexión pendiente — arrancá de nuevo desde la página Conexiones"
            )
        if not fut.done():
            fut.set_result((code, state))
        try:
            return await asyncio.wait_for(task, timeout=_FINISH_TIMEOUT)
        finally:
            _web_flow.clear()

    try:
        return asyncio.run_coroutine_threadsafe(_finish(), _loop).result()
    except BaseExceptionGroup as eg:
        raise _unwrap(eg) from eg
