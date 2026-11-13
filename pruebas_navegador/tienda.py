"""Tienda de prueba con el SDK: señales del navegador, la tarjeta escrita nunca sale, sin clave en la página y decisión de la API.

Recorrido automático en Chromium y WebKit (escritorio, 320 px, iPhone 13 y Pixel 5) sobre un servidor de PRUEBA con una base desechable.
Necesita Playwright:   pip install playwright && python -m playwright install chromium webkit
Uso:                   python modelo_ia/pruebas_navegador/tienda.py
Las capturas quedan en la carpeta temporal que se imprime al final. No toca tu demo ni tus datos.
"""
import os, sys, tempfile, time, subprocess, json, shutil
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
RAIZ = Path(__file__).resolve().parents[2]
S = Path(tempfile.mkdtemp(prefix="pruebas_navegador_")); CAPTURAS = S / "capturas"; CAPTURAS.mkdir(); carpeta = S / "demo"
srv = subprocess.Popen([sys.executable, "-m", "modelo_ia.api.demo", "--carpeta", str(carpeta), "--port", "8771", "--reiniciar"], cwd=str(RAIZ),
                       stdout=open(S / "servidor.log", "w"), stderr=subprocess.STDOUT)
BASE = "http://127.0.0.1:8771"; resultados = []
try:
    for _ in range(80):
        try:
            if httpx.get(BASE + "/v1/salud").status_code == 200: break
        except Exception: time.sleep(0.5)
    claves = [l.split("=", 1)[1].strip() for l in (carpeta / "claves_demo.txt").read_text().splitlines() if "=" in l]
    with sync_playwright() as p:
        perfiles = [("escritorio 1280", "chromium", {"viewport": {"width": 1280, "height": 900}}), ("escritorio 1280", "webkit", {"viewport": {"width": 1280, "height": 900}}),
                    ("320 px", "chromium", {"viewport": {"width": 320, "height": 568}}), ("320 px", "webkit", {"viewport": {"width": 320, "height": 568}}),
                    ("iPhone 13", "webkit", p.devices["iPhone 13"]), ("Pixel 5", "chromium", p.devices["Pixel 5"])]
        for nombre, motor, opts in perfiles:
            nav = getattr(p, motor).launch(); ctx = nav.new_context(**opts); page = ctx.new_page(); consola = []; peticiones = []; cuerpos = []
            page.on("console", lambda m, c=consola: c.append(f"{m.type}: {m.text[:140]}") if m.type == "error" and "Content Security" not in m.text else None)
            page.on("request", lambda r: (peticiones.append(r.url), cuerpos.append(r.post_data or "")) )
            f = {"motor": motor, "perfil": nombre}
            page.goto(BASE + "/tienda", wait_until="networkidle")
            html = page.content(); f["clave_en_la_pagina"] = any(k in html for k in claves)
            # --- compra normal, escribiendo una tarjeta de prueba y pegándola
            page.select_option("#producto", "0"); page.locator("#tarjeta").click(); page.keyboard.type("4111 1111 1111 1111", delay=40)
            page.evaluate("document.getElementById('tarjeta').dispatchEvent(new Event('paste', {bubbles: true}))")
            page.mouse.move(10, 10); [page.mouse.move(10 + i * 7, 10 + (i * 31) % 90) for i in range(1, 30)]
            page.click("#pagar"); page.wait_for_selector("#resultado:not([hidden])", timeout=20000); time.sleep(0.6)
            f["decision_normal"] = page.inner_text("#decision")
            sen = json.loads(page.inner_text("#senales"))
            d, c = sen["dispositivo"], sen["comportamiento"]
            f["huella_ok"] = d["huella"].startswith("FP-") and len(d["huella"]) == 19
            f["so"] = d["sistema_operativo"]; f["navegador"] = d["navegador"]; f["tipo"] = d["tipo"]; f["emulador_detectado"] = d["es_emulador"]
            f["pego_detectado"] = c.get("pego_numero_tarjeta") is True; f["tiempo_form"] = c.get("tiempo_formulario_seg"); f["tecleo_ms"] = c.get("velocidad_tecleo_ms"); f["mouse"] = c.get("mouse_natural")
            f["tarjeta_salio_en_alguna_peticion"] = any("4111" in b or "1111111" in b for b in cuerpos) or any("4111" in u for u in peticiones)
            f["peticiones_fuera_del_servidor"] = [u for u in peticiones if not u.startswith((BASE, "data:", "blob:"))][:2]
            # --- compra sospechosa
            page.select_option("#producto", "4"); page.select_option("#estado", "NO_RECONOCIDO"); page.select_option("#ip", "203.0.113.200")
            page.select_option("#kyc", "BASICO"); page.fill("#edad", "2"); page.select_option("#tfa", "EXIGIDA_FALLIDA"); page.fill("#cliente", "cliente-sospechoso-" + motor + nombre.split()[0])
            page.click("#pagar"); page.wait_for_function("document.getElementById('decision').textContent === 'RECHAZADA'", timeout=20000)
            f["decision_sospechosa"] = page.inner_text("#decision"); f["regla_visible"] = "Regla:" in page.inner_text("#motivos")
            w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]"); f["desborde_px"] = max(0, w[0] - w[1])
            f["controles_menores_a_44px"] = page.evaluate("Array.from(document.querySelectorAll('button, select, input[type=text], input[type=number]')).filter(e => e.offsetParent !== null && e.getBoundingClientRect().height < 43).length")
            page.screenshot(path=str(CAPTURAS / f"{motor}_{nombre.replace(' ', '_')}.png"), full_page=False)
            f["consola"] = consola[:2]; resultados.append(f); nav.close()
finally:
    srv.terminate(); srv.wait(10)
for r in resultados: print(json.dumps(r, ensure_ascii=False))

print("capturas en", CAPTURAS)
