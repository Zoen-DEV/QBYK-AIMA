"""Tests de los endpoints de cuenta e identidades visuales.

Lo que se comprueba aquí no es el CRUD (eso ya lo cubre `test_identity_store`) sino el
contrato HTTP: que cada error del store salga con su código —403 la de la casa, 404 la
ajena, 422 el JSON inválido, 503 sin base— y que **todo esté scopeado por usuario**.
El scoping importa el doble porque hoy el dueño lo dice una cabecera: si el filtro por
`user_id` se cayera, no habría nada más deteniendo a nadie.
"""

import pytest
from fastapi.testclient import TestClient

import app as api
import users
import visual_identity as vi

_JSON = {
    "paleta": ["#101014", "#F2EFE6", "#FF5C2B"],
    "paleta_nombres": ["ink", "paper", "ember"],
    "color_texto": "paper (#F2EFE6) over the ink",
    "color_acento": "ember (#FF5C2B)",
    "tipografia": "condensed heavy grotesque, ALL CAPS",
    "tipografia_secundaria": "same face, bold, tracking opened",
    "tono_visual": "sunlit still life, hard shadows, warm bounce",
    "aspect_ratio": "4:5",
    "referencias": ["editorial food photography"],
}

_YO = "qbyk"
_OTRO = "cliente-1"


@pytest.fixture
def cliente():
    with TestClient(api.app) as c:
        yield c


def _h(user_id: str) -> dict:
    return {users.HEADER: user_id}


def _crear(cliente, user=_YO, **body) -> dict:
    res = cliente.post("/identities", json={"identity_json": _JSON, **body}, headers=_h(user))
    assert res.status_code == 200, res.text
    return res.json()


# ── Usuarios ──────────────────────────────────────────────────────────────────

def test_lista_de_usuarios_y_cual_esta_activo(cliente):
    data = cliente.get("/users", headers=_h(_OTRO)).json()
    assert [u["id"] for u in data["users"]] == [u["id"] for u in users.USERS]
    assert data["current"] == _OTRO


def test_sin_cabecera_manda_el_usuario_por_defecto(cliente):
    assert cliente.get("/users").json()["current"] == users.DEFAULT_USER_ID


def test_una_cabecera_desconocida_cae_al_por_defecto(cliente):
    assert cliente.get("/users", headers=_h("hacker")).json()["current"] == users.DEFAULT_USER_ID


# ── Listar ────────────────────────────────────────────────────────────────────

def test_sin_base_solo_se_lista_la_de_la_casa(cliente, sin_base):
    filas = cliente.get("/identities", headers=_h(_YO)).json()["identities"]
    assert [f["id"] for f in filas] == [vi.SYSTEM_ID]
    assert filas[0]["is_system"] is True and filas[0]["is_default"] is True


def test_la_de_la_casa_trae_el_json_de_brand(cliente, sin_base):
    fila = cliente.get("/identities", headers=_h(_YO)).json()["identities"][0]
    assert fila["identity_json"] == vi.identidad_system()


def test_listar_es_por_usuario(cliente, identidades):
    _crear(cliente, user=_OTRO, name="Ajena")
    filas = cliente.get("/identities", headers=_h(_YO)).json()["identities"]
    assert [f["id"] for f in filas] == [vi.SYSTEM_ID]


# ── Crear ─────────────────────────────────────────────────────────────────────

def test_crear_devuelve_la_fila(cliente, identidades):
    fila = _crear(cliente, name="Mía")
    assert fila["name"] == "Mía"
    assert fila["user_id"] == _YO
    assert fila["is_system"] is False and fila["is_default"] is False
    assert fila["identity_json"]["paleta"] == _JSON["paleta"]


def test_crear_sin_nombre_sugiere_uno(cliente, identidades):
    """Vacío no puede rechazar el guardado: se perdería la extracción recién pagada."""
    assert _crear(cliente)["name"] == "Ember · ink"


def test_un_json_invalido_da_422_con_el_motivo(cliente, identidades):
    res = cliente.post("/identities",
                       json={"identity_json": {**_JSON, "color_acento": "hot pink (#FF00AA)"}},
                       headers=_h(_YO))
    assert res.status_code == 422
    assert "no es el tercer color de la paleta" in res.json()["detail"]
    assert identidades.docs == []


def test_un_cuerpo_que_no_es_json_da_400(cliente, identidades):
    res = cliente.post("/identities", content=b"no soy json", headers=_h(_YO))
    assert res.status_code == 400


def test_sin_base_crear_da_503(cliente, sin_base):
    res = cliente.post("/identities", json={"identity_json": _JSON}, headers=_h(_YO))
    assert res.status_code == 503
    assert "MONGODB_URI" in res.json()["detail"]


# ── Editar ────────────────────────────────────────────────────────────────────

def test_renombrar(cliente, identidades):
    fila = _crear(cliente, name="Vieja")
    res = cliente.patch(f"/identities/{fila['id']}", json={"name": "Nueva"}, headers=_h(_YO))
    assert res.status_code == 200 and res.json()["name"] == "Nueva"


def test_renombrar_no_toca_el_json(cliente, identidades):
    fila = _crear(cliente)
    nueva = cliente.patch(f"/identities/{fila['id']}", json={"name": "Otra"},
                          headers=_h(_YO)).json()
    assert nueva["identity_json"] == fila["identity_json"]


def test_editar_el_json_a_mano(cliente, identidades):
    fila = _crear(cliente)
    nuevo = {**_JSON, "tono_visual": "overcast daylight, flat shadows"}
    res = cliente.patch(f"/identities/{fila['id']}", json={"identity_json": nuevo},
                        headers=_h(_YO))
    assert res.json()["identity_json"]["tono_visual"] == "overcast daylight, flat shadows"


def test_editar_con_un_json_invalido_da_422(cliente, identidades):
    fila = _crear(cliente)
    res = cliente.patch(f"/identities/{fila['id']}",
                        json={"identity_json": {**_JSON, "referencias": []}}, headers=_h(_YO))
    assert res.status_code == 422 and "`referencias`" in res.json()["detail"]


def test_un_nombre_vacio_da_400(cliente, identidades):
    fila = _crear(cliente)
    res = cliente.patch(f"/identities/{fila['id']}", json={"name": "  "}, headers=_h(_YO))
    assert res.status_code == 400


# ── La identidad de la casa ───────────────────────────────────────────────────

def test_la_de_la_casa_no_se_edita(cliente, identidades):
    res = cliente.patch(f"/identities/{vi.SYSTEM_ID}", json={"name": "Otra"}, headers=_h(_YO))
    assert res.status_code == 403


def test_la_de_la_casa_no_se_elimina(cliente, identidades):
    res = cliente.delete(f"/identities/{vi.SYSTEM_ID}", headers=_h(_YO))
    assert res.status_code == 403
    assert "clónala" in res.json()["detail"]


def test_la_de_la_casa_si_se_activa(cliente, identidades):
    mia = _crear(cliente, activar=True)
    res = cliente.post(f"/identities/{vi.SYSTEM_ID}/activate", headers=_h(_YO))
    assert res.status_code == 200 and res.json()["is_default"] is True
    filas = cliente.get("/identities", headers=_h(_YO)).json()["identities"]
    assert [f["id"] for f in filas if f["is_default"]] == [vi.SYSTEM_ID]
    assert next(f for f in filas if f["id"] == mia["id"])["is_default"] is False


# ── Activar y eliminar ────────────────────────────────────────────────────────

def test_activar_deja_una_sola_activa(cliente, identidades):
    a = _crear(cliente, name="A")
    b = _crear(cliente, name="B")
    cliente.post(f"/identities/{a['id']}/activate", headers=_h(_YO))
    cliente.post(f"/identities/{b['id']}/activate", headers=_h(_YO))
    filas = cliente.get("/identities", headers=_h(_YO)).json()["identities"]
    assert [f["id"] for f in filas if f["is_default"]] == [b["id"]]


def test_eliminar_una_propia(cliente, identidades):
    fila = _crear(cliente)
    assert cliente.delete(f"/identities/{fila['id']}", headers=_h(_YO)).json() == {"ok": True}
    assert [f["id"] for f in cliente.get("/identities", headers=_h(_YO)).json()["identities"]] \
        == [vi.SYSTEM_ID]


def test_eliminar_una_que_no_existe_da_404(cliente, identidades):
    assert cliente.delete("/identities/no-existe", headers=_h(_YO)).status_code == 404


# ── Scoping (lo único que separa a un usuario de otro) ────────────────────────

@pytest.mark.parametrize("metodo, kwargs", [
    ("patch", {"json": {"name": "Secuestrada"}}),
    ("delete", {}),
    ("post", {}),
])
def test_no_se_puede_tocar_la_identidad_de_otro_usuario(cliente, identidades, metodo, kwargs):
    ajena = _crear(cliente, user=_OTRO, name="Ajena")
    ruta = f"/identities/{ajena['id']}"
    if metodo == "post":
        ruta += "/activate"
    res = getattr(cliente, metodo)(ruta, headers=_h(_YO), **kwargs)
    assert res.status_code == 404
    assert len(identidades.docs) == 1
