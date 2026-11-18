"""Ataque de CONCURRENCIA a la cola de revisión del panel React: dos analistas deciden el MISMO pago a la vez.

Levanta un servidor de PRUEBA con una base desechable y abre dos navegadores (un par Chromium+Chromium y un par Chromium+WebKit). En cada ronda
los dos ven la misma tarjeta, uno la APRUEBA y el otro la RECHAZA, y las dos escrituras se RETIENEN en el navegador y se sueltan juntas.
Invariante por ronda: gana EXACTAMENTE una decisión; la base guarda la del ganador; la otra pantalla lo dice («ya estaba resuelta») y no dice «guardado».
Invariante final: cada pago quedó resuelto una sola vez (sin repetidos) y las cuentas de la base cuadran con las rondas.
Solo se permite escribir en `/v1/revisiones/<id>/resolver`. Uso: python modelo_ia/pruebas_navegador/app_revision_concurrencia.py [rondas]
"""

import os
import re
import signal
import socket
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def puerto_libre():
    """Un puerto que nadie usa ahora: así la prueba nunca escribe contra un servidor ajeno que ya esté vivo en uno fijo."""
    with socket.socket() as c:
        c.bind(("127.0.0.1", 0))
        return c.getsockname()[1]


RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "modelo_ia" / "frontend" / "app"
S = Path(tempfile.mkdtemp(prefix="pruebas_app_concurrencia_"))
carpeta = S / "demo"
PUERTO = puerto_libre()
API = f"http://127.0.0.1:{PUERTO}"
RESOLVER = re.compile(r"/v1/revisiones/[^/]+/resolver$")
RONDAS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
procesos = []


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


class Analista:
    """Un navegador con su sesión y una compuerta que retiene su escritura hasta que se la suelte."""

    def __init__(self, pw, motor, clave, nombre):
        self.nombre = nombre
        self.retenidas = []
        self.bloqueadas = []
        self.escrituras = 0
        self.br = getattr(pw, motor).launch()
        self.ctx = self.br.new_context(viewport={"width": 1280, "height": 800})
        self.pag = self.ctx.new_page()
        self.ctx.route("**/*", self._ruta)
        self.pag.goto(f"{API}/app")
        self.pag.fill("#clave", clave)
        self.pag.click("button:has-text('Entrar')")
        self.pag.wait_for_selector("text=Sesión de")
        self.pag.click("a:has-text('Revisión manual')")
        self.pag.wait_for_selector("ul li details")

    def _ruta(self, route, request):
        if request.method == "GET":
            return route.continue_()
        if not RESOLVER.search(request.url):
            self.bloqueadas.append(request.method + " " + request.url)
            return route.abort()
        self.retenidas.append(route)  # se queda esperando hasta soltarla

    def soltar(self):
        for r in self.retenidas:
            self.escrituras += 1
            r.continue_()
        self.retenidas.clear()

    def abrir_primera(self):
        d = self.pag.locator("ul li details").first
        if not d.evaluate("e => e.open"):
            d.locator("summary").click()
        self.pag.wait_for_selector("details[open] textarea")
        return self.pag.locator("details[open] textarea").first.get_attribute("id").replace("nota-", "")

    def armar(self, boton):
        self.pag.locator("details[open]").get_by_role("button", name=boton, exact=True).click()

    def confirmar(self, boton):
        self.pag.locator("details[open]").get_by_role("button", name=f"Confirmar: {boton}").click(no_wait_after=True)

    def cerrar(self):
        self.br.close()


fallos = []
try:
    assert subprocess.run(["npm", "run", "build"], cwd=str(APP), capture_output=True, timeout=300).returncode == 0
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
    assert p.poll() is None, "el servidor propio ya terminó: el puerto lo contesta OTRO proceso, no se sigue"
    subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "modelo_ia.api.cargar_ejemplos", "--url", API]
        + ["--carpeta", str(carpeta), "--n", "1000", "--dias", "10"],
        cwd=str(RAIZ),
        capture_output=True,
        timeout=300,
        check=True,
    )
    claves = (carpeta / "claves_demo.txt").read_text(encoding="utf-8")
    k_ana = next(x.split("=", 1)[1].strip() for x in claves.splitlines() if x.startswith("ANALISTA="))
    pendientes0 = bd("SELECT COUNT(*) FROM revisiones WHERE estado='pendiente'")[0][0]
    assert pendientes0 >= 2 * RONDAS, f"la base trae pocas pendientes ({pendientes0}) para {RONDAS} rondas por par"
    print(f"base de prueba lista: {pendientes0} pendientes; {RONDAS} rondas por par de navegadores", flush=True)

    hechas = 0
    with sync_playwright() as pw:
        for motor_b in ("chromium", "webkit"):
            a = Analista(pw, "chromium", k_ana, "A")
            b = Analista(pw, motor_b, k_ana, "B")
            for ronda in range(RONDAS):
                ida, idb = a.abrir_primera(), b.abrir_primera()
                if ida != idb:
                    fallos.append(f"{motor_b} ronda {ronda}: los dos no ven el mismo pago ({ida} / {idb})")
                    a.pag.click("button:has-text('Actualizar')")
                    b.pag.click("button:has-text('Actualizar')")
                    a.pag.wait_for_timeout(600)
                    continue
                a.armar("Aprobar")
                b.armar("Rechazar")
                a.pag.wait_for_timeout(800)  # la pantalla exige una pausa entre armar y confirmar
                a.confirmar("aprobar")
                b.confirmar("rechazar")
                for _ in range(100):  # las dos escrituras quedan retenidas en su navegador
                    if a.retenidas and b.retenidas:
                        break
                    a.pag.wait_for_timeout(30)
                if not (a.retenidas and b.retenidas):
                    fallos.append(f"{motor_b} ronda {ronda}: no se retuvieron las dos escrituras")
                a.soltar()
                b.soltar()  # salen casi a la vez
                # cada pantalla termina en uno de dos avisos
                for x in (a, b):
                    x.pag.wait_for_selector("text=/Pago .*: (aprobado|rechazado)\\.|ya estaba resuelta/", timeout=20000)
                ok_a = a.pag.locator("text=/Pago .*: aprobado\\./").count() == 1
                ok_b = b.pag.locator("text=/Pago .*: rechazado\\./").count() == 1
                perdio_a = a.pag.locator("text=/ya estaba resuelta/").count() == 1
                perdio_b = b.pag.locator("text=/ya estaba resuelta/").count() == 1
                fila = bd("SELECT estado, decision_analista FROM revisiones WHERE id_evaluacion=?", ida)[0]
                ganador = "A" if ok_a and perdio_b else "B" if ok_b and perdio_a else None
                if ganador is None:
                    fallos.append(
                        f"{motor_b} ronda {ronda}: no hay UN ganador (A ok={ok_a}/perdió={perdio_a}, B ok={ok_b}/perdió={perdio_b})"
                    )
                esperada = {"A": "APROBADA", "B": "RECHAZADA"}.get(ganador or "")
                if fila != ("resuelta", esperada):
                    fallos.append(f"{motor_b} ronda {ronda}: la base dice {fila} y la pantalla ganadora fue {ganador}")
                hechas += 1
                a.pag.wait_for_timeout(300)
            if a.bloqueadas or b.bloqueadas:
                fallos.append(f"{motor_b}: se bloquearon escrituras no permitidas {a.bloqueadas + b.bloqueadas}")
            a.cerrar()
            b.cerrar()

    resueltas = bd("SELECT COUNT(*) FROM revisiones WHERE estado='resuelta'")[0][0]
    pendientes = bd("SELECT COUNT(*) FROM revisiones WHERE estado='pendiente'")[0][0]
    repetidas = bd(
        "SELECT COUNT(*) FROM (SELECT id_evaluacion FROM revisiones GROUP BY id_evaluacion HAVING COUNT(*) > 1)"
    )[0][0]
    print(
        f"BASE: {resueltas} resueltas (esperadas {hechas}), {pendientes} pendientes (esperadas {pendientes0 - hechas}), repetidas {repetidas}",
        flush=True,
    )
    if resueltas != hechas or pendientes != pendientes0 - hechas or repetidas:
        fallos.append("las cuentas finales de la base no cuadran")
    print("RESULTADO:", "TODO BIEN" if not fallos else "FALLOS: " + " | ".join(fallos[:10]), flush=True)
finally:
    for pr in procesos:
        try:
            os.killpg(pr.pid, signal.SIGTERM)
            try:
                pr.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(pr.pid, signal.SIGKILL)  # no quiso terminar: se fuerza
                pr.wait(timeout=5)
        except ProcessLookupError:
            pass
if not fallos:
    shutil.rmtree(S, ignore_errors=True)  # si hubo fallos se conserva la carpeta (servidor.log) para investigar
sys.exit(1 if fallos else 0)
