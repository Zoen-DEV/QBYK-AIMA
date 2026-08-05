import { etiquetaSubkey } from "./RegenerateImage";

/**
 * Veredicto del QA de conjunto (`job.images.qa_set`), una entrada por ronda.
 *
 * La última dice cómo quedó el carrusel. Es la única comprobación que ve las N piezas
 * JUNTAS: ningún QA por imagen puede detectar que cinco piezas no se parecen entre sí.
 */
export type QaSet = {
  ronda: number;
  ok: boolean;
  verificado: boolean;
  motivo: string;
  peor: string;
  piezas: { subkey: string; ok: boolean; fallos: string[]; motivo: string }[];
}[];

const _FALLO: Record<string, string> = {
  mismo_mundo: "otro mundo visual",
  mismo_sistema_tipografico: "otra tipografía",
  mismo_grade: "otro color",
  sin_marco_ni_bandas: "marco o banda",
};

/**
 * Aviso de conjunto en la compuerta de revisión, en los DOS flujos.
 *
 * Un slide marcado como outlier tiene que ser visualmente evidente aquí, porque el
 * botón de rehacer esa imagen ya está al lado y es exactamente la acción que toca.
 */
export default function AvisoConjunto({ qaSet, compact = false }: { qaSet?: QaSet; compact?: boolean }) {
  const ultima = (qaSet || []).at(-1);
  if (!ultima || !ultima.verificado || ultima.ok) return null;
  const malas = (ultima.piezas || []).filter((p) => !p.ok);
  if (malas.length === 0) return null;
  return (
    <div
      className={`rounded-xl border border-amber-700/50 bg-amber-900/30 text-amber-300 ${
        compact ? "mt-3 px-3 py-2 text-[11px]" : "mb-6 px-4 py-3 text-sm"
      } flex items-start gap-2`}
    >
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
      <div className="space-y-1">
        <p className="font-medium">El carrusel no se lee como un set.</p>
        <ul className="space-y-0.5">
          {malas.map((p) => (
            <li key={p.subkey}>
              <strong className={p.subkey === ultima.peor ? "text-amber-200" : ""}>
                {etiquetaSubkey(p.subkey)}
              </strong>
              {": "}
              {p.fallos.map((f) => _FALLO[f] || f).join(", ")}
              {p.motivo ? ` — ${p.motivo}` : ""}
            </li>
          ))}
        </ul>
        <p className="opacity-80">Rehaz la pieza marcada desde el botón de esta pantalla.</p>
      </div>
    </div>
  );
}
