# Plantillas de imagen (respaldo / modo "plantillas directas")

Cada post puede elegir uno de **3 sets de estilo** (campo `template_set` = 1, 2 o 3).
Cada set tiene 2 PNG **1080×1080** (cuadrado 1:1, sRGB), **sin texto**. La app los
recorta al aspecto de cada red (feed 4:5, historia 9:16 — se pierde de los lados) y
les dibuja encima el texto de la pieza con el mismo lockup que se le pide al modelo:
**titular en la banda alta y segunda línea anclada al pie**, así que el espacio
negativo conviene arriba y abajo, con el sujeto en la banda central:

| archivo         | rol                                             |
|-----------------|-------------------------------------------------|
| `template-1.png`| base / portada (LinkedIn, IG single, slide 0)   |
| `template-2.png`| slides de info / argumento (todos los extras)   |

`template-3.png` era el slide de créditos / cierre del carrusel. Ese slide ya no
existe (el último es informativo como los del centro), así que el archivo quedó sin
uso: podés dejarlo o borrarlo, la app no lo pide.

## Layout de carpetas

- **Set 1** (default): los `template-1/2.png` en esta carpeta (raíz) **o** en `set-1/`.
- **Set 2**: `set-2/template-1.png`, `set-2/template-2.png`.
- **Set 3**: `set-3/template-1.png`, `set-3/template-2.png`.

Si a un set le falta algún PNG, la app **cae al set 1** para ese rol (así los 3 sets
funcionan de inmediato: el 2 y el 3 se ven como el 1 hasta que agregas sus archivos).
Para diferenciar los sets, crea las carpetas `set-2/` y `set-3/` y deja ahí tus 2 PNG.
