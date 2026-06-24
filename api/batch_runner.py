"""Orquestador de la creación en lote (en dos fases, con aprobación intermedia).

1. `run_batch` GENERA el contenido de cada fila (`make_job` → `run_pipeline`) pero
   NO publica: al terminar deja el batch en estado "review" para que el usuario vea
   un preview de todos los posts.
2. Tras la aprobación del usuario, `publish_batch` PUBLICA/PROGRAMA en Blotato
   (`publish_job_posts`) usando la fecha/hora de cada fila.

Las filas se procesan de a una (secuencial) para no chocar con el rate-limit de
subida de medios de Blotato (10 req/min). Cada fila queda registrada como un job
individual en el store global, así puede inspeccionarse con las páginas /jobs/{id}.
"""
from datetime import datetime, timedelta

from job_runner import make_job, run_pipeline, publish_job_posts


def to_utc_iso(dt: datetime | None, tz_offset_min: int) -> str | None:
    """Fecha/hora local naive → ISO-8601 UTC ('...Z') para el campo scheduledAt.

    `tz_offset_min` es lo que devuelve `Date.getTimezoneOffset()` en el navegador:
    los minutos que hay que SUMAR a la hora local para obtener UTC (p.ej. UTC-5
    → 300). None/sin fecha = publicar de inmediato.
    """
    if dt is None:
        return None
    utc = dt + timedelta(minutes=tz_offset_min)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_status_from_result(result: dict, schedule_iso: str | None, dry_run: bool) -> str:
    """Deriva el estado mostrado de una fila a partir del resultado de publicación."""
    if dry_run:
        return "dry-run"
    statuses = [result[k] for k in ("linkedin", "instagram", "facebook") if isinstance(result.get(k), dict)]
    ok = [s for s in statuses if s.get("status") in ("published", "scheduled")]
    errs = [s for s in statuses if s.get("error")]
    if ok and errs:
        return "partial"
    if ok:
        return "scheduled" if schedule_iso else "published"
    if errs:
        return "error"
    return "done"


async def run_batch(batch: dict, jobs: dict) -> None:
    """Fase 1 — genera el contenido de todas las filas (sin publicar).

    Al terminar deja el batch en "review": el usuario revisa el preview de cada post
    y aprueba antes de que `publish_batch` los publique/programe.
    """
    cfg = batch["_cfg"]

    for row in batch["rows"]:
        spec = row["_spec"]
        # params por fila + cuentas globales + dry-run global.
        params = dict(spec["params"])
        params.update(batch["account_params"])
        params["dry_run"] = batch["dry_run"]

        try:
            row["status"] = "generating"
            job = make_job(
                cfg, params,
                upload_bytes=spec["upload_bytes"],
                upload_filename=spec["upload_filename"],
                flow="bulk",
                batch_id=batch["id"],
            )
            row["job_id"] = job["id"]
            jobs[job["id"]] = job

            await run_pipeline(job)
            if job["status"] == "error":
                row["status"] = "error"
                row["error"] = job.get("error_msg") or "Error generando el contenido."
                continue

            # El título ya está disponible tras el pipeline.
            row["title"] = (job.get("content") or {}).get("title") or row.get("label")
            # Generado y a la espera de aprobación del usuario.
            row["status"] = "ready"
        except Exception as e:  # noqa: BLE001 - una fila no debe tumbar el batch
            row["status"] = "error"
            row["error"] = str(e)

    batch["status"] = "review"


async def publish_batch(batch: dict, jobs: dict) -> None:
    """Fase 2 — publica/programa las filas ya generadas tras la aprobación.

    Solo procesa filas en estado "ready" (generadas con éxito); las que fallaron en
    la generación se dejan como están. Idempotente respecto a esas filas.
    """
    tz_offset = batch.get("tz_offset", 0)

    for row in batch["rows"]:
        if row["status"] != "ready":
            continue
        job = jobs.get(row.get("job_id"))
        if job is None:
            row["status"] = "error"
            row["error"] = "El contenido del post ya no está disponible (¿se reinició el servidor?)."
            continue

        spec = row["_spec"]
        try:
            row["status"] = "publishing"
            schedule_iso = to_utc_iso(spec["schedule_dt"], tz_offset)
            result = await publish_job_posts(job, schedule_iso)
            row["result"] = result
            row["status"] = _row_status_from_result(result, schedule_iso, batch["dry_run"])
        except Exception as e:  # noqa: BLE001 - una fila no debe tumbar el batch
            row["status"] = "error"
            row["error"] = str(e)

    batch["status"] = "done"
