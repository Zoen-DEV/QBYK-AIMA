import { useEffect, useMemo, useState } from "react";

// Dashboard de costos (Fase 4). Consume los endpoints /costs/* del backend a
// través del proxy /api. Las gráficas son SVG puro (sin librerías externas) para
// no añadir dependencias al frontend. Todos los montos llegan en USD y se
// convierten a la moneda de visualización con el fx_rate de la tabla de precios.

// ── Tipos del API ───────────────────────────────────────────────────────────

interface ServiceCost { service: string; cost_usd: number; events?: number }
interface FixedItem { service: string; monthly_usd: number; prorated_usd: number; note?: string }

interface Summary {
  period: string;
  kind: "month" | "year";
  months: number;
  display_currency: string;
  fx_rate: number;
  variable: { by_service: ServiceCost[]; total_usd: number };
  fixed: { items: FixedItem[]; total_usd: number };
  total_usd: number;
  events_count: number;
  jobs_count: number;
  avg_cost_per_job_usd: number;
}

interface Timeseries {
  granularity: "day" | "month";
  buckets: string[];
  series: Record<string, number[]>;
  total_usd: number;
}

interface JobRow {
  job_id: string;
  flow: string;
  batch_id: string | null;
  source_type: string | null;
  ts: string | null;
  cost_usd: number;
  by_service: ServiceCost[];
}
interface ByJob { jobs: JobRow[]; count: number }

// ── Presentación de servicios ───────────────────────────────────────────────

const SERVICE_LABELS: Record<string, string> = {
  anthropic: "Claude",
  perplexity: "Perplexity",
  higgsfield: "Higgsfield",
  whisper: "Whisper",
};
const SERVICE_COLORS: Record<string, string> = {
  anthropic: "#4f6ef7",
  perplexity: "#22c55e",
  higgsfield: "#f59e0b",
  whisper: "#a855f7",
};
const FALLBACK_COLORS = ["#ec4899", "#14b8a6", "#ef4444", "#64748b"];

function serviceLabel(s: string): string {
  return SERVICE_LABELS[s] || s;
}
function serviceColor(s: string, idx: number): string {
  return SERVICE_COLORS[s] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length];
}
function sourceLabel(s: string | null): string {
  switch (s) {
    case "youtube": return "YouTube";
    case "audio": return "Nota de voz";
    case "texto": return "Documento";
    case "manual": return "Texto manual";
    default: return s || "—";
  }
}

// ── Período y rango de fechas ───────────────────────────────────────────────

function pad(n: number): string { return String(n).padStart(2, "0"); }

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
}

// Rango (from/to/granularity) para timeseries y by-job, derivado del período.
function deriveRange(kind: "month" | "year", period: string) {
  if (kind === "year") {
    const y = period.slice(0, 4);
    return { from: `${y}-01-01`, to: `${y}-12-31`, granularity: "month" as const };
  }
  const [ys, ms] = period.split("-");
  const y = Number(ys), m = Number(ms);
  const lastDay = new Date(y, m, 0).getDate(); // día 0 del mes siguiente = último de este
  return { from: `${ys}-${ms}-01`, to: `${ys}-${ms}-${pad(lastDay)}`, granularity: "day" as const };
}

// ── Formato de moneda ───────────────────────────────────────────────────────

function makeFmt(currency: string, fx: number) {
  return (usd: number, digits = 2) => {
    const v = usd * (fx || 1);
    try {
      return new Intl.NumberFormat("es-CO", {
        style: "currency", currency: currency || "USD",
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      }).format(v);
    } catch {
      return `${(currency || "USD")} ${v.toFixed(digits)}`;
    }
  };
}

// ── Componente ──────────────────────────────────────────────────────────────

function isValidPeriod(kind: "month" | "year", period: string): boolean {
  return kind === "month" ? /^\d{4}-\d{2}$/.test(period) : /^\d{4}$/.test(period);
}

// FastAPI devuelve `detail` como string (errores 400/500) o como array de objetos
// (validación 422). String(obj) daría "[object Object]"; aquí lo aplanamos a texto.
function errText(detail: unknown): string {
  if (detail == null) return "Error desconocido";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as any).msg) : JSON.stringify(d))).join("; ");
  }
  if (typeof detail === "object") {
    const o = detail as any;
    return typeof o.msg === "string" ? o.msg : JSON.stringify(o);
  }
  return String(detail);
}

export default function CostDashboard({ apiUrl = "/api" }: { apiUrl?: string }) {
  const [kind, setKind] = useState<"month" | "year">("month");
  const [period, setPeriod] = useState<string>(currentMonth());

  const [summary, setSummary] = useState<Summary | null>(null);
  const [series, setSeries] = useState<Timeseries | null>(null);
  const [byJob, setByJob] = useState<ByJob | null>(null);
  const [yearSeries, setYearSeries] = useState<Timeseries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");

  const range = useMemo(() => deriveRange(kind, period), [kind, period]);
  const periodOk = isValidPeriod(kind, period);
  const year = period.slice(0, 4);

  useEffect(() => {
    if (!periodOk) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    const qs = `from=${range.from}&to=${range.to}`;
    Promise.all([
      fetch(`${apiUrl}/costs/summary?period=${period}`).then((r) => r.json()),
      fetch(`${apiUrl}/costs/timeseries?${qs}&granularity=${range.granularity}`).then((r) => r.json()),
      fetch(`${apiUrl}/costs/by-job?${qs}`).then((r) => r.json()),
    ])
      .then(([s, t, j]) => {
        if (cancelled) return;
        // Cualquiera de las tres respuestas puede traer `detail` (error del API).
        const bad = [s, t, j].find((x) => x?.detail);
        if (bad) { setError(errText(bad.detail)); return; }
        setSummary(s); setSeries(t); setByJob(j);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiUrl, period, range.from, range.to, range.granularity, periodOk]);

  // Serie del año completo (por mes) para la gráfica "Gasto por mes". Se pide
  // aparte del período seleccionado para mostrar siempre los 12 meses del año.
  useEffect(() => {
    if (!/^\d{4}$/.test(year)) return;
    let cancelled = false;
    fetch(`${apiUrl}/costs/timeseries?from=${year}-01-01&to=${year}-12-31&granularity=month`)
      .then((r) => r.json())
      .then((t) => { if (!cancelled && !t?.detail) setYearSeries(t); })
      .catch(() => { /* best-effort: la gráfica anual simplemente no se muestra */ });
    return () => { cancelled = true; };
  }, [apiUrl, year]);

  const fmt = useMemo(
    () => makeFmt(summary?.display_currency || "USD", summary?.fx_rate || 1),
    [summary?.display_currency, summary?.fx_rate],
  );

  // Costo fijo mensual (suma de los fixed_monthly de pricing.json) — constante por
  // mes, lo usa la gráfica anual para apilar el fijo sobre el variable.
  const fixedMonthly = useMemo(
    () => (summary?.fixed.items || []).reduce((a, i) => a + (i.monthly_usd || 0), 0),
    [summary],
  );

  const hasData = (summary?.events_count || 0) > 0 || (summary?.fixed.total_usd || 0) > 0;

  return (
    <div className="space-y-6">
      {/* Cabecera + selector de período */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard de costos</h1>
          <p className="text-sm text-gray-400 mt-1">
            Gasto variable por uso (Claude, Perplexity, Higgsfield, Whisper) + costos fijos prorrateados.
          </p>
        </div>
        <PeriodPicker kind={kind} period={period} onKind={setKind} onPeriod={setPeriod} />
      </div>

      {error && (
        <div className="rounded-xl border border-red-900/60 bg-red-950/40 text-red-200 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {loading && !summary ? (
        <div className="text-gray-400 text-sm py-10 text-center">Cargando…</div>
      ) : (
        <>
          {!hasData && (
            <div className="rounded-xl border border-amber-900/60 bg-amber-950/30 text-amber-200 px-4 py-3 text-sm">
              No hay eventos registrados en este período. Si esperabas datos, revisa que{" "}
              <code className="text-amber-100">MONGODB_URI</code> esté configurado en el <code>.env</code> y
              que las tarifas de <code>pricing.json</code> no estén en <code>null</code>.
            </div>
          )}

          {summary && <Kpis summary={summary} fmt={fmt} />}

          {summary && (summary.total_usd > 0) && (
            <Card title="Composición del gasto — fijo vs. variable">
              <CompositionBar
                variable={summary.variable.total_usd}
                fixed={summary.fixed.total_usd}
                fmt={fmt}
              />
            </Card>
          )}

          {yearSeries && (
            <Card title={`Gasto por mes — ${year}`}>
              <MonthlyChart ts={yearSeries} fixedMonthly={fixedMonthly} fmt={fmt} />
            </Card>
          )}

          {series && (
            <Card title={`Gasto por servicio en el tiempo (${series.granularity === "day" ? "por día" : "por mes"})`}>
              <StackedBars ts={series} fmt={fmt} />
            </Card>
          )}

          {summary && (
            <div className="grid md:grid-cols-2 gap-6">
              <Card title="Desglose por servicio (variable)">
                <ServiceBreakdown rows={summary.variable.by_service} total={summary.variable.total_usd} fmt={fmt} />
              </Card>
              <Card title="Costos fijos (prorrateados)">
                <FixedBreakdown items={summary.fixed.items} total={summary.fixed.total_usd} fmt={fmt} months={summary.months} />
              </Card>
            </div>
          )}

          {byJob && (
            <Card title={`Costo por uso — ${byJob.count} job(s)`}>
              <JobsBarChart rows={byJob.jobs} fmt={fmt} />
              <JobsTable rows={byJob.jobs} fmt={fmt} />
            </Card>
          )}

          <EventsPanel apiUrl={apiUrl} fmt={fmt} />
        </>
      )}
    </div>
  );
}

// ── Selector de período ─────────────────────────────────────────────────────

function PeriodPicker({
  kind, period, onKind, onPeriod,
}: {
  kind: "month" | "year"; period: string;
  onKind: (k: "month" | "year") => void; onPeriod: (p: string) => void;
}) {
  const year = period.slice(0, 4);
  return (
    <div className="flex items-center gap-2">
      <div className="inline-flex rounded-lg border border-gray-800 bg-gray-900 p-0.5">
        {(["month", "year"] as const).map((k) => (
          <button
            key={k}
            onClick={() => {
              if (k === kind) return;
              onKind(k);
              onPeriod(k === "month" ? `${year}-${pad(new Date().getMonth() + 1)}` : year);
            }}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
              kind === k ? "bg-brand-500 text-white" : "text-gray-300 hover:text-white"
            }`}
          >
            {k === "month" ? "Mes" : "Año"}
          </button>
        ))}
      </div>
      {kind === "month" ? (
        <input
          type="month"
          value={period}
          onChange={(e) => onPeriod(e.target.value)}
          className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-1.5 text-sm text-white"
        />
      ) : (
        <input
          type="number"
          min={2024}
          max={2099}
          value={year}
          onChange={(e) => onPeriod(e.target.value.slice(0, 4))}
          className="w-24 rounded-lg border border-gray-800 bg-gray-900 px-3 py-1.5 text-sm text-white"
        />
      )}
    </div>
  );
}

// ── KPIs ────────────────────────────────────────────────────────────────────

function Kpis({ summary, fmt }: { summary: Summary; fmt: (n: number, d?: number) => string }) {
  const cards = [
    { label: "Total del período", value: fmt(summary.total_usd), hint: `${summary.kind === "month" ? "Mensual" : "Anual"} · variable + fijo` },
    { label: "Variable (por uso)", value: fmt(summary.variable.total_usd), hint: `${summary.events_count} evento(s)` },
    { label: "Fijo (prorrateado)", value: fmt(summary.fixed.total_usd), hint: `${summary.months} mes(es)` },
    { label: "Promedio por post", value: fmt(summary.avg_cost_per_job_usd, 4), hint: `${summary.jobs_count} job(s)` },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="text-xs text-gray-400">{c.label}</div>
          <div className="text-2xl font-bold text-white mt-1 tabular-nums">{c.value}</div>
          <div className="text-xs text-gray-500 mt-1">{c.hint}</div>
        </div>
      ))}
    </div>
  );
}

// ── Card contenedor ─────────────────────────────────────────────────────────

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-sm font-semibold text-gray-200 mb-4">{title}</h2>
      {children}
    </div>
  );
}

// ── Gráfica de barras apiladas (SVG puro) ───────────────────────────────────

function StackedBars({ ts, fmt }: { ts: Timeseries; fmt: (n: number, d?: number) => string }) {
  const services = Object.keys(ts.series);
  if (ts.buckets.length === 0 || services.length === 0) {
    return <div className="text-sm text-gray-500 py-8 text-center">Sin datos en el rango.</div>;
  }

  const totals = ts.buckets.map((_, i) => services.reduce((acc, s) => acc + (ts.series[s][i] || 0), 0));
  const max = Math.max(...totals, 1e-9);

  const H = 200, padTop = 10, padBottom = 28, chartH = H - padTop - padBottom;
  const bw = 30, gap = 14, leftPad = 6;
  const W = leftPad * 2 + ts.buckets.length * bw + (ts.buckets.length - 1) * gap;

  // Etiquetas del eje X: si hay muchos buckets (días), muestra ~10 espaciadas.
  const step = Math.ceil(ts.buckets.length / 10);

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMinYMid meet" role="img">
          {ts.buckets.map((bucket, i) => {
            const x = leftPad + i * (bw + gap);
            let yCursor = padTop + chartH;
            const segs = services.map((s, si) => {
              const val = ts.series[s][i] || 0;
              const h = (val / max) * chartH;
              yCursor -= h;
              return h > 0 ? (
                <rect key={s} x={x} y={yCursor} width={bw} height={h} fill={serviceColor(s, si)} rx={1}>
                  <title>{`${bucket} · ${serviceLabel(s)}: ${fmt(val, 4)}`}</title>
                </rect>
              ) : null;
            });
            const showLabel = i % step === 0;
            return (
              <g key={bucket}>
                {segs}
                {showLabel && (
                  <text x={x + bw / 2} y={H - 10} textAnchor="middle" className="fill-gray-500" fontSize={10}>
                    {bucket.slice(5)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <Legend services={services} />
    </div>
  );
}

function Legend({ services }: { services: string[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {services.map((s, i) => (
        <span key={s} className="inline-flex items-center gap-1.5 text-xs text-gray-400">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: serviceColor(s, i) }} />
          {serviceLabel(s)}
        </span>
      ))}
    </div>
  );
}

// ── Composición fijo vs. variable (barra 100% apilada) ──────────────────────

const VAR_COLOR = "#4f6ef7";
const FIX_COLOR = "#64748b";

function CompositionBar({ variable, fixed, fmt }: { variable: number; fixed: number; fmt: (n: number, d?: number) => string }) {
  const total = variable + fixed;
  if (total <= 0) return <div className="text-sm text-gray-500">Sin gasto en el período.</div>;
  const vPct = (variable / total) * 100;
  const fPct = 100 - vPct;
  return (
    <div className="space-y-3">
      <div className="flex h-6 w-full overflow-hidden rounded-lg bg-gray-800">
        {variable > 0 && <div style={{ width: `${vPct}%`, background: VAR_COLOR }} title={`Variable: ${fmt(variable)}`} />}
        {fixed > 0 && <div style={{ width: `${fPct}%`, background: FIX_COLOR }} title={`Fijo: ${fmt(fixed)}`} />}
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <CompItem color={VAR_COLOR} label="Variable (por uso)" value={fmt(variable)} pct={vPct} />
        <CompItem color={FIX_COLOR} label="Fijo (prorrateado)" value={fmt(fixed)} pct={fPct} />
        <span className="ml-auto text-gray-400">Total <span className="text-white font-semibold tabular-nums">{fmt(total)}</span></span>
      </div>
    </div>
  );
}

function CompItem({ color, label, value, pct }: { color: string; label: string; value: string; pct: number }) {
  return (
    <span className="inline-flex items-center gap-2 text-gray-400">
      <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
      <span>{label}</span>
      <span className="text-white font-medium tabular-nums">{value}</span>
      <span className="text-gray-500">({pct.toFixed(0)}%)</span>
    </span>
  );
}

// ── Gasto por mes (12 meses del año, variable + fijo apilados) ──────────────

const MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

function MonthlyChart({ ts, fixedMonthly, fmt }: { ts: Timeseries; fixedMonthly: number; fmt: (n: number, d?: number) => string }) {
  // Variable por mes: suma de todos los servicios en cada bucket "YYYY-MM".
  const variable = new Array(12).fill(0) as number[];
  ts.buckets.forEach((b, i) => {
    const m = Number(b.slice(5, 7)) - 1;
    if (m >= 0 && m < 12) {
      variable[m] += Object.keys(ts.series).reduce((a, s) => a + (ts.series[s][i] || 0), 0);
    }
  });
  const fixed = fixedMonthly || 0;
  const totals = variable.map((v) => v + fixed);
  const max = Math.max(...totals, 1e-9);
  if (!totals.some((t) => t > 0)) {
    return <div className="text-sm text-gray-500 py-8 text-center">Sin gasto en el año.</div>;
  }

  const H = 200, padTop = 10, padBottom = 28, chartH = H - padTop - padBottom;
  const bw = 34, gap = 16, leftPad = 6;
  const W = leftPad * 2 + 12 * bw + 11 * gap;

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMinYMid meet" role="img">
          {MONTH_LABELS.map((label, m) => {
            const x = leftPad + m * (bw + gap);
            const vH = (variable[m] / max) * chartH;
            const fH = (fixed / max) * chartH;
            const yVar = padTop + chartH - vH;
            const yFix = yVar - fH;
            return (
              <g key={label}>
                {fixed > 0 && (
                  <rect x={x} y={yFix} width={bw} height={fH} fill={FIX_COLOR} rx={1}>
                    <title>{`${label} · Fijo: ${fmt(fixed)}`}</title>
                  </rect>
                )}
                {variable[m] > 0 && (
                  <rect x={x} y={yVar} width={bw} height={vH} fill={VAR_COLOR} rx={1}>
                    <title>{`${label} · Variable: ${fmt(variable[m], 4)} · Total: ${fmt(totals[m], 4)}`}</title>
                  </rect>
                )}
                <text x={x + bw / 2} y={H - 10} textAnchor="middle" className="fill-gray-500" fontSize={10}>{label}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: VAR_COLOR }} />Variable
        </span>
        {fixed > 0 && (
          <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: FIX_COLOR }} />Fijo (mensual)
          </span>
        )}
      </div>
    </div>
  );
}

// ── Top jobs por costo (barras horizontales) ────────────────────────────────

function JobsBarChart({ rows, fmt }: { rows: JobRow[]; fmt: (n: number, d?: number) => string }) {
  const top = [...rows].filter((r) => r.cost_usd > 0).sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8);
  if (top.length === 0) return null;
  const max = Math.max(...top.map((r) => r.cost_usd), 1e-9);
  return (
    <div className="space-y-2 mb-5">
      <div className="text-xs text-gray-500">Top {top.length} por costo</div>
      {top.map((r) => (
        <div key={r.job_id} className="flex items-center gap-3">
          <div className="w-28 shrink-0 text-xs text-gray-400 truncate" title={r.job_id || ""}>
            {sourceLabel(r.source_type)} · {r.flow === "bulk" ? "Lote" : "Indiv."}
          </div>
          <div className="flex-1 h-4 rounded bg-gray-800 overflow-hidden">
            <div className="h-full rounded" style={{ width: `${(r.cost_usd / max) * 100}%`, background: VAR_COLOR }} />
          </div>
          <div className="w-20 shrink-0 text-right text-xs text-gray-300 tabular-nums">{fmt(r.cost_usd, 4)}</div>
        </div>
      ))}
    </div>
  );
}

// ── Desglose por servicio (barras horizontales) ─────────────────────────────

function ServiceBreakdown({ rows, total, fmt }: { rows: ServiceCost[]; total: number; fmt: (n: number, d?: number) => string }) {
  if (rows.length === 0) return <div className="text-sm text-gray-500">Sin gasto variable.</div>;
  const max = Math.max(...rows.map((r) => r.cost_usd), 1e-9);
  return (
    <div className="space-y-3">
      {rows.map((r, i) => (
        <div key={r.service}>
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-gray-300">{serviceLabel(r.service)}</span>
            <span className="text-gray-400 tabular-nums">{fmt(r.cost_usd, 4)}</span>
          </div>
          <div className="h-2 rounded-full bg-gray-800 overflow-hidden">
            <div className="h-full rounded-full" style={{ width: `${(r.cost_usd / max) * 100}%`, background: serviceColor(r.service, i) }} />
          </div>
        </div>
      ))}
      <div className="flex justify-between border-t border-gray-800 pt-2 text-sm">
        <span className="text-gray-400">Total variable</span>
        <span className="text-white font-semibold tabular-nums">{fmt(total)}</span>
      </div>
    </div>
  );
}

// ── Costos fijos ────────────────────────────────────────────────────────────

function FixedBreakdown({ items, total, fmt, months }: { items: FixedItem[]; total: number; fmt: (n: number, d?: number) => string; months: number }) {
  if (items.length === 0) return <div className="text-sm text-gray-500">Sin costos fijos configurados.</div>;
  return (
    <div className="space-y-2">
      {items.map((it) => (
        <div key={it.service} className="flex items-center justify-between text-sm">
          <div>
            <span className="text-gray-200 capitalize">{it.service}</span>
            {it.note && <span className="text-gray-500 text-xs ml-2">{it.note}</span>}
          </div>
          <div className="text-right tabular-nums">
            <div className="text-white">{fmt(it.prorated_usd)}</div>
            <div className="text-xs text-gray-500">{fmt(it.monthly_usd)}/mes × {months}</div>
          </div>
        </div>
      ))}
      <div className="flex justify-between border-t border-gray-800 pt-2 text-sm">
        <span className="text-gray-400">Total fijo</span>
        <span className="text-white font-semibold tabular-nums">{fmt(total)}</span>
      </div>
      <p className="text-xs text-gray-500 pt-1">
        Los fijos no se reparten por post; se prorratean al período. Edítalos en <code>pricing.json</code>.
      </p>
    </div>
  );
}

// ── Tabla de costo por job ──────────────────────────────────────────────────

function JobsTable({ rows, fmt }: { rows: JobRow[]; fmt: (n: number, d?: number) => string }) {
  if (rows.length === 0) return <div className="text-sm text-gray-500">Sin jobs en el rango.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
            <th className="py-2 pr-3 font-medium">Fecha</th>
            <th className="py-2 pr-3 font-medium">Flujo</th>
            <th className="py-2 pr-3 font-medium">Fuente</th>
            <th className="py-2 pr-3 font-medium">Servicios</th>
            <th className="py-2 pl-3 font-medium text-right">Costo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.job_id} className="border-b border-gray-800/60">
              <td className="py-2 pr-3 text-gray-400 whitespace-nowrap">{fmtTs(r.ts)}</td>
              <td className="py-2 pr-3">
                <span className={`text-xs px-1.5 py-0.5 rounded ${r.flow === "bulk" ? "bg-brand-500/15 text-brand-500" : "bg-gray-700/50 text-gray-300"}`}>
                  {r.flow === "bulk" ? "Lote" : "Individual"}
                </span>
              </td>
              <td className="py-2 pr-3 text-gray-300">{sourceLabel(r.source_type)}</td>
              <td className="py-2 pr-3 text-gray-400 text-xs">
                {r.by_service.map((s) => `${serviceLabel(s.service)} ${fmt(s.cost_usd, 4)}`).join(" · ")}
              </td>
              <td className="py-2 pl-3 text-right text-white font-medium tabular-nums whitespace-nowrap">{fmt(r.cost_usd, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtTs(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Panel de auditoría de eventos crudos ─────────────────────────────────────

interface RawEvent {
  _id: string;
  ts: string | null;
  service: string;
  operation: string;
  model: string | null;
  units: Record<string, number>;
  cost_usd: number;
  flow: string;
  job_id: string | null;
  source_type: string | null;
  dry_run: boolean;
  status: string;
}

const PAGE = 20;

function unitsLabel(units: Record<string, number>): string {
  if (!units) return "—";
  const parts: string[] = [];
  if (units.input_tokens != null) parts.push(`${units.input_tokens.toLocaleString()} in`);
  if (units.output_tokens != null) parts.push(`${units.output_tokens.toLocaleString()} out`);
  if (units.cache_creation_input_tokens) parts.push(`${units.cache_creation_input_tokens.toLocaleString()} cache+`);
  if (units.cache_read_input_tokens) parts.push(`${units.cache_read_input_tokens.toLocaleString()} cache-`);
  if (units.generations != null) parts.push(`${units.generations} gen`);
  if (units.minutes != null) parts.push(`${units.minutes.toFixed(2)} min`);
  if (units.requests != null && units.input_tokens == null) parts.push(`${units.requests} req`);
  return parts.length ? parts.join(" · ") : "—";
}

function EventsPanel({ apiUrl, fmt }: { apiUrl: string; fmt: (n: number, d?: number) => string }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [serviceFilter, setServiceFilter] = useState("");

  const fetchPage = (newSkip: number, service: string) => {
    setLoading(true);
    setError("");
    const svc = service ? `&service=${encodeURIComponent(service)}` : "";
    fetch(`${apiUrl}/costs/events?limit=${PAGE}&skip=${newSkip}${svc}`)
      .then((r) => r.json())
      .then((d) => {
        if (d?.detail) { setError(errText(d.detail)); return; }
        if (newSkip === 0) {
          setEvents(d.events || []);
        } else {
          setEvents((prev) => [...prev, ...(d.events || [])]);
        }
        setTotal(d.count || 0);
        setSkip(newSkip + (d.events?.length || 0));
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  const handleOpen = () => {
    if (!open) { setSkip(0); setEvents([]); fetchPage(0, serviceFilter); }
    setOpen(!open);
  };

  const handleFilter = (svc: string) => {
    setServiceFilter(svc);
    setSkip(0);
    setEvents([]);
    fetchPage(0, svc);
  };

  const canLoadMore = events.length < total || (total === 0 && events.length === PAGE);

  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900">
      <button
        onClick={handleOpen}
        className="w-full flex items-center justify-between px-5 py-4 text-sm font-semibold text-gray-200 hover:text-white transition-colors"
      >
        <span className="flex items-center gap-2">
          <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          Auditoría de eventos
        </span>
        <svg
          className={`w-4 h-4 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="border-t border-gray-800 px-5 pb-5 pt-4 space-y-4">
          {/* Filtro por servicio */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Filtrar:</span>
            {["", "anthropic", "perplexity", "higgsfield", "whisper"].map((s) => (
              <button
                key={s}
                onClick={() => handleFilter(s)}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                  serviceFilter === s
                    ? "bg-brand-500 text-white"
                    : "bg-gray-800 text-gray-300 hover:text-white"
                }`}
              >
                {s ? serviceLabel(s) : "Todos"}
              </button>
            ))}
          </div>

          {error && (
            <div className="text-xs text-red-400">{error}</div>
          )}

          {events.length === 0 && !loading ? (
            <div className="text-sm text-gray-500">Sin eventos.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-800">
                    <th className="py-2 pr-3 font-medium">Fecha (UTC)</th>
                    <th className="py-2 pr-3 font-medium">Servicio</th>
                    <th className="py-2 pr-3 font-medium">Operación</th>
                    <th className="py-2 pr-3 font-medium">Modelo</th>
                    <th className="py-2 pr-3 font-medium">Unidades</th>
                    <th className="py-2 pr-3 font-medium">Flujo</th>
                    <th className="py-2 pl-3 font-medium text-right">Costo</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e._id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap">{fmtTs(e.ts)}</td>
                      <td className="py-1.5 pr-3">
                        <span className="inline-flex items-center gap-1">
                          <span className="w-2 h-2 rounded-sm" style={{ background: serviceColor(e.service, 0) }} />
                          {serviceLabel(e.service)}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3 text-gray-400">{e.operation}</td>
                      <td className="py-1.5 pr-3 text-gray-500">{e.model || "—"}</td>
                      <td className="py-1.5 pr-3 text-gray-400 font-mono">{unitsLabel(e.units)}</td>
                      <td className="py-1.5 pr-3">
                        <span className={`px-1.5 py-0.5 rounded ${e.flow === "bulk" ? "bg-brand-500/15 text-brand-400" : "bg-gray-700/50 text-gray-400"}`}>
                          {e.flow === "bulk" ? "Lote" : "Indiv."}
                        </span>
                      </td>
                      <td className="py-1.5 pl-3 text-right text-white font-medium tabular-nums whitespace-nowrap">
                        {fmt(e.cost_usd, 5)}
                        {e.dry_run && <span className="ml-1 text-gray-500">(dry)</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {loading && <div className="text-xs text-gray-500 text-center py-2">Cargando…</div>}

          {!loading && canLoadMore && events.length > 0 && (
            <button
              onClick={() => fetchPage(skip, serviceFilter)}
              className="w-full text-xs text-gray-400 hover:text-white py-2 border border-gray-800 rounded-lg hover:border-gray-700 transition-colors"
            >
              Ver más eventos ({events.length} / {total > 0 ? total : "?"})
            </button>
          )}
        </div>
      )}
    </div>
  );
}
