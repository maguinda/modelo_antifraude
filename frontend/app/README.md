# Panel de analistas (React + TypeScript)

Primera pieza del frontend nuevo. **Convive con las pantallas actuales** (`panel/`, `dashboard/`, `tienda/`): no las reemplaza ni cambia sus direcciones. Se migra pantalla por pantalla, en PRs aparte.

Hoy incluye dos pantallas: **Transacciones** (lectura, con paginación) y **Revisión manual** (`?vista=revision`, la única que ESCRIBE: aprobar o rechazar un pago). La entrega la API en **`/app`** (archivos en `/estaticos/app/`) **si está compilada** (`frontend/app/dist`); sin compilar, `/app` no existe y todo lo demás sigue igual. La imagen Docker la compila sola (etapa de Node; la imagen final no lleva Node). En desarrollo se ve con `npm run dev`.

## Comandos (Node 24)
```bash
cd modelo_ia/frontend/app
npm ci --ignore-scripts      # instala las versiones exactas del package-lock
npm run dev                  # http://localhost:5173/estaticos/app/  (reenvía /v1 a http://127.0.0.1:8000; cámbialo con MODELO_IA_DESTINO)
npm run typecheck && npm test && npm run build
python modelo_ia/pruebas_navegador/app_react.py        # recorrido en Chromium y WebKit con `vite preview`
python modelo_ia/pruebas_navegador/app_react.py --api  # lo mismo, servida por la API en /app y con la política de contenido (CSP) en bloqueo
```

## Reglas de esta carpeta
- **Estilos:** CSS Modules + las variables de `comun/diseno.css` (colores, espacios, tipografía). Antes de escribir un estilo nuevo se busca si ya existe allí (`.btn`, `.campo`, `.tarjeta`, `.chip`). Sin estilos en línea con valores fijos.
- **Seguridad:** todo texto de la API se muestra como texto (React lo escapa); no se usa `dangerouslySetInnerHTML`. La clave de API vive solo en memoria. El cliente solo hace GET.
- **Dependencias:** versiones exactas (`--save-exact`), instaladas con `npm ci --ignore-scripts`, y al agregar una nueva se elige una publicada hace más de 2 semanas (`npm install --before=<fecha>`). La CI corre `npm audit` sobre las de producción.
- **Tipos de la API:** el OpenAPI describe las rutas del panel como «objeto libre», así que `src/red/tipos.ts` se escribe a mano. Pendiente: modelos de respuesta en el backend para generarlos.

## Paginación (por qué es así)
- La **página** y el **tamaño** (10, 25 o 50) viven en la dirección (`?pagina=2&limite=25`): se puede copiar el enlace y «atrás» funciona. Valores raros o fuera de rango se corrigen, nunca dan error.
- Mientras llega una página, **los datos anteriores siguen a la vista** (atenuados) y los botones se bloquean: ni saltos ni doble clic. Una respuesta vieja nunca pisa a la nueva.
- Si falla (corte, 500), aparece el aviso con **Reintentar** y se conserva lo que había. Los cortes transitorios (502/503/504 y fallas de red) se reintentan solos hasta 2 veces; **no** se reintenta el tiempo agotado (20 s) ni los errores del usuario (401, 403, 404, 422). El cliente solo acepta rutas del mismo origen, sin cookies y sin caché.
- **Teclado y lectores de pantalla:** los botones de página usan `aria-disabled` (no `disabled`), así el foco no se pierde al paginar; la barra dice «Cargando…» en una zona `aria-live`. Cuando la página corregida por el total difiere de la pedida, se reemplaza la entrada del historial (no se empuja), para que «atrás» no quede atrapado.
- **La barra coincide con las filas:** muestra la página de los datos que se ven, no la pedida; si falla un cambio de página, sigue diciendo la anterior.
- **Tope:** el backend no acepta un desplazamiento mayor a 100.000 filas. Más allá se avisa que hay que afinar con filtros (fecha, comercio…).

## Revisión manual (la pantalla que escribe)
- Reglas de seguridad al decidir: el primer clic solo pide **confirmar** (no escribe); un doble clic manda **una** escritura; la escritura **nunca se reintenta sola** (repetirla podría duplicarla); si no hay respuesta, la pantalla **no dice «guardado»**: avisa que no se sabe, recarga la lista y deja decidir de nuevo solo si el pago sigue pendiente; si otra persona ya lo resolvió (409) lo dice y recarga.
- La lista se vigila cada 20 s (solo cuenta; no redibuja, para no perder una nota a medio escribir) y se pausa con la pestaña oculta.
- Los filtros viven en la dirección y se sanean (un valor raro se descarta). Las etiquetas del contador salen de los datos que se ven, no de los filtros recién elegidos.
- **Diferencias con `/panel` (a propósito):** la clave NO se guarda en `sessionStorage` (al recargar hay que volver a entrar) y no hay entrada automática de la demo. Menos cómodo, menos superficie ante un XSS.
- Prueba en vivo: `python modelo_ia/pruebas_navegador/app_revision.py` (ver `pruebas_navegador/README.md`).
