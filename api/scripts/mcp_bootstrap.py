"""
Spike de investigación para el MCP de Higgsfield (Fases 0–1 del plan).

NO se importa desde la app. Es un script standalone que se corre a mano UNA vez
para:

  Fase 0 — Consentimiento OAuth (navegador local) + descubrir las tools:
      python mcp_bootstrap.py
    → abre el navegador para autenticar contra tu cuenta Higgsfield, persiste los
      tokens en el token store (JSON en disco) y vuelca el schema de todas las
      tools del MCP (nombres, inputs, outputs). Con eso sabemos cómo se llaman las
      tools de imagen/video, qué parámetros aceptan y si hay una tool de "status"
      (patrón handle vs. bloqueante).

  Fase 1 — Generación de prueba (ya autenticado, reusa el token store):
      python mcp_bootstrap.py --call <tool> --prompt "..." --arg aspect_ratio=1:1
    → llama una tool y vuelca el resultado CRUDO completo, para ver dónde viene la
      URL (structuredContent vs content[].text) y cuánto tarda. Después de correrla,
      confirmá en el panel de Higgsfield que bajó el contador de la SUSCRIPCIÓN
      (App), no el de Cloud.

Requiere el paquete `mcp` (pip install mcp). No toca el resto de la app.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anyio

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

# ── Configuración ─────────────────────────────────────────────────────────────

MCP_URL = "https://mcp.higgsfield.ai/mcp"
CALLBACK_PORT = 3030
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"

# Token store: web/api/.hf_oauth.json (junto al paquete api/). El mismo archivo lo
# reusará el cliente de producción (higgsfield_mcp.py) en la Fase 2.
_DEFAULT_TOKEN_STORE = Path(__file__).resolve().parent.parent / ".hf_oauth.json"


# ── Token store persistente (escritura atómica) ───────────────────────────────

class FileTokenStorage(TokenStorage):
    """Persiste tokens + client_info en un JSON de disco.

    La escritura es atómica (tmp + replace) porque el refresh token rota y es de
    un solo uso: una escritura corrupta te deja sin sesión.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        d = self._read()
        d["tokens"] = tokens.model_dump(mode="json")
        # El cliente de producción (higgsfield_mcp) usa issued_at para saber si el
        # token guardado sigue vivo; sin esto quedaría el sello de una sesión vieja.
        d["issued_at"] = time.time()
        self._write(d)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        d = self._read()
        d["client_info"] = client_info.model_dump(mode="json")
        self._write(d)

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


# ── Handlers del flujo OAuth ──────────────────────────────────────────────────

async def _redirect_handler(url: str) -> None:
    """Abre la URL de autorización en el navegador."""
    print("\n>> Abriendo el navegador para autorizar. Si no abre solo, pegá esta URL:")
    print(f"   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _make_callback_handler(port: int):
    """Devuelve un callback_handler que levanta un server local y captura ?code=."""
    holder: dict[str, str | None] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path.startswith("/callback"):
                holder["code"] = (qs.get("code") or [None])[0]
                holder["state"] = (qs.get("state") or [None])[0]
                holder["done"] = "1"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Autenticación completa.</h2><p>Podés cerrar esta pestaña y "
                "volver a la terminal.</p>".encode("utf-8")
            )

        def log_message(self, *args):  # silenciar el log del server
            return

    async def callback_handler() -> tuple[str, str | None]:
        server = HTTPServer(("localhost", port), _Handler)
        try:
            # handle_request() atiende UNA request; el navegador puede pedir favicon
            # antes del callback, así que iteramos hasta capturar el code.
            while "done" not in holder:
                await anyio.to_thread.run_sync(server.handle_request)
        finally:
            server.server_close()
        return holder.get("code") or "", holder.get("state")

    return callback_handler


def _build_oauth(storage: FileTokenStorage) -> OAuthClientProvider:
    metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": "AIMA web",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # Cliente público con PKCE (sin client_secret). Higgsfield soporta "none".
            "token_endpoint_auth_method": "none",
            # offline_access => refresh token (operación desatendida).
            "scope": "openid email offline_access",
        }
    )
    # server_url = la URL COMPLETA del MCP (con /mcp): el SDK valida por RFC 8707
    # que el `resource` de la metadata protegida (https://.../mcp) matchee el
    # server_url; con la raíz pelada el flujo muere en "Protected resource ...
    # does not match expected ...".
    return OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_make_callback_handler(CALLBACK_PORT),
    )


# ── Utilidades de parseo de --arg ─────────────────────────────────────────────

def _coerce(value: str):
    """`--arg k=v`: intenta JSON (int/float/bool/obj); si no, string crudo."""
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _parse_args_list(pairs: list[str]) -> dict:
    args: dict = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--arg mal formado (esperaba k=v): {p!r}")
        k, v = p.split("=", 1)
        args[k] = _coerce(v)
    return args


# ── Acciones ──────────────────────────────────────────────────────────────────

# Modelos por defecto para las pruebas (IDs citados en las descripciones de las
# tools del MCP). Sobrescribibles con --model; si son inválidos, --models lista los
# correctos.
_DEFAULT_IMAGE_MODEL = "nano_banana_pro"
_DEFAULT_VIDEO_MODEL = "kling3_0_turbo"

_TERMINAL_OK = "completed"
_TERMINAL_FAIL = {"failed", "canceled", "cancelled", "nsfw", "ip_detected"}


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


async def _list_tools(session: ClientSession) -> None:
    result = await session.list_tools()
    # Guardar el dump COMPLETO a disco (la terminal trunca listas largas).
    dump_path = Path(__file__).resolve().parent / "mcp_tools.json"
    dump_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), "utf-8"
    )
    print(f"\n=== {len(result.tools)} tools (dump completo -> {dump_path}) ===\n")
    for t in result.tools:
        props = list((t.inputSchema or {}).get("properties", {}).keys())
        desc = ((t.description or "").splitlines() or [""])[0][:90]
        print(f"- {t.name}")
        print(f"    inputs: {props}")
        print(f"    {desc}")
    print(f"\n>> Schema completo guardado en {dump_path.name}. Pasámelo o dejá que lo lea.")


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> None:
    print(f"\n>> Llamando tool {name!r} con args: {json.dumps(arguments, ensure_ascii=False)}")
    started = time.time()
    result = await session.call_tool(name, arguments)
    elapsed = time.time() - started
    print(f"\n=== Resultado ({elapsed:.1f}s, isError={result.isError}) ===\n")
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    print(f"\n>> Tardó {elapsed:.1f}s. Fijate dónde viene la URL (structuredContent vs")
    print("   content[].text) y confirmá en el panel de Higgsfield que bajó el")
    print("   contador de la SUSCRIPCIÓN (App), no el de Cloud.")


async def _balance(session: ClientSession) -> float | None:
    data = _structured(await session.call_tool("balance", {}))
    credits = data.get("credits")
    print(f">> balance: credits={credits}  plan={data.get('subscription_plan_type')}")
    if data.get("error"):
        print(f"   error: {data['error']}")
    return credits


async def _voices(session: ClientSession) -> None:
    """Voces TTS disponibles (para elegir HIGGSFIELD_TTS_VOICE_TYPE/ID del .env)."""
    data = _structured(await session.call_tool("list_voices", {"size": 100}))
    if data.get("error"):
        print(f"!! error: {data['error']}")
        return
    voices = data.get("voices") or []
    print(f"\n=== {len(voices)} voces (list_voices) ===\n")
    for v in voices:
        print(f"  {v.get('voice_type'):7}  {v.get('voice_id')}  {v.get('name')!r}"
              f"  gender={v.get('gender')}  preview={v.get('preview_url', '')}")
    print("\n>> En .env: HIGGSFIELD_TTS_VOICE_TYPE=<voice_type>  HIGGSFIELD_TTS_VOICE_ID=<voice_id>")


async def _models(session: ClientSession, type_: str) -> None:
    # Sin filtro `input` para ver TODO el catálogo del tipo (con input=text el MCP
    # oculta los text-to-video como kling/seedance).
    result = await session.call_tool(
        "models_explore", {"action": "list", "type": type_, "limit": 100}
    )
    print(f"\n=== models_explore(type={type_}) ===\n")
    print(json.dumps(_structured(result) or result.model_dump(mode="json"), indent=2, ensure_ascii=False))


async def _generate_and_poll(session: ClientSession, tool: str, params: dict) -> None:
    """Genera 1 medio, pollea job_status hasta terminal, y mide el delta de créditos."""
    print(f"\n>> {tool}  params={json.dumps(params, ensure_ascii=False)}")
    before = await _balance(session)
    started = time.time()

    submit = _structured(await session.call_tool(tool, {"params": params}))
    if submit.get("recovery_tool") or submit.get("error"):
        print(f"!! No se pudo generar: error={submit.get('error')} recovery_tool={submit.get('recovery_tool')}")
        print(json.dumps(submit, indent=2, ensure_ascii=False)[:1500])
        return
    results = submit.get("results") or []
    if not results:
        print("!! Respuesta sin results:")
        print(json.dumps(submit, indent=2, ensure_ascii=False)[:1500])
        return

    job_id = results[0]["id"]
    status = results[0].get("status")
    print(f">> job_id={job_id} status={status}")

    raw_url = None
    while True:
        js = _structured(await session.call_tool("job_status", {"jobId": job_id, "sync": True}))
        gen = js.get("generation") or {}
        status = gen.get("status")
        if status == _TERMINAL_OK:
            raw_url = (gen.get("results") or {}).get("rawUrl")
            break
        if status in _TERMINAL_FAIL:
            print(f"!! job terminó en estado {status}")
            print(json.dumps(js, indent=2, ensure_ascii=False)[:1500])
            break
        wait = js.get("poll_after_seconds") or 5
        print(f"   status={status} — esperando {wait}s…")
        await anyio.sleep(float(wait))

    elapsed = time.time() - started
    after = await _balance(session)
    print("\n=== Resultado ===")
    print(f"   rawUrl:   {raw_url}")
    print(f"   tardó:    {elapsed:.1f}s")
    if before is not None and after is not None:
        print(f"   créditos: {before} -> {after}  (Δ {after - before})")
        print("   >> Si Δ es negativo, el consumo SALE DE LA SUSCRIPCIÓN. Gate confirmado.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Spike del MCP de Higgsfield.")
    parser.add_argument("--token-store", default=str(_DEFAULT_TOKEN_STORE),
                        help=f"Ruta del token store (default: {_DEFAULT_TOKEN_STORE}).")
    # Acciones (elegí una; por defecto lista las tools):
    parser.add_argument("--balance", action="store_true", help="Mostrar créditos + plan.")
    parser.add_argument("--models", choices=["image", "video"], help="Listar modelos de ese tipo.")
    parser.add_argument("--voices", action="store_true",
                        help="Listar voces TTS (voice_id para HIGGSFIELD_TTS_VOICE_ID).")
    parser.add_argument("--test-image", action="store_true", help="Generar 1 imagen y medir créditos.")
    parser.add_argument("--test-video", action="store_true", help="Generar 1 video y medir créditos.")
    parser.add_argument("--model", help="ID de modelo para la prueba (override del default).")
    parser.add_argument("--prompt", default="a calm minimal abstract background, soft gradient",
                        help="Prompt para la prueba.")
    parser.add_argument("--aspect", help="aspect_ratio para la prueba.")
    parser.add_argument("--duration", type=int, help="Duración en seg para --test-video.")
    # Escape hatch genérico:
    parser.add_argument("--call", metavar="TOOL", help="Invocar una tool arbitraria.")
    parser.add_argument("--arg", action="append", default=[], metavar="k=v",
                        help="Arg para --call (repetible). Ej: --arg action=list --arg type=image")
    ns = parser.parse_args()

    storage = FileTokenStorage(Path(ns.token_store))
    oauth = _build_oauth(storage)
    print(f"MCP:          {MCP_URL}")
    print(f"Token store:  {ns.token_store}")

    # La primera request (initialize) dispara el flujo OAuth si no hay token válido:
    # 401 -> discovery -> DCR (/oauth2/register) -> navegador -> token. Luego el
    # OAuthClientProvider refresca solo en cada expiración.
    async with streamablehttp_client(MCP_URL, auth=oauth, timeout=60, sse_read_timeout=900) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(">> Sesión MCP inicializada (OAuth OK).")

            if ns.balance:
                await _balance(session)
            elif ns.models:
                await _models(session, ns.models)
            elif ns.voices:
                await _voices(session)
            elif ns.test_image:
                params = {"model": ns.model or _DEFAULT_IMAGE_MODEL,
                          "prompt": ns.prompt, "aspect_ratio": ns.aspect or "1:1"}
                await _generate_and_poll(session, "generate_image", params)
            elif ns.test_video:
                params = {"model": ns.model or _DEFAULT_VIDEO_MODEL,
                          "prompt": ns.prompt, "aspect_ratio": ns.aspect or "9:16"}
                if ns.duration:
                    params["duration"] = ns.duration
                await _generate_and_poll(session, "generate_video", params)
            elif ns.call:
                await _call_tool(session, ns.call, _parse_args_list(ns.arg))
            else:
                await _list_tools(session)


if __name__ == "__main__":
    try:
        anyio.run(main)
    except KeyboardInterrupt:
        sys.exit(130)
