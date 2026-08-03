import { useCallback, useEffect, useState } from "react";
import {
  ErrorApi,
  activar,
  actualizar,
  eliminar,
  listar,
  type Identidad,
  type Limites,
} from "../lib/identidades";
import { IdentityModal } from "./IdentityModal";

/**
 * Sección «Identidades visuales» de la página de cuenta.
 *
 * La identidad **activa** es la que usan los posts que se generen a partir de ahora
 * (se congela en cada job al crearlo, así que cambiarla no altera nada que ya esté
 * generándose). La identidad de la casa siempre está y no se puede eliminar ni editar:
 * vive en `prompts/brand.json`, no en la base. Para partir de ella, se clona.
 */
export function VisualIdentities() {
  const [identidades, setIdentidades] = useState<Identidad[]>([]);
  const [limites, setLimites] = useState<Limites | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState("");
  const [aviso, setAviso] = useState("");
  const [ocupada, setOcupada] = useState("");
  const [modal, setModal] = useState<{ modo: "nueva" | "clonar" | "editar"; base?: Identidad } | null>(null);
  const [renombrando, setRenombrando] = useState<{ id: string; valor: string } | null>(null);

  const recargar = useCallback(async () => {
    try {
      const data = await listar();
      setIdentidades(data.identities);
      setLimites(data.limites);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudieron cargar las identidades.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    recargar();
  }, [recargar]);

  async function accion(id: string, fn: () => Promise<unknown>, exito: string) {
    setOcupada(id);
    setError("");
    try {
      await fn();
      await recargar();
      setAviso(exito);
    } catch (e) {
      setError(e instanceof ErrorApi ? e.motivos.join(" · ") : (e as Error).message);
    } finally {
      setOcupada("");
    }
  }

  function borrar(ident: Identidad) {
    const ok = window.confirm(
      `¿Eliminar «${ident.name}»?\n\nNo se puede deshacer. Los posts que ya se generaron con ella no cambian.`,
    );
    if (ok) accion(ident.id, () => eliminar(ident.id), `Identidad «${ident.name}» eliminada.`);
  }

  function confirmarNombre() {
    if (!renombrando) return;
    const { id, valor } = renombrando;
    const actual = identidades.find((i) => i.id === id);
    setRenombrando(null);
    if (!actual || valor.trim() === actual.name) return;
    accion(id, () => actualizar(id, { name: valor }), "Nombre actualizado.");
  }

  if (cargando) {
    return <p className="text-sm text-gray-400">Cargando identidades…</p>;
  }

  return (
    <div>
      {error && (
        <div className="mb-5 rounded-xl border border-red-700/50 bg-red-900/30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}
      {aviso && !error && (
        <div className="mb-5 rounded-xl border border-emerald-700/50 bg-emerald-900/30 px-4 py-3 text-sm text-emerald-300">
          {aviso}
        </div>
      )}

      <ul className="space-y-3">
        {identidades.map((ident) => {
          const trabajando = ocupada === ident.id;
          return (
            <li
              key={ident.id}
              className={`rounded-2xl border p-5 transition ${
                ident.is_default
                  ? "border-brand-500/60 bg-brand-500/5"
                  : "border-gray-800 bg-gray-900"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {renombrando?.id === ident.id ? (
                      <input
                        autoFocus
                        value={renombrando.valor}
                        maxLength={limites?.nombre_max ?? 60}
                        onChange={(e) => setRenombrando({ id: ident.id, valor: e.target.value })}
                        onBlur={confirmarNombre}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") confirmarNombre();
                          if (e.key === "Escape") setRenombrando(null);
                        }}
                        className="rounded-lg border border-gray-700 bg-gray-950 px-2 py-1 text-sm text-white outline-none focus:border-brand-500"
                      />
                    ) : (
                      <h3 className="truncate font-medium text-white">{ident.name}</h3>
                    )}
                    {ident.is_default && <Insignia tono="brand">Activa</Insignia>}
                    {ident.is_system && <Insignia tono="gris">De la casa</Insignia>}
                  </div>

                  <Paleta json={ident.identity_json} />

                  <p className="mt-2.5 line-clamp-2 text-xs text-gray-500">
                    {ident.identity_json.tipografia || "sin tipografía definida"}
                    {ident.identity_json.tono_visual ? ` · ${ident.identity_json.tono_visual}` : ""}
                  </p>
                </div>

                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {!ident.is_default && (
                    <Boton
                      onClick={() => accion(ident.id, () => activar(ident.id), `Ahora se genera con «${ident.name}».`)}
                      disabled={trabajando}
                      principal
                    >
                      Usar esta
                    </Boton>
                  )}
                  {ident.is_system ? (
                    <Boton onClick={() => setModal({ modo: "clonar", base: ident })} disabled={trabajando}>
                      Clonar
                    </Boton>
                  ) : (
                    <>
                      <Boton
                        onClick={() => setRenombrando({ id: ident.id, valor: ident.name })}
                        disabled={trabajando}
                      >
                        Renombrar
                      </Boton>
                      <Boton onClick={() => setModal({ modo: "editar", base: ident })} disabled={trabajando}>
                        Editar
                      </Boton>
                      {/* La de la casa nunca muestra "eliminar": no hay fila que borrar. */}
                      <Boton onClick={() => borrar(ident)} disabled={trabajando} peligro>
                        Eliminar
                      </Boton>
                    </>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        onClick={() => setModal({ modo: "nueva" })}
        disabled={!limites}
        className="mt-5 inline-flex items-center gap-2 rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-600 disabled:opacity-50"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
        </svg>
        Agregar identidad visual
      </button>

      {modal && limites && (
        <IdentityModal
          modo={modal.modo}
          base={modal.base}
          limites={limites}
          onCerrar={() => setModal(null)}
          onGuardada={(mensaje) => {
            setModal(null);
            setAviso(mensaje);
            setError("");
            recargar();
          }}
        />
      )}
    </div>
  );
}

/** Muestras de color en el orden real de la paleta: fondo, texto, acento. */
function Paleta({ json }: { json: Identidad["identity_json"] }) {
  if (!json.paleta?.length) {
    return <p className="mt-2 text-xs text-gray-600">Sin paleta definida.</p>;
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      {json.paleta.map((hex, i) => (
        <span
          key={`${hex}-${i}`}
          title={`${json.paleta_nombres?.[i] ?? hex} — ${hex}`}
          style={{ backgroundColor: hex }}
          className="h-7 w-7 rounded-md border border-white/10 shadow-inner"
        />
      ))}
      <span className="ml-1 font-mono text-[11px] text-gray-600">{json.paleta.join(" ")}</span>
    </div>
  );
}

function Insignia({ tono, children }: { tono: "brand" | "gris"; children: React.ReactNode }) {
  const clases =
    tono === "brand"
      ? "bg-brand-500/15 text-brand-500 border-brand-500/40"
      : "bg-gray-800 text-gray-400 border-gray-700";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${clases}`}>
      {children}
    </span>
  );
}

function Boton({
  onClick,
  disabled,
  principal,
  peligro,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  principal?: boolean;
  peligro?: boolean;
  children: React.ReactNode;
}) {
  const base = "rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-40";
  const tono = principal
    ? "border-brand-500/60 bg-brand-500/10 text-brand-500 hover:bg-brand-500/20"
    : peligro
      ? "border-gray-700 text-gray-400 hover:border-red-700/60 hover:text-red-400"
      : "border-gray-700 text-gray-300 hover:border-gray-500 hover:text-white";
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`${base} ${tono}`}>
      {children}
    </button>
  );
}
