"""Migraciones de la base (Mongo).

El proyecto no tenía ninguna: hasta ahora la única colección (`usage_events`) se
creaba sola al primer insert y sus índices los sembraba `db.py` al vuelo. Eso sirve
para telemetría, pero no para datos que el usuario crea y espera volver a encontrar.

Aquí vive el mínimo que hace falta para que un cambio de esquema sea **reversible**:
cada migración es un módulo `NNN_nombre.py` con `up()` y `down()`, y el runner
(`python -m migrations.run`) lleva la cuenta en la colección `_migrations`.
Sin frameworks ni dependencias nuevas.
"""
