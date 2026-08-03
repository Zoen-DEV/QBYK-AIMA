"""Orquestador de la creación en lote (tres fases, con dos aprobaciones).

El lote recorre exactamente las mismas compuertas que el flujo individual, solo que
a nivel de lote en vez de por post:

1. `run_batch` ESCRIBE el contenido de cada fila (`make_job` → `run_pipeline`). El
   pipeline pausa en `status="preview"` antes de gastar créditos, así que el batch
   queda en "preview": el usuario revisa y edita guiones, storyboards y prompts de
   todas las filas (con `POST /jobs/{id}/edit`, el mismo endpoint del individual).
2. Tras esa aprobación, `generate_batch_media` GENERA el medio de cada fila
   (`resume_media`) y deja el batch en "review" para ver los posts ya con imagen/video.
3. Tras la segunda aprobación, `publish_batch` PUBLICA/PROGRAMA en Blotato
   (`publish_job_posts`) usando la fecha/hora de cada fila.

La compuerta de la fase 1 existe porque el video es lo caro y lo que más falla: un
storyboard malo detectado acá cuesta cero, detectado después cuesta el reel entero.

Las filas se procesan de a una (secuencial) para no chocar con el rate-limit de
subida de medios de Blotato (10 req/min). Cada fila queda registrada como un job
individual en el store global, así puede inspeccionarse con las páginas /jobs/{id}.
"""
from datetime import datetime, timedelta

from job_runner import make_job, run_pipeline, resume_media, publish_job_posts


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
    """Fase 1 — escribe el contenido de todas las filas (sin generar medio ni publicar).

    Con la compuerta de preview activada (default) el pipeline de cada fila pausa
    apenas termina la escritura, así que el batch queda en "preview" y espera a que
    el usuario revise los guiones antes de que `generate_batch_media` gaste créditos.
    """
    cfg = batch["_cfg"]

    for row in batch["rows"]:
        spec = row["_spec"]
        # params por fila + cuentas globales + dry-run global + identidad visual global.
        params = dict(spec["params"])
        params.update(batch["account_params"])
        params["dry_run"] = batch["dry_run"]
        # La identidad se resolvió UNA vez al subir el sheet (no por fila): un lote es
        # un envío de un usuario en un momento, y cambiar la identidad activa a mitad
        # de la escritura no puede partir el lote en dos estéticas.
        params.update(batch.get("identidad_params") or {})

        try:
            # "writing" (fase 1) vs "generating" (fase 2, el medio): estados distintos
            # para que la UI sepa en qué compuerta está cada fila.
            row["status"] = "writing"
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

            # El título ya está disponible tras la fase de escritura.
            row["title"] = (job.get("content") or {}).get("title") or row.get("label")
            # Con la compuerta activada el job quedó en "preview": la fila espera la
            # revisión del guion. Sin compuerta (preview_step=0) el medio ya se generó
            # y la fila pasa directo a esperar la aprobación de publicación.
            row["status"] = "preview" if job["status"] == "preview" else "ready"
        except Exception as e:  # noqa: BLE001 - una fila no debe tumbar el batch
            row["status"] = "error"
            row["error"] = str(e)

    # Si ninguna fila pausó (compuerta apagada, o todas fallaron) no hay nada que
    # revisar: el lote salta directo a la aprobación de publicación.
    batch["status"] = ("preview" if any(r["status"] == "preview" for r in batch["rows"])
                       else "review")


async def generate_batch_media(batch: dict, jobs: dict) -> None:
    """Fase 2 — genera el medio de las filas aprobadas en el preview del lote.

    Espejo exacto de `POST /jobs/{id}/generate` del flujo individual, aplicado a
    todas las filas. Solo procesa las que están en "preview"; las que fallaron en la
    escritura se dejan como están. Secuencial, como el resto del lote.
    """
    for row in batch["rows"]:
        if row["status"] != "preview":
            continue
        job = jobs.get(row.get("job_id"))
        if job is None:
            row["status"] = "error"
            row["error"] = "El contenido del post ya no está disponible (¿se reinició el servidor?)."
            continue
        try:
            row["status"] = "generating"
            await resume_media(job)
            if job["status"] == "error":
                row["status"] = "error"
                row["error"] = job.get("error_msg") or "Error generando el medio."
                continue
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
