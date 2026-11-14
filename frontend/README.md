# Frontend (pantallas)

Las pantallas del sistema: **panel** de revisión, **dashboard** de control, **tienda** de demostración, el **SDK**
que corre en el navegador del comprador y el diseño compartido (`comun/diseno.css`). Son HTML, CSS y JavaScript
propios, sin pasos de compilación y sin terceros.

## Cómo se relaciona con el backend
- Solo hablan con la API por su **contrato**: rutas `/v1/...` (y `/demo/...` en la demo), del mismo origen, con la
  clave en la cabecera `X-API-Key`. No importan nada del backend ni leen su disco.
- Una prueba (`test_cada_ruta_de_la_api_que_usa_el_frontend_existe_en_el_contrato`) falla si algún script llama a
  una ruta que ya no está en `openapi.json`; otra (`test_los_limites_entre_carpetas_se_respetan`) falla si las
  pantallas vuelven a `api/` o si el frontend referencia al backend.
- Las direcciones públicas no cambiaron: `/panel`, `/dashboard`, `/tienda` y `/estaticos/<carpeta>/<archivo>`.

## Quién las entrega
| Modo | Cómo | Cuándo |
|---|---|---|
| **Con la API** (por omisión) | La API sirve esta carpeta (`MODELO_IA_FRONTEND` cambia dónde está). | Desarrollo, demo y el despliegue actual. |
| **Aparte** | `MODELO_IA_PANTALLAS=0` en la API; el servidor web (Caddy) entrega esta carpeta con las mismas cabeceras (CSP). Ver `despliegue/Caddyfile.pantallas-aparte`. | Cuando el frontend se despliegue por separado. **Variante preparada y no probada con Caddy real.** |

En los dos modos todo sigue en el mismo origen (la API detrás del mismo servidor web), así que no hace falta CORS.

## Formato
No hay paso en la CI: se formatea a mano con
`npx prettier@3.3.3 --print-width 120 --write frontend/<carpeta>/<archivo>`.

## Seguridad
Sin scripts ni estilos en línea y sin terceros (CSP `default-src 'none'; script-src 'self'; ...`). La política la
pone quien entrega las páginas: la API (`api/panel.py`) o, en el modo «aparte», el servidor web.
