import { Fragment, useEffect, useRef, useState } from "react";

interface RowResult {
  status?: string;
  url?: string;
  error?: string;
}

// Snapshot del job generado (mismo shape que /jobs/{id}) usado para el preview.
interface RowPreviewData {
  posts: { linkedin_text?: string; instagram_text?: string; facebook_text?: string };
  images: {
    has_li_hook: boolean;
    has_fb_hook: boolean;
    has_ig_single: boolean;
    ig_slides: string[];
    blotato_urls: { linkedin?: string; instagram?: string[]; facebook?: string };
  };
  video?: { url?: string };
  params: Record<string, unknown>;
  li_media_urls: string[];
  ig_media_urls: string[];
  fb_media_urls: string[];
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
  preview: RowPreviewData | null;
}

interface Batch {
  id: string;
  status: string; // running | review | publishing | done
  warnings: string[];
  dry_run: boolean;
  rows: Row[];
}

const POLL_MS = 2500;

// Filas que terminaron de GENERAR (listas para revisar, o con error de generación).
const GEN_DONE = new Set(["ready", "scheduled", "published", "partial", "dry-run", "error", "done"]);
// Filas que terminaron la fase de PUBLICACIÓN.
const PUB_DONE = new Set(["scheduled", "published", "partial", "dry-run", "error", "done"]);
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

// ── Logos de red (mismos que ReviewCards) ───────────────────────────────────

function LinkedInLogo() {
  return (
    <svg className="w-4 h-4 text-blue-400" fill="currentColor" viewBox="0 0 24 24">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
    </svg>
  );
}

function InstagramLogo() {
  return (
    <svg className="w-4 h-4 text-pink-400" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" />
    </svg>
  );
}

function FacebookLogo() {
  return (
    <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 24 24">
      <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
    </svg>
  );
}

// ── Mapeos de estado ────────────────────────────────────────────────────────

function statusMeta(status: string): { label: string; classes: string } {
  switch (status) {
    case "queued":
      return { label: "En cola", classes: "bg-gray-700/40 text-gray-300" };
    case "generating":
      return { label: "Generando", classes: "bg-brand-500/15 text-brand-500" };
    case "ready":
      return { label: "Listo para revisar", classes: "bg-blue-900/40 text-blue-300" };
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
    case "ready":
      gen = "done";
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

// ── Preview de una red (texto + medio), solo lectura ────────────────────────

function NetworkPreview({
  logo,
  name,
  text,
  images,
  videoUrl,
}: {
  logo: React.ReactNode;
  name: string;
  text: string;
  images: string[];
  videoUrl?: string;
}) {
  return (
    <div className="bg-gray-950/60 rounded-xl border border-gray-800 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-800 flex items-center gap-2">
        {logo}
        <span className="text-sm font-medium text-gray-200">{name}</span>
      </div>
      {videoUrl ? (
        <video src={videoUrl} controls playsInline className="w-full max-h-72 bg-black" />
      ) : images.length > 0 ? (
        <div className="flex gap-1.5 overflow-x-auto bg-gray-950 p-2">
          {images.map((url, i) => (
            <img
              key={i}
              src={url}
              alt={`${name} ${i + 1}`}
              className="h-40 w-auto rounded-md object-cover flex-shrink-0"
            />
          ))}
        </div>
      ) : null}
      <p className="p-4 text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">{text}</p>
    </div>
  );
}

// Resuelve los datos de preview de una fila a las redes/medios/textos a mostrar.
function RowPreview({ row, apiUrl }: { row: Row; apiUrl: string }) {
  const p = row.preview;
  if (!p || !row.job_id) return null;

  const redes = Array.isArray(p.params.redes) ? (p.params.redes as string[]) : ["linkedin", "instagram", "facebook"];
  const has = (n: string) => redes.includes(n);
  const videoUrl = p.video?.url || "";
  const isCarousel = p.params.formato_instagram === "carrusel";

  const liImg = p.images.has_li_hook ? `${apiUrl}/jobs/${row.job_id}/image/li-hook` : p.li_media_urls[0] || "";
  const fbImg = p.images.has_fb_hook ? `${apiUrl}/jobs/${row.job_id}/image/fb-hook` : p.fb_media_urls[0] || "";
  const igSingle = p.images.has_ig_single ? `${apiUrl}/jobs/${row.job_id}/image/ig-single` : p.ig_media_urls[0] || "";
  const igSlides =
    p.images.ig_slides.length > 0
      ? p.images.ig_slides.map((k) => `${apiUrl}/jobs/${row.job_id}/image/${k}`)
      : p.ig_media_urls;

  const liImages = videoUrl ? [] : liImg ? [liImg] : [];
  const fbImages = videoUrl ? [] : fbImg ? [fbImg] : [];
  const igImages = videoUrl ? [] : isCarousel ? igSlides : igSingle ? [igSingle] : [];

  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      {has("linkedin") && (
        <NetworkPreview logo={<LinkedInLogo />} name="LinkedIn" text={p.posts.linkedin_text || ""} images={liImages} videoUrl={videoUrl || undefined} />
      )}
      {has("instagram") && (
        <NetworkPreview logo={<InstagramLogo />} name="Instagram" text={p.posts.instagram_text || ""} images={igImages} videoUrl={videoUrl || undefined} />
      )}
      {has("facebook") && (
        <NetworkPreview logo={<FacebookLogo />} name="Facebook" text={p.posts.facebook_text || ""} images={fbImages} videoUrl={videoUrl || undefined} />
      )}
    </div>
  );
}

// ── Cabecera (dinámica según el estado del lote) ────────────────────────────

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  // La navegación "Volver" la provee la página (batches/[id].astro) con el
  // componente compartido BackLink, para que sea igual en toda la app.
  return (
    <div>
      <h1 className="text-2xl font-bold text-white">{title}</h1>
      <p className="text-gray-400 text-sm mt-1 max-w-xl">{subtitle}</p>
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
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  // Cambia para reanudar el polling tras aprobar (el efecto depende de él).
  const [pollKey, setPollKey] = useState(0);
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
        // En "review" se detiene el polling (espera al usuario); approveAndPublish lo
        // reanuda vía pollKey una vez que el POST dejó el lote en "publishing". En
        // "done" termina. En el resto, sigue sondeando.
        if (data.status !== "done" && data.status !== "review") timer = setTimeout(poll, POLL_MS);
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
  }, [batchId, apiUrl, pollKey]);

  const phase = batch?.status; // running | review | publishing | done
  const isReview = phase === "review";
  const isDone = phase === "done";
  const isGenerating = phase === "running";

  // Cronómetro vivo mientras el lote trabaja (genera o publica). En "review" se
  // detiene: la pelota está en el usuario.
  useEffect(() => {
    if (isDone || isReview || fatal) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [isDone, isReview, fatal]);

  async function approveAndPublish() {
    setPublishing(true);
    setPublishError(null);
    try {
      const res = await fetch(`${apiUrl}/sheets/batches/${batchId}/publish`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      startRef.current = Date.now(); // reinicia el cronómetro para la fase de publicación
      // El POST ya dejó el lote en "publishing"; reanuda el polling para seguirla.
      setPollKey((k) => k + 1);
    } catch (e) {
      setPublishError(e instanceof Error ? e.message : String(e));
      setPublishing(false);
    }
  }

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
  // El progreso del hero depende de la fase: generación vs. publicación.
  const genDone = batch.rows.filter((r) => GEN_DONE.has(r.status)).length;
  const pubDone = batch.rows.filter((r) => PUB_DONE.has(r.status)).length;
  const done = isGenerating ? genDone : pubDone;
  const errors = batch.rows.filter((r) => NEEDS_ATTENTION.has(r.status)).length;
  const readyRows = batch.rows.filter((r) => r.status === "ready");
  const genErrors = batch.rows.filter((r) => r.status === "error").length;
  const percent = isReview ? 100 : total ? Math.round((done / total) * 100) : 0;
  const active = batch.rows.find((r) => r.status === "generating" || r.status === "publishing");

  const title = isGenerating
    ? "Creando tus posts"
    : isReview
    ? "Revisa y aprueba tus posts"
    : phase === "publishing"
    ? "Publicando tus posts"
    : errors > 0
    ? "Lote completado con avisos"
    : batch.dry_run
    ? "Simulación completada"
    : "¡Listo! Tus posts están programados";

  const subtitle = isGenerating
    ? "Generamos el contenido de cada post. Puede tomar varios minutos — puedes dejar esta pestaña abierta."
    : isReview
    ? "Generamos todo. Revisa el contenido de cada post abajo y publícalo cuando estés conforme."
    : phase === "publishing"
    ? "Estamos publicando y programando cada post. No cierres esta pestaña."
    : errors > 0
    ? "Algunos posts necesitan tu atención. Revisa el detalle abajo."
    : batch.dry_run
    ? "Todo se generó sin publicar (dry-run)."
    : "Cada post quedó programado en su fecha y hora. Ya puedes cerrar esta pestaña.";

  const heroIcon = isReview ? "done" : !isDone ? "running" : errors > 0 ? "warn" : "done";
  const heroLabel = isGenerating
    ? "Generando contenido…"
    : isReview
    ? "Contenido generado — pendiente de aprobación"
    : phase === "publishing"
    ? "Publicando lote…"
    : errors > 0
    ? "Completado con avisos"
    : "Lote completado";
  const activity = active
    ? `${active.status === "publishing" ? "Publicando y programando" : "Generando contenido para"} el post #${active.index}`
    : !isDone && !isReview && done < total
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
              {isGenerating
                ? `${done} de ${total} posts generados`
                : isReview
                ? `${readyRows.length} de ${total} posts listos para publicar`
                : `${done} de ${total} posts procesados`}
              {errors > 0 && <span className="text-amber-400"> · {errors} con avisos</span>}
            </div>
          </div>
          <div className="ml-auto text-right flex-shrink-0">
            <div className="text-3xl font-bold text-white tabular-nums leading-none">{percent}%</div>
            {!isDone && !isReview && (
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
          {!isDone && !isReview && (
            <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/20 to-transparent" />
          )}
        </div>

        {activity && (
          <div className="mt-3 text-sm text-brand-500 flex items-center gap-2">
            <Spinner className="w-3.5 h-3.5" />
            {activity}
          </div>
        )}

        {/* Aprobación: aparece cuando el lote terminó de generar y espera al usuario */}
        {isReview && (
          <div className="mt-5 border-t border-gray-800 pt-5">
            {genErrors > 0 && (
              <p className="text-xs text-amber-400 mb-3">
                {genErrors} post(s) no se generaron y se omitirán al publicar.
              </p>
            )}
            <div className="flex flex-col sm:flex-row sm:items-center gap-3">
              <button
                onClick={approveAndPublish}
                disabled={publishing || readyRows.length === 0}
                className={`inline-flex items-center justify-center gap-2 text-white text-sm font-semibold px-5 py-2.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  batch.dry_run ? "bg-amber-600 hover:bg-amber-700" : "bg-green-600 hover:bg-green-700"
                }`}
              >
                {publishing ? (
                  <>
                    <Spinner className="w-4 h-4" />
                    {batch.dry_run ? "Simulando…" : "Publicando…"}
                  </>
                ) : batch.dry_run ? (
                  <>Confirmar (dry-run)</>
                ) : (
                  <>Publicar y programar {readyRows.length} post{readyRows.length === 1 ? "" : "s"}</>
                )}
              </button>
              <a
                href="/bulk"
                className="inline-flex items-center justify-center text-sm text-gray-400 hover:text-gray-200 px-4 py-2.5 rounded-lg border border-gray-800 hover:border-gray-700 transition-colors"
              >
                Cancelar
              </a>
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Cada post se programa según la columna fecha_hora de su fila (vacío = se publica de inmediato).
            </p>
            {publishError && (
              <div className="mt-3 bg-red-900/30 border border-red-700/50 rounded-lg px-4 py-2.5 text-red-300 text-sm">
                {publishError}
              </div>
            )}
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

              {/* Preview del contenido generado (en revisión y mientras se publica) */}
              {(isReview || phase === "publishing") && row.preview && row.status !== "error" && (
                <RowPreview row={row} apiUrl={apiUrl} />
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
