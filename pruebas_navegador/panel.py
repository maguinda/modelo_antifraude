"""Pantalla de analistas: acceso, cola, aprobar/rechazar con confirmación, conflicto 409, ataque XSS, teclado, 44 px y desborde.

Recorrido automático en Chromium y WebKit (escritorio, 320 px, iPhone 13 y Pixel 5) sobre un servidor de PRUEBA con una base desechable.
Necesita Playwright:   pip install playwright && python -m playwright install chromium webkit
Uso:                   python modelo_ia/pruebas_navegador/panel.py
Las capturas quedan en la carpeta temporal que se imprime al final. No toca tu demo ni tus datos.
"""
import os, sys, tempfile, time, subprocess, secrets, json
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
RAIZ = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(RAIZ))
from modelo_ia.api.almacen import Almacen
S = Path(tempfile.mkdtemp(prefix="pruebas_navegador_")); CAPTURAS = S / "capturas"; CAPTURAS.mkdir(); bd = S / "nav5.sqlite3"
for f in S.glob("nav5.sqlite3*"): f.unlink()
pepper = secrets.token_hex(8); alm = Almacen(bd, pepper); alm.inicializar()
cid = alm.crear_comercio("Tienda"); k_com = alm.crear_clave(cid, "comercio"); k_ana = alm.crear_clave(1, "analista")
srv = subprocess.Popen([sys.executable, "-m", "modelo_ia.api.servidor", "--port", "8770"], env=dict(os.environ, MODELO_IA_BD=str(bd), MODELO_IA_PEPPER=pepper, MODELO_IA_LIMITE="100000"),
                       cwd=str(RAIZ), stdout=open(S / "servidor_nav5.log", "w"), stderr=subprocess.STDOUT)
BASE = "http://127.0.0.1:8770"; H = {"X-API-Key": k_com}; HA = {"X-API-Key": k_ana}
XSS = '<img src=x onerror="window.__xss=1">'
def pago(tx, ciudad="Cali", cli=None):
    return {"id_transaccion": tx, "id_cliente": cli or "C-" + tx, "monto_cop": 90000, "metodo_pago": "PSE", "canal_pago": "WEB", "categoria": "FARMACIA", "ciudad": ciudad,
            "dispositivo": {"tipo": "ANDROID", "estado": "CONOCIDO"}, "red": {"tipo_ip": "RESIDENCIAL"}}
def sembrar(prefijo, n=4):
    ids = []
    for i in range(n):
        r = httpx.post(BASE + "/v1/evaluaciones", json=pago(f"{prefijo}-{i}"), headers=H); assert r.status_code == 201 and r.json()["decision"] == "EN_REVISION", r.text
        ids.append(r.json()["id_evaluacion"])
    r = httpx.post(BASE + "/v1/evaluaciones", json=pago(f"{prefijo}-xss", ciudad=XSS), headers=H); assert r.status_code == 201, r.text
    return ids, r.json()["id_evaluacion"]
resultados = []
try:
    for _ in range(80):
        try:
            if httpx.get(BASE + "/v1/salud").status_code == 200: break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        perfiles = [("escritorio 1280", "chromium", {"viewport": {"width": 1280, "height": 800}}), ("escritorio 1280", "webkit", {"viewport": {"width": 1280, "height": 800}}),
                    ("320 px", "chromium", {"viewport": {"width": 320, "height": 568}}), ("320 px", "webkit", {"viewport": {"width": 320, "height": 568}}),
                    ("iPhone 13", "webkit", p.devices["iPhone 13"]), ("Pixel 5", "chromium", p.devices["Pixel 5"])]
        for n, (nombre, motor, opts) in enumerate(perfiles):
            ids, id_xss = sembrar(f"P{n}")
            nav = getattr(p, motor).launch(); ctx = nav.new_context(**opts); page = ctx.new_page(); consola = []
            page.on("console", lambda m, c=consola: c.append(f"{m.type}: {m.text[:150]}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda x, c=consola: c.append("pageerror: " + str(x)[:150]))
            f = {"motor": motor, "perfil": nombre}
            page.goto(BASE + "/panel", wait_until="networkidle")
            f["acceso_visible"] = page.locator("#acceso").is_visible() and page.locator("#panel").is_hidden()
            for etiqueta, clave, esperado in (("clave_falsa", "mia_analista_inventada", "no es válida"), ("clave_de_comercio", k_com, "no es de analista")):
                page.fill("#clave", clave); page.click("#formAcceso button[type=submit]")
                try: page.wait_for_function("(t) => !document.getElementById('errorAcceso').hidden && document.getElementById('errorAcceso').textContent.indexOf(t) >= 0", arg=esperado, timeout=10000); f[etiqueta] = True
                except Exception: f[etiqueta] = "mensaje: " + page.inner_text("#errorAcceso")
            page.fill("#clave", k_ana); page.click("#formAcceso button[type=submit]"); page.wait_for_selector("#panel:not([hidden])", timeout=10000); page.wait_for_selector("#lista > li", timeout=10000)
            f["tarjetas"] = page.locator("#lista > li").count(); f["contador"] = page.inner_text("#contador")
            # --- ataque XSS: el texto malicioso debe verse tal cual y no ejecutarse
            page.locator("#lista > li summary").nth(f["tarjetas"] - 1).click(); time.sleep(0.4)
            f["xss_texto_literal"] = "<img src=x" in page.inner_text("#lista")
            f["xss_ejecutado"] = page.evaluate("window.__xss === 1"); f["xss_elemento_img"] = page.locator("#lista img").count()
            # --- clave solo en sessionStorage
            f["clave_en_sessionStorage"] = page.evaluate("!!sessionStorage.getItem('clave_analista')"); f["clave_en_localStorage"] = page.evaluate("localStorage.length > 0")
            # --- aprobar con confirmación en dos pasos
            primero = page.locator("#lista > li").first
            id_primero = httpx.get(BASE + "/v1/revisiones?limite=1", headers=HA).json()["revisiones"][0]["id_evaluacion"]
            primero.locator("summary").click(); primero.locator("textarea").fill("Llamé al cliente y confirmó la compra.")
            b = primero.locator("button.ok"); b.click(); f["pide_confirmacion"] = "Confirmar: aprobar" in b.inner_text()
            f["aun_no_resuelto"] = httpx.get(f"{BASE}/v1/evaluaciones/{id_primero}", headers=HA).json()["decision_final"] is None
            primero.locator("button.ok").click(); page.wait_for_function("document.getElementById('mensaje').textContent.indexOf('aprobado') >= 0", timeout=10000)
            ev = httpx.get(f"{BASE}/v1/evaluaciones/{id_primero}", headers=HA).json()
            f["aprobada_en_servidor"] = ev["decision_final"] == "APROBADA" and ev["revision"]["nota"] == "Llamé al cliente y confirmó la compra."
            f["tarjetas_despues"] = page.locator("#lista > li").count()
            # --- cancelar: Rechazar y luego el otro botón vuelve atrás
            seg = page.locator("#lista > li").first; seg.locator("summary").click(); bots = seg.locator(".botones button")
            bots.nth(1).click(); f["rechazar_pide_confirmacion"] = "Confirmar: rechazar" in bots.nth(1).inner_text() and bots.nth(0).inner_text() == "Cancelar"
            bots.nth(0).click(); f["cancelar_vuelve"] = bots.nth(1).inner_text() == "Rechazar" and bots.nth(0).inner_text() == "Aprobar"
            # --- conflicto: otro analista resuelve primero
            id_conf = httpx.get(BASE + "/v1/revisiones?limite=1", headers=HA).json()["revisiones"][0]["id_evaluacion"]
            httpx.post(f"{BASE}/v1/revisiones/{id_conf}/resolver", json={"decision": "RECHAZADA"}, headers=HA)
            bots.nth(0).click(); bots.nth(0).click()
            page.wait_for_function("document.getElementById('mensaje').textContent.indexOf('otra persona') >= 0", timeout=10000)
            f["conflicto_409_avisado"] = True
            # --- teclado: Tab llega al resumen y Enter lo abre
            page.reload(wait_until="networkidle"); page.wait_for_selector("#lista > li summary", timeout=10000)
            f["entra_solo_con_la_sesion"] = page.locator("#panel").is_visible()
            page.locator("#lista > li summary").first.focus(); page.keyboard.press("Enter"); f["teclado_abre_detalle"] = page.locator("#lista > li details[open]").count() == 1
            w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]"); f["desborde_px"] = max(0, w[0] - w[1])
            m44 = page.evaluate("Array.from(document.querySelectorAll('button, summary, input, select')).filter(e => e.offsetParent !== null).map(e => e.getBoundingClientRect().height).filter(h => h < 43).length")
            f["controles_menores_a_44px"] = m44
            if m44: f["cuales_menores"] = page.evaluate("Array.from(document.querySelectorAll('button, summary, input, select')).filter(e => e.offsetParent !== null && e.getBoundingClientRect().height < 43).map(e => e.tagName + '#' + e.id + '.' + e.className + ' h=' + e.getBoundingClientRect().height)")
            page.screenshot(path=str(CAPTURAS / f"{motor}_{nombre.replace(' ', '_')}.png"))
            page.click("#salir"); f["salir_limpia"] = page.locator("#acceso").is_visible() and not page.evaluate("!!sessionStorage.getItem('clave_analista')")
            f["consola"] = consola[:3]; resultados.append(f); nav.close()
finally:
    srv.terminate(); srv.wait(10)
for r in resultados: print(json.dumps(r, ensure_ascii=False))

print("capturas en", CAPTURAS)
