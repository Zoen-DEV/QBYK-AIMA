/**
 * Cliente de la API de cuenta e identidades visuales.
 *
 * Todo pasa por `api()`, que adjunta la cabecera `X-User-Id` con el perfil elegido en
 * el selector de la barra. Es el espejo de `users.current_user_id` en el backend: hoy
 * el perfil lo dice el cliente y mañana lo dirá una sesión, y este es el único sitio
 * del frontend que hay que tocar cuando eso pase.
 */

const CLAVE_USUARIO = "aima_user";

export type IdentityJson = {
  /** ORDENADA: [fondo, texto, acento]. El orden es contrato, no presentación. */
  paleta: string[];
  paleta_nombres: string[];
  color_texto: string;
  color_acento: string;
  tipografia: string;
  tipografia_secundaria: string;
  tono_visual: string;
  aspect_ratio: string;
  referencias: string[];
  /**
   * ORDENADA por los beats del carrusel: [tensión, desarrollo, prueba, remate]. La
   * posición ES el beat. Opcional: lo que falte cae al respaldo de la casa.
   */
  ritmo_carrusel: string[];
};

export type Identidad = {
  id: string;
  user_id: string | null;
  name: string;
  identity_json: IdentityJson;
  is_default: boolean;
  is_system: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type Limites = {
  min_fotos: number;
  max_fotos: number;
  formatos: string[];
  max_mb_foto: number;
  min_colores: number;
  max_colores: number;
  max_referencias: number;
  nombre_max: number;
};

export type Usuario = { id: string; nombre: string };

/** Roles de los tres primeros colores. El resto de la paleta son colores de apoyo. */
export const ROLES_PALETA = ["Fondo", "Texto", "Acento"];

/**
 * Los beats del carrusel, en el orden de `ritmo_carrusel` (espejo de
 * `prompt_architect.ROLES_BEAT`). Cada uno es la función del slide en la secuencia:
 * el plano que se escribe aquí es cómo la marca fotografía ESE momento.
 */
export const BEATS_CARRUSEL = [
  { clave: "tensión", ayuda: "el problema — el plano más cerrado del set" },
  { clave: "desarrollo", ayuda: "la explicación — plano medio, legible" },
  { clave: "prueba", ayuda: "el dato — un objeto solo, a plomo" },
  { clave: "remate", ayuda: "el cierre — el plano más abierto y profundo" },
];

export function usuarioActual(): string {
  try {
    return localStorage.getItem(CLAVE_USUARIO) || "";
  } catch {
    return "";
  }
}

export function fijarUsuario(id: string): void {
  try {
    localStorage.setItem(CLAVE_USUARIO, id);
  } catch {
    /* modo privado sin storage: se sigue con el usuario por defecto del backend */
  }
}

async function api(ruta: string, init: RequestInit = {}): Promise<any> {
  const headers = new Headers(init.headers);
  const uid = usuarioActual();
  if (uid) headers.set("X-User-Id", uid);
  const res = await fetch(`/api${ruta}`, { ...init, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ErrorApi(data?.detail || `HTTP ${res.status}`, res.status);
  return data;
}

/**
 * Error de la API con su `detail` intacto.
 *
 * Los mensajes del validador llegan unidos por "; " y son accionables uno a uno
 * ("`color_acento` usa #FF00AA, que no es el tercer color de la paleta"), así que
 * `motivos` los devuelve separados para pintarlos como lista.
 */
export class ErrorApi extends Error {
  status: number;

  constructor(mensaje: string, status: number) {
    super(mensaje);
    this.status = status;
  }

  get motivos(): string[] {
    return this.message.split("; ").filter(Boolean);
  }
}

export async function listarUsuarios(): Promise<{ users: Usuario[]; current: string }> {
  return api("/users");
}

export async function listar(): Promise<{ identities: Identidad[]; limites: Limites }> {
  return api("/identities");
}

export async function crear(name: string, identity_json: IdentityJson,
                            activar = false): Promise<Identidad> {
  return api("/identities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, identity_json, activar }),
  });
}

/** Renombra y/o reemplaza el JSON. Lo que no se pasa, no se toca. */
export async function actualizar(
  id: string,
  cambios: { name?: string; identity_json?: IdentityJson },
): Promise<Identidad> {
  return api(`/identities/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cambios),
  });
}

export async function eliminar(id: string): Promise<void> {
  await api(`/identities/${id}`, { method: "DELETE" });
}

export async function activar(id: string): Promise<Identidad> {
  return api(`/identities/${id}/activate`, { method: "POST" });
}

export async function extraer(
  fotos: File[],
): Promise<{ identity_json: IdentityJson; name: string; avisos: string[] }> {
  const body = new FormData();
  for (const f of fotos) body.append("photos", f);
  return api("/identities/extract", { method: "POST", body });
}

/** Una identidad vacía, para partir de algo con la forma correcta al clonar o editar. */
export function identidadVacia(): IdentityJson {
  return {
    paleta: [],
    paleta_nombres: [],
    color_texto: "",
    color_acento: "",
    tipografia: "",
    tipografia_secundaria: "",
    tono_visual: "",
    aspect_ratio: "4:5",
    referencias: [],
    ritmo_carrusel: [],
  };
}
