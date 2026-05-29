import { useState } from "react";
import PublishBar from "./PublishBar";

interface Props {
  jobId: string;
  apiUrl: string;
  initialPosts: { linkedin_text?: string; instagram_text?: string };
  images: {
    has_li_hook: boolean;
    has_ig_single: boolean;
    has_ig_carousel: boolean;
    ig_slides: string[];
    blotato_urls: { linkedin: string; instagram: string[] };
  };
  video?: { url?: string; provider?: string; notice?: string };
  params: Record<string, string | boolean>;
  liMediaUrls: string[];
  igMediaUrls: string[];
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
  videoUrl,
  wordRange,
}: {
  platform: string;
  logo: React.ReactNode;
  text: string;
  onTextChange: (t: string) => void;
  onSave: () => void;
  saving: boolean;
  imageUrl?: string;
  extraImageUrls?: string[];
  videoUrl?: string;
  wordRange: [number, number];
}) {
  const [editing, setEditing] = useState(false);
  const [slideIdx, setSlideIdx] = useState(0);

  const allImages = imageUrl ? [imageUrl, ...(extraImageUrls || [])] : extraImageUrls || [];

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

      {/* Image */}
      {!videoUrl && allImages.length > 0 && (
        <div className="relative bg-gray-950">
          <img
            src={allImages[slideIdx]}
            alt={`Visual ${platform}`}
            className="w-full object-cover max-h-64"
          />
          {allImages.length > 1 && (
            <div className="absolute bottom-2 left-0 right-0 flex justify-center gap-1.5">
              {allImages.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setSlideIdx(i)}
                  className={`w-2 h-2 rounded-full transition ${i === slideIdx ? "bg-white" : "bg-white/40"}`}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Text */}
      <div className="p-5">
        {editing ? (
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
        )}
      </div>
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
}: Props) {
  const videoUrl = video?.url || "";
  const [liText, setLiText] = useState(initialPosts.linkedin_text || "");
  const [igText, setIgText] = useState(initialPosts.instagram_text || "");
  const [saving, setSaving] = useState(false);

  const solo = params.solo as string || "";
  const doLinkedIn = solo !== "instagram";
  const doInstagram = solo !== "linkedin";

  const liImageUrl = images.has_li_hook ? `${apiUrl}/jobs/${jobId}/image/li-hook` : (liMediaUrls[0] || "");
  const igSingleUrl = images.has_ig_single ? `${apiUrl}/jobs/${jobId}/image/ig-single` : (igMediaUrls[0] || "");
  const igSlideUrls = images.ig_slides.length > 0
    ? images.ig_slides.map((k) => `${apiUrl}/jobs/${jobId}/image/${k}`)
    : igMediaUrls;

  const isCarousel = params.formato_instagram === "carrusel";

  async function savePost(field: "linkedin_text" | "instagram_text", value: string) {
    setSaving(true);
    const form = new FormData();
    form.set(field, value);
    await fetch(`${apiUrl}/jobs/${jobId}/edit`, { method: "POST", body: form });
    setSaving(false);
  }

  return (
    <div className="space-y-6">
      <div className={`grid gap-6 ${doLinkedIn && doInstagram ? "lg:grid-cols-2" : "grid-cols-1"}`}>
        {doLinkedIn && (
          <PostCard
            platform="LinkedIn"
            logo={<LinkedInLogo />}
            text={liText}
            onTextChange={setLiText}
            onSave={() => savePost("linkedin_text", liText)}
            saving={saving}
            imageUrl={videoUrl ? undefined : liImageUrl}
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
            imageUrl={videoUrl ? undefined : (isCarousel ? undefined : igSingleUrl)}
            extraImageUrls={videoUrl ? undefined : (isCarousel ? igSlideUrls : undefined)}
            videoUrl={videoUrl || undefined}
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
