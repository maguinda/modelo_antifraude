"""Recorrido de la pantalla Resumen de la app nueva (React) en Chromium y WebKit, comparada con la pantalla vieja (/dashboard).

Levanta un servidor de PRUEBA (base desechable, ~400 pagos) y sirve la app compilada con la PROPIA API en /app, con la CSP en BLOQUEO
(cualquier script o estilo que la política no permita aparece como error de consola). Esta pantalla solo lee: se bloquea y cuenta
cualquier petición que no sea GET, y al final se comprueba que ninguna llegó a la base.
Qué mide en cada perfil: desborde horizontal, controles táctiles menores de 44 px (celular), gráficas que se salen de la pantalla,
errores de consola, peticiones fuera del servidor, que los indicadores coincidan con los de la API y con los de la pantalla vieja,
que un filtro de fechas cambie los números como la API y que un rango al revés se avise sin pedir nada.
Uso: python modelo_ia/pruebas_navegador/app_resumen.py        (sale con código 1 si algo falla)
"""

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "modelo_ia" / "frontend" / "app"
S = Path(tempfile.mkdtemp(prefix="pruebas_app_resumen_"))
CAPTURAS = S / "capturas"
CAPTURAS.mkdir()
carpeta = S / "demo"
PUERTO_API = 8775
API = f"http://127.0.0.1:{PUERTO_API}"
URL = f"{API}/app?vista=resumen"
procesos = []
navegadores_abiertos = []
fallos = []


def lanzar(cmd, **kw):
    # Sesión propia: al terminar se mata el GRUPO entero
    p = subprocess.Popen(
        cmd, stdout=open(S / f"{len(procesos)}.log", "w"), stderr=subprocess.STDOUT, start_new_session=True, **kw
    )
    procesos.append(p)
    return p


def esperar(url, veces=120):
    for _ in range(veces):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def comprobar(r, nombre, ok, detalle=None):
    r.setdefault("comprobaciones", {})[nombre] = bool(ok)
    if not ok:
        r.setdefault("fallos", []).append(nombre + (f": {detalle}" if detalle is not None else ""))


def capturar(pag, errores_consola, ruta):
    """Captura del viewport SIN contar el aviso que ella misma provoca en WebKit con la CSP en bloqueo (Playwright inyecta un estilo)."""
    antes = len(errores_consola)
    pag.screenshot(path=str(ruta))
    errores_consola[antes:] = [e for e in errores_consola[antes:] if "Refused to apply a stylesheet" not in e]


MIDE_44 = (
    "Array.from(document.querySelectorAll('a, button, select, input')).filter(e => e.offsetParent !== null && "
    "e.getBoundingClientRect().height < 43.5 && !e.closest('[hidden]')).map(e => (e.className || e.tagName) + '[' + (e.textContent || '').trim().slice(0, 20) + ']:' + Math.round(e.getBoundingClientRect().height))"
)
# los indicadores tal como se ven: «etiqueta | valor | nota»
LEE_KPIS = "Array.from(document.querySelectorAll('.kpi')).map(k => ['.etiqueta', '.valor', '.nota'].map(s => (k.querySelector(s) || {}).innerText || '').join(' | '))"
# gráficas o tablas que se salen del ancho de la pantalla
MIDE_SALIDAS = (
    "Array.from(document.querySelectorAll('svg[role=img]')).filter(s => s.getBoundingClientRect().right > document.documentElement.clientWidth + 1)"
    ".map(s => s.getAttribute('aria-label'))"
)


def sin_miles(texto):
    return re.sub(r"\s+", " ", texto.replace(" ", " ")).strip()


try:
    compilado = subprocess.run(["npm", "run", "build"], cwd=str(APP), capture_output=True, text=True, timeout=300)
    assert compilado.returncode == 0, "npm run build falló:\n" + compilado.stdout[-1500:] + compilado.stderr[-1500:]
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
        env={**os.environ, "MODELO_IA_CSP": "enforce"},
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
    assert esperar(API + "/app"), "la app no la sirve la API (¿falta compilar?)"

    def api(ruta, **params):
        r = httpx.get(API + ruta, params=params, headers={"X-API-Key": k_ana}, timeout=30)
        r.raise_for_status()
        return r.json()

    base = api("/v1/panel/resumen")
    fechas = [d["dia"] for d in base["por_dia"]]
    assert len(fechas) >= 3, "la base de prueba debería traer varios días"
    desde, hasta = fechas[1], fechas[-2]  # un rango interior: cambia los números
    filtrado = api("/v1/panel/resumen", desde=desde, hasta=hasta)
    assert filtrado["totales"]["pagos"] != base["totales"]["pagos"], "el rango de prueba no cambia nada"

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
            navegadores_abiertos.append(br)
            ctx = br.new_context(**(p.devices[opc] if isinstance(opc, str) else opc))
            pag = ctx.new_page()
            r = {"motor": motor, "perfil": nombre}
            no_get, fuera, errores_consola = [], [], []

            def ruta(route, request):
                if request.method != "GET":
                    no_get.append(request.method + " " + request.url)
                    return route.abort()
                route.continue_()

            ctx.route("**/*", ruta)
            pag.on("console", lambda m: errores_consola.append(m.text) if m.type == "error" else None)
            pag.on(
                "request",
                lambda q: fuera.append(q.url) if not q.url.startswith(("http://127.0.0.1", "data:")) else None,
            )
            etiqueta = f"{motor}_{nombre.replace(' ', '_')}"

            # ---- la pantalla vieja, para comparar los indicadores
            pag.goto(API + "/dashboard#/resumen")
            pag.wait_for_selector(
                "#clave:visible, .kpi", timeout=15000
            )  # la demo puede entrar sola; si pide clave, se escribe
            if pag.locator("#clave").is_visible():
                pag.fill("#clave", k_ana)
                pag.click("button:has-text('Entrar')")
            pag.wait_for_selector(".kpi")
            viejos = [sin_miles(x) for x in pag.evaluate(LEE_KPIS)]
            capturar(pag, errores_consola, CAPTURAS / f"{etiqueta}_vieja.png")

            # ---- la pantalla nueva
            pag.goto(URL)
            pag.fill("#clave", k_ana)
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector(".kpi")
            pag.wait_for_selector("svg[role=img]")
            nuevos = [sin_miles(x) for x in pag.evaluate(LEE_KPIS)]
            r["indicadores"] = nuevos
            comprobar(
                r,
                "indicadores iguales a los de la pantalla vieja",
                nuevos == viejos,
                {"viejos": viejos, "nuevos": nuevos},
            )
            t = base["totales"]
            comprobar(
                r,
                "el total de pagos coincide con la API",
                f"{t['pagos']:,}".replace(",", ".") in nuevos[0] or str(t["pagos"]) in nuevos[0],
                nuevos[0],
            )
            r["desborde_px"] = pag.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            comprobar(r, "sin desborde horizontal", r["desborde_px"] == 0, r["desborde_px"])
            salidas = pag.evaluate(MIDE_SALIDAS)
            comprobar(r, "ninguna gráfica se sale de la pantalla", not salidas, salidas)
            if nombre in ("320 px", "iPhone 13", "Pixel 5"):
                chicos = pag.evaluate(MIDE_44)
                r["controles_menores_a_44px"] = chicos
                comprobar(r, "controles táctiles de 44 px o más", not chicos, chicos[:4])
            comprobar(
                r,
                "las dos gráficas tienen su tabla de datos",
                pag.locator("details summary:has-text('Ver los datos en tabla')").count() == 2,
            )
            capturar(pag, errores_consola, CAPTURAS / f"{etiqueta}_nueva.png")  # vista del viewport

            # ---- filtro de fechas: la pantalla debe mostrar lo mismo que la API para ese rango
            if pag.locator("button:has-text('Filtros')").is_visible():
                pag.click("button:has-text('Filtros')")
            pag.fill("#r-desde", desde)
            pag.fill("#r-hasta", hasta)
            pag.click("button:has-text('Aplicar')")
            pag.wait_for_function(
                "(n) => document.querySelector('.kpi .valor') && document.querySelector('.kpi .valor').innerText.replace(/\\D/g, '') === n",
                arg=str(filtrado["totales"]["pagos"]),
                timeout=15000,
            )
            comprobar(r, "con el filtro, el total coincide con la API", True)
            comprobar(
                r,
                "la dirección recuerda el filtro",
                f"desde={desde}" in pag.url and "vista=resumen" in pag.url,
                pag.url,
            )

            # ---- rango al revés: se avisa y no se pide nada
            pedidos = []
            pag.on("request", lambda q: pedidos.append(q.url) if "/v1/panel/resumen" in q.url else None)
            pag.fill("#r-desde", hasta)
            pag.fill("#r-hasta", desde)
            pag.click("button:has-text('Aplicar')")
            pag.wait_for_selector("#error-fechas-resumen")
            comprobar(r, "rango al revés: aviso a la vista y nada pedido", len(pedidos) == 0, pedidos)
            comprobar(
                r,
                "el foco pasa al campo con el error",
                pag.evaluate("document.activeElement && document.activeElement.id") == "r-hasta",
            )

            # ---- limpiar vuelve a lo de antes
            pag.click("button:has-text('Limpiar')")
            pag.wait_for_function(
                "(n) => document.querySelector('.kpi .valor').innerText.replace(/\\D/g, '') === n",
                arg=str(t["pagos"]),
                timeout=15000,
            )
            comprobar(r, "«Limpiar» vuelve al resumen completo", True)

            comprobar(r, "ninguna petición que no sea GET (se bloquearon y contaron)", len(no_get) == 0, no_get[:2])
            comprobar(r, "sin peticiones fuera del servidor", not fuera, fuera[:2])
            r["errores_de_consola"] = errores_consola[:3]
            comprobar(r, "sin avisos de CSP ni errores de consola", not errores_consola, errores_consola[:2])
            fallos += [f"{motor} {nombre}: {f}" for f in r.get("fallos", [])]
            print(json.dumps(r, ensure_ascii=False), flush=True)
            br.close()
            navegadores_abiertos.remove(br)

    # nada se escribió en la base
    base_datos = next(carpeta.glob("*.sqlite3"), None)
    if base_datos:
        con = sqlite3.connect(base_datos)
        resueltas = con.execute("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'").fetchone()[0]
        con.close()
        print("revisiones resueltas en la base (debe ser 0, nadie escribió):", resueltas, flush=True)
        if resueltas:
            fallos.append("hay revisiones resueltas: algo escribió")
    print("RESULTADO:", "TODO BIEN" if not fallos else "FALLOS: " + " | ".join(fallos), flush=True)
    print("capturas en", CAPTURAS, flush=True)
finally:
    for abierto in navegadores_abiertos:
        try:
            abierto.close()
        except Exception:
            pass
    for pr in procesos:
        try:
            os.killpg(pr.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

# que un script o la integración continua vean el fallo, no solo quien lea la salida
sys.exit(1 if fallos else 0)
