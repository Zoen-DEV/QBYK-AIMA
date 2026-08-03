import { useState } from "react";
import PublishBar from "./PublishBar";
import { RegenerateButton, conVersion } from "./RegenerateImage";

interface Props {
  jobId: string;
  apiUrl: string;
  initialPosts: { linkedin_text?: string; instagram_text?: string; facebook_text?: string };
  images: {
    has_li_hook: boolean;
    has_fb_hook: boolean;
    has_ig_single: boolean;
    has_ig_story?: boolean;
    has_ig_carousel: boolean;
    ig_slides: string[];
    // Imágenes que se pueden rehacer de a una; lo decide el backend (formato + redes).
    regenerables?: string[];
    blotato_urls: { linkedin: string; instagram: string[]; facebook: string };
  };
  video?: { url?: string; provider?: string; notice?: string; cost?: { credits?: number; usd?: number; segments?: number; seconds?: number; voice?: boolean } | null };
  params: Record<string, string | boolean>;
  liMediaUrls: string[];
  igMediaUrls: string[];
  fbMediaUrls: string[];
}

function LinkedInLogo() {
  return (
    <svg className="w-5 h-5 text-blue-400" fill="currentColor" viewBox="0 0 24 24">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
    </svg>
  );
}

function InstagramLogo() {
  return (
    <svg className="w-5 h-5 text-pink-400" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" />
    </svg>
  );
}

function FacebookLogo() {
  return (
    <svg className="w-5 h-5 text-blue-500" fill="currentColor" viewBox="0 0 24 24">
      <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
    </svg>
  );
}

function TikTokLogo() {
  return (
    <svg className="w-5 h-5 text-cyan-300" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" />
    </svg>
  );
}

function CharCount({ text, min, max }: { text: string; min: number; max: number }) {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const ok = words >= min && words <= max;
  return (
    <span className={`text-xs ${ok ? "text-gray-500" : "text-amber-400"}`}>
      {words} palabras {ok ? "" : `(objetivo: ${min}–${max})`}
    </span>
  );
}

function PostCard({
  platform,
  logo,
  text,
  onTextChange,
  onSave,
  saving,
  imageUrl,
  extraImageUrls,
  imageKeys,
  regenControl,
  videoUrl,
  wordRange,
  verticalMedia,
}: {
  platform: string;
  logo: React.ReactNode;
  text: string;
  onTextChange: (t: string) => void;
  onSave: () => void;
  saving: boolean;
  imageUrl?: string;
  extraImageUrls?: string[];
  // Subkeys en el MISMO orden que las imágenes mostradas: es lo que permite
  // rehacer justo la que se está mirando.
  imageKeys?: string[];
  regenControl?: (subkey: string) => React.ReactNode;
  videoUrl?: string;
  wordRange: [number, number];
  // Medio vertical 9:16 (reel/historia): el medio va en una columna y el caption
  // en otra (dos columnas en md+), en vez del layout apilado a ancho completo.
  verticalMedia?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [slideIdx, setSlideIdx] = useState(0);

  const allImages = imageUrl ? [imageUrl, ...(extraImageUrls || [])] : extraImageUrls || [];
  // Control para rehacer la imagen que se está viendo (null si no aplica).
  const regen = regenControl?.((imageKeys || [])[slideIdx] || "") ?? null;

  // El bloque de texto (vista + edición) es idéntico en ambos layouts.
  const textBlock = editing ? (
    <div className="space-y-3">
      <textarea
        value={text}
        onChange={(e) => onTextChange(e.target.value)}
        rows={12}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-y font-mono leading-relaxed"
      />
      <div className="flex items-center justify-between">
        <CharCount text={text} min={wordRange[0]} max={wordRange[1]} />
        <div className="flex gap-2">
          <button
            onClick={() => setEditing(false)}
            className="text-sm text-gray-500 hover:text-gray-300 transition px-3 py-1.5 rounded-lg"
          >
            Cancelar
          </button>
          <button
            onClick={() => { onSave(); setEditing(false); }}
            disabled={saving}
            className="text-sm bg-brand-500 hover:bg-brand-600 text-white px-4 py-1.5 rounded-lg transition disabled:opacity-50"
          >
            {saving ? "Guardando..." : "Guardar"}
          </button>
        </div>
      </div>
    </div>
  ) : (
    <div className="space-y-3">
      <p className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">{text}</p>
      <div className="flex items-center justify-between pt-1">
        <CharCount text={text} min={wordRange[0]} max={wordRange[1]} />
        <button
          onClick={() => setEditing(true)}
          className="text-xs text-gray-500 hover:text-gray-300 transition flex items-center gap-1"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
          Editar
        </button>
      </div>
    </div>
  );

  // Layout de dos columnas para medios verticales (reel/historia de IG): el
  // medio 9:16 a la izquierda y el caption a la derecha (apilado en móvil).
  if (verticalMedia) {
    return (
      <div className="bg-gray-900 rounded-2xl border border-gray-800 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-800 flex items-center gap-2">
          {logo}
          <span className="font-semibold text-white">{platform}</span>
        </div>
        <div className="md:flex md:items-stretch">
          {(videoUrl || allImages.length > 0) && (
            <div className="bg-gray-950 flex flex-col items-center justify-center gap-3 p-4 md:w-72 md:flex-shrink-0">
              {videoUrl ? (
                <video
                  src={videoUrl}
                  controls
                  playsInline
                  className="w-full max-h-[30rem] rounded-xl bg-black"
                />
              ) : (
                <img
                  src={allImages[0]}
                  alt={`Visual ${platform}`}
                  className="w-full max-h-[30rem] rounded-xl object-contain bg-black"
                />
              )}
              {!videoUrl && regen}
            </div>
          )}
          <div className="p-5 border-t border-gray-800 md:flex-1 md:border-t-0 md:border-l">
            {textBlock}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-2xl border border-gray-800 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-800 flex items-center gap-2">
        {logo}
        <span className="font-semibold text-white">{platform}</span>
      </div>

      {/* Video (takes precedence over image when present) */}
      {videoUrl && (
        <div className="bg-gray-950">
          <video
            src={videoUrl}
            controls
            playsInline
            className="w-full max-h-80 bg-black"
          />
        </div>
      )}

      {/* Image / carousel */}
      {!videoUrl && allImages.length > 0 && (
        <div className="bg-gray-950">
          <div className="relative">
            {/* Las imágenes de feed son 4:5: `object-cover` recortaba el copy que
                renderiza el modelo. Se muestran completas (alto acotado, ancho
                proporcional y centrado) para poder revisar el texto impreso. */}
            <img
              src={allImages[slideIdx]}
              alt={`Visual ${platform} ${slideIdx + 1}`}
              className="mx-auto block w-auto max-w-full max-h-[32rem] object-contain"
            />
            {allImages.length > 1 && (
              <>
                {/* Slide counter */}
                <div className="absolute top-2 right-2 bg-black/60 text-white text-xs font-medium px-2 py-1 rounded-full">
                  {slideIdx + 1} / {allImages.length}
                </div>
                {/* Prev / next arrows */}
                <button
                  type="button"
                  aria-label="Anterior"
                  onClick={() => setSlideIdx((i) => (i - 1 + allImages.length) % allImages.length)}
                  className="absolute left-2 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <button
                  type="button"
                  aria-label="Siguiente"
                  onClick={() => setSlideIdx((i) => (i + 1) % allImages.length)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
                {/* Dots */}
                <div className="absolute bottom-2 left-0 right-0 flex justify-center gap-1.5">
                  {allImages.map((_, i) => (
                    <button
                      key={i}
                      type="button"
                      aria-label={`Slide ${i + 1}`}
                      onClick={() => setSlideIdx(i)}
                      className={`w-2 h-2 rounded-full transition ${i === slideIdx ? "bg-white" : "bg-white/40"}`}
                    />
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Rehacer la imagen que se está viendo — descartar una mala cuesta una
              generación, no el post entero. */}
          {regen && (
            <div className="flex items-center justify-end px-3 py-2 border-t border-gray-800">
              {regen}
            </div>
          )}

          {/* Thumbnail strip — all slides visible at a glance */}
          {allImages.length > 1 && (
            <div className="flex gap-2 overflow-x-auto p-3 border-t border-gray-800">
              {allImages.map((url, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setSlideIdx(i)}
                  className={`flex-shrink-0 rounded-md overflow-hidden border-2 transition ${
                    i === slideIdx ? "border-brand-500" : "border-transparent opacity-60 hover:opacity-100"
                  }`}
                >
                  <img src={url} alt={`Slide ${i + 1}`} className="h-16 w-auto object-contain" />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Text */}
      <div className="p-5">{textBlock}</div>
    </div>
  );
}

export default function ReviewCards({
  jobId,
  apiUrl,
  initialPosts,
  images,
  video,
  params,
  liMediaUrls,
  igMediaUrls,
  fbMediaUrls,
}: Props) {
  // El clip se sirve vía la API (same-origin) en vez de hot-linkear la URL de
  // Blotato: su Content-Type (octet-stream) y los bloqueadores del navegador
  // rompen el <video> con la URL externa.
  const videoUrl = video?.url ? `${apiUrl}/jobs/${jobId}/video` : "";
  const [liText, setLiText] = useState(initialPosts.linkedin_text || "");
  const [igText, setIgText] = useState(initialPosts.instagram_text || "");
  const [fbText, setFbText] = useState(initialPosts.facebook_text || "");
  const [saving, setSaving] = useState(false);

  // Redes destino: el campo nuevo `redes` (lista) manda; si falta, se respeta el
  // `solo` legacy (una sola red) por compatibilidad.
  const redes = Array.isArray(params.redes) ? (params.redes as string[]) : null;
  const solo = (params.solo as string) || "";
  const enabled = (n: string) => (redes ? redes.includes(n) : solo === "" || solo === n);
  const doLinkedIn = enabled("linkedin");
  const doInstagram = enabled("instagram");
  const doFacebook = enabled("facebook");
  // TikTok solo si viene explícito en `redes`: el `solo` vacío del legacy significaba
  // "las redes de siempre" (LI/IG/FB), cuando TikTok todavía no existía en la app.
  const doTiktok = redes ? redes.includes("tiktok") : false;
  const networkCount = [doLinkedIn, doInstagram, doFacebook, doTiktok].filter(Boolean).length;

  // Marca de tiempo de la última regeneración por imagen: la URL de la API no
  // cambia al rehacerla, así que sin esto el navegador seguiría mostrando la vieja.
  const [bust, setBust] = useState<Record<string, number>>({});
  const bustear = (keys: string[]) => {
    const t = Date.now();
    setBust((b) => ({ ...b, ...Object.fromEntries(keys.map((k) => [k, t])) }));
  };
  const apiImage = (key: string) => conVersion(`${apiUrl}/jobs/${jobId}/image/${key}`, bust[key]);

  const liImageUrl = images.has_li_hook ? apiImage("li-hook") : (liMediaUrls[0] || "");
  const fbImageUrl = images.has_fb_hook ? apiImage("fb-hook") : (fbMediaUrls[0] || "");
  const igSingleUrl = images.has_ig_single ? apiImage("ig-single") : (igMediaUrls[0] || "");
  // La historia es una sola imagen vertical compartida por Instagram y Facebook.
  const storyUrl = images.has_ig_story ? apiImage("ig-story") : (igMediaUrls[0] || fbMediaUrls[0] || "");
  // Slides del carrusel: los comparten todas las redes (IG nativo, LinkedIn
  // document carousel, Facebook multi-foto), así que sirven para las tres cards.
  const slideUrls = images.ig_slides.length > 0
    ? images.ig_slides.map((k) => apiImage(k))
    : igMediaUrls.length > 1 ? igMediaUrls : (liMediaUrls.length > 1 ? liMediaUrls : fbMediaUrls);

  const isCarousel = params.formato === "carrusel" || params.formato_instagram === "carrusel";

  // Rehacer una imagen suelta: el backend dice cuáles se pueden (formato + redes) y
  // devuelve cuáles cambiaron — rehacer la portada de un post de imagen única cambia
  // la de las tres redes, porque las tres salen de la misma base.
  const regenerables = images.regenerables || [];
  const regenControl = (subkey: string) =>
    subkey && regenerables.includes(subkey) ? (
      <RegenerateButton apiUrl={apiUrl} jobId={jobId} subkey={subkey} onDone={bustear} />
    ) : null;

  // Reel e Historia usan medio vertical 9:16 → cada card va en dos columnas
  // (medio | caption) y las cards se apilan una arriba de otra a ancho completo,
  // para que el texto del caption tenga espacio de lectura.
  const tipoPost = (params.tipo_post as string) || "post";
  const isVertical = tipoPost === "reel" || tipoPost === "historia";
  // Historia en imagen: una sola imagen vertical, la misma para Instagram y Facebook.
  const esHistoriaImagen = tipoPost === "historia" && !videoUrl;

  // Subkeys en el orden en que cada card muestra sus imágenes.
  const keysFeed = (red: "li-hook" | "fb-hook" | "ig-single") =>
    isCarousel ? images.ig_slides : esHistoriaImagen ? ["ig-story"] : [red];

  async function savePost(field: "linkedin_text" | "instagram_text" | "facebook_text", value: string) {
    setSaving(true);
    const form = new FormData();
    form.set(field, value);
    await fetch(`${apiUrl}/jobs/${jobId}/edit`, { method: "POST", body: form });
    setSaving(false);
  }

  const videoCost = video?.cost || null;

  return (
    <div className="space-y-6">
      {videoUrl && videoCost && (videoCost.credits ?? 0) > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900/60 px-4 py-3 text-sm text-gray-300 flex items-center gap-2">
          <svg className="w-4 h-4 flex-shrink-0 text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 7h6m0 10v-3m0 0V9m0 5H9m0 0v3m0-3V9m-4 12h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
          </svg>
          <span>
            Costo del video: <strong className="text-white">{Math.round(videoCost.credits ?? 0)} créditos</strong>
            {(videoCost.usd ?? 0) > 0 && <> (~${(videoCost.usd ?? 0).toFixed(2)})</>}
            {videoCost.segments ? <span className="text-gray-500"> · {videoCost.segments} segmento(s)</span> : null}
            {videoCost.seconds ? <span className="text-gray-500"> · ~{videoCost.seconds}s</span> : null}
            {videoCost.voice ? <span className="text-gray-500"> · con voz y subtítulos</span> : null}
          </span>
        </div>
      )}
      <div className={`grid gap-6 ${networkCount > 1 && !isVertical ? "lg:grid-cols-2" : "grid-cols-1"}`}>
        {doLinkedIn && (
          <PostCard
            platform="LinkedIn"
            logo={<LinkedInLogo />}
            text={liText}
            onTextChange={setLiText}
            onSave={() => savePost("linkedin_text", liText)}
            saving={saving}
            imageUrl={videoUrl || isCarousel ? undefined : liImageUrl}
            extraImageUrls={videoUrl ? undefined : (isCarousel ? slideUrls : undefined)}
            imageKeys={keysFeed("li-hook")}
            regenControl={regenControl}
            videoUrl={videoUrl || undefined}
            wordRange={[150, 300]}
          />
        )}
        {doInstagram && (
          <PostCard
            platform="Instagram"
            logo={<InstagramLogo />}
            text={igText}
            onTextChange={setIgText}
            onSave={() => savePost("instagram_text", igText)}
            saving={saving}
            imageUrl={videoUrl || isCarousel ? undefined : (esHistoriaImagen ? storyUrl : igSingleUrl)}
            extraImageUrls={videoUrl ? undefined : (isCarousel ? slideUrls : undefined)}
            imageKeys={keysFeed("ig-single")}
            regenControl={regenControl}
            videoUrl={videoUrl || undefined}
            verticalMedia={isVertical}
            wordRange={[80, 150]}
          />
        )}
        {doFacebook && (
          <PostCard
            platform="Facebook"
            logo={<FacebookLogo />}
            text={fbText}
            onTextChange={setFbText}
            onSave={() => savePost("facebook_text", fbText)}
            saving={saving}
            imageUrl={videoUrl || isCarousel ? undefined : (esHistoriaImagen ? storyUrl : fbImageUrl)}
            extraImageUrls={videoUrl ? undefined : (isCarousel ? slideUrls : undefined)}
            imageKeys={keysFeed("fb-hook")}
            regenControl={regenControl}
            videoUrl={videoUrl || undefined}
            verticalMedia={isVertical}
            wordRange={[80, 180]}
          />
        )}
        {doTiktok && (
          <PostCard
            platform="TikTok"
            logo={<TikTokLogo />}
            /* TikTok comparte el caption del reel de Instagram (post_writer lo escribe
               aunque IG no sea destino), así que edita el mismo campo. */
            text={igText}
            onTextChange={setIgText}
            onSave={() => savePost("instagram_text", igText)}
            saving={saving}
            videoUrl={videoUrl || undefined}
            verticalMedia
            wordRange={[80, 150]}
          />
        )}
      </div>

      <PublishBar
        jobId={jobId}
        apiUrl={apiUrl}
        dryRun={!!params.dry_run}
        initialSchedule={typeof params.publicar === "string" ? params.publicar : ""}
      />
    </div>
  );
}
