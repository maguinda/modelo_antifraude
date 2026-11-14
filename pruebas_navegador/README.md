# Pruebas en navegador

Recorridos automáticos con Playwright (Chromium y WebKit) que complementan las pruebas de `tests/`: miden en navegadores reales cosas que
un test de código no ve (desborde, tamaño de los botones, XSS, lo que de verdad se envía por la red).

```bash
pip install playwright
python -m playwright install chromium webkit
python modelo_ia/pruebas_navegador/documentacion.py   # Swagger y ReDoc: sin internet, sin desborde a 320 px
python modelo_ia/pruebas_navegador/panel.py           # pantalla de analistas, incluido un ataque XSS
python modelo_ia/pruebas_navegador/tienda.py          # tienda de prueba con el SDK; la tarjeta escrita no debe salir
```

Cada uno levanta su propio servidor de PRUEBA con una base desechable (nunca toca tu demo ni tus datos) y imprime un resultado por perfil.
Lo que debe verse: `xss_ejecutado: false`, `desborde_px: 0`, `controles_menores_a_44px: 0`, `tarjeta_salio_en_alguna_peticion: false` y
`peticiones_fuera_del_servidor: []`. Los mensajes de «Content Security Policy» en WebKit son esperados (en modo «solo observar» ese
navegador no hace nada con la política); los avisos reales se leen en Chromium.
No pruebas un celular real ni Android real.


## dashboard.py
Recorre `/dashboard` (resumen, transacciones con filtro, validación de fechas y paginación, detalle de un pago, pago inexistente, vínculos y el salto de un vínculo a sus pagos) en Chromium y WebKit: escritorio, 320 px, iPhone 13 y Pixel 5. Levanta un servidor de prueba (puerto 8773) con una base desechable y le carga ~400 pagos por la API real. Mide desborde horizontal, controles táctiles menores de 44 px (solo móvil), errores de consola y peticiones externas, y **bloquea y cuenta cualquier petición que no sea GET**: el dashboard no puede escribir. Las capturas quedan en una carpeta temporal.


## Cambios por el rediseño
- **panel.py** ahora también prueba los filtros de la cola (riesgo, búsqueda por ID, validación de fechas, limpiar y orden por riesgo); siembra dos pagos con puntaje para poder filtrarlos.
- **tienda.py** prueba el checkout: la compra normal va en modo automático; la sospechosa activa el interruptor «probar otro escenario»; verifica que ni la tarjeta, ni el nombre, ni el vencimiento ni el CVV salgan, y que el número de seguimiento y el enlace al dashboard existan.
