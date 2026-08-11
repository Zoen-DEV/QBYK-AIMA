import { Fragment, useEffect, useRef, useState } from "react";
import AvisoBandas, { type Bandas } from "./AvisoBandas";
import AvisoConjunto, { type QaSet } from "./AvisoConjunto";
import { RegenerateButton, conVersion, etiquetaSubkey } from "./RegenerateImage";

interface RowResult {
  status?: string;
  url?: string;
  error?: string;
}

// Snapshot del job generado (mismo shape que /jobs/{id}) usado para el preview.
interface RowPreviewData {
  posts: {
    linkedin_text?: string;
    instagram_text?: string;
    facebook_text?: string;
    // Guion del video: editable en la compuerta de revisión previa del lote (los
    // mismos campos que el preview del flujo individual).
    video_prompt?: string;
    video_style?: string;
    video_storyboard?: string[];
    video_voiceover?: string[];
    // Prompts de las imágenes (escena, no el texto impreso): misma compuerta.
    image_prompt?: string;
    image_style?: string;
    image_slide_prompts?: string[];
    // Copy que el modelo IMPRIME en la pieza (hook de portada + una idea por slide).
    image_text?: { hook?: string; slides?: string[] };
  };
  images: {
    has_li_hook: boolean;
    has_fb_hook: boolean;
    has_ig_single: boolean;
    has_ig_story?: boolean;
    ig_slides: string[];
    // Imágenes que se pueden rehacer de a una desde la revisión del lote.
    regenerables?: string[];
    // Veredicto del detector de passe-partout/letterbox sobre la imagen cruda.
    bandas?: Bandas;
    // Veredicto del QA de conjunto: las N piezas vistas juntas.
    qa_set?: QaSet;
    blotato_urls: { linkedin?: string; instagram?: string[]; facebook?: string };
  };
  // Avisos sobre los prompts de esta fila (mismo lint que el preview individual).
  lint?: { campo: string; nivel: string; mensaje: string }[];
  // Qué campos visuales pide este job y cuáles siguen vacíos. Lo decide el backend
  // (misma fuente que el escritor y el lint): el editor dibuja sus campos a partir de
  // esto y no de lo que el modelo entregó, para que cuando la escritura devuelva los
  // prompts vacíos haya dónde escribirlos.
  needs?: { imagenes?: boolean; video?: boolean; captions?: string[]; n_info?: number;
            beats?: string[]; arco?: string; arco_funcion?: string; escenario?: string;
            n_shots?: number; faltan?: string[] };
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
  status: string; // running | preview | generating | review | publishing | done
  warnings: string[];
  dry_run: boolean;
  rows: Row[];
}

const POLL_MS = 2500;

// Fases en las que el lote NO avanza solo: espera una acción del usuario (o terminó).
const STOP_POLL = new Set(["preview", "review", "done"]);

// Filas que terminaron de ESCRIBIR (esperando revisión del guion, o con error).
const WRITE_DONE = new Set(["preview", "ready", "scheduled", "published", "partial", "dry-run", "error", "done"]);
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
    case "writing":
      return { label: "Escribiendo", classes: "bg-brand-500/15 text-brand-500" };
    case "generating":
      return { label: "Generando", classes: "bg-brand-500/15 text-brand-500" };
    case "preview":
      return { label: "Revisar guion", classes: "bg-purple-900/40 text-purple-300" };
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

// Las tres fases por fila, espejo del flujo individual: escribir el contenido,
// generar el medio (tras aprobar el guion) y publicar/programar.
function rowSteps(row: Row): { label: string; status: StepStatus }[] {
  const reachedPublish = row.result && Object.keys(row.result).length > 0;
  // Señal de que la fase de escritura llegó a producir algo: si hay texto de post,
  // el fallo fue después (generando el medio), no escribiendo.
  const p = row.preview?.posts;
  const wroteOk = Boolean(p && (p.linkedin_text || p.instagram_text || p.facebook_text));
  let write: StepStatus = "pending";
  let gen: StepStatus = "pending";
  let pub: StepStatus = "pending";
  let pubLabel = "Publicar y programar";

  switch (row.status) {
    case "writing":
      write = "running";
      break;
    case "preview":
      write = "done";
      break;
    case "generating":
      write = "done";
      gen = "running";
      break;
    case "ready":
      write = "done";
      gen = "done";
      break;
    case "publishing":
      write = "done";
      gen = "done";
      pub = "running";
      break;
    case "scheduled":
    case "published":
    case "done":
      write = "done";
      gen = "done";
      pub = "done";
      break;
    case "dry-run":
      write = "done";
      gen = "done";
      pub = "warn";
      pubLabel = "Simulado (no se publicó)";
      break;
    case "partial":
      write = "done";
      gen = "done";
      pub = "warn";
      break;
    case "error":
      // El error se marca en la fase donde ocurrió: publicación si ya hubo intento,
      // generación de medio si el guion llegó a existir, escritura si ni eso.
      if (reachedPublish) {
        write = "done";
        gen = "done";
        pub = "error";
      } else if (wroteOk) {
        write = "done";
        gen = "error";
      } else {
        write = "error";
      }
      break;
    // "queued" → las tres pendientes.
  }

  return [
    { label: "Escribir", status: write },
    { label: "Generar medio", status: gen },
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
  verticalMedia,
}: {
  logo: React.ReactNode;
  name: string;
  text: string;
  images: string[];
  videoUrl?: string;
  // Medio vertical 9:16 (reel/historia): el medio va en una columna y el texto en
  // otra (mismo layout que ReviewCards en el flujo individual), en vez del layout
  // apilado a ancho completo.
  verticalMedia?: boolean;
}) {
  if (verticalMedia) {
    return (
      <div className="bg-gray-950/60 rounded-xl border border-gray-800 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-gray-800 flex items-center gap-2">
          {logo}
          <span className="text-sm font-medium text-gray-200">{name}</span>
        </div>
        <div className="md:flex md:items-stretch">
          {(videoUrl || images.length > 0) && (
            <div className="bg-gray-950 flex items-center justify-center p-4 md:w-72 md:flex-shrink-0">
              {videoUrl ? (
                <video src={videoUrl} controls playsInline className="w-full max-h-[30rem] rounded-xl bg-black" />
              ) : (
                <img
                  src={images[0]}
                  alt={`Visual ${name}`}
                  className="w-full max-h-[30rem] rounded-xl object-contain bg-black"
                />
              )}
            </div>
          )}
          <div className="p-4 border-t border-gray-800 md:flex-1 md:border-t-0 md:border-l">
            <p className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">{text}</p>
          </div>
        </div>
      </div>
    );
  }

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

// ── Editor de los prompts de una fila (video e imágenes) ─────────────────────
// La compuerta previa a gastar créditos, espejo de /jobs/{id}/preview del flujo
// individual: se edita con el MISMO endpoint (`POST /jobs/{id}/edit`), así la
// revisión del lote no es un camino aparte que pueda divergir. Guarda al salir
// del campo; el botón de aprobar del lote solo dispara la generación.
// Nombre visible de cada caption en el editor del lote.
const CAPTION_LABEL: Record<string, string> = {
  linkedin_text: "LinkedIn",
  instagram_text: "Instagram",
  facebook_text: "Facebook",
};

// Qué encadena las imágenes del carrusel. Mismo texto que el preview del individual: es
// la misma decisión y tiene que leerse igual en las dos compuertas.
const ARCO_AYUDA: Record<string, string> = {
  transformacion: "el mismo objeto vuelve en cada slide con su estado cambiado",
  cadena: "cada slide arranca en lo que dejó el anterior",
  recorrido: "un mismo lugar recorrido por partes, un rincón por slide",
  escala: "piezas del mismo sistema: la pieza, el conjunto, el sitio donde vive",
};

// Nombre de cada nivel de texto de un slide. Duplicado del preview individual por el
// mismo motivo que `ARCO_AYUDA`: es el mismo campo y tiene que leerse igual en las dos
// compuertas. Las claves las decide `architect.json` → `sistemas_texto`.
const BLOQUE_ETIQUETA: Record<string, string> = {
  etiqueta: "Etiqueta",
  titular: "Titular",
  cuerpo: "Cuerpo",
  apoyo: "Apoyo",
};

function RowPromptEditor({ row, apiUrl }: { row: Row; apiUrl: string }) {
  const p = row.preview?.posts;
  const [storyboard, setStoryboard] = useState((p?.video_storyboard || []).join("\n"));
  const [voiceover, setVoiceover] = useState((p?.video_voiceover || []).join("\n"));
  const [style, setStyle] = useState(p?.video_style || "");
  const [prompt, setPrompt] = useState(p?.video_prompt || "");
  const [imgPrompt, setImgPrompt] = useState(p?.image_prompt || "");
  const [imgStyle, setImgStyle] = useState(p?.image_style || "");
  const [imgSlides, setImgSlides] = useState((p?.image_slide_prompts || []).join("\n"));
  // Copy impreso en la pieza: el preview del individual ya lo dejaba editar acá no
  // estaba, así que un aviso sobre el texto no se podía arreglar sin salir del lote.
  const [imgHook, setImgHook] = useState(p?.image_text?.hook || "");
  // Un texto por slide, en su propio campo (antes: un textarea con una idea por línea).
  // El array conserva la POSICIÓN: vaciar el slide 2 lo deja vacío en vez de correr el
  // 3 a su sitio. Se dimensiona con los slides del carrusel, no con lo que entregó el
  // modelo, para que los huecos que falten se vean y se puedan llenar.
  const needs = row.preview?.needs || {};
  const nInfo: number = Number(needs.n_info) || 0;
  const beats: string[] = Array.isArray(needs.beats) ? needs.beats : [];
  // El arco y el mundo que la app congeló en esta fila. Se muestran y no se editan,
  // igual que en el preview del individual: son la decisión que explica por qué las
  // escenas de esta fila dicen lo que dicen.
  const arco: string = needs.arco || "";
  const escenario: string = needs.escenario || "";
  // El sistema de texto congelado en esta fila: qué bloques imprime cada slide. Igual
  // que los beats, viene del backend y no se deduce acá — si esta pantalla y el prompt
  // contaran bloques distintos, se editarían dos campos donde la pieza imprime tres.
  const bloquesSistema: { clave: string; palabras: number[] }[] = Array.isArray(
    needs.sistema_texto?.bloques
  )
    ? needs.sistema_texto.bloques
    : [{ clave: "titular", palabras: [3, 8] }];
  const bloquesDe = (slide: unknown): Record<string, string> =>
    slide && typeof slide === "object"
      ? (slide as Record<string, string>)
      : { titular: typeof slide === "string" ? slide : "" };
  const [imgTexts, setImgTexts] = useState<Record<string, string>[]>(() =>
    Array.from({ length: nInfo }, (_, i) => bloquesDe((p?.image_text?.slides || [])[i]))
  );
  // Captions de las redes destino. Se dibujan desde `needs.captions` (lo que el job
  // pide) y no desde lo que el modelo entregó: un caption vacío tiene que verse como
  // un hueco editable, no desaparecer junto con su campo.
  const captions: string[] = Array.isArray(needs.captions) ? needs.captions : [];
  const capsDe = (v: RowPreviewData["posts"] | undefined): Record<string, string> => ({
    linkedin_text: v?.linkedin_text || "",
    instagram_text: v?.instagram_text || "",
    facebook_text: v?.facebook_text || "",
  });
  const [caps, setCaps] = useState<Record<string, string>>(() => capsDe(p));
  const [state, setState] = useState<"idle" | "saving" | "ok" | "error">("idle");
  // Avisos sobre los prompts (escenas repetidas, clichés, escenas que faltan, shots
  // sin línea de voz). Vienen del servidor —el mismo lint que el preview individual—
  // y se refrescan con la respuesta de cada guardado.
  const [lint, setLint] = useState(row.preview?.lint || []);
  // Campos visuales que siguen vacíos: mientras haya alguno se ofrece el reintento.
  const [faltan, setFaltan] = useState<string[]>(needs.faltan || []);
  const [rewriting, setRewriting] = useState(false);
  const [rewriteErr, setRewriteErr] = useState("");

  const lines = (s: string) => s.split("\n").filter((l) => l.trim()).length;
  const sbCount = lines(storyboard);
  const voCount = lines(voiceover);
  // Filas a mostrar del guion: las que pide el job, aunque el modelo no las entregara.
  const nShots = Math.max(Number(needs.n_shots) || 0, sbCount, voCount);

  // Video o imágenes según lo que pide la fila (un job es una cosa o la otra). Sale
  // del backend y no de lo entregado: con la escritura vacía, antes no se dibujaba
  // ningún campo y el aviso pedía escribir prompts en un formulario inexistente.
  const hasVideo = !!needs.video;
  const hasImages = !!needs.imagenes;
  if (!p || (!hasVideo && !hasImages)) return null;

  async function reescribir() {
    if (!row.job_id) return;
    setRewriting(true);
    setRewriteErr("");
    try {
      // Se guarda primero lo editado: el reintento solo pide lo que siga faltando,
      // así lo que ya se escribió a mano no se pisa.
      await save();
      const res = await fetch(`${apiUrl}/jobs/${row.job_id}/rewrite`, { method: "POST" });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const np = data.posts || {};
      setImgPrompt(np.image_prompt || "");
      setImgStyle(np.image_style || "");
      setImgSlides((np.image_slide_prompts || []).join("\n"));
      setImgHook(np.image_text?.hook || "");
      setImgTexts(Array.from({ length: nInfo }, (_, i) => (np.image_text?.slides || [])[i] || ""));
      setStoryboard((np.video_storyboard || []).join("\n"));
      setVoiceover((np.video_voiceover || []).join("\n"));
      setStyle(np.video_style || "");
      setPrompt(np.video_prompt || "");
      setCaps(capsDe(np));
      setLint(data.lint || []);
      setFaltan(data.needs?.faltan || []);
    } catch (e) {
      setRewriteErr(String((e as Error).message || e));
    } finally {
      setRewriting(false);
    }
  }

  async function save() {
    if (!row.job_id) return;
    setState("saving");
    try {
      const body = new FormData();
      captions.forEach((c) => body.append(c, caps[c] ?? ""));
      if (hasVideo) {
        body.append("video_storyboard", storyboard);
        body.append("video_voiceover", voiceover);
        body.append("video_style", style);
        body.append("video_prompt", prompt);
      }
      if (hasImages) {
        body.append("image_prompt", imgPrompt);
        body.append("image_style", imgStyle);
        body.append("image_slide_prompts", imgSlides);
        body.append("image_hook", imgHook);
        imgTexts.forEach((bloques, i) =>
          bloquesSistema.forEach((b) =>
            body.append(`image_slide_${b.clave}_${i}`, bloques[b.clave] ?? "")
          )
        );
      }
      const res = await fetch(`${apiUrl}/jobs/${row.job_id}/edit`, { method: "POST", body });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLint(data?.lint || []);
      // Escribir a mano lo que faltaba también retira la oferta de reintentar.
      setFaltan(data?.needs?.faltan || []);
      setState("ok");
    } catch {
      setState("error");
    }
  }

  const inputCls =
    "w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-gray-200 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-brand-500 font-mono";

  return (
    <div className="mt-3 pt-3 border-t border-gray-800 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-purple-300">
          {hasVideo ? "Guion del video" : "Prompts de las imágenes"}
        </span>
        <span className="text-[11px] text-gray-500">
          Revisa antes de generar — acá el cambio es gratis.
        </span>
        <span className="ml-auto text-[11px]">
          {state === "saving" && <span className="text-gray-500">Guardando…</span>}
          {state === "ok" && <span className="text-green-400">Guardado</span>}
          {state === "error" && <span className="text-red-400">No se pudo guardar</span>}
        </span>
      </div>

      {captions.length > 0 && (
        <div className="space-y-2">
          <span className="text-[11px] text-gray-400 block">
            Texto de los posts <span className="text-gray-600">(el caption de cada red)</span>
          </span>
          {captions.map((c) => (
            <label key={c} className="block">
              <span className="text-[11px] text-gray-500 mb-1 block">{CAPTION_LABEL[c] || c}</span>
              <textarea
                value={caps[c] ?? ""}
                rows={5}
                className={inputCls}
                onChange={(e) => { setCaps((v) => ({ ...v, [c]: e.target.value })); setState("idle"); }}
                onBlur={save}
              />
            </label>
          ))}
        </div>
      )}

      {hasImages && (
        <>
          {/* Copy impreso: lo renderiza el propio modelo desde el prompt, así que se
              edita ANTES de generar, igual que en el preview del individual. */}
          <p className="text-[11px] text-gray-500">
            Encierra una palabra o frase entre <code className="text-gray-400">**dobles asteriscos**</code>{" "}
            para elegir qué se pinta en el color de acento. Sin marcas, lo elige el modelo. Los
            asteriscos no se imprimen.
          </p>
          <label className="block">
            <span className="text-[11px] text-gray-400 mb-1 block">
              Texto de la portada (lo que se imprime en la imagen)
            </span>
            <textarea
              value={imgHook}
              rows={2}
              className={inputCls}
              onChange={(e) => { setImgHook(e.target.value); setState("idle"); }}
              onBlur={save}
            />
          </label>
          {(arco || escenario) && (
            <div className="rounded-lg border border-gray-800 bg-gray-950/60 p-2.5 space-y-1">
              {arco && (
                <p className="text-[11px] text-gray-500">
                  <span className="text-gray-400">Arco:</span>{" "}
                  <span className="font-mono text-gray-300">{arco}</span>
                  {ARCO_AYUDA[arco] ? ` — ${ARCO_AYUDA[arco]}` : ""}
                </p>
              )}
              {escenario && (
                <p className="text-[11px] text-gray-500">
                  <span className="text-gray-400">Mundo:</span> {escenario}
                </p>
              )}
            </div>
          )}
          {nInfo > 0 && (
            <div className="space-y-2">
              <span className="text-[11px] text-gray-400 block">Texto de cada slide</span>
              <p className="text-[11px] text-gray-500">
                El carrusel tiene que CONTAR el contenido: quien no vio el video termina el último
                slide sabiendo la cosa. Cada slide tiene su función —la etiqueta de la izquierda— y
                su imagen se genera para ese momento.
                {bloquesSistema.length > 1 ? (
                  <span>
                    {" "}Los campos son los niveles de texto que imprime esta identidad
                    {needs.sistema_texto?.nombre ? ` (${needs.sistema_texto.nombre})` : ""}.
                  </span>
                ) : (
                  <span>
                    {" "}Si la idea tiene dos tiempos, sepáralos con una raya espaciada
                    (<code className="text-gray-400">Titular — apoyo</code>): lo de antes va grande
                    arriba y lo de después, pequeño, al pie. La raya no se imprime.
                  </span>
                )}
              </p>
              {imgTexts.map((bloques, i) => (
                <div key={i} className="flex items-start gap-2">
                  {/* El beat sale del backend (`needs.beats`), la misma secuencia con la
                      que se genera la imagen de ese slide: quien reescribe el texto a
                      mano tiene que ver qué función cumple ahí. */}
                  <span className="mt-2 w-20 flex-shrink-0 text-[11px] text-gray-500">
                    <span className="block font-mono">Slide {i + 2}</span>
                    {beats[i] && <span className="block text-gray-600">{beats[i]}</span>}
                  </span>
                  <div className="flex-1 space-y-1.5">
                    {bloquesSistema.map((b) => (
                      <label key={b.clave} className="block">
                        {bloquesSistema.length > 1 && (
                          <span className="text-[11px] text-gray-600 mb-0.5 block">
                            {BLOQUE_ETIQUETA[b.clave] || b.clave}
                            {b.palabras?.[1] ? ` · hasta ${b.palabras[1]} palabras` : ""}
                          </span>
                        )}
                        <textarea
                          value={bloques[b.clave] || ""}
                          rows={b.clave === "cuerpo" ? 3 : 2}
                          className={inputCls}
                          onChange={(e) => {
                            const v = e.target.value;
                            setImgTexts((prev) =>
                              prev.map((x, j) => (j === i ? { ...x, [b.clave]: v } : x))
                            );
                            setState("idle");
                          }}
                          onBlur={save}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          <label className="block">
            <span className="text-[11px] text-gray-400 mb-1 block">
              Portada (escena en inglés, sacada de la transcripción)
            </span>
            <textarea
              value={imgPrompt}
              rows={3}
              className={inputCls}
              onChange={(e) => { setImgPrompt(e.target.value); setState("idle"); }}
              onBlur={save}
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-gray-400 mb-1 block">
              Dirección de arte — luz, material, óptica y acabado (va igual en todas las imágenes;
              la paleta la pone la marca)
            </span>
            <textarea
              value={imgStyle}
              rows={2}
              className={inputCls}
              onChange={(e) => { setImgStyle(e.target.value); setState("idle"); }}
              onBlur={save}
            />
          </label>
          {nInfo > 0 && (
            <label className="block">
              <span className="text-[11px] text-gray-400 mb-1 block">
                Slides del carrusel — una escena por línea ({lines(imgSlides)} de {nInfo})
              </span>
              <textarea
                value={imgSlides}
                rows={Math.max(3, nInfo)}
                className={inputCls}
                onChange={(e) => { setImgSlides(e.target.value); setState("idle"); }}
                onBlur={save}
              />
            </label>
          )}
        </>
      )}

      {hasVideo && (
        <label className="block">
          <span className="text-[11px] text-gray-400 mb-1 block">
            Voz en off — una línea por shot ({voCount} de {nShots})
          </span>
          <textarea
            value={voiceover}
            rows={Math.max(3, nShots)}
            className={inputCls}
            onChange={(e) => { setVoiceover(e.target.value); setState("idle"); }}
            onBlur={save}
          />
        </label>
      )}

      {hasVideo && (
        <label className="block">
          <span className="text-[11px] text-gray-400 mb-1 block">
            Storyboard (en inglés) — un shot por línea ({sbCount} de {nShots})
          </span>
          <textarea
            value={storyboard}
            rows={Math.max(3, nShots)}
            className={inputCls}
            onChange={(e) => { setStoryboard(e.target.value); setState("idle"); }}
            onBlur={save}
          />
        </label>
      )}

      {lint.length > 0 && (
        <ul className="space-y-1">
          {lint.map((aviso, i) => (
            <li
              key={i}
              className={`text-[11px] ${aviso.nivel === "alto" ? "text-amber-400" : "text-gray-500"}`}
            >
              • {aviso.mensaje}
            </li>
          ))}
        </ul>
      )}

      {/* Reintento manual de la escritura: vuelve a pedirle al modelo SOLO los campos
          que dejó vacíos, sin relanzar la fila ni perder lo ya editado. Mismo endpoint
          que usa el preview del individual. */}
      {faltan.length > 0 && (
        <div className="flex items-center gap-3 flex-wrap bg-gray-950 border border-gray-800 rounded-lg px-3 py-2">
          <span className="text-[11px] text-gray-400 flex-1 min-w-[12rem]">
            Faltan {faltan.length} campo(s) visual(es). Puedes escribirlos arriba o pedírselos otra
            vez al modelo (cuesta una llamada al LLM, no créditos de imagen).
          </span>
          <button
            type="button"
            disabled={rewriting}
            onClick={reescribir}
            className="bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-100 text-[11px] font-medium py-1.5 px-3 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {rewriting ? "Escribiendo…" : "Reintentar escritura"}
          </button>
        </div>
      )}
      {rewriteErr && (
        <p className="text-[11px] text-red-400">No se pudo reescribir: {rewriteErr}</p>
      )}

      {/* Look-lock y escena única del VIDEO: solo tienen sentido si la fila genera
          video. En una fila de imágenes se pintaban igual, invitando a editar dos
          campos que ese job no usa. */}
      {hasVideo && (
        <>
          <label className="block">
            <span className="text-[11px] text-gray-400 mb-1 block">Estilo visual (video_style)</span>
            <textarea
              value={style}
              rows={2}
              className={inputCls}
              onChange={(e) => { setStyle(e.target.value); setState("idle"); }}
              onBlur={save}
            />
          </label>

          {/* Escena única: la usa el pipeline cuando no hay storyboard (reel de 1 shot). */}
          <label className="block">
            <span className="text-[11px] text-gray-400 mb-1 block">Escena principal (video_prompt)</span>
            <textarea
              value={prompt}
              rows={3}
              className={inputCls}
              onChange={(e) => { setPrompt(e.target.value); setState("idle"); }}
              onBlur={save}
            />
          </label>
        </>
      )}
    </div>
  );
}

// Resuelve los datos de preview de una fila a las redes/medios/textos a mostrar.
function RowPreview({ row, apiUrl }: { row: Row; apiUrl: string }) {
  // Marca de tiempo por imagen rehecha: la URL de la API no cambia al regenerarla.
  // Vive acá (no en el componente principal) para que el polling del lote no la pise.
  const [bust, setBust] = useState<Record<string, number>>({});
  const p = row.preview;
  if (!p || !row.job_id) return null;

  const redes = Array.isArray(p.params.redes) ? (p.params.redes as string[]) : ["linkedin", "instagram", "facebook"];
  const has = (n: string) => redes.includes(n);
  const apiImage = (key: string) => conVersion(`${apiUrl}/jobs/${row.job_id}/image/${key}`, bust[key]);
  const bustear = (keys: string[]) => {
    const t = Date.now();
    setBust((b) => ({ ...b, ...Object.fromEntries(keys.map((k) => [k, t])) }));
  };
  // Mismo criterio que en ReviewCards: el clip se sirve vía la API (same-origin)
  // porque la URL externa de Blotato no es confiable dentro de un <video>.
  const videoUrl = p.video?.url ? `${apiUrl}/jobs/${row.job_id}/video` : "";
  const isCarousel = p.params.formato === "carrusel" || p.params.formato_instagram === "carrusel";
  // Reel e Historia usan medio vertical 9:16 → cada card va en dos columnas
  // (medio | texto) y las cards se apilan a ancho completo, igual que en la
  // revisión del flujo individual (ReviewCards).
  const tipoPost = (p.params.tipo_post as string) || "post";
  const isVertical = tipoPost === "reel" || tipoPost === "historia";

  const liImg = p.images.has_li_hook ? apiImage("li-hook") : p.li_media_urls[0] || "";
  const fbImg = p.images.has_fb_hook ? apiImage("fb-hook") : p.fb_media_urls[0] || "";
  const story = p.images.has_ig_story ? apiImage("ig-story") : "";
  const igSingle = story || (p.images.has_ig_single ? apiImage("ig-single") : p.ig_media_urls[0] || "");
  // Slides del carrusel: compartidos por todas las redes activas de la fila.
  const slides =
    p.images.ig_slides.length > 0
      ? p.images.ig_slides.map((k) => apiImage(k))
      : p.ig_media_urls.length > 1 ? p.ig_media_urls
      : p.li_media_urls.length > 1 ? p.li_media_urls
      : p.fb_media_urls;

  const liImages = videoUrl ? [] : isCarousel ? slides : liImg ? [liImg] : [];
  const fbImages = videoUrl ? [] : isCarousel ? slides : story ? [story] : fbImg ? [fbImg] : [];
  const igImages = videoUrl ? [] : isCarousel ? slides : igSingle ? [igSingle] : [];

  // Rehacer una imagen suelta de ESTA fila, con el mismo endpoint que el flujo
  // individual. El backend dice cuáles se pueden rehacer (vacío mientras la fila no
  // esté generada, y siempre en video: ahí la unidad de reintento no es una imagen).
  const regenerables = row.status === "ready" ? p.images.regenerables || [] : [];

  return (
    <>
      <div className={`mt-3 grid gap-3 ${isVertical ? "" : "sm:grid-cols-2"}`}>
        {has("linkedin") && (
          <NetworkPreview logo={<LinkedInLogo />} name="LinkedIn" text={p.posts.linkedin_text || ""} images={liImages} videoUrl={videoUrl || undefined} verticalMedia={isVertical} />
        )}
        {has("instagram") && (
          <NetworkPreview logo={<InstagramLogo />} name="Instagram" text={p.posts.instagram_text || ""} images={igImages} videoUrl={videoUrl || undefined} verticalMedia={isVertical} />
        )}
        {has("facebook") && (
          <NetworkPreview logo={<FacebookLogo />} name="Facebook" text={p.posts.facebook_text || ""} images={fbImages} videoUrl={videoUrl || undefined} verticalMedia={isVertical} />
        )}
      </div>
      {row.status === "ready" && <AvisoConjunto qaSet={p.images.qa_set} compact />}
      {row.status === "ready" && <AvisoBandas bandas={p.images.bandas} compact />}
      {regenerables.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-gray-500">Rehacer imagen:</span>
          {regenerables.map((k) => (
            <RegenerateButton
              key={k}
              apiUrl={apiUrl}
              jobId={row.job_id as string}
              subkey={k}
              label={etiquetaSubkey(k)}
              onDone={bustear}
              compact
            />
          ))}
        </div>
      )}
    </>
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
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
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
        // En las dos compuertas ("preview" y "review") se detiene el polling: la
        // pelota está en el usuario. approveScripts/approveAndPublish lo reanudan vía
        // pollKey una vez que el POST movió el lote de fase. En "done" termina.
        if (!STOP_POLL.has(data.status)) timer = setTimeout(poll, POLL_MS);
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

  const phase = batch?.status; // running | preview | generating | review | publishing | done
  const isPreview = phase === "preview";      // compuerta 1: revisión de guiones
  const isReview = phase === "review";        // compuerta 2: revisión del medio
  const isDone = phase === "done";
  const isWriting = phase === "running";      // fase 1: escritura
  const isGenerating = phase === "generating"; // fase 2: medio
  const waiting = isPreview || isReview;      // el lote espera al usuario

  // Cronómetro vivo mientras el lote trabaja. En las compuertas se detiene: la
  // pelota está en el usuario.
  useEffect(() => {
    if (isDone || waiting || fatal) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [isDone, waiting, fatal]);

  async function approveScripts() {
    setApproving(true);
    setApproveError(null);
    try {
      const res = await fetch(`${apiUrl}/sheets/batches/${batchId}/generate`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      startRef.current = Date.now(); // reinicia el cronómetro para la fase de medio
      setPollKey((k) => k + 1);
      setApproving(false);
    } catch (e) {
      setApproveError(e instanceof Error ? e.message : String(e));
      setApproving(false);
    }
  }

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
  // El progreso del hero depende de la fase: escritura vs. medio vs. publicación.
  const writeDone = batch.rows.filter((r) => WRITE_DONE.has(r.status)).length;
  const genDone = batch.rows.filter((r) => GEN_DONE.has(r.status)).length;
  const pubDone = batch.rows.filter((r) => PUB_DONE.has(r.status)).length;
  const done = isWriting ? writeDone : isGenerating ? genDone : pubDone;
  const errors = batch.rows.filter((r) => NEEDS_ATTENTION.has(r.status)).length;
  const previewRows = batch.rows.filter((r) => r.status === "preview");
  const readyRows = batch.rows.filter((r) => r.status === "ready");
  const genErrors = batch.rows.filter((r) => r.status === "error").length;
  const percent = waiting ? 100 : total ? Math.round((done / total) * 100) : 0;
  const active = batch.rows.find(
    (r) => r.status === "writing" || r.status === "generating" || r.status === "publishing"
  );

  const title = isWriting
    ? "Escribiendo tus posts"
    : isPreview
    ? "Revisa los guiones antes de generar"
    : isGenerating
    ? "Generando el contenido visual"
    : isReview
    ? "Revisa y aprueba tus posts"
    : phase === "publishing"
    ? "Publicando tus posts"
    : errors > 0
    ? "Lote completado con avisos"
    : batch.dry_run
    ? "Simulación completada"
    : "¡Listo! Tus posts están programados";

  const subtitle = isWriting
    ? "Escribimos el texto y el guion de cada post. Todavía no gastamos créditos de generación."
    : isPreview
    ? "Acá el cambio es gratis: corregí el guion de video de cada fila antes de gastar créditos. Al aprobar, se generan las imágenes y los videos."
    : isGenerating
    ? "Generamos las imágenes y los videos de cada post. Puede tomar varios minutos — puedes dejar esta pestaña abierta."
    : isReview
    ? "Generamos todo. Revisa el contenido de cada post abajo y publícalo cuando estés conforme."
    : phase === "publishing"
    ? "Estamos publicando y programando cada post. No cierres esta pestaña."
    : errors > 0
    ? "Algunos posts necesitan tu atención. Revisa el detalle abajo."
    : batch.dry_run
    ? "Todo se generó sin publicar (dry-run)."
    : "Cada post quedó programado en su fecha y hora. Ya puedes cerrar esta pestaña.";

  const heroIcon = waiting ? "done" : !isDone ? "running" : errors > 0 ? "warn" : "done";
  const heroLabel = isWriting
    ? "Escribiendo contenido…"
    : isPreview
    ? "Guiones escritos — pendiente de aprobación"
    : isGenerating
    ? "Generando imágenes y videos…"
    : isReview
    ? "Contenido generado — pendiente de aprobación"
    : phase === "publishing"
    ? "Publicando lote…"
    : errors > 0
    ? "Completado con avisos"
    : "Lote completado";
  const activityVerb =
    active?.status === "publishing"
      ? "Publicando y programando"
      : active?.status === "writing"
      ? "Escribiendo"
      : "Generando el medio de";
  const activity = active
    ? `${activityVerb} el post #${active.index}`
    : !isDone && !waiting && done < total
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
              {isWriting
                ? `${done} de ${total} posts escritos`
                : isPreview
                ? `${previewRows.length} de ${total} guiones listos para revisar`
                : isGenerating
                ? `${done} de ${total} posts generados`
                : isReview
                ? `${readyRows.length} de ${total} posts listos para publicar`
                : `${done} de ${total} posts procesados`}
              {errors > 0 && <span className="text-amber-400"> · {errors} con avisos</span>}
            </div>
          </div>
          <div className="ml-auto text-right flex-shrink-0">
            <div className="text-3xl font-bold text-white tabular-nums leading-none">{percent}%</div>
            {!isDone && !waiting && (
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
          {!isDone && !waiting && (
            <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/20 to-transparent" />
          )}
        </div>

        {activity && (
          <div className="mt-3 text-sm text-brand-500 flex items-center gap-2">
            <Spinner className="w-3.5 h-3.5" />
            {activity}
          </div>
        )}

        {/* Compuerta 1: guiones escritos, todavía sin gastar créditos de generación */}
        {isPreview && (
          <div className="mt-5 border-t border-gray-800 pt-5">
            {genErrors > 0 && (
              <p className="text-xs text-amber-400 mb-3">
                {genErrors} fila(s) no se pudieron escribir y se omitirán.
              </p>
            )}
            <div className="flex flex-col sm:flex-row sm:items-center gap-3">
              <button
                onClick={approveScripts}
                disabled={approving || previewRows.length === 0}
                className="inline-flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold px-5 py-2.5 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {approving ? (
                  <>
                    <Spinner className="w-4 h-4" />
                    Iniciando…
                  </>
                ) : (
                  <>
                    Aprobar y generar {previewRows.length} post{previewRows.length === 1 ? "" : "s"}
                  </>
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
              A partir de acá se consumen créditos de Higgsfield. Los cambios en los guiones se
              guardan solos al salir de cada campo.
            </p>
            {approveError && (
              <div className="mt-3 bg-red-900/30 border border-red-700/50 rounded-lg px-4 py-2.5 text-red-300 text-sm">
                {approveError}
              </div>
            )}
          </div>
        )}

        {/* Compuerta 2: medio generado, espera aprobación para publicar */}
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
            row.error || row.result?.linkedin || row.result?.instagram || row.result?.facebook
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

              {/* Compuerta 1: prompts editables, antes de gastar créditos */}
              {isPreview && row.status === "preview" && row.preview && (
                <RowPromptEditor row={row} apiUrl={apiUrl} />
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
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
