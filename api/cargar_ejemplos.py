"""Llena el servidor de demostración con pagos de ejemplo, ENVIADOS POR LA API REAL (pasan por el modelo y las reglas como cualquier pago).

Crea 3 comercios nuevos y manda pagos normales de todos los días (hasta `--dias`) más escenarios sospechosos para que el panel tenga qué mostrar:
  - una misma IP con muchos clientes distintos en pocos minutos          - un mismo dispositivo usado por varios clientes
  - un cliente que paga desde muchas IPs distintas                        - viaje imposible (Bogotá y luego Cali a los pocos minutos)
  - ráfaga de pagos pequeños desde una IP de VPN (prueba de tarjetas)     - IP de un proxy en otro país, y pagos sin los datos que el modelo necesita
Todo es inventado (semilla fija: `--semilla`), con IPs de documentación (192.0.2.x, 198.51.100.x, 203.0.113.x) que el proveedor simulado sí reconoce.

Uso:   python -m modelo_ia.api.cargar_ejemplos                (servidor en http://127.0.0.1:8000, clave de administrador de la demo)
       python -m modelo_ia.api.cargar_ejemplos --n 500 --dias 7
Las claves de los comercios nuevos se usan en memoria y NO se imprimen ni se guardan.
"""
import argparse
import random
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .ejemplos import _pago

CARPETA_DEMO = Path(__file__).resolve().parent / "demo"
COMERCIOS = [("Moda Bogotá (ejemplo)", "ROPA", "Bogotá"), ("Electro Medellín (ejemplo)", "ELECTRONICA", "Medellín"), ("Farmacia Cali (ejemplo)", "FARMACIA", "Cali")]
SOLO_IP = {"tipo_ip": None, "pais": None, "ciudad_estimada": None, "distancia_a_ciudad_km": None, "es_datacenter": None, "reputacion": None}


def _clave(archivo: Path, rol: str) -> str:
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if linea.upper().startswith(rol.upper() + "="):
            return linea.split("=", 1)[1].strip()
    raise SystemExit(f"No encontré la clave {rol} en {archivo}")


def _ip(rng: random.Random) -> str:
    rango = rng.choices(["192.0.2", "198.51.100", "203.0.113"], weights=[6, 3, 1])[0]
    return f"{rango}.{rng.randint(1, 60) if rango == '203.0.113' else rng.randint(1, 250)}"


def generar(n: int, dias: int, semilla: int, ahora: datetime, prefijo: str) -> List[Tuple[int, datetime, Dict]]:
    """Lista de (comercio, momento, pago) ya ordenada por momento."""
    rng = random.Random(semilla)
    pagos: List[Tuple[int, datetime, Dict]] = []
    contador = [0]

    def nuevo(k, momento, cliente, **cambios):
        contador[0] += 1
        _, categoria, ciudad = COMERCIOS[k]
        base = dict(categoria=categoria, ciudad=ciudad, canal_pago="WEB", metodo_pago=rng.choice(["NEQUI", "VISA", "PSE", "MASTERCARD"]))
        base.update(cambios)
        pagos.append((k, momento, _pago(f"{prefijo}-{contador[0]:05d}", cliente, **base)))

    def momento_al_azar() -> datetime:
        dia = ahora - timedelta(days=rng.uniform(0.05, dias))
        hora = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 8, 9, 9, 8, 6, 4, 2, 1])[0]
        return dia.replace(hour=hora, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)

    clientes = {k: [f"{prefijo}-C{k}{i:03d}" for i in range(120)] for k in range(len(COMERCIOS))}
    ip_de = {c: _ip(rng) for k in clientes for c in clientes[k]}
    escenarios = max(8, n // 60)
    normales = n - escenarios * 8
    for _ in range(normales):                                                    # pagos de todos los días, clientes que repiten su IP y su equipo
        k = rng.choices(range(len(COMERCIOS)), weights=[5, 3, 2])[0]
        c = rng.choice(clientes[k])
        nuevo(k, momento_al_azar(), c, monto_cop=int(min(rng.lognormvariate(11.3, 0.8), 1_800_000)), red={"ip": ip_de[c], **SOLO_IP},
              dispositivo={"estado": rng.choices(["CONOCIDO", "NUEVO", None], weights=[80, 8, 12])[0]})

    def en_ventana(minutos: int) -> datetime:
        return momento_al_azar().replace(minute=0, second=0) + timedelta(minutes=rng.randint(0, minutos))

    for e in range(escenarios):
        k, t0, tipo = rng.randrange(len(COMERCIOS)), momento_al_azar(), e % 6
        if tipo == 0:                                                            # misma IP, muchos clientes
            ip = rng.choice(["203.0.113.10", "203.0.113.70"])
            for i in range(rng.randint(7, 10)):
                nuevo(k, t0 + timedelta(minutes=i * 2), f"{prefijo}-IP{e}-{i}", monto_cop=rng.randint(80_000, 400_000), red={"ip": ip, **SOLO_IP},
                      dispositivo={"huella": f"FP-{prefijo}-IP{e}-{i}", "estado": "NUEVO"}, identidad={"edad_cuenta_dias": rng.randint(1, 20)})
        elif tipo == 1:                                                          # un dispositivo, muchos clientes
            for i in range(rng.randint(6, 8)):
                nuevo(k, t0 + timedelta(minutes=i * 5), f"{prefijo}-DISP{e}-{i}", monto_cop=rng.randint(150_000, 600_000), red={"ip": _ip(rng), **SOLO_IP},
                      dispositivo={"huella": f"FP-{prefijo}-GRANJA{e}", "estado": "NUEVO"}, identidad={"edad_cuenta_dias": rng.randint(1, 10)})
        elif tipo == 2:                                                          # un cliente, muchas IPs
            for i, ultimo in enumerate(rng.sample([20, 40, 80, 100, 150, 210, 230], rng.randint(5, 7))):          # cada pago, desde una IP distinta
                nuevo(k, t0 + timedelta(minutes=i * 20), f"{prefijo}-VIAJA{e}", monto_cop=rng.randint(100_000, 500_000),
                      red={"ip": f"203.0.113.{ultimo}", **SOLO_IP}, dispositivo={"huella": f"FP-{prefijo}-VIAJA{e}"})
        elif tipo == 3:                                                          # viaje imposible
            cliente = f"{prefijo}-VIAJERO{e}"
            nuevo(k, t0, cliente, red={"ip": "192.0.2.50", **SOLO_IP})
            nuevo(k, t0 + timedelta(minutes=9), cliente, ciudad="Cali", monto_cop=2_400_000, categoria="ELECTRONICA", metodo_pago="VISA",
                  dispositivo={"tipo": "ANDROID", "estado": "NUEVO", "huella": f"FP-{prefijo}-VIAJERO{e}-OTRO", "sistema_operativo": "Android", "navegador": "Chrome"},
                  red={"ip": "198.51.100.40", **SOLO_IP})
        elif tipo == 4:                                                          # ráfaga de pagos pequeños desde una VPN
            for i in range(rng.randint(10, 14)):
                nuevo(k, t0 + timedelta(seconds=i * 25), f"{prefijo}-RAFAGA{e}-{i}", monto_cop=rng.randint(5_000, 25_000), red={"ip": "203.0.113.200", **SOLO_IP},
                      dispositivo={"huella": f"FP-{prefijo}-RAF{e}-{i}", "estado": "NO_RECONOCIDO"}, identidad={"edad_cuenta_dias": 1, "autenticacion_2fa": "EXIGIDA_FALLIDA"},
                      comportamiento={"tiempo_formulario_seg": 4, "pego_numero_tarjeta": True})
        else:                                                                    # proxy de otro país, y un pago con datos faltantes
            nuevo(k, t0, f"{prefijo}-PROXY{e}", monto_cop=rng.randint(300_000, 1_200_000), red={"ip": "203.0.113.150", **SOLO_IP})
            contador[0] += 1
            pagos.append((k, t0 + timedelta(minutes=30), {"id_transaccion": f"{prefijo}-{contador[0]:05d}", "id_cliente": f"{prefijo}-MIN{e}", "monto_cop": 90_000,
                          "metodo_pago": "PSE", "canal_pago": "WEB", "categoria": COMERCIOS[k][1], "ciudad": COMERCIOS[k][2],
                          "dispositivo": {"tipo": "ANDROID", "estado": "CONOCIDO"}, "red": {"tipo_ip": "RESIDENCIAL"}}))
    tope = ahora - timedelta(minutes=1)
    pagos = [(k, min(t, tope), p) for k, t, p in pagos]
    for k, t, p in pagos:
        p["fecha_pago"] = t.isoformat(timespec="seconds")                     # hora de Bogotá, sin zona
    return sorted(pagos, key=lambda x: x[1])


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Llena la demo con pagos de ejemplo enviados por la API real.")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--dias", type=int, default=20, help="hasta cuántos días atrás (la API acepta máximo 30)")
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--carpeta", type=Path, default=CARPETA_DEMO, help="carpeta donde está claves_demo.txt")
    a = ap.parse_args(argv)
    if not 1 <= a.dias <= 29:
        raise SystemExit("--dias debe estar entre 1 y 29")
    admin = _clave(a.carpeta / "claves_demo.txt", "ADMIN")
    prefijo = "EJ" + datetime.now().strftime("%H%M")
    cli = httpx.Client(base_url=a.url, timeout=60)
    claves = []
    for nombre, _, _ in COMERCIOS:
        r = cli.post("/v1/comercios", json={"nombre": nombre}, headers={"X-API-Key": admin})
        r.raise_for_status()
        claves.append(r.json()["clave_api"])
    pagos = generar(a.n, a.dias, a.semilla, datetime.now(), prefijo)
    resultado, errores = Counter(), Counter()
    t0 = time.time()
    for i, (k, _, pago) in enumerate(pagos, 1):
        for intento in range(6):
            r = cli.post("/v1/evaluaciones", json=pago, headers={"X-API-Key": claves[k]})
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            break
        if r.status_code in (200, 201):
            resultado[r.json()["decision"]] += 1
        else:
            errores[r.status_code] += 1
        if i % 500 == 0:
            print(f"  {i}/{len(pagos)} enviados…", flush=True)
    print(f"Listo: {len(pagos)} pagos en {time.time() - t0:.0f} s | decisiones: {dict(resultado)} | errores: {dict(errores) or 'ninguno'}")
    print("Abre http://127.0.0.1:8000/panel (o el dashboard cuando esté) para verlos.")


if __name__ == "__main__":
    main()
