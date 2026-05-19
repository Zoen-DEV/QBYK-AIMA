import { useState } from "react";

interface Props {
  jobId: string;
  apiUrl: string;
  dryRun: boolean;
  initialSchedule: string;
}

export default function PublishBar({ jobId, apiUrl, dryRun, initialSchedule }: Props) {
  const [schedule, setSchedule] = useState(initialSchedule || "");
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handlePublish() {
    setPublishing(true);
    setError(null);
    try {
      const form = new FormData();
      if (schedule) form.set("schedule_time", new Date(schedule).toISOString());

      const res = await fetch(`${apiUrl}/jobs/${jobId}/publish`, { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || JSON.stringify(err));
      }
      window.location.href = `/jobs/${jobId}/result`;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setPublishing(false);
    }
  }

  return (
    <div className="bg-gray-900 rounded-2xl border border-gray-800 p-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-end gap-4">
        <div className="flex-1">
          <label className="block">
            <span className="text-xs text-gray-400 mb-1.5 block">Programar publicación (opcional)</span>
            <input
              type="datetime-local"
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 w-full sm:w-auto"
            />
          </label>
          <p className="text-xs text-gray-600 mt-1">Vacío = publicar ahora</p>
        </div>

        <div className="flex items-center gap-3 w-full sm:w-auto">
          <a
            href="/"
            className="flex-1 sm:flex-none text-center text-sm text-gray-500 hover:text-gray-300 transition px-4 py-2.5 rounded-xl border border-gray-800 hover:border-gray-700"
          >
            Cancelar
          </a>
          <button
            onClick={handlePublish}
            disabled={publishing}
            className={`flex-1 sm:flex-none flex items-center justify-center gap-2 font-semibold py-2.5 px-6 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              dryRun
                ? "bg-amber-600 hover:bg-amber-700 text-white"
                : "bg-green-600 hover:bg-green-700 text-white"
            }`}
          >
            {publishing ? (
              <>
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                {dryRun ? "Simulando..." : "Publicando..."}
              </>
            ) : (
              <>
                {dryRun ? (
                  <>
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Confirmar (dry-run)
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                    </svg>
                    {schedule ? "Programar" : "Publicar ahora"}
                  </>
                )}
              </>
            )}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-4 bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}
    </div>
  );
}
