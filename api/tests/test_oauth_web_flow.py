"""Un flujo OAuth web abandonado no puede romper la conexión que todavía servía.

El DCR ocurre al principio del consentimiento y el usuario puede no volver nunca
(cierra la pestaña, el login de Higgsfield se cuelga, vence `_WEB_FLOW_DEADLINE`).
Mientras `_FreshStorage` escribía el client_info en cuanto se registraba, ese
abandono dejaba el store con **client_id nuevo + tokens viejos**: un refresh token
solo lo canjea el client_id al que se emitió, así que el refresh silencioso moría
(`Token refresh failed: 400`) y la única salida era re-consentir. Es un fallo que
no da error en el momento — se descubre días después, cuando el token vence y el
refresh que debía renovarlo ya no puede.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken  # noqa: E402

import higgsfield_mcp as hfmcp  # noqa: E402

CALLBACK = "http://localhost:4321/api/connections/higgsfield/callback"


def _client(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull.model_validate(
        {
            "client_id": client_id,
            "redirect_uris": [CALLBACK],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )


def _tokens(access: str, refresh: str) -> OAuthToken:
    return OAuthToken.model_validate(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 86399,
            "refresh_token": refresh,
            "scope": "openid email offline_access",
        }
    )


async def _store_conectado(path: Path) -> hfmcp.FileTokenStorage:
    """Store como lo deja una conexión que funcionó: client_id y tokens del MISMO flujo."""
    real = hfmcp.FileTokenStorage(path)
    await real.set_client_info(_client("viejo-client"))
    await real.set_tokens(_tokens("access-viejo", "refresh-viejo"))
    return real


async def test_flujo_abandonado_deja_el_store_intacto(tmp_path):
    """DCR sin consentimiento: el par client_id/tokens guardado no se toca."""
    path = tmp_path / ".hf_oauth.json"
    real = await _store_conectado(path)

    fresh = hfmcp._FreshStorage(real)
    # El SDK registra un client nuevo (`_FreshStorage` le oculta el guardado)…
    await fresh.set_client_info(_client("nuevo-client"))
    # …y acá el usuario abandona: nunca llega `set_tokens`.

    info = await real.get_client_info()
    tokens = await real.get_tokens()
    assert info is not None and tokens is not None
    assert info.client_id == "viejo-client", "el DCR pisó el client_id de una conexión viva"
    assert tokens.refresh_token == "refresh-viejo"


async def test_flujo_completo_persiste_client_id_y_tokens_juntos(tmp_path):
    """El camino feliz sigue guardando todo: el buffer no puede tragarse el client_info."""
    path = tmp_path / ".hf_oauth.json"
    real = await _store_conectado(path)

    fresh = hfmcp._FreshStorage(real)
    await fresh.set_client_info(_client("nuevo-client"))
    await fresh.set_tokens(_tokens("access-nuevo", "refresh-nuevo"))

    info = await real.get_client_info()
    tokens = await real.get_tokens()
    assert info is not None and tokens is not None
    assert info.client_id == "nuevo-client"
    assert tokens.access_token == "access-nuevo"
    assert tokens.refresh_token == "refresh-nuevo"
    # El par tiene que quedar coherente: los dos son del flujo que acaba de cerrar.
    assert real.token_info() is not None


async def test_fresh_storage_oculta_lo_guardado(tmp_path):
    """La premisa del flujo web: forzar DCR + authorize aunque el store esté sano."""
    path = tmp_path / ".hf_oauth.json"
    real = await _store_conectado(path)

    fresh = hfmcp._FreshStorage(real)
    assert await fresh.get_tokens() is None
    assert await fresh.get_client_info() is None
