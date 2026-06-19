"""Hace importables los módulos de `api/` desde los tests (p.ej. `import cost_calc`)
sin depender del directorio de trabajo desde el que se invoque pytest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
