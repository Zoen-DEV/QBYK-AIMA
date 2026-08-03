"""Reconexión al stream SSE: la pantalla de progreso no se puede quedar colgada.

La cola de eventos es de consumo ÚNICO y sin repetición. Un stream que se murió
—recarga de página, corte de red, reload del dev server— igual saca el evento de
cierre de la cola antes de enterarse de que ya nadie lo escucha, y ahí se pierde
para siempre. El cliente que reconectaba se quedaba esperando un "preview"/"done"
que no iba a volver a emitirse: "Generando tu contenido" eterno con el job
terminado hacía rato, y sin forma de llegar a la compuerta.

El estado terminal ya está espejado en `job["status"]` antes de cada push, así que
el stream lo reconstruye al conectarse. Acá se ejercita esa convergencia para los
dos flujos (el snapshot del job es el mismo) y, sobre todo, que NO se convierta en
un rebote infinito cuando el usuario aprueba el preview.
"""
import asyncio
import json

import pytest

import app


def _job(job_id: str, status: str, **extra) -> dict:
    job = {"id": job_id, "status": status, "error_msg": None, "_queue": asyncio.Queue()}
    job.update(extra)
    app.jobs[job_id] = job
    return job


@pytest.fixture(autouse=True)
def _limpiar_store():
    """El store de jobs es global y vive en memoria: no filtrar entre tests."""
    yield
    app.jobs.clear()


async def _eventos(job_id: str, maximo: int = 5) -> list[dict]:
    """Los eventos que emite el stream hasta que cierra (o hasta `maximo`)."""
    respuesta = await app.stream_job(job_id)
    salida: list[dict] = []
    async for chunk in respuesta.body_iterator:
        texto = chunk.decode() if isinstance(chunk, bytes) else chunk
        for linea in texto.strip().splitlines():
            if linea.startswith("data: "):
                salida.append(json.loads(linea[len("data: "):]))
        if len(salida) >= maximo:
            break
    return salida


# ── Reconexión sobre un job que ya llegó a destino ────────────────────────────

@pytest.mark.parametrize(
    "status, step, destino",
    [
        ("preview", "preview", "preview"),   # compuerta previa: guiones editables
        ("review", "done", "review"),        # compuerta de revisión: medio generado
        ("done", "done", "result"),          # ya publicado/programado
    ],
)
async def test_reconectar_a_un_job_terminado_redirige(status, step, destino):
    """Con la cola vacía, el stream reconstruye el cierre desde job["status"]."""
    _job("j1", status)
    eventos = await _eventos("j1")
    assert eventos == [{"step": step, "redirect": f"/jobs/j1/{destino}"}]


async def test_reconectar_a_un_job_con_error_lo_reporta():
    """El error no se traga: la pantalla tiene que poder mostrarlo tras reconectar."""
    _job("j1", "error", error_msg="Se cayó la extracción")
    assert await _eventos("j1") == [{"step": "error", "msg": "Se cayó la extracción"}]


async def test_un_job_terminado_sin_mensaje_de_error_igual_cierra():
    """Sin `error_msg` el evento sigue siendo válido: nunca dejar el stream abierto."""
    _job("j1", "error")
    assert await _eventos("j1") == [{"step": "error", "msg": "Error desconocido"}]


# ── Un job vivo no se corta ───────────────────────────────────────────────────

async def test_un_job_corriendo_streamea_su_progreso():
    """El replay es SOLO para estados terminales: corriendo, manda los eventos reales."""
    job = _job("j1", "running")
    job["_queue"].put_nowait({"step": "extract", "status": "done"})
    job["_queue"].put_nowait({"step": "preview", "redirect": "/jobs/j1/preview"})

    eventos = await _eventos("j1")

    assert eventos == [
        {"step": "extract", "status": "done"},
        {"step": "preview", "redirect": "/jobs/j1/preview"},
    ]


# ── La trampa: aprobar el preview no puede rebotar al preview ─────────────────

async def test_generate_saca_al_job_de_preview_antes_de_responder(monkeypatch):
    """El flip a "running" va en el endpoint, no en la tarea de fondo.

    El front navega a /jobs/{id} ni bien responde `/generate` y abre el stream. Si el
    estado siguiera en "preview" en ese momento, el replay lo mandaría de vuelta a la
    compuerta que acaba de aprobar: bucle infinito y el medio nunca se muestra.
    """
    _job("j1", "preview")
    corrio = asyncio.Event()

    async def _resume_falso(job):
        corrio.set()

    monkeypatch.setattr(app, "resume_media", _resume_falso)

    await app.generate_job_media("j1")

    # Ya no está en preview APENAS responde, sin depender de que la tarea haya corrido.
    # Se mira el evento que el stream emitiría (no se consume: con la cola vacía y el
    # job vivo, el stream se queda esperando de verdad, que es justo lo que se quiere).
    assert app.jobs["j1"]["status"] == "running"
    assert app._evento_terminal(app.jobs["j1"]) is None

    await asyncio.wait_for(corrio.wait(), timeout=1)  # la fase de medio igual arranca


async def test_generate_rechaza_un_job_que_no_esta_en_preview(monkeypatch):
    """El 409 sigue vivo: el flip no puede volverlo reentrante."""
    from fastapi import HTTPException

    _job("j1", "running")
    monkeypatch.setattr(app, "resume_media", lambda job: asyncio.sleep(0))

    with pytest.raises(HTTPException) as exc:
        await app.generate_job_media("j1")
    assert exc.value.status_code == 409
