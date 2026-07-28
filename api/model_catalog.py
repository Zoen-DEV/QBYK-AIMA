"""Modelos de generación elegibles por post (catálogo curado del MCP de Higgsfield).

Fuente única de los IDs válidos para los selectores del frontend (los valida
`create_job` en app.py) y las columnas del sheet (`sheets.py`). El pipeline no
depende de este módulo: `job_runner` usa el id que llegue en params (ya validado
en la entrada) o el default del .env.

Los costos de las etiquetas son créditos de la SUSCRIPCIÓN, medidos con el
preflight `get_cost` del MCP (jul 2026) — mantener en sincronía con
`pricing.example.json → higgsfield_mcp`. El id `elevenlabs` es de la app:
`higgsfield_mcp.generate_audio` lo traduce a `text2speech_v2` + variant.
"""

# Imagen: créditos por generación.
IMAGE_MODELS = {
    "nano_banana_pro": "Nano Banana Pro · 2 cr/imagen (máxima calidad)",
    "nano_banana_2": "Nano Banana 2 · 1.5 cr/imagen",
    "nano_banana": "Nano Banana · 1 cr/imagen (económico)",
    "gpt_image_2": "GPT Image 2 (ChatGPT) · 0.5 cr/imagen",
    "z_image": "Z Image · 0.15 cr/imagen (ultra económico)",
}

# Video: créditos por segundo generado.
VIDEO_MODELS = {
    "kling3_0_turbo": "Kling 3.0 Turbo · 1.5 cr/segundo (económico)",
    "seedance_2_0_mini": "Seedance 2.0 Mini · 2.5 cr/segundo",
    "seedance_2_0": "Seedance 2.0 · 4.5 cr/segundo (máxima calidad)",
}

# Voz en off (TTS): créditos por carácter del guion.
TTS_MODELS = {
    "seed_audio": "Seed Audio (ByteDance) · ~0.007 cr/carácter",
    "elevenlabs": "ElevenLabs · ~0.003 cr/carácter",
}
