"""Tests del esquema de identidad visual y del resolver de usuario.

El validador existe para atrapar dos cosas que hoy fallarían en silencio: una paleta
desordenada (que pinta la plantilla de respaldo con los colores cambiados) y un
`color_texto`/`color_acento` sin su hex (que la dibuja con el color de otra marca).
Esos dos contratos son la mitad de este archivo.
"""

import pytest

import users
import visual_identity as vi

# La identidad de la casa tal como está en `prompts/brand.json`.
_VALIDA = {
    "paleta": ["#0B0C0E", "#EDEAE0", "#C9F227"],
    "paleta_nombres": ["near-black", "bone white", "acid lime"],
    "color_texto": "bone white (#EDEAE0) on the near-black",
    "color_acento": "acid lime (#C9F227)",
    "tipografia": "ultra-condensed heavy display grotesque, ALL CAPS, tight tracking",
    "tipografia_secundaria": "same face, bold, tracking opened",
    "tono_visual": "cinematic poster still: one spotlit subject on a near-black field",
    "aspect_ratio": "4:5",
    "referencias": ["film-poster art direction: one hero object under a hard spotlight"],
}


def _con(**over) -> dict:
    return vi.normalizar({**_VALIDA, **over})


# ── Caso feliz ────────────────────────────────────────────────────────────────

def test_la_identidad_de_la_casa_valida():
    """`prompts/brand.json` es la identidad system: si deja de validar, la feature
    entera arranca rota. Este test mira el archivo REAL, no una copia."""
    assert vi.validar(vi.identidad_system()) == []


def test_la_identidad_de_ejemplo_valida():
    assert vi.validar(_con()) == []


def test_normalizar_deja_exactamente_los_campos_del_esquema():
    ident = vi.normalizar({**_VALIDA, "version": "2", "_comment": "doc", "inventado": 1})
    assert list(ident) == list(vi.CAMPOS)
    # `version` y `_comment` describen el ARCHIVO, no la identidad: no entran.
    assert "version" not in ident and "_comment" not in ident and "inventado" not in ident


def test_identidad_vacia_no_pisa_nada():
    """Una identidad sin datos tiene que salir toda falsy.

    Es lo que mantiene intacto el comportamiento actual: `prompt_architect` resuelve
    cada campo con `marca.get(x) or marca_def.get(x)`, así que un vacío cae al valor
    de `brand.json` en vez de imponer un campo en blanco.
    """
    ident = vi.normalizar({})
    assert list(ident) == list(vi.CAMPOS)
    assert not any(ident.values())


def test_normalizar_colapsa_espacios_y_saltos():
    ident = _con(tono_visual="  cinematic\n  poster   still  ")
    assert ident["tono_visual"] == "cinematic poster still"


# ── Colores ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("crudo, esperado", [
    ("0B0C0E", "#0B0C0E"),      # sin almohadilla
    ("#c9f227", "#C9F227"),     # minúsculas
    ("#fff", "#FFFFFF"),        # tres dígitos
])
def test_los_hex_despistados_se_normalizan(crudo, esperado):
    """Corregirlos aquí ahorra un reintento entero del extractor."""
    assert vi.normalizar({"paleta": [crudo]})["paleta"] == [esperado]


def test_un_color_que_no_es_hex_se_rechaza():
    errores = vi.validar(_con(paleta=["#0B0C0E", "#EDEAE0", "verde lima"]))
    assert any("#RRGGBB" in e and "verde lima" in e for e in errores)


def test_paleta_corta_se_rechaza():
    """Menos de 3 colores rompe `_lockup_plantilla`, que indexa hasta `paleta[2]`."""
    errores = vi.validar(_con(paleta=["#0B0C0E", "#EDEAE0"],
                              paleta_nombres=["near-black", "bone white"]))
    assert any("`paleta` debe tener entre" in e for e in errores)


def test_paleta_nombres_desparejos_se_rechaza():
    errores = vi.validar(_con(paleta_nombres=["near-black", "bone white"]))
    assert any("un nombre por color" in e for e in errores)


def test_color_texto_sin_hex_se_rechaza():
    """Sin hex, `image_overlay._color` cae a su gris por defecto y la pieza de
    respaldo sale con un color que no es el de la identidad."""
    errores = vi.validar(_con(color_texto="warm off-white"))
    assert any("`color_texto` tiene que incluir su color" in e for e in errores)


def test_el_acento_tiene_que_ser_el_tercer_color_de_la_paleta():
    """El contrato del ORDEN de la paleta, que hoy vive implícito en job_runner."""
    errores = vi.validar(_con(color_acento="hot pink (#FF00AA)"))
    assert any("no es el tercer color de la paleta" in e and "#C9F227" in e
               for e in errores)


def test_el_color_del_texto_tiene_que_ser_el_segundo():
    errores = vi.validar(_con(color_texto="something (#FF00AA)"))
    assert any("no es el segundo color de la paleta" in e for e in errores)


def test_el_hex_del_color_se_compara_sin_importar_mayusculas():
    assert vi.validar(_con(color_acento="acid lime (#c9f227)")) == []


# ── Resto del esquema ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("campo", ["tipografia", "tipografia_secundaria", "tono_visual"])
def test_los_campos_de_texto_son_obligatorios(campo):
    assert any(f"falta `{campo}`" in e for e in vi.validar(_con(**{campo: ""})))


@pytest.mark.parametrize("campo", ["tipografia", "tono_visual"])
def test_un_campo_larguisimo_se_rechaza(campo):
    """Los topes son presupuesto del brief de 9 secciones, no estética."""
    errores = vi.validar(_con(**{campo: "x" * (vi.MAX_TEXTO + 1)}))
    assert any(f"`{campo}` supera" in e for e in errores)


@pytest.mark.parametrize("valor", ["", "4x5", "cuadrado", "4:"])
def test_aspect_ratio_invalido_se_rechaza(valor):
    assert any("`aspect_ratio`" in e for e in vi.validar(_con(aspect_ratio=valor)))


def test_sin_referencias_se_rechaza():
    assert any("`referencias`" in e for e in vi.validar(_con(referencias=[])))


def test_demasiadas_referencias_se_rechazan():
    errores = vi.validar(_con(referencias=["ref"] * (vi.MAX_REFERENCIAS + 1)))
    assert any("`referencias`" in e for e in errores)


def test_exigir_valida_lanza_con_la_lista_completa_de_errores():
    with pytest.raises(vi.IdentidadInvalida) as exc:
        vi.exigir_valida({**_VALIDA, "tipografia": "", "aspect_ratio": "nope"})
    assert len(exc.value.errores) == 2
    assert "; ".join(exc.value.errores) == str(exc.value)


def test_exigir_valida_devuelve_la_identidad_normalizada():
    ident = vi.exigir_valida({**_VALIDA, "paleta": ["0b0c0e", "#EDEAE0", "#C9F227"]})
    assert ident["paleta"][0] == "#0B0C0E"


# ── Nombre ────────────────────────────────────────────────────────────────────

def test_el_nombre_sugerido_usa_el_acento_y_el_fondo():
    """Lo que distingue dos identidades de un vistazo en la lista."""
    assert vi.nombre_sugerido(_VALIDA) == "Acid lime · near-black"


def test_el_nombre_sugerido_aguanta_una_identidad_vacia():
    assert vi.nombre_sugerido({}) == "Identidad visual"


def test_el_nombre_vacio_se_rechaza():
    assert vi.validar_nombre("   ") == ["el nombre no puede estar vacío"]


def test_el_nombre_larguisimo_se_rechaza():
    assert vi.validar_nombre("x" * (vi.NOMBRE_MAX + 1))


# ── Identidad system ──────────────────────────────────────────────────────────

def test_la_fila_system_se_marca_como_system():
    fila = vi.fila_system()
    assert fila["id"] == vi.SYSTEM_ID
    assert fila["is_system"] is True
    # Sin dueño: la identidad de la casa la ven todos los usuarios.
    assert fila["user_id"] is None


def test_la_fila_system_trae_el_json_de_brand():
    assert vi.fila_system()["identity_json"] == vi.identidad_system()


# ── Resolver de usuario ───────────────────────────────────────────────────────

def test_un_usuario_conocido_se_respeta():
    assert users.resolver("cliente-1") == "cliente-1"


@pytest.mark.parametrize("valor", ["", None, "   ", "no-existe", "'; drop"])
def test_un_usuario_desconocido_cae_al_por_defecto(valor):
    """Sin auth real, rechazar la petición solo produciría una app rota tras limpiar
    el navegador."""
    assert users.resolver(valor) == users.DEFAULT_USER_ID


def test_el_usuario_por_defecto_existe():
    assert users.existe(users.DEFAULT_USER_ID)
    assert len(users.listar()) == len(users.USERS)
