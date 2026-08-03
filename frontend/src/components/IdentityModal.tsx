import { useEffect, useMemo, useRef, useState } from "react";
import {
  ErrorApi,
  ROLES_PALETA,
  actualizar,
  crear,
  extraer,
  identidadVacia,
  type Identidad,
  type IdentityJson,
  type Limites,
} from "../lib/identidades";

/**
 * Modal de identidades visuales, en tres modos que comparten el mismo editor:
 *
 * - `nueva`   — sube fotos, extrae y previsualiza antes de guardar.
 * - `clonar`  — parte del JSON de otra identidad (así se "usa" la de la casa, que no
 *               se puede editar) y guarda una nueva.
 * - `editar`  — reescribe el JSON de una identidad propia.
 *
 * El paso de extracción y el de edición están separados a propósito: lo que devuelve
 * el modelo es una propuesta, y **nada se guarda sin pasar por el editor**. Si la
 * extracción falla, las fotos siguen en memoria: reintentar no obliga a volver a
 * elegirlas.
 */

type Modo = "nueva" | "clonar" | "editar";
type Paso = "fotos" | "extrayendo" | "editor" | "guardando";

export function IdentityModal({
  modo,
  base,
  limites,
  onCerrar,
  onGuardada,
}: {
  modo: Modo;
  base?: Identidad;
  limites: Limites;
  onCerrar: () => void;
  onGuardada: (mensaje: string) => void;
}) {
  const [paso, setPaso] = useState<Paso>(modo === "nueva" ? "fotos" : "editor");
  const [fotos, setFotos] = useState<File[]>([]);
  const [nombre, setNombre] = useState(
    modo === "clonar" ? recorta(`${base?.name ?? ""} (copia)`, limites.nombre_max) : base?.name ?? "",
  );
  const [json, setJson] = useState<IdentityJson>(base?.identity_json ?? identidadVacia());
  const [errores, setErrores] = useState<string[]>([]);
  const [avisos, setAvisos] = useState<string[]>([]);

  // Cerrar con Escape, como cualquier diálogo. No se cierra al hacer clic fuera: se
  // perdería una extracción que ya costó una llamada.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && paso !== "extrayendo" && paso !== "guardando") onCerrar();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCerrar, paso]);

  async function lanzarExtraccion(archivos: File[]) {
    setErrores([]);
    setAvisos([]);
    setPaso("extrayendo");
    try {
      const data = await extraer(archivos);
      setJson(data.identity_json);
      if (!nombre.trim()) setNombre(data.name);
      setAvisos(data.avisos ?? []);
      setPaso("editor");
    } catch (e) {
      setErrores(e instanceof ErrorApi ? e.motivos : [mensaje(e)]);
      // Se vuelve al paso de fotos con las fotos PUESTAS: reintentar es un clic.
      setPaso("fotos");
    }
  }

  async function guardar() {
    setErrores([]);
    setPaso("guardando");
    try {
      if (modo === "editar" && base) {
        await actualizar(base.id, { name: nombre, identity_json: json });
        onGuardada(`Identidad «${nombre}» actualizada.`);
      } else {
        const fila = await crear(nombre, json);
        onGuardada(`Identidad «${fila.name}» creada. Actívala para usarla al generar.`);
      }
    } catch (e) {
      setErrores(e instanceof ErrorApi ? e.motivos : [mensaje(e)]);
      setPaso("editor");
    }
  }

  const ocupado = paso === "extrayendo" || paso === "guardando";
  const titulo =
    modo === "editar" ? "Editar identidad visual"
      : modo === "clonar" ? `Clonar «${base?.name ?? ""}»`
        : "Agregar identidad visual";

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 sm:p-8">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={titulo}
        className="w-full max-w-2xl rounded-2xl border border-gray-800 bg-gray-900 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-gray-800 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-white">{titulo}</h2>
            <p className="mt-0.5 text-xs text-gray-500">{subtitulo(modo, paso, limites)}</p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            disabled={ocupado}
            className="rounded-lg px-2 py-1 text-sm text-gray-400 transition hover:text-white disabled:opacity-40"
          >
            Cerrar
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
          {errores.length > 0 && (
            <div className="mb-5 rounded-xl border border-red-700/50 bg-red-900/30 px-4 py-3 text-sm text-red-300">
              <p className="font-medium">No se pudo continuar:</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-red-200/90">
                {errores.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {paso === "fotos" && (
            <PasoFotos
              fotos={fotos}
              setFotos={setFotos}
              limites={limites}
              onExtraer={lanzarExtraccion}
              setErrores={setErrores}
            />
          )}

          {paso === "extrayendo" && (
            <Esperando
              titulo="Leyendo tus fotos…"
              detalle="Un modelo de visión está buscando la paleta, la luz y el tratamiento que comparten. Suele tardar unos segundos."
            />
          )}

          {(paso === "editor" || paso === "guardando") && (
            <>
              {avisos.length > 0 && (
                <div className="mb-5 rounded-xl border border-amber-700/50 bg-amber-900/20 px-4 py-3 text-xs text-amber-300">
                  {avisos.map((a, i) => (
                    <p key={i}>{a}</p>
                  ))}
                </div>
              )}
              <Editor
                nombre={nombre}
                setNombre={setNombre}
                json={json}
                setJson={setJson}
                limites={limites}
                deshabilitado={paso === "guardando"}
              />
            </>
          )}
        </div>

        {(paso === "editor" || paso === "guardando") && (
          <div className="flex items-center justify-between gap-3 border-t border-gray-800 px-6 py-4">
            {modo === "nueva" ? (
              <button
                type="button"
                onClick={() => setPaso("fotos")}
                disabled={ocupado}
                className="text-xs text-gray-400 transition hover:text-white disabled:opacity-40"
              >
                ← Volver a las fotos
              </button>
            ) : (
              <span />
            )}
            <button
              type="button"
              onClick={guardar}
              disabled={ocupado}
              className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
            >
              {paso === "guardando" ? "Guardando…" : "Guardar identidad"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Paso 1: las fotos ─────────────────────────────────────────────────────────

function PasoFotos({
  fotos,
  setFotos,
  limites,
  onExtraer,
  setErrores,
}: {
  fotos: File[];
  setFotos: (f: File[]) => void;
  limites: Limites;
  onExtraer: (f: File[]) => void;
  setErrores: (e: string[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const previews = useMemo(() => fotos.map((f) => URL.createObjectURL(f)), [fotos]);

  // Las object URLs se sueltan al cambiar de selección o al cerrar: si no, cada
  // reintento deja las anteriores retenidas en memoria.
  useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews]);

  const problemas = revisarFotos(fotos, limites);
  const listo = fotos.length > 0 && problemas.length === 0;

  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-gray-300">
        Fotos de referencia ({limites.min_fotos}–{limites.max_fotos})
      </label>
      <p className="mb-3 text-xs text-gray-500">
        Sube fotos que ya se parezcan entre sí: mismo tipo de luz, mismos materiales, misma
        paleta. De un set disperso sale una identidad dispersa. Las fotos no se guardan en
        ningún sitio — se leen para extraer la identidad y se descartan.
      </p>

      <input
        ref={input}
        type="file"
        multiple
        accept={limites.formatos.join(",")}
        onChange={(e) => {
          setErrores([]);
          setFotos(Array.from(e.target.files ?? []));
        }}
        className="block w-full cursor-pointer rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-300 file:mr-3 file:rounded-md file:border-0 file:bg-gray-800 file:px-3 file:py-1.5 file:text-xs file:text-gray-200 hover:border-gray-600"
      />

      {fotos.length > 0 && (
        <div className="mt-4 grid grid-cols-4 gap-2 sm:grid-cols-5">
          {previews.map((url, i) => (
            <img
              key={url}
              src={url}
              alt={`Referencia ${i + 1}`}
              className="aspect-square w-full rounded-lg border border-gray-800 object-cover"
            />
          ))}
        </div>
      )}

      {problemas.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-400">
          {problemas.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          onClick={() => onExtraer(fotos)}
          disabled={!listo}
          className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-40"
        >
          Extraer identidad
        </button>
        <span className="text-xs text-gray-500">
          {fotos.length === 0
            ? "Aún no has elegido fotos."
            : `${fotos.length} foto${fotos.length === 1 ? "" : "s"} seleccionada${fotos.length === 1 ? "" : "s"}.`}
        </span>
      </div>
    </div>
  );
}

/**
 * Comprobación en el navegador: **la de verdad la hace el backend**, esta solo evita
 * un viaje inútil y dice el motivo antes de pulsar nada.
 */
function revisarFotos(fotos: File[], limites: Limites): string[] {
  if (fotos.length === 0) return [];
  const problemas: string[] = [];
  if (fotos.length < limites.min_fotos || fotos.length > limites.max_fotos) {
    problemas.push(
      `Sube entre ${limites.min_fotos} y ${limites.max_fotos} fotos (elegiste ${fotos.length}).`,
    );
  }
  for (const f of fotos) {
    if (f.type && !limites.formatos.includes(f.type)) {
      problemas.push(`«${f.name}» no es un formato admitido.`);
    }
    if (f.size > limites.max_mb_foto * 1024 * 1024) {
      problemas.push(
        `«${f.name}» pesa ${(f.size / 1024 / 1024).toFixed(1)} MB; el máximo es ${limites.max_mb_foto} MB.`,
      );
    }
  }
  return problemas;
}

// ── Paso 2: el editor ─────────────────────────────────────────────────────────

function Editor({
  nombre,
  setNombre,
  json,
  setJson,
  limites,
  deshabilitado,
}: {
  nombre: string;
  setNombre: (n: string) => void;
  json: IdentityJson;
  setJson: (j: IdentityJson) => void;
  limites: Limites;
  deshabilitado: boolean;
}) {
  const set = (campo: keyof IdentityJson, valor: any) => setJson({ ...json, [campo]: valor });

  function cambiarColor(i: number, hex: string) {
    const paleta = [...json.paleta];
    paleta[i] = hex.toUpperCase();
    setJson(sincronizarColores({ ...json, paleta }));
  }

  function cambiarNombreColor(i: number, valor: string) {
    const nombres = [...json.paleta_nombres];
    nombres[i] = valor;
    set("paleta_nombres", nombres);
  }

  function agregarColor() {
    setJson({
      ...json,
      paleta: [...json.paleta, "#888888"],
      paleta_nombres: [...json.paleta_nombres, "nuevo color"],
    });
  }

  function quitarColor(i: number) {
    setJson(
      sincronizarColores({
        ...json,
        paleta: json.paleta.filter((_, k) => k !== i),
        paleta_nombres: json.paleta_nombres.filter((_, k) => k !== i),
      }),
    );
  }

  return (
    <div className="space-y-5">
      <Campo etiqueta="Nombre" ayuda={`Máximo ${limites.nombre_max} caracteres.`}>
        <input
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          maxLength={limites.nombre_max}
          disabled={deshabilitado}
          placeholder="Cómo la vas a reconocer en la lista"
          className={ENTRADA}
        />
      </Campo>

      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-sm font-medium text-gray-300">Paleta</span>
          <span className="text-xs text-gray-500">
            {json.paleta.length}/{limites.max_colores}
          </span>
        </div>
        <p className="mb-2.5 text-xs text-gray-500">
          El orden importa: el primero es el fondo, el segundo el color del texto y el tercero
          el acento. Cambiar uno actualiza solo las frases de abajo.
        </p>
        <div className="space-y-2">
          {json.paleta.map((hex, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="color"
                value={/^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "#888888"}
                onChange={(e) => cambiarColor(i, e.target.value)}
                disabled={deshabilitado}
                aria-label={`Color ${i + 1}`}
                className="h-9 w-10 shrink-0 cursor-pointer rounded border border-gray-700 bg-gray-950"
              />
              <input
                value={hex}
                onChange={(e) => cambiarColor(i, e.target.value)}
                disabled={deshabilitado}
                className={`${ENTRADA} w-28 shrink-0 font-mono text-xs uppercase`}
              />
              <input
                value={json.paleta_nombres[i] ?? ""}
                onChange={(e) => cambiarNombreColor(i, e.target.value)}
                disabled={deshabilitado}
                placeholder="nombre del color"
                className={ENTRADA}
              />
              <span className="w-14 shrink-0 text-right text-[11px] text-gray-500">
                {ROLES_PALETA[i] ?? "apoyo"}
              </span>
              <button
                type="button"
                onClick={() => quitarColor(i)}
                disabled={deshabilitado || json.paleta.length <= limites.min_colores}
                title={
                  json.paleta.length <= limites.min_colores
                    ? `La paleta necesita al menos ${limites.min_colores} colores`
                    : "Quitar este color"
                }
                className="shrink-0 rounded px-1.5 text-gray-500 transition hover:text-red-400 disabled:opacity-30"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        {json.paleta.length < limites.max_colores && (
          <button
            type="button"
            onClick={agregarColor}
            disabled={deshabilitado}
            className="mt-2 text-xs text-brand-500 transition hover:text-brand-600 disabled:opacity-40"
          >
            + Añadir color
          </button>
        )}
      </div>

      <Campo
        etiqueta="Color del texto"
        ayuda="Tiene que incluir el hex del segundo color de la paleta."
      >
        <input value={json.color_texto} onChange={(e) => set("color_texto", e.target.value)}
               disabled={deshabilitado} className={ENTRADA} />
      </Campo>

      <Campo etiqueta="Color de acento" ayuda="Tiene que incluir el hex del tercer color.">
        <input value={json.color_acento} onChange={(e) => set("color_acento", e.target.value)}
               disabled={deshabilitado} className={ENTRADA} />
      </Campo>

      <Campo
        etiqueta="Tipografía"
        ayuda="Una familia descrita por su clase (peso, ancho, caja), no una fuente concreta."
      >
        <input value={json.tipografia} onChange={(e) => set("tipografia", e.target.value)}
               disabled={deshabilitado} className={ENTRADA} />
      </Campo>

      <Campo etiqueta="Tipografía secundaria" ayuda="La de la segunda línea de la pieza.">
        <input value={json.tipografia_secundaria}
               onChange={(e) => set("tipografia_secundaria", e.target.value)}
               disabled={deshabilitado} className={ENTRADA} />
      </Campo>

      <Campo
        etiqueta="Tratamiento fotográfico"
        ayuda="Luz, contraste, profundidad y textura — sin nombrar colores (de eso ya se ocupa la paleta). El texto de cada post puede pisarlo."
      >
        <input value={json.tono_visual} onChange={(e) => set("tono_visual", e.target.value)}
               disabled={deshabilitado} className={ENTRADA} />
      </Campo>

      <Campo etiqueta="Aspecto" ayuda="Informativo: el aspecto real lo fija cada formato de post.">
        <input value={json.aspect_ratio} onChange={(e) => set("aspect_ratio", e.target.value)}
               disabled={deshabilitado} className={`${ENTRADA} w-24`} />
      </Campo>

      <Campo
        etiqueta="Referencias"
        ayuda={`Una por línea, hasta ${limites.max_referencias}. Son la dirección de arte que se le da al modelo.`}
      >
        <textarea
          value={json.referencias.join("\n")}
          onChange={(e) =>
            set("referencias", e.target.value.split("\n").map((l) => l.trim()).filter(Boolean))
          }
          disabled={deshabilitado}
          rows={3}
          className={ENTRADA}
        />
      </Campo>
    </div>
  );
}

/**
 * Mantiene el contrato de la paleta al editar: `color_texto` lleva el hex del segundo
 * color y `color_acento` el del tercero.
 *
 * Sin esto, tocar un color en el selector deja las dos frases apuntando al color viejo
 * y el guardado rebota con un error que el usuario no provocó a propósito.
 */
function sincronizarColores(json: IdentityJson): IdentityJson {
  return {
    ...json,
    color_texto: conHex(json.color_texto, json.paleta[1]),
    color_acento: conHex(json.color_acento, json.paleta[2]),
  };
}

function conHex(frase: string, hex?: string): string {
  if (!hex) return frase;
  if (!frase.trim()) return hex;
  return /#[0-9a-fA-F]{6}/.test(frase) ? frase.replace(/#[0-9a-fA-F]{6}/, hex) : `${frase} (${hex})`;
}

// ── Piezas sueltas ────────────────────────────────────────────────────────────

const ENTRADA =
  "w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 outline-none transition focus:border-brand-500 disabled:opacity-50";

function Campo({
  etiqueta,
  ayuda,
  children,
}: {
  etiqueta: string;
  ayuda?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-300">{etiqueta}</label>
      {ayuda && <p className="mb-1.5 text-xs text-gray-500">{ayuda}</p>}
      {children}
    </div>
  );
}

function Esperando({ titulo, detalle }: { titulo: string; detalle: string }) {
  return (
    <div className="flex flex-col items-center py-10 text-center">
      <svg className="mb-4 h-8 w-8 animate-spin text-brand-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <p className="text-sm font-medium text-white">{titulo}</p>
      <p className="mt-1 max-w-sm text-xs text-gray-500">{detalle}</p>
    </div>
  );
}

function subtitulo(modo: Modo, paso: Paso, limites: Limites): string {
  if (modo === "editar") return "Los cambios se validan igual que una identidad recién extraída.";
  if (modo === "clonar") return "Partes de una copia; el original no se toca.";
  return paso === "fotos"
    ? `De ${limites.min_fotos} a ${limites.max_fotos} fotos que compartan estética.`
    : "Revisa y ajusta antes de guardar.";
}

function recorta(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) : s;
}

function mensaje(e: unknown): string {
  return e instanceof Error ? e.message : "Error inesperado.";
}
