import { useState } from "react";

/** Nombre legible de una imagen del post (el subkey es interno del pipeline). */
export function etiquetaSubkey(key: string): string {
  if (key === "ig-story") return "Historia";
  if (key === "li-hook") return "LinkedIn";
  if (key === "fb-hook") return "Facebook";
  if (key === "ig-single") return "Instagram";
  if (key === "ig-0") return "Portada";
  const n = Number(key.split("-")[1]);
  return Number.isFinite(n) ? `Slide ${n + 1}` : key;
}

/**
 * Rehace UNA imagen del post ya generado (`POST /jobs/{id}/regenerate`).
 *
 * Es la compuerta de revisión hecha útil: hasta ahora, un slide que salía mal
 * obligaba a rehacer el post entero. Cuesta una generación, así que la llamada
 * tarda lo que tarde el modelo y el botón lo dice mientras espera.
 *
 * `onDone` recibe los subkeys que efectivamente cambiaron — rehacer la portada de
 * un post de imagen única cambia la de las tres redes — para que quien lo use
 * refresque esas imágenes (el navegador las tiene cacheadas por URL).
 */
export function RegenerateButton({
  apiUrl,
  jobId,
  subkey,
  label,
  onDone,
  compact,
}: {
  apiUrl: string;
  jobId: string;
  subkey: string;
  label?: string;
  onDone: (subkeys: string[]) => void;
  compact?: boolean;
}) {
  const [state, setState] = useState<"idle" | "working" | "error">("idle");
  const [msg, setMsg] = useState("");

  async function run() {
    setState("working");
    setMsg("");
    try {
      const body = new FormData();
      body.set("subkey", subkey);
      const res = await fetch(`${apiUrl}/jobs/${jobId}/regenerate`, { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      setState("idle");
      if (data?.aviso) setMsg(data.aviso);
      onDone(data?.subkeys?.length ? data.subkeys : [subkey]);
    } catch (e) {
      setState("error");
      setMsg(e instanceof Error ? e.message : "No se pudo rehacer la imagen.");
    }
  }

  const working = state === "working";
  const texto = label ?? "Rehacer imagen";

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={run}
        disabled={working}
        title="Genera otra vez esta imagen (consume créditos de Higgsfield)"
        className={`inline-flex items-center gap-1.5 rounded-lg border border-gray-700 text-gray-300 transition hover:border-gray-500 hover:text-white disabled:opacity-50 ${
          compact ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs"
        }`}
      >
        <svg
          className={`w-3.5 h-3.5 ${working ? "animate-spin" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        {working ? "Rehaciendo..." : texto}
      </button>
      {working && <span className="text-[11px] text-gray-500">puede tardar ~1 min</span>}
      {!working && msg && (
        <span className={`text-[11px] ${state === "error" ? "text-red-400" : "text-amber-400"}`}>{msg}</span>
      )}
    </span>
  );
}

/**
 * Cache-busting de las imágenes servidas por la API.
 *
 * `/jobs/{id}/image/{key}` devuelve siempre la misma URL, así que tras rehacer una
 * imagen el navegador seguiría mostrando la vieja: se le agrega la marca de tiempo
 * de la última regeneración de esa key.
 */
export function conVersion(url: string, version?: number): string {
  if (!url || !version) return url;
  return `${url}${url.includes("?") ? "&" : "?"}v=${version}`;
}
