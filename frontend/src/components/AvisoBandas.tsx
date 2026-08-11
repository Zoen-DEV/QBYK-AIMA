import { etiquetaSubkey } from "./RegenerateImage";

/**
 * Veredicto del detector de bandas por imagen (`job.images.bandas`).
 *
 * Cada entrada es el registro de intentos de esa imagen; el último dice cómo quedó.
 * El backend lo mide sobre la imagen CRUDA del proveedor —antes del recorte, del
 * texto de la plantilla y del grade— así que lo que reporta es lo que hizo el modelo.
 */
export type Bandas = Record<string, { intento: number; bordes: string[] }[]>;

/** Imágenes que TERMINARON con banda de color liso, con los bordes afectados. */
export function conBanda(bandas?: Bandas): { subkey: string; bordes: string[] }[] {
  return Object.entries(bandas || {})
    .map(([subkey, intentos]) => ({
      subkey,
      bordes: (intentos || []).at(-1)?.bordes || [],
    }))
    .filter((x) => x.bordes.length > 0);
}

const _NOMBRE: Record<string, string> = {
  marco: "un marco en los cuatro lados",
  arriba: "arriba",
  abajo: "abajo",
  izquierda: "a la izquierda",
  derecha: "a la derecha",
};

/**
 * Aviso de passe-partout / letterbox en la compuerta de revisión.
 *
 * Lo comparten los DOS flujos (la revisión del individual y la del lote) porque el
 * defecto y la acción son los mismos: el botón de rehacer esa imagen ya está al lado,
 * y es exactamente lo que toca hacer. El backend ya reintentó una vez con el sangrado
 * reforzado; lo que llega aquí es lo que sobrevivió a ese reintento.
 */
export default function AvisoBandas({ bandas, compact = false }: { bandas?: Bandas; compact?: boolean }) {
  const malas = conBanda(bandas);
  if (malas.length === 0) return null;
  const detalle = malas
    .map((m) => `${etiquetaSubkey(m.subkey)} (${m.bordes.map((b) => _NOMBRE[b] || b).join(", ")})`)
    .join(" · ");
  return (
    <div
      className={`rounded-xl border border-amber-700/50 bg-amber-900/30 text-amber-300 ${
        compact ? "mt-3 px-3 py-2 text-[11px]" : "mb-6 px-4 py-3 text-sm"
      } flex items-start gap-2`}
    >
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
      <span>
        {malas.length === 1 ? "Una imagen salió" : `${malas.length} imágenes salieron`} con una banda de
        color liso en el borde en vez de a sangre: {detalle}. Ya se reintentó una vez reforzando el
        sangrado. Rehazla desde el botón de esta misma pantalla si rompe el set.
      </span>
    </div>
  );
}
