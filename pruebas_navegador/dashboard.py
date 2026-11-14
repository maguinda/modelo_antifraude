"""Panel de control (/dashboard): recorrido automático en Chromium y WebKit (escritorio, 320 px, iPhone 13 y Pixel 5).

Levanta un servidor de PRUEBA con una base desechable (puerto 8773), le carga pagos de ejemplo por la API real y recorre:
resumen, transacciones (filtrar, validar, paginar), detalle de un pago, vínculos y el salto de un vínculo a sus pagos.
Mide: desborde horizontal, controles táctiles menores de 44 px, errores de consola, peticiones fuera del servidor y, sobre todo,
BLOQUEA y cuenta toda petición que no sea GET: el dashboard no debe escribir nada. No toca tu demo ni tus datos.
Necesita Playwright:   pip install playwright && python -m playwright install chromium webkit
Uso:                   python modelo_ia/pruebas_navegador/dashboard.py
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[2]
S = Path(tempfile.mkdtemp(prefix="pruebas_dashboard_"))
CAPTURAS = S / "capturas"
CAPTURAS.mkdir()
carpeta = S / "demo"
PUERTO = 8773
BASE = f"http://127.0.0.1:{PUERTO}"
srv = subprocess.Popen([sys.executable, "-m", "modelo_ia.api.demo", "--carpeta", str(carpeta), "--port", str(PUERTO), "--reiniciar"], cwd=str(RAIZ),
                       stdout=open(S / "servidor.log", "w"), stderr=subprocess.STDOUT)
resultados = []
MIDE_44 = ("Array.from(document.querySelectorAll('a.btn, button, select, input[type=text], input[type=number], input[type=date], input[type=password], a.seg, a.codigo, a.pildora-nav'))"
           ".filter(e => e.offsetParent !== null && e.getBoundingClientRect().height < 43.5 && !e.closest('[hidden]')).map(e => (e.className || e.tagName) + ':' + Math.round(e.getBoundingClientRect().height))")
try:
    for _ in range(80):
        try:
            if httpx.get(BASE + "/v1/salud").status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    carga = subprocess.run([sys.executable, "-W", "ignore", "-m", "modelo_ia.api.cargar_ejemplos", "--url", BASE, "--carpeta", str(carpeta), "--n", "400", "--dias", "10"],
                           cwd=str(RAIZ), capture_output=True, text=True, timeout=300)
    print((carga.stdout.strip().splitlines() or ["(sin salida)"])[-2][:200])
    claves_demo = (carpeta / "claves_demo.txt").read_text(encoding="utf-8")
    k_ana = next(l.split("=", 1)[1].strip() for l in claves_demo.splitlines() if l.startswith("ANALISTA="))
    pend = httpx.get(BASE + "/v1/revisiones?limite=3", headers={"X-API-Key": k_ana}).json()["revisiones"]        # se resuelven 3 para que haya quién-decidió que mostrar
    for x in pend:
        assert httpx.post(f"{BASE}/v1/revisiones/{x['id_evaluacion']}/resolver", json={"decision": "APROBADA"}, headers={"X-API-Key": k_ana}).status_code == 200
    seg_resuelto = pend[0]["id_seguimiento"]
    with sync_playwright() as p:
        perfiles = [("escritorio 1280", "chromium", {"viewport": {"width": 1280, "height": 900}}), ("escritorio 1280", "webkit", {"viewport": {"width": 1280, "height": 900}}),
                    ("320 px", "chromium", {"viewport": {"width": 320, "height": 568}}), ("320 px", "webkit", {"viewport": {"width": 320, "height": 568}}),
                    ("iPhone 13", "webkit", p.devices["iPhone 13"]), ("Pixel 5", "chromium", p.devices["Pixel 5"])]
        for nombre, motor, opts in perfiles:
            nav = getattr(p, motor).launch()
            ctx = nav.new_context(**opts)
            page = ctx.new_page()
            consola, externas, escrituras = [], [], []
            f = {"motor": motor, "perfil": nombre}
            page.on("console", lambda m, c=consola: c.append(m.text[:140]) if m.type == "error" and "Content Security" not in m.text else None)

            def revisar(r, ex=externas, es=escrituras):
                if r.request.method != "GET":
                    es.append(r.request.method + " " + r.request.url[:80])
                    r.abort()                                                    # se BLOQUEA cualquier escritura
                    return
                if not r.request.url.startswith((BASE, "data:", "blob:")):
                    ex.append(r.request.url[:80])
                r.continue_()
            page.route("**/*", revisar)
            medidas = {}

            def medir(clave):
                w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                medidas[clave] = {"desborde_px": max(0, w[0] - w[1]), "controles_menores_a_44px": page.evaluate(MIDE_44)[:4]}
            tag = f"{motor}_{nombre.replace(' ', '_')}"
            # --- resumen
            page.goto(BASE + "/dashboard#/resumen", wait_until="networkidle")
            page.wait_for_selector(".kpi", timeout=20000)
            f["kpis"] = page.locator(".kpi").count()
            f["usuario_en_la_barra"] = page.locator("#usuario").is_visible() and page.locator("#usuario").inner_text() == "Sesión de Analista de demostración"
            f["equipo_de_revision_visible"] = page.locator("h2:has-text('Equipo de revisión')").count() == 1 and "Analista de demostración" in page.inner_text("#contenido")
            f["graficas"] = page.locator("svg.grafica").count()
            f["pagos_en_resumen"] = page.locator(".kpi .valor").first.inner_text()
            medir("resumen")
            page.screenshot(path=str(CAPTURAS / f"{tag}_resumen.png"))
            # --- transacciones: tabla, filtro por decisión desde la pantalla, validación de fechas
            page.click("#nav a[data-ruta=transacciones]")
            page.wait_for_selector("#orden", timeout=20000)                      # solo existe en Transacciones (el resumen también tiene una tabla)
            page.wait_for_selector("table.tabla tbody tr", timeout=20000)
            f["filas_pagina_1"] = page.locator("table.tabla tbody tr").count()
            if page.locator(".alternar-filtros").is_visible():
                page.click(".alternar-filtros")
            page.select_option("#f_decision", "RECHAZADA")
            page.click("button[type=submit]:has-text('Aplicar')")
            page.wait_for_function("location.hash.includes('decision=RECHAZADA')")
            try:                                                                  # espera por CONTENIDO: solo pasa cuando la tabla ya es la filtrada
                page.wait_for_function("(() => { const e = [...document.querySelectorAll('table.tabla tbody tr .estado')]; return e.length > 0 && e.every(x => x.textContent === 'Rechazada'); })()", timeout=20000)
                f["filtro_decision_ok"] = True
            except Exception:
                f["filtro_decision_ok"] = False
            medir("transacciones")
            page.screenshot(path=str(CAPTURAS / f"{tag}_transacciones.png"))
            if page.locator(".alternar-filtros").is_visible() and page.locator(".filtros-cuerpo").is_hidden():
                page.click(".alternar-filtros")
            page.fill("#f_desde", "2026-11-10")
            page.fill("#f_hasta", "2026-11-01")
            page.click("button[type=submit]:has-text('Aplicar')")
            f["validacion_fechas_visible"] = page.locator(".error-campo:not([hidden])").count() == 1 and "decision=RECHAZADA" in page.evaluate("location.hash")
            page.fill("#f_desde", "")
            page.fill("#f_hasta", "")
            # --- paginación
            page.goto(BASE + "/dashboard#/transacciones", wait_until="networkidle")
            page.wait_for_selector("table.tabla tbody tr", timeout=20000)
            primero = page.locator("table.tabla tbody tr .seg").first.inner_text()
            page.locator("a.btn:has-text('Siguiente')").click()
            page.wait_for_function("location.hash.includes('d=25')")
            try:
                page.wait_for_function("(p) => { const s = document.querySelector('table.tabla tbody tr .seg'); return s && s.textContent !== p; }", arg=primero, timeout=20000)
                f["paginacion_cambia_de_pagina"] = True
            except Exception:
                f["paginacion_cambia_de_pagina"] = False
            # --- detalle
            page.goto(BASE + "/dashboard#/transacciones", wait_until="networkidle")
            page.wait_for_selector(".seg", timeout=20000)
            seg = page.locator(".seg").first.inner_text()
            page.locator(".seg").first.click()
            page.wait_for_selector(".pasos li", timeout=20000)
            f["detalle_ok"] = page.locator(".seg-grande").inner_text() == seg and page.locator(".pasos li").count() >= 3 and page.locator(".datos-grupo").count() >= 4
            medir("detalle")
            page.screenshot(path=str(CAPTURAS / f"{tag}_detalle.png"))
            # un pago ya resuelto muestra quién lo resolvió
            page.goto(BASE + f"/dashboard#/pago/{seg_resuelto}", wait_until="networkidle")
            page.wait_for_selector(".pasos li", timeout=20000)
            f["resuelta_por_en_el_detalle"] = "Analista de demostración" in page.inner_text("#contenido")
            # un pago que no existe: mensaje claro, sin romper
            page.goto(BASE + "/dashboard#/pago/AF-ZZZZ-ZZZZ", wait_until="networkidle")
            page.wait_for_selector("#contenido .tarjeta h2", timeout=20000)
            f["pago_inexistente_dice_no_encontrado"] = page.locator("#contenido h2").first.inner_text() == "No encontrado"
            # --- vínculos y salto a sus pagos
            page.goto(BASE + "/dashboard#/vinculos", wait_until="networkidle")
            page.wait_for_selector(".vinculo", timeout=20000)
            f["vinculos"] = page.locator(".vinculo").count()
            medir("vinculos")
            page.screenshot(path=str(CAPTURAS / f"{tag}_vinculos.png"))
            page.locator(".vinculo a.btn").first.click()
            page.wait_for_selector("table.tabla tbody tr", timeout=20000)
            f["vinculo_lleva_a_pagos"] = page.locator("table.tabla tbody tr").count() > 0 and any(k in page.evaluate("location.hash") for k in ("ip=", "dispositivo=", "id_cliente="))
            # --- resumen
            f["medidas"] = medidas
            f["desborde_max_px"] = max(m["desborde_px"] for m in medidas.values())
            movil = opts.get("viewport", {}).get("width", 0) <= 760               # el tamaño táctil de 44 px se exige en móvil
            f["controles_menores_a_44px"] = sorted({c for m in medidas.values() for c in m["controles_menores_a_44px"]})[:6] if movil else []
            f["escrituras_bloqueadas"] = escrituras
            f["peticiones_fuera_del_servidor"] = externas[:3]
            f["consola"] = [c for c in consola if "404" not in c][:3]            # el único 404 esperado es el del pago inexistente que se pide a propósito
            resultados.append(f)
            nav.close()
finally:
    srv.terminate()
    srv.wait(10)
for r in resultados:
    print(json.dumps(r, ensure_ascii=False))
print("capturas en", CAPTURAS)
