import { useState, useEffect, useRef } from "react";

type StepStatus = "pending" | "running" | "done" | "warn" | "error";

interface Step {
  key: string;
  label: string;
  status: StepStatus;
  msg?: string;
  chunks?: string;
}

const STEP_DEFS: { key: string; label: string }[] = [
  { key: "extract", label: "Extraer video de YouTube" },
  { key: "accounts", label: "Verificar cuentas" },
  { key: "writing", label: "Escribir posts con Claude" },
  { key: "images", label: "Generar imágenes" },
  { key: "overlay", label: "Aplicar overlay de texto" },
  { key: "upload", label: "Subir imágenes a Blotato" },
];

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === "running") {
    return (
      <svg className="w-5 h-5 text-brand-500 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
      </svg>
    );
  }
  if (status === "done") {
    return (
      <svg className="w-5 h-5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (status === "warn") {
    return (
      <svg className="w-5 h-5 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg className="w-5 h-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  return <div className="w-5 h-5 rounded-full border border-gray-700 bg-gray-800" />;
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
      const { step, status, msg, text, redirect } = event;

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
          className={`flex items-start gap-4 py-3 ${i < steps.length - 1 ? "border-b border-gray-800/60" : ""}`}
        >
          <div className="mt-0.5 flex-shrink-0">{<StatusIcon status={step.status} />}</div>
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
