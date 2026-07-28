# Plantillas de imagen (respaldo / modo "plantillas directas")

Cada post puede elegir uno de **3 sets de estilo** (campo `template_set` = 1, 2 o 3).
Cada set tiene 3 PNG **1080×1080** (cuadrado 1:1, sRGB), **sin texto**, con espacio
negativo (sobre todo abajo/centro) donde la app dibuja el copy blanco:

| archivo         | rol                                             |
|-----------------|-------------------------------------------------|
| `template-1.png`| base / portada (LinkedIn, IG single, slide 0)   |
| `template-2.png`| slides de info / argumento                      |
| `template-3.png`| slide de créditos / cierre                      |

## Layout de carpetas

- **Set 1** (default): los `template-1/2/3.png` en esta carpeta (raíz) **o** en `set-1/`.
- **Set 2**: `set-2/template-1.png`, `set-2/template-2.png`, `set-2/template-3.png`.
- **Set 3**: `set-3/template-1.png`, `set-3/template-2.png`, `set-3/template-3.png`.

Si a un set le falta algún PNG, la app **cae al set 1** para ese rol (así los 3 sets
funcionan de inmediato: el 2 y el 3 se ven como el 1 hasta que agregas sus archivos).
Para diferenciar los sets, crea las carpetas `set-2/` y `set-3/` y deja ahí tus 3 PNG.
