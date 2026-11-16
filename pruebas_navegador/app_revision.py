"""Prueba EN VIVO de la cola de revisión del panel React, la única pantalla que ESCRIBE (aprobar o rechazar un pago).

Levanta un servidor de PRUEBA con una base desechable (~400 pagos), sirve la app desde la propia API en /app con la política de contenido (CSP)
en modo BLOQUEO, y recorre en Chromium y WebKit (1280, 320, iPhone 13 y Pixel 5): clave mala y buena, resolver un pago aprobándolo y otro
rechazándolo (con teclado), una carrera con otra persona (409), un corte antes de llegar al servidor y una RESPUESTA PERDIDA (el servidor sí guardó,
el navegador no lo supo). Después comprueba EN LA BASE que cada pago quedó resuelto exactamente una vez y con la decisión y la nota esperadas.
Solo se permite escribir en `/v1/revisiones/<id>/resolver`; cualquier otra petición que no sea GET se bloquea y se cuenta.
Requiere Node y `npm ci` en frontend/app. Uso: python modelo_ia/pruebas_navegador/app_revision.py
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
S = Path(tempfile.mkdtemp(prefix="pruebas_app_revision_"))
carpeta = S / "demo"
PUERTO = 8778
API = f"http://127.0.0.1:{PUERTO}"
URL = f"{API}/app"
procesos = []
RESOLVER = re.compile(r"/v1/revisiones/[^/]+/resolver$")
MIDE_44 = (
    "Array.from(document.querySelectorAll('main a, main button, main select, main input, main textarea, summary')).filter(e => e.offsetParent !== null "
    "&& e.getBoundingClientRect().height < 43.5 && !e.closest('[hidden]')).map(e => (e.className || e.tagName) + ':' + Math.round(e.getBoundingClientRect().height))"
)


def esperar(url, veces=120):
    for _ in range(veces):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def bd(sql, *par):
    con = sqlite3.connect(f"{(carpeta / 'demo.sqlite3').as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, par).fetchall()
    finally:
        con.close()


def numero_de(texto):
    m = re.search(r"([\d.]+)\s+(?:pendiente|resuelta)", texto)
    return int(m.group(1).replace(".", "")) if m else None


def comprobar(r, nombre, ok, extra=""):
    r.setdefault("fallos", [])
    if not ok:
        r["fallos"].append(nombre + (f" [{extra}]" if extra else ""))


todos_los_fallos = []
try:
    compilado = subprocess.run(["npm", "run", "build"], cwd=str(APP), capture_output=True, text=True, timeout=300)
    assert compilado.returncode == 0, "npm run build falló:\n" + compilado.stdout[-1500:] + compilado.stderr[-1500:]
    p = subprocess.Popen(
        [sys.executable, "-m", "modelo_ia.api.demo", "--carpeta", str(carpeta), "--port", str(PUERTO), "--reiniciar"],
        cwd=str(RAIZ),
        env={**os.environ, "MODELO_IA_CSP": "enforce"},
        stdout=open(S / "servidor.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    procesos.append(p)
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
    k_com = next(l.split("=", 1)[1].strip() for l in claves.splitlines() if l.startswith("COMERCIO="))
    pendientes_al_inicio = bd("SELECT COUNT(*) FROM revisiones WHERE estado='pendiente'")[0][0]
    resueltas_esperadas = 0
    print(f"base de prueba lista: {pendientes_al_inicio} revisiones pendientes", flush=True)

    PERFILES = [
        ("chromium", "escritorio 1280", {"viewport": {"width": 1280, "height": 800}}),
        ("webkit", "escritorio 1280", {"viewport": {"width": 1280, "height": 800}}),
        ("chromium", "320 px", {"viewport": {"width": 320, "height": 640}}),
        ("webkit", "320 px", {"viewport": {"width": 320, "height": 640}}),
        ("webkit", "iPhone 13", "iPhone 13"),
        ("chromium", "Pixel 5", "Pixel 5"),
    ]
    PERFILES = [x for x in PERFILES if not os.environ.get("SOLO") or os.environ["SOLO"] in f"{x[0]} {x[1]}"]
    with sync_playwright() as pw:
        for motor, nombre, opc in PERFILES:
            br = getattr(pw, motor).launch()
            ctx = br.new_context(**(pw.devices[opc] if isinstance(opc, str) else opc))
            pag = ctx.new_page()
            r = {"motor": motor, "perfil": nombre}
            bloqueadas, fuera, consola = [], [], []
            modo = {
                "post": "normal"
            }  # normal | cortar (no llega) | perder_respuesta (llega, pero el navegador no lo sabe)
            envios = {"llegaron": 0, "cortados": 0}

            def ruta(route, request):
                if request.method != "GET":
                    if not RESOLVER.search(request.url):
                        bloqueadas.append(request.method + " " + request.url)
                        return route.abort()
                    if modo["post"] == "cortar":
                        envios["cortados"] += 1
                        return route.abort()
                    if modo["post"] == "perder_respuesta":
                        route.fetch()  # el servidor SÍ lo guarda...
                        envios["llegaron"] += 1
                        return route.abort()  # ...pero el navegador no recibe la respuesta
                    envios["llegaron"] += 1
                if "/v1/revisiones?" in request.url and "estado=resuelta" in request.url:
                    resp = route.fetch()  # se mira lo que el servidor contestó AL NAVEGADOR en ese instante
                    try:
                        recibidas.append(
                            (resp.status, resp.json().get("total"), len(resp.json().get("revisiones", [])))
                        )
                    except Exception as e:
                        recibidas.append((resp.status, type(e).__name__))
                    return route.fulfill(response=resp)
                route.continue_()

            ctx.route("**/*", ruta)
            recibidas = []
            vistas = []  # las peticiones de la lista «resueltas» tal como las hizo el navegador

            def al_pedir(q):
                if "/v1/revisiones?" in q.url and "estado=resuelta" in q.url:
                    vistas.append(q.url.split("/v1/revisiones?")[-1])

            pag.on("request", al_pedir)
            pag.on("console", lambda m: consola.append(m.text[:140]) if m.type == "error" else None)
            pag.on(
                "request",
                lambda q: fuera.append(q.url) if not q.url.startswith(("http://127.0.0.1", "data:")) else None,
            )

            # --- acceso
            pag.goto(URL)
            pag.fill("#clave", "clave-que-no-existe")
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector("text=Esa clave no es válida.")
            pag.fill("#clave", k_com)
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector("text=Esa clave no es de analista.")
            pag.fill("#clave", k_ana)
            pag.click("button:has-text('Entrar')")
            pag.wait_for_selector("text=Sesión de")
            pag.click("a:has-text('Revisión manual')")
            pag.wait_for_selector("ul li details")
            contador = lambda: numero_de(pag.locator(".chip[role=status]").inner_text())
            r["pendientes_antes"] = contador()
            comprobar(
                r,
                "el contador coincide con la base",
                r["pendientes_antes"] == bd("SELECT COUNT(*) FROM revisiones WHERE estado='pendiente'")[0][0],
                r["pendientes_antes"],
            )
            r["tarjetas_en_pagina"] = pag.locator("ul li details").count()
            r["desborde_px"] = pag.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            primera = pag.locator("ul li details").first
            primera.locator("summary").click()
            r["desborde_con_tarjeta_abierta_px"] = pag.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            if nombre in ("320 px", "iPhone 13", "Pixel 5"):
                r["controles_menores_a_44px"] = pag.evaluate(MIDE_44)
            antes_cap = len(
                consola
            )  # en WebKit Playwright inyecta un estilo al capturar y la CSP en bloqueo lo rechaza: aviso falso, se descarta SOLO ese
            pag.screenshot(path=str(S / f"{motor}_{nombre.replace(' ', '_')}_abierta.png"))
            consola[antes_cap:] = [c for c in consola[antes_cap:] if "Refused to apply a stylesheet" not in c]

            def abrir_primera():
                """Abre la primera tarjeta SOLO si está cerrada (un clic sobre una abierta la cierra)."""
                d = pag.locator("ul li details").first
                if not d.evaluate("e => e.open"):
                    d.locator("summary").click()
                pag.wait_for_selector("details[open] textarea")

            def id_de_la_abierta():
                return pag.locator("details[open] textarea").first.get_attribute("id").replace("nota-", "")

            # --- 1) APROBAR con nota (clic): primer clic NO escribe; se confirma
            id1 = id_de_la_abierta()
            pag.locator("details[open] textarea").first.fill("verificado por teléfono")
            antes = bd("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'")[0][0]
            pag.locator("details[open]").get_by_role("button", name="Aprobar", exact=True).click()
            comprobar(
                r,
                "el primer clic no escribe",
                bd("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'")[0][0] == antes,
            )
            pag.locator("details[open]").get_by_role("button", name="Confirmar: aprobar").click()
            pag.wait_for_selector("text=/Pago .*: aprobado\\./")
            resueltas_esperadas += 1
            fila = bd("SELECT estado, decision_analista, nota FROM revisiones WHERE id_evaluacion=?", id1)[0]
            comprobar(
                r, "aprobada con nota en la base", fila == ("resuelta", "APROBADA", "verificado por teléfono"), fila
            )
            pag.wait_for_function(
                "(n) => Number((document.querySelector('.chip[role=status]').innerText.match(/[\\d.]+/)||[0])[0].replace('.','')) === n",
                arg=r["pendientes_antes"] - 1,
            )

            # --- 2) RECHAZAR con TECLADO (Tab/Enter), sin nota
            abrir_primera()
            id2 = id_de_la_abierta()
            pag.locator("details[open]").get_by_role("button", name="Rechazar", exact=True).focus()
            pag.keyboard.press("Enter")
            pag.locator("details[open]").get_by_role("button", name="Confirmar: rechazar").focus()
            pag.keyboard.press("Enter")
            pag.wait_for_selector("text=/Pago .*: rechazado\\./")
            resueltas_esperadas += 1
            fila = bd("SELECT estado, decision_analista, nota FROM revisiones WHERE id_evaluacion=?", id2)[0]
            comprobar(
                r,
                "rechazada con teclado y sin nota (null)",
                fila[:2] == ("resuelta", "RECHAZADA") and not fila[2],
                fila,
            )

            # --- 3) CARRERA: otra persona la resuelve mientras esta pantalla la muestra
            abrir_primera()
            id3 = id_de_la_abierta()
            otro = httpx.post(
                f"{API}/v1/revisiones/{id3}/resolver",
                json={"decision": "RECHAZADA", "nota": "otra persona"},
                headers={"X-API-Key": k_ana},
            )
            resueltas_esperadas += 1
            comprobar(r, "la otra persona sí pudo resolver", otro.status_code == 200, otro.status_code)
            pag.locator("details[open]").get_by_role("button", name="Aprobar", exact=True).click()
            pag.locator("details[open]").get_by_role("button", name="Confirmar: aprobar").click()
            pag.wait_for_selector("text=/ya la resolvió otra persona/")
            fila = bd("SELECT decision_analista, nota FROM revisiones WHERE id_evaluacion=?", id3)[0]
            comprobar(
                r,
                "en la carrera gana UNA sola decisión (la de la otra persona) y no se pisa",
                fila == ("RECHAZADA", "otra persona"),
                fila,
            )

            # --- 4) CORTE ANTES de llegar al servidor: no se guarda, se dice, se puede reintentar
            abrir_primera()
            id4 = id_de_la_abierta()
            modo["post"] = "cortar"
            pag.locator("details[open]").get_by_role("button", name="Aprobar", exact=True).click()
            pag.locator("details[open]").get_by_role("button", name="Confirmar: aprobar").click()
            pag.wait_for_selector("text=/No se pudo confirmar si se guardó/")
            comprobar(r, "con el corte NO dice «aprobado»", pag.locator("text=/aprobado\\./").count() == 0)
            comprobar(
                r,
                "con el corte la base no cambió",
                bd("SELECT estado FROM revisiones WHERE id_evaluacion=?", id4)[0][0] == "pendiente",
            )
            modo["post"] = "normal"

            # --- 5) RESPUESTA PERDIDA: el servidor SÍ guardó, el navegador no lo supo; no se duplica ni se reintenta solo
            abrir_primera()
            id5 = id_de_la_abierta()
            llegaron_antes = envios["llegaron"]
            modo["post"] = "perder_respuesta"
            pag.locator("details[open]").get_by_role("button", name="Rechazar", exact=True).click()
            pag.locator("details[open]").get_by_role("button", name="Confirmar: rechazar").click()
            pag.wait_for_selector("text=/No se pudo confirmar si se guardó/")
            modo["post"] = "normal"
            pag.wait_for_timeout(1200)
            resueltas_esperadas += 1
            comprobar(
                r,
                "respuesta perdida: llegó UNA sola escritura (no se reintentó)",
                envios["llegaron"] - llegaron_antes == 1,
                envios["llegaron"] - llegaron_antes,
            )
            comprobar(
                r,
                "respuesta perdida: quedó resuelta una vez en la base",
                bd("SELECT estado, decision_analista FROM revisiones WHERE id_evaluacion=?", id5)[0]
                == ("resuelta", "RECHAZADA"),
            )
            comprobar(
                r,
                "respuesta perdida: tras recargar ya no está entre las pendientes",
                pag.locator(f"#nota-{id5}").count() == 0,
            )

            # --- 6) filtros y paginación
            pag.click("button:has-text('Filtros')") if pag.locator("button.alternar-filtros").is_visible() else None
            pag.select_option("#f-riesgo", "alto")
            pag.wait_for_timeout(600)
            r["riesgo_alto_visibles"] = pag.locator("ul li details").count()
            pag.fill("#f-monto", "-5")
            pag.click("button:has-text('Aplicar')")
            comprobar(
                r, "monto negativo avisa", pag.locator("text=Escribe un número igual o mayor que 0.").count() == 1
            )
            pag.select_option("#f-riesgo", "")
            pag.fill("#f-monto", "")
            pag.select_option("#f-estado", "resuelta")
            pag.wait_for_selector("text=/\\d+ resueltas?/")
            texto_chip = pag.locator(".chip[role=status]").inner_text()
            lis_antes = pag.locator("ul li details").count()
            pag.wait_for_timeout(2500)
            chip_despues = pag.locator(".chip[role=status]").inner_text()
            api_ahora = httpx.get(API + "/v1/revisiones?estado=resuelta&limite=1", headers={"X-API-Key": k_ana}).json()[
                "total"
            ]
            base_ahora = bd("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'")[0][0]
            comprobar(
                r,
                "las resueltas de esta sesión aparecen en «Resueltas»",
                (numero_de(texto_chip) or 0) >= resueltas_esperadas,
                f"chip={texto_chip!r} esperadas>={resueltas_esperadas} url={pag.url.split(chr(63))[-1]} api_ahora={api_ahora} base_ahora={base_ahora} lis={lis_antes} chip_2s_despues={chip_despues!r} recibio={recibidas} pidio={vistas} mismas_con_httpx={[httpx.get(API + '/v1/revisiones?' + v, headers={'X-API-Key': k_ana}).json()['total'] for v in vistas]}",
            )
            r["peticiones_no_get_bloqueadas"] = len(bloqueadas)
            r["peticiones_fuera_del_servidor"] = fuera
            r["errores_de_consola"] = [
                c
                for c in consola
                if "Failed to load resource" not in c and "net::ERR" not in c and "load failed" not in c.lower()
            ][:3]
            comprobar(r, "sin escrituras no permitidas", not bloqueadas, bloqueadas[:2])
            comprobar(r, "sin peticiones externas", not fuera)
            comprobar(
                r, "sin avisos de CSP ni errores de consola", not r["errores_de_consola"], r["errores_de_consola"]
            )
            comprobar(r, "sin desborde", r["desborde_px"] == 0 and r["desborde_con_tarjeta_abierta_px"] == 0)
            todos_los_fallos += [f"{motor} {nombre}: {f}" for f in r.get("fallos", [])]
            print(json.dumps(r, ensure_ascii=False), flush=True)
            br.close()

    # --- cuentas finales EN LA BASE: nada se perdió, nada se duplicó, nada más cambió
    resueltas = bd("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'")[0][0]
    pendientes = bd("SELECT COUNT(*) FROM revisiones WHERE estado='pendiente'")[0][0]
    dobles = bd(
        "SELECT COUNT(*) FROM (SELECT id_evaluacion FROM revisiones GROUP BY id_evaluacion HAVING COUNT(*) > 1)"
    )[0][0]
    ok_final = (
        resueltas == resueltas_esperadas and pendientes == pendientes_al_inicio - resueltas_esperadas and dobles == 0
    )
    print(
        f"BASE: {resueltas} resueltas (esperadas {resueltas_esperadas}), {pendientes} pendientes (esperadas {pendientes_al_inicio - resueltas_esperadas}), repetidas {dobles}",
        flush=True,
    )
    if not ok_final:
        todos_los_fallos.append("las cuentas finales de la base no cuadran")
    print("RESULTADO:", "TODO BIEN" if not todos_los_fallos else "FALLOS: " + "; ".join(todos_los_fallos), flush=True)
    print("capturas en", S, flush=True)
finally:
    for pr in procesos:
        try:
            os.killpg(pr.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
