"""Swagger y ReDoc locales: sin peticiones a internet, sin desborde a 320 px y sin avisos de la política de contenido.

Recorrido automático en Chromium y WebKit (escritorio, 320 px, iPhone 13 y Pixel 5) sobre un servidor de PRUEBA con una base desechable.
Necesita Playwright:   pip install playwright && python -m playwright install chromium webkit
Uso:                   python modelo_ia/pruebas_navegador/documentacion.py
Las capturas quedan en la carpeta temporal que se imprime al final. No toca tu demo ni tus datos.
"""
import os, sys, tempfile, time, subprocess, secrets, json, sqlite3
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
RAIZ = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(RAIZ))
from modelo_ia.api.almacen import Almacen
S = Path(tempfile.mkdtemp(prefix="pruebas_navegador_")); CAPTURAS = S / "capturas"; CAPTURAS.mkdir(); bd = S / "nav2.sqlite3"
for f in S.glob("nav2.sqlite3*"): f.unlink()
pepper = secrets.token_hex(8); Almacen(bd, pepper).inicializar()
env = dict(os.environ, MODELO_IA_BD=str(bd), MODELO_IA_PEPPER=pepper)
srv = subprocess.Popen([sys.executable, "-m", "modelo_ia.api.servidor", "--port", "8768"], env=env, cwd=str(RAIZ),
                       stdout=open(S / "servidor_nav2.log", "w"), stderr=subprocess.STDOUT)
BASE = "http://127.0.0.1:8768"; resultados = []; bloqueadas = []
try:
    for _ in range(80):
        try:
            if httpx.get(BASE + "/v1/salud").status_code == 200: break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        perfiles = [("escritorio 1280", "chromium", {"viewport": {"width": 1280, "height": 800}}), ("escritorio 1280", "webkit", {"viewport": {"width": 1280, "height": 800}}),
                    ("320 px", "chromium", {"viewport": {"width": 320, "height": 568}}), ("320 px", "webkit", {"viewport": {"width": 320, "height": 568}}),
                    ("iPhone 13", "webkit", p.devices["iPhone 13"]), ("Pixel 5", "chromium", p.devices["Pixel 5"])]
        for nombre, motor, opts in perfiles:
            nav = getattr(p, motor).launch(); ctx = nav.new_context(**opts)
            def guardia(route, request):
                if request.method != "GET" and request.url.startswith(BASE): bloqueadas.append(request.url); route.abort()
                else: route.continue_()
            ctx.route("**/*", guardia); page = ctx.new_page(); externas = []; consola = []
            page.on("request", lambda r, e=externas: e.append(r.url) if not r.url.startswith((BASE, "data:", "blob:")) else None)
            page.on("console", lambda m, c=consola: c.append(f"{m.type}: {m.text[:160]}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda x, c=consola: c.append("pageerror: " + str(x)[:160]))
            fila = {"motor": motor, "perfil": nombre}
            for ruta, espera in (("/docs", ".swagger-ui .opblock"), ("/redoc", "h1")):
                page.goto(BASE + ruta, wait_until="networkidle", timeout=60000); page.wait_for_selector(espera, timeout=30000)
                w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                fila[ruta] = {"desborde_px": max(0, w[0] - w[1])}
                if ruta == "/docs":
                    page.locator(".opblock-summary").first.click(); page.wait_for_selector(".opblock.is-open", timeout=10000)
                    w2 = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]"); fila["docs_expandida_desborde_px"] = max(0, w2[0] - w2[1])
                    fila["operaciones"] = page.locator(".swagger-ui .opblock").count()
                page.screenshot(path=str(CAPTURAS / f"{motor}_{nombre.replace(' ', '_')}_{ruta.strip('/')}.png"))
            fila["peticiones_externas"] = externas[:3]; fila["consola"] = consola[:4]
            resultados.append(fila); nav.close()
finally:
    srv.terminate(); srv.wait(10)
for r in resultados: print(json.dumps(r, ensure_ascii=False))
print("escrituras bloqueadas:", len(bloqueadas), "| evaluaciones en la base:", sqlite3.connect(bd).execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0])

print("capturas en", CAPTURAS)
