# Panel de analistas (React + TypeScript)

Primera pieza del frontend nuevo. **Convive con las pantallas actuales** (`panel/`, `dashboard/`, `tienda/`): no las reemplaza ni cambia sus direcciones. Se migra pantalla por pantalla, en PRs aparte.

Hoy incluye una pantalla: **Transacciones**, con paginación. Todavía **no la entrega la API** (llega en el siguiente paso); se ve con `npm run dev`.

## Comandos (Node 24)
```bash
cd modelo_ia/frontend/app
npm ci --ignore-scripts      # instala las versiones exactas del package-lock
npm run dev                  # http://localhost:5173/estaticos/app/  (reenvía /v1 a http://127.0.0.1:8000; cámbialo con MODELO_IA_DESTINO)
npm run typecheck && npm test && npm run build
python modelo_ia/pruebas_navegador/app_react.py   # recorrido en Chromium y WebKit (ver pruebas_navegador/README.md)
```

## Reglas de esta carpeta
- **Estilos:** CSS Modules + las variables de `comun/diseno.css` (colores, espacios, tipografía). Antes de escribir un estilo nuevo se busca si ya existe allí (`.btn`, `.campo`, `.tarjeta`, `.chip`). Sin estilos en línea con valores fijos.
- **Seguridad:** todo texto de la API se muestra como texto (React lo escapa); no se usa `dangerouslySetInnerHTML`. La clave de API vive solo en memoria. El cliente solo hace GET.
- **Dependencias:** versiones exactas (`--save-exact`), instaladas con `npm ci --ignore-scripts`, y al agregar una nueva se elige una publicada hace más de 2 semanas (`npm install --before=<fecha>`). La CI corre `npm audit` sobre las de producción.
- **Tipos de la API:** el OpenAPI describe las rutas del panel como «objeto libre», así que `src/red/tipos.ts` se escribe a mano. Pendiente: modelos de respuesta en el backend para generarlos.

## Paginación (por qué es así)
- La **página** y el **tamaño** (10, 25 o 50) viven en la dirección (`?pagina=2&limite=25`): se puede copiar el enlace y «atrás» funciona. Valores raros o fuera de rango se corrigen, nunca dan error.
- Mientras llega una página, **los datos anteriores siguen a la vista** (atenuados) y los botones se bloquean: ni saltos ni doble clic. Una respuesta vieja nunca pisa a la nueva.
- Si falla (corte, 500), aparece el aviso con **Reintentar** y se conserva lo que había. Los cortes transitorios (502/503/504, red, tiempo de espera de 20 s) se reintentan solos hasta 2 veces; los errores del usuario (401, 403, 404, 422), nunca.
- **Tope:** el backend no acepta un desplazamiento mayor a 100.000 filas. Más allá se avisa que hay que afinar con filtros (fecha, comercio…).
