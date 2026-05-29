import { useState, useEffect, useRef } from "react";

type StepStatus = "pending" | "running" | "done" | "warn" | "error";

interface SubRow {
  key: string;
  label: string;
  status: StepStatus;
  msg?: string;
  thumbnailUrl?: string;
}

interface Step {
  key: string;
  label: string;
  status: StepStatus;
  msg?: string;
  chunks?: string;
  subrows?: SubRow[];
}

const STEP_DEFS: { key: string; label: string }[] = [
  { key: "extract", label: "Extraer video de YouTube" },
  { key: "accounts", label: "Verificar cuentas" },
  { key: "writing", label: "Escribir posts con Claude" },
  { key: "images", label: "Generar imágenes" },
];

const SUBKEY_LABELS: Record<string, string> = {
  "li-hook": "LinkedIn",
  "ig-single": "Instagram",
  "ig-0": "Instagram · Slide 1",
  "ig-1": "Instagram · Slide 2",
  "ig-2": "Instagram · Slide 3",
};

function StatusIcon({ status, size = "md" }: { status: StepStatus; size?: "sm" | "md" }) {
  const cls = size === "sm" ? "w-4 h-4" : "w-5 h-5";
  if (status === "running") {
    return (
      <svg className={`${cls} text-brand-500 animate-spin`} fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
      </svg>
    );
  }
  if (status === "done") {
    return (
      <svg className={`${cls} text-green-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (status === "warn") {
    return (
      <svg className={`${cls} text-amber-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg className={`${cls} text-red-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  return <div className={`${cls} rounded-full border border-gray-700 bg-gray-800`} />;
}

export default function ProgressView({ jobId, apiUrl }: { jobId: string; apiUrl: string }) {
  const [steps, setSteps] = useState<Step[]>(
    STEP_DEFS.map((s) => ({ ...s, status: "pending" }))
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(`${apiUrl}/jobs/${jobId}/stream`);
    esRef.current = es;

    es.onmessage = (e) => {
      const event = JSON.parse(e.data);
      const { step, status, msg, text, redirect, subkeys, subkey } = event;

      if (step === "ping") return;

      if (step === "done" && redirect) {
        es.close();
        window.location.href = redirect;
        return;
      }

      if (step === "error") {
        setErrorMsg(msg || "Error desconocido");
        es.close();
        return;
      }

      if (step === "images") {
        if (status === "init") {
          setSteps((prev) =>
            prev.map((s) => {
              if (s.key !== "images") return s;
              return {
                ...s,
                subrows: (subkeys as string[]).map((k) => ({
                  key: k,
                  label: SUBKEY_LABELS[k] ?? k,
                  status: "pending" as StepStatus,
                })),
              };
            })
          );
          return;
        }

        if (subkey) {
          setSteps((prev) =>
            prev.map((s) => {
              if (s.key !== "images") return s;
              return {
                ...s,
                subrows: s.subrows?.map((sr) => {
                  if (sr.key !== subkey) return sr;
                  return {
                    ...sr,
                    status: status as StepStatus,
                    msg,
                    thumbnailUrl:
                      status === "done"
                        ? `${apiUrl}/jobs/${jobId}/image/${subkey}`
                        : sr.thumbnailUrl,
                  };
                }),
              };
            })
          );
          return;
        }

        if (status === "running") {
          setSteps((prev) =>
            prev.map((s) => {
              if (s.key !== "images") return s;
              return {
                ...s,
                status: "running",
                msg,
                subrows: s.subrows?.map((sr) =>
                  sr.status === "pending" ? { ...sr, status: "running" as StepStatus } : sr
                ),
              };
            })
          );
          return;
        }

        // Parent done/warn/error (no subkey)
        setSteps((prev) =>
          prev.map((s) => {
            if (s.key !== "images") return s;
            return { ...s, status: status as StepStatus, msg };
          })
        );
        return;
      }

      if (step === "video") {
        // Video jobs reuse the 4th row: turn the "images" (or prior "video") step
        // into a "Generar video" step and update its status.
        setSteps((prev) =>
          prev.map((s) =>
            s.key === "images" || s.key === "video"
              ? { key: "video", label: "Generar video con Higgsfield", status: status as StepStatus, msg }
              : s
          )
        );
        return;
      }

      setSteps((prev) =>
        prev.map((s) => {
          if (s.key !== step) return s;
          if (status === "chunk") {
            return { ...s, chunks: (s.chunks || "") + text };
          }
          return {
            ...s,
            status: status as StepStatus,
            msg: msg || s.msg,
          };
        })
      );
    };

    es.onerror = () => {
      setErrorMsg("Conexión perdida con el servidor.");
      es.close();
    };

    return () => es.close();
  }, [jobId, apiUrl]);

  return (
    <div className="bg-gray-900 rounded-2xl border border-gray-800 p-6 space-y-1">
      {steps.map((step, i) => (
        <div
          key={step.key}
          className={`py-3 ${i < steps.length - 1 ? "border-b border-gray-800/60" : ""}`}
        >
          <div className="flex items-start gap-4">
            <div className="mt-0.5 flex-shrink-0">
              <StatusIcon status={step.status} />
            </div>
            <div className="flex-1 min-w-0">
              <div
                className={`text-sm font-medium ${
                  step.status === "pending"
                    ? "text-gray-600"
                    : step.status === "error"
                    ? "text-red-400"
                    : "text-gray-200"
                }`}
              >
                {step.label}
              </div>
              {step.msg && step.status !== "pending" && (
                <div
                  className={`text-xs mt-0.5 ${
                    step.status === "warn" ? "text-amber-400" : "text-gray-500"
                  }`}
                >
                  {step.msg}
                </div>
              )}
              {step.key === "writing" && step.status === "running" && step.chunks && (
                <div className="text-xs text-gray-500 mt-1 line-clamp-2 font-mono">{step.chunks}</div>
              )}
            </div>
          </div>

          {step.subrows && step.subrows.length > 0 && (
            <div className="mt-2 ml-9 space-y-2">
              {step.subrows.map((sr) => (
                <div key={sr.key} className="flex items-center gap-3">
                  <StatusIcon status={sr.status} size="sm" />
                  <span
                    className={`text-xs flex-1 ${
                      sr.status === "pending" ? "text-gray-600" : "text-gray-400"
                    }`}
                  >
                    {sr.label}
                  </span>
                  {sr.msg && (sr.status === "warn" || sr.status === "error") && (
                    <span className="text-xs text-amber-400 truncate max-w-[180px]">{sr.msg}</span>
                  )}
                  {sr.thumbnailUrl && (
                    <img
                      src={sr.thumbnailUrl}
                      alt={sr.label}
                      className="w-10 h-10 rounded object-cover border border-gray-700 flex-shrink-0"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {errorMsg && (
        <div className="mt-4 bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 text-red-300 text-sm">
          {errorMsg}
          <a href="/" className="ml-3 text-red-400 underline hover:no-underline">Volver al inicio</a>
        </div>
      )}
    </div>
  );
}
