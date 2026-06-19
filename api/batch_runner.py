"""Orquestador de la creación en lote.

Por cada fila parseada del sheet: construye un job normal (`make_job`), corre el
pipeline completo (`run_pipeline`) y luego publica/programa el resultado en Blotato
(`publish_job_posts`) usando la fecha/hora de la fila. Las filas se procesan de a
una (secuencial) para no chocar con el rate-limit de subida de medios de Blotato
(10 req/min). Cada fila queda registrada como un job individual en el store global,
así puede inspeccionarse con las páginas /jobs/{id} existentes.
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
    """Procesa todas las filas del batch en orden y actualiza su estado in-place."""
    cfg = batch["_cfg"]
    tz_offset = batch.get("tz_offset", 0)

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

            row["status"] = "publishing"
            schedule_iso = to_utc_iso(spec["schedule_dt"], tz_offset)
            result = await publish_job_posts(job, schedule_iso)
            row["result"] = result
            row["status"] = _row_status_from_result(result, schedule_iso, batch["dry_run"])
        except Exception as e:  # noqa: BLE001 - una fila no debe tumbar el batch
            row["status"] = "error"
            row["error"] = str(e)

    batch["status"] = "done"
