import { Fragment, useEffect, useRef, useState } from "react";

interface RowResult {
  status?: string;
  url?: string;
  error?: string;
}

interface Row {
  index: number;
  source: string;
  label: string;
  title: string;
  schedule: string;
  status: string;
  job_id: string | null;
  result: { linkedin?: RowResult; instagram?: RowResult; facebook?: RowResult; dry_run?: boolean };
  error: string | null;
}

interface Batch {
  id: string;
  status: string;
  warnings: string[];
  dry_run: boolean;
  rows: Row[];
}

const POLL_MS = 2500;

// Filas que llegaron a un estado final (para el contador de progreso).
const TERMINAL = new Set(["scheduled", "published", "partial", "dry-run", "error", "done"]);
// Filas terminadas que requieren atención del usuario.
const NEEDS_ATTENTION = new Set(["error", "partial"]);

type StepStatus = "pending" | "running" | "done" | "warn" | "error";

function fmtTime(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ── Iconos (mismo lenguaje visual que ProgressView) ─────────────────────────

function Spinner({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  );
}

function StatusIcon({ status, className = "w-5 h-5" }: { status: StepStatus; className?: string }) {
  if (status === "running") return <Spinner className={`${className} text-brand-500`} />;
  if (status === "done") {
    return (
      <svg className={`${className} text-green-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (status === "warn") {
    return (
      <svg className={`${className} text-amber-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg className={`${className} text-red-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  return <div className={`${className} rounded-full border-2 border-gray-700`} />;
}

// ── Mapeos de estado ────────────────────────────────────────────────────────

function statusMeta(status: string): { label: string; classes: string } {
  switch (status) {
    case "queued":
      return { label: "En cola", classes: "bg-gray-700/40 text-gray-300" };
    case "generating":
      return { label: "Generando", classes: "bg-brand-500/15 text-brand-500" };
    case "publishing":
      return { label: "Publicando", classes: "bg-brand-500/15 text-brand-500" };
    case "scheduled":
      return { label: "Programado", classes: "bg-green-900/40 text-green-300" };
    case "published":
      return { label: "Publicado", classes: "bg-green-900/40 text-green-300" };
    case "partial":
      return { label: "Parcial", classes: "bg-amber-900/40 text-amber-300" };
    case "dry-run":
      return { label: "Simulado", classes: "bg-amber-900/40 text-amber-300" };
    case "error":
      return { label: "Error", classes: "bg-red-900/40 text-red-300" };
    case "done":
      return { label: "Listo", classes: "bg-green-900/40 text-green-300" };
    default:
      return { label: status, classes: "bg-gray-700/40 text-gray-300" };
  }
}

// Las dos fases por fila: generar el contenido y publicarlo/programarlo.
function rowSteps(row: Row): { label: string; status: StepStatus }[] {
  const reachedPublish = row.result && Object.keys(row.result).length > 0;
  let gen: StepStatus = "pending";
  let pub: StepStatus = "pending";
  let pubLabel = "Publicar y programar";

  switch (row.status) {
    case "generating":
      gen = "running";
      break;
    case "publishing":
      gen = "done";
      pub = "running";
      break;
    case "scheduled":
    case "published":
    case "done":
      gen = "done";
      pub = "done";
      break;
    case "dry-run":
      gen = "done";
      pub = "warn";
      pubLabel = "Simulado (no se publicó)";
      break;
    case "partial":
      gen = "done";
      pub = "warn";
      break;
    case "error":
      if (reachedPublish) {
        gen = "done";
        pub = "error";
      } else {
        gen = "error";
      }
      break;
    // "queued" → ambas pendientes.
  }

  return [
    { label: "Generar contenido", status: gen },
    { label: pubLabel, status: pub },
  ];
}

function NetworkResult({ name, res }: { name: string; res?: RowResult }) {
  if (!res) return null;
  if (res.error) return <span className="text-xs text-red-400">{name}: {res.error}</span>;
  if (res.url) {
    return (
      <a href={res.url} target="_blank" rel="noopener" className="text-xs text-brand-500 hover:underline inline-flex items-center gap-1">
        Ver en {name}
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
        </svg>
      </a>
    );
  }
  if (res.status) return <span className="text-xs text-gray-500">{name}: {res.status}</span>;
  return null;
}

// ── Cabecera (dinámica según el estado del lote) ────────────────────────────

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold text-white">{title}</h1>
        <p className="text-gray-400 text-sm mt-1 max-w-xl">{subtitle}</p>
      </div>
      <a href="/" className="text-sm text-gray-500 hover:text-gray-300 transition whitespace-nowrap mt-1">
        ← Inicio
      </a>
    </div>
  );
}

// ── Skeleton mientras llega el primer dato (la pantalla nunca queda vacía) ──

function Skeleton() {
  return (
    <div className="space-y-5">
      <div className="bg-gray-900 rounded-2xl border border-gray-800 p-6 animate-pulse">
        <div className="flex items-center justify-between mb-5">
          <div className="h-4 w-40 bg-gray-800 rounded" />
          <div className="h-7 w-16 bg-gray-800 rounded" />
        </div>
        <div className="h-2.5 bg-gray-800 rounded-full" />
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="bg-gray-900 rounded-2xl border border-gray-800 p-4 animate-pulse">
          <div className="flex items-center justify-between mb-3">
            <div className="h-4 w-1/2 bg-gray-800 rounded" />
            <div className="h-6 w-20 bg-gray-800 rounded-full" />
          </div>
          <div className="h-3 w-3/4 bg-gray-800 rounded" />
        </div>
      ))}
    </div>
  );
}

// ── Componente principal ────────────────────────────────────────────────────

export default function BulkProgress({ batchId, apiUrl }: { batchId: string; apiUrl: string }) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const res = await fetch(`${apiUrl}/sheets/batches/${batchId}`);
        if (res.status === 404) {
          if (active) setFatal("No encontramos este lote. Es posible que el servidor se haya reiniciado.");
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: Batch = await res.json();
        if (!active) return;
        setBatch(data);
        setConnError(null);
        if (data.status !== "done") timer = setTimeout(poll, POLL_MS);
      } catch (e) {
        if (!active) return;
        setConnError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(poll, POLL_MS * 2);
      }
    }

    poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [batchId, apiUrl]);

  const isDone = batch?.status === "done";

  // Cronómetro vivo mientras el lote corre: deja claro que NO está congelado.
  useEffect(() => {
    if (isDone || fatal) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [isDone, fatal]);

  // Estado fatal: el lote no existe / no se puede recuperar.
  if (fatal) {
    return (
      <div className="space-y-6">
        <Header title="No se pudo cargar el lote" subtitle="Algo salió mal al recuperar el progreso." />
        <div className="bg-red-900/20 border border-red-700/40 rounded-2xl px-5 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <span className="text-red-300 text-sm">{fatal}</span>
          <a
            href="/bulk"
            className="inline-flex items-center justify-center bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors whitespace-nowrap"
          >
            Crear de nuevo
          </a>
        </div>
      </div>
    );
  }

  // Primer fetch en curso: skeleton + cabecera neutra.
  if (!batch) {
    return (
      <div className="space-y-6">
        <Header title="Creando tus posts" subtitle="Preparando el lote…" />
        {connError && (
          <p className="text-xs text-gray-500 flex items-center gap-2">
            <Spinner className="w-3.5 h-3.5" /> Conectando con el servidor…
          </p>
        )}
        <Skeleton />
      </div>
    );
  }

  const total = batch.rows.length;
  const done = batch.rows.filter((r) => TERMINAL.has(r.status)).length;
  const errors = batch.rows.filter((r) => NEEDS_ATTENTION.has(r.status)).length;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const active = batch.rows.find((r) => r.status === "generating" || r.status === "publishing");

  const title = !isDone
    ? "Creando tus posts"
    : errors > 0
    ? "Lote completado con avisos"
    : batch.dry_run
    ? "Simulación completada"
    : "¡Listo! Tus posts están programados";

  const subtitle = !isDone
    ? "Generamos y programamos cada post automáticamente. Puede tomar varios minutos — puedes dejar esta pestaña abierta."
    : errors > 0
    ? "Algunos posts necesitan tu atención. Revisa el detalle abajo."
    : batch.dry_run
    ? "Todo se generó sin publicar. Revisa cada post antes de lanzarlo de verdad."
    : "Cada post quedó programado en su fecha y hora. Ya puedes cerrar esta pestaña.";

  const heroIcon = !isDone ? "running" : errors > 0 ? "warn" : "done";
  const heroLabel = !isDone ? "Procesando lote…" : errors > 0 ? "Completado con avisos" : "Lote completado";
  const activity = active
    ? `${active.status === "publishing" ? "Publicando y programando" : "Generando contenido para"} el post #${active.index}`
    : !isDone && done < total
    ? "Preparando el siguiente post…"
    : null;

  return (
    <div className="space-y-5">
      <Header title={title} subtitle={subtitle} />

      {/* Aviso de reconexión (mantiene visible el último estado conocido). */}
      {connError && (
        <div className="bg-amber-900/20 border border-amber-700/40 rounded-xl px-4 py-2.5 text-amber-300 text-xs flex items-center gap-2">
          <Spinner className="w-3.5 h-3.5" />
          Se perdió la conexión un momento. Reintentando automáticamente…
        </div>
      )}

      {/* Hero de progreso */}
      <div className="bg-gray-900 rounded-2xl border border-gray-800 p-6">
        <div className="flex items-center gap-3 mb-4">
          <StatusIcon status={heroIcon as StepStatus} className="w-5 h-5" />
          <div className="min-w-0">
            <div className="text-base font-semibold text-white flex items-center gap-2 flex-wrap">
              {heroLabel}
              {batch.dry_run && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-900/40 text-amber-300 uppercase tracking-wide">
                  Dry-run
                </span>
              )}
            </div>
            <div className="text-sm text-gray-400">
              {done} de {total} posts listos
              {errors > 0 && <span className="text-amber-400"> · {errors} con avisos</span>}
            </div>
          </div>
          <div className="ml-auto text-right flex-shrink-0">
            <div className="text-3xl font-bold text-white tabular-nums leading-none">{percent}%</div>
            {!isDone && (
              <div className="text-xs text-gray-500 tabular-nums mt-1 flex items-center justify-end gap-1">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {fmtTime(elapsed)}
              </div>
            )}
          </div>
        </div>

        {/* Barra de progreso (con brillo en movimiento mientras corre) */}
        <div className="relative h-2.5 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-500 rounded-full transition-all duration-700"
            style={{ width: `${percent}%` }}
          />
          {!isDone && (
            <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/20 to-transparent" />
          )}
        </div>

        {activity && (
          <div className="mt-3 text-sm text-brand-500 flex items-center gap-2">
            <Spinner className="w-3.5 h-3.5" />
            {activity}
          </div>
        )}

        {/* Cierre: resumen + acciones */}
        {isDone && (
          <div className="mt-5 flex flex-col sm:flex-row sm:items-center gap-3">
            <a
              href="/bulk"
              className="inline-flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors"
            >
              Crear más posts
            </a>
            <a
              href="/"
              className="inline-flex items-center justify-center text-sm text-gray-400 hover:text-gray-200 px-4 py-2.5 rounded-lg border border-gray-800 hover:border-gray-700 transition-colors"
            >
              Ir al inicio
            </a>
          </div>
        )}
      </div>

      {/* Avisos del parseo del sheet */}
      {batch.warnings && batch.warnings.length > 0 && (
        <div className="bg-amber-900/20 border border-amber-700/40 rounded-xl px-4 py-3 text-amber-300 text-xs space-y-1">
          {batch.warnings.map((w, i) => (
            <div key={i} className="flex gap-2">
              <span className="flex-shrink-0">•</span>
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Filas */}
      <div className="space-y-3">
        {batch.rows.map((row) => {
          const isActive = row.index === active?.index;
          const steps = rowSteps(row);
          const meta = statusMeta(row.status);
          const hasFooter = Boolean(
            row.error || row.result?.linkedin || row.result?.instagram || row.result?.facebook || (row.job_id && row.status !== "error")
          );

          return (
            <div
              key={row.index}
              className={`rounded-2xl border p-4 transition-colors ${
                isActive
                  ? "border-brand-500/50 bg-brand-500/[0.04] ring-1 ring-brand-500/30"
                  : "border-gray-800 bg-gray-900"
              }`}
            >
              {/* Encabezado de la fila */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-gray-500">#{row.index}</span>
                    <span className="text-[10px] uppercase tracking-wide font-semibold text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">
                      {row.source === "youtube" ? "YouTube" : "Texto"}
                    </span>
                  </div>
                  <p className="text-sm text-gray-200 truncate" title={row.title}>
                    {row.title}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {row.schedule ? `Programado: ${row.schedule}` : "Publicar ahora"}
                  </p>
                </div>
                <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${meta.classes}`}>
                  {(row.status === "generating" || row.status === "publishing") && <Spinner className="w-3.5 h-3.5" />}
                  {meta.label}
                </span>
              </div>

              {/* Mini-stepper de las 2 fases */}
              <div className="mt-3 flex items-center gap-2 text-xs">
                {steps.map((s, i) => (
                  <Fragment key={s.label}>
                    <span className="inline-flex items-center gap-1.5">
                      <StatusIcon status={s.status} className="w-4 h-4" />
                      <span
                        className={
                          s.status === "pending"
                            ? "text-gray-600"
                            : s.status === "running"
                            ? "text-brand-500"
                            : s.status === "error"
                            ? "text-red-400"
                            : s.status === "warn"
                            ? "text-amber-400"
                            : "text-gray-400"
                        }
                      >
                        {s.label}
                      </span>
                    </span>
                    {i < steps.length - 1 && (
                      <span className={`h-px flex-1 ${steps[i].status === "done" ? "bg-gray-700" : "bg-gray-800"}`} />
                    )}
                  </Fragment>
                ))}
              </div>

              {/* Barra indeterminada en la fila activa: trabajo continuo en curso */}
              {isActive && (
                <div className="mt-3 relative h-1 overflow-hidden rounded-full bg-gray-800">
                  <div className="absolute inset-y-0 left-0 w-2/5 rounded-full bg-brand-500 animate-indeterminate" />
                </div>
              )}

              {/* Resultados / error / link al detalle */}
              {hasFooter && (
                <div className="mt-3 pt-3 border-t border-gray-800 flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  {row.error && <span className="text-xs text-red-400">{row.error}</span>}
                  <NetworkResult name="LinkedIn" res={row.result?.linkedin} />
                  <NetworkResult name="Instagram" res={row.result?.instagram} />
                  <NetworkResult name="Facebook" res={row.result?.facebook} />
                  {row.job_id && row.status !== "error" && (
                    <a href={`/jobs/${row.job_id}/result`} className="text-xs text-gray-500 hover:text-gray-300 ml-auto">
                      Ver detalle →
                    </a>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
