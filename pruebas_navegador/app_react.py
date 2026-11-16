"""Recorrido de la app nueva (React) en Chromium y WebKit: paginación, cargas lentas, corte del servidor, desborde y controles táctiles.

Levanta un servidor de PRUEBA (base desechable, ~400 pagos) y la app ya compilada (`npm run build` + `vite preview`, con la API por proxy).
Bloquea y cuenta cualquier petición que no sea GET: esta pantalla no escribe. Requiere Node y `npm ci` hecho en frontend/app.
Uso: python modelo_ia/pruebas_navegador/app_react.py
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
APP = RAIZ / "modelo_ia" / "frontend" / "app"
S = Path(tempfile.mkdtemp(prefix="pruebas_app_react_"))
CAPTURAS = S / "capturas"
CAPTURAS.mkdir()
carpeta = S / "demo"
PUERTO_API, PUERTO_APP = 8774, 5199
API = f"http://127.0.0.1:{PUERTO_API}"
URL = f"http://127.0.0.1:{PUERTO_APP}/estaticos/app/"
procesos = []


def lanzar(cmd, **kw):
    p = subprocess.Popen(cmd, stdout=open(S / f"{len(procesos)}.log", "w"), stderr=subprocess.STDOUT, **kw)
    procesos.append(p)
    return p


def esperar(url, veces=120):
    for _ in range(veces):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


MIDE_44 = (
    "Array.from(document.querySelectorAll('a, button, select, input')).filter(e => e.offsetParent !== null && "
    "e.getBoundingClientRect().height < 43.5 && !e.closest('[hidden]')).map(e => (e.className || e.tagName) + ':' + Math.round(e.getBoundingClientRect().height))"
)
resultados = []
try:
    lanzar(
        [
            sys.executable,
            "-m",
            "modelo_ia.api.demo",
            "--carpeta",
            str(carpeta),
            "--port",
            str(PUERTO_API),
            "--reiniciar",
        ],
        cwd=str(RAIZ),
    )
    assert esperar(API + "/v1/salud"), "el servidor de prueba no arrancó"
    subprocess.run(
        [
            sys.executable,
            "-W",
            "ignore",
            "-m",
            "modelo_ia.api.cargar_ejemplos",
            "--url",
            API,
            "--carpeta",
            str(carpeta),
            "--n",
            "400",
            "--dias",
            "10",
        ],
        cwd=str(RAIZ),
        capture_output=True,
        timeout=300,
        check=True,
    )
    claves = (carpeta / "claves_demo.txt").read_text(encoding="utf-8")
    k_ana = next(l.split("=", 1)[1].strip() for l in claves.splitlines() if l.startswith("ANALISTA="))
    subprocess.run(["npm", "run", "build"], cwd=str(APP), capture_output=True, check=True, timeout=300)
    lanzar(
        ["npx", "vite", "preview", "--port", str(PUERTO_APP), "--strictPort", "--host", "127.0.0.1"],
        cwd=str(APP),
        env={**__import__("os").environ, "MODELO_IA_DESTINO": API},
    )
    assert esperar(URL), "la app compilada no arrancó"

    PERFILES = [
        ("chromium", "escritorio 1280", {"viewport": {"width": 1280, "height": 800}}),
        ("webkit", "escritorio 1280", {"viewport": {"width": 1280, "height": 800}}),
        ("chromium", "320 px", {"viewport": {"width": 320, "height": 640}}),
        ("webkit", "320 px", {"viewport": {"width": 320, "height": 640}}),
        ("webkit", "iPhone 13", "iPhone 13"),
        ("chromium", "Pixel 5", "Pixel 5"),
    ]
    with sync_playwright() as p:
        for motor, nombre, opc in PERFILES:
            br = getattr(p, motor).launch()
            kw = p.devices[opc] if isinstance(opc, str) else opc
            ctx = br.new_context(**kw)
            pag = ctx.new_page()
            r = {"motor": motor, "perfil": nombre}
            no_get, fuera, errores_consola = [], [], []
            estado = {"modo": "normal"}
            retenidas = []

            def ruta(route, request):
                if request.method != "GET":
                    no_get.append(request.method + " " + request.url)
                    return route.abort()
                if "/v1/panel/transacciones" in request.url:
                    if estado["modo"] == "lento":
                        retenidas.append(
                            route
                        )  # se libera desde el recorrido: así la carga queda «en curso» el tiempo que haga falta
                        return
                    if estado["modo"] == "caido":
                        return route.fulfill(
                            status=500, content_type="application/json", body=json.dumps({"detail": "caída de prueba"})
                        )
                route.continue_()

            ctx.route("**/*", ruta)
            pag.on("console", lambda m: errores_consola.append(m.text) if m.type == "error" else None)
            pag.on(
                "request",
                lambda q: fuera.append(q.url) if not q.url.startswith(("http://127.0.0.1", "data:")) else None,
            )
            pag.goto(URL)
            pag.fill("#clave", k_ana)
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector("tbody tr td[data-etiqueta]")
            filas = lambda: pag.locator("tbody tr td[data-etiqueta='Seguimiento']").count()
            r["filas_pagina_1"] = filas()
            r["etiqueta_pagina"] = pag.locator("text=/Página 1 de/").inner_text()
            r["mostrando"] = pag.locator("text=/Mostrando/").inner_text()
            r["desborde_px"] = pag.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            r["controles_menores_a_44px"] = (
                pag.evaluate(MIDE_44) if nombre in ("320 px", "iPhone 13", "Pixel 5") else "n/a"
            )
            pag.screenshot(path=str(CAPTURAS / f"{motor}_{nombre.replace(' ', '_')}_p1.png"))
            # carga lenta: la tabla no se vacía y los botones se bloquean
            estado["modo"] = "lento"
            primero = pag.locator("tbody tr td[data-etiqueta='Seguimiento']").first.inner_text()
            pag.click("button:has-text('Siguiente')")
            pag.wait_for_timeout(500)
            r["durante_carga_filas_visibles"] = filas()
            r["durante_carga_boton_bloqueado"] = pag.locator("button:has-text('Siguiente')").is_disabled()
            r["durante_carga_tabla_marcada_ocupada"] = pag.locator("section[aria-busy='true']").count() == 1
            pag.screenshot(path=str(CAPTURAS / f"{motor}_{nombre.replace(' ', '_')}_cargando.png"))
            for ret in retenidas:
                ret.continue_()
            pag.wait_for_function(
                "(p) => !document.querySelector('tbody td[data-etiqueta=Seguimiento]') || document.querySelector('tbody td[data-etiqueta=Seguimiento]').innerText !== p",
                arg=primero,
                timeout=10000,
            )
            estado["modo"] = "normal"
            pag.wait_for_selector("text=/Página 2 de/")
            r["url_recuerda_pagina"] = "pagina=2" in pag.url
            # una pagina fuera de rango (enlace viejo) cae en la última, sin error
            pag.goto(URL + "?pagina=9999")
            pag.fill("#clave", k_ana)
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector("text=/Página 16 de 16/", timeout=15000)
            r["pagina_fuera_de_rango_va_a_la_ultima"] = pag.locator("[role=alert]").count() == 0 and filas() > 0
            # corte del servidor: avisa, conserva y se recupera
            estado["modo"] = "caido"
            pag.click("button:has-text('Anterior')")
            pag.wait_for_selector("[role=alert]")
            r["con_corte_avisa_y_conserva_filas"] = (
                pag.locator("[role=alert] button:has-text('Reintentar')").count() == 1 and filas() > 0
            )
            estado["modo"] = "normal"
            pag.click("button:has-text('Reintentar')")
            pag.wait_for_selector("[role=alert]", state="detached", timeout=10000)
            r["reintentar_recupera"] = True
            r["peticiones_no_get_bloqueadas"] = len(no_get)
            r["peticiones_fuera_del_servidor"] = fuera
            r["errores_de_consola_inesperados"] = [e for e in errores_consola if "500" not in e and "caída" not in e][
                :3
            ]
            resultados.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
            br.close()
    print("capturas en", CAPTURAS)
finally:
    for pr in procesos:
        pr.terminate()
