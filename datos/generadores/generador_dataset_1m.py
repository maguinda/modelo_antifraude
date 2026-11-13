#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador del conjunto de datos SINTÉTICO de 1.000.000 de transacciones
del 1 de enero de 2025 al 12 de noviembre de 2026.

- Todo es inventado: no hay datos de personas reales (los identificadores son códigos).
- Semilla fija (42): al correrlo otra vez sale exactamente el mismo archivo.
- Trae las columnas de siempre (las 13 del ejemplo + id_cliente y patron) y columnas nuevas
  para poder construir y probar los componentes de un modelo antifraude completo:
    * id_comercio ............. la pyme donde se hizo la compra (producto para varias pymes)
    * huella y datos del dispositivo (huella, sistema, navegador, pantalla, zona horaria, emulador)
    * geolocalización de la IP (país, ciudad, distancia a la ciudad declarada, centro de datos, reputación)
    * identidad (nivel KYC, antigüedad de la cuenta, resultado del 2FA, accesos fallidos, cambio de credenciales)
    * comportamiento (tiempo en el formulario, velocidad de tecleo, tarjeta pegada, naturalidad del mouse)
    * lo que pasa DESPUÉS del pago (revisión manual, contracargo, fecha en que se confirma el fraude)
- Las columnas de "después del pago" NO pueden ser entrada del modelo: están en COLUMNAS_PROHIBIDAS.
- El fraude CAMBIA con el tiempo: unos patrones desaparecen y otros crecen (sirve para probar reentrenamiento).
- Las reglas del fraude y de las columnas nuevas las define este script: lo que aprenda un modelo
  son estas reglas, no el fraude real.

Uso:  python3 generador_dataset_1m.py [carpeta_de_salida]
      python3 generador_dataset_1m.py --afinidad [--movilidad=0.3] [carpeta_de_salida]
      (por defecto escribe en la carpeta modelo_ia/datos/)

Con --afinidad (versión 2) cada cliente compra sobre todo en 1 a 3 pymes «habituales» (como en la vida real) y el fraude cae
en una de ellas la mitad de las veces; sin esa opción, cada compra cae en una pyme al azar (versión 1, que se conserva igual).
La versión 2 sirve para medir qué cambia si el historial se calcula por comercio o con todos los comercios juntos.
"""
import bisect
import csv
import gzip
import io
import math
import os
import random
import sys
import zlib
from collections import defaultdict
from datetime import datetime, timedelta

SEMILLA = 42
ESC = 10                          # factor respecto al generador de 100.000
TOTAL = 100_000 * ESC
FRAUDES = 4_000 * ESC             # 4 %
INICIO = datetime(2025, 1, 1)
FIN = datetime(2026, 11, 12, 18, 0, 0)   # fecha y hora de generación (fija: reproducible)
DIAS = (FIN - INICIO).days + 1

rng = random.Random(SEMILLA)

CIUDADES = {"Bogotá": 30, "Medellín": 15, "Cali": 12, "Barranquilla": 8, "Cartagena": 6,
            "Bucaramanga": 6, "Pereira": 5, "Manizales": 4, "Santa Marta": 5, "Ibagué": 5}
COORD = {"Bogotá": (4.711, -74.072), "Medellín": (6.244, -75.581), "Cali": (3.451, -76.532),
         "Barranquilla": (10.968, -74.781), "Cartagena": (10.391, -75.479),
         "Bucaramanga": (7.119, -73.122), "Pereira": (4.814, -75.696), "Manizales": (5.070, -75.514),
         "Santa Marta": (11.241, -74.199), "Ibagué": (4.438, -75.232)}
# país -> (ciudad, latitud, longitud, zona horaria)
EXTERIOR_LEGITIMO = {"Estados Unidos": [("Miami", 25.76, -80.19, "America/New_York"), ("Nueva York", 40.71, -74.01, "America/New_York")],
                     "España": [("Madrid", 40.42, -3.70, "Europe/Madrid"), ("Barcelona", 41.39, 2.17, "Europe/Madrid")],
                     "México": [("Ciudad de México", 19.43, -99.13, "America/Mexico_City"), ("Cancún", 21.16, -86.85, "America/Cancun")],
                     "Panamá": [("Ciudad de Panamá", 8.98, -79.52, "America/Panama")],
                     "Ecuador": [("Quito", -0.18, -78.47, "America/Guayaquil")]}
EXTERIOR_ANONIMO = {"Estados Unidos": ("Ashburn", 39.04, -77.49, "America/New_York"),
                    "Países Bajos": ("Ámsterdam", 52.37, 4.90, "Europe/Amsterdam"),
                    "Alemania": ("Fráncfort", 50.11, 8.68, "Europe/Berlin"),
                    "Rusia": ("Moscú", 55.76, 37.62, "Europe/Moscow"),
                    "Nigeria": ("Lagos", 6.52, 3.38, "Africa/Lagos"),
                    "Vietnam": ("Ciudad Ho Chi Minh", 10.82, 106.63, "Asia/Ho_Chi_Minh")}
TZ_RAROS = ["Europe/Moscow", "Asia/Ho_Chi_Minh", "UTC", "America/New_York", "Africa/Lagos", "Asia/Shanghai"]

DISPOSITIVOS = ["MacBook", "Tablet Android", "Android", "iPhone", "Windows PC"]
SO = {"iPhone": "iOS", "Android": "Android", "Tablet Android": "Android", "Windows PC": "Windows", "MacBook": "macOS"}
RESOLUCION = {"iPhone": ["390x844", "393x852", "430x932"], "Android": ["412x915", "360x800", "393x873"],
              "Tablet Android": ["800x1280", "1200x1920"], "Windows PC": ["1920x1080", "1366x768", "1536x864"],
              "MacBook": ["1440x900", "1512x982", "1728x1117"]}
RESOLUCION_RARA = ["800x600", "1024x768", "1280x720", "1920x1200", "2560x1080", "640x480"]
METODOS = ["PayPal", "Visa", "Daviplata", "PSE", "Mastercard", "Nequi"]
CANALES = ["App móvil", "API", "POS", "Web"]
CATEGORIAS = {"Electrónica": 2.2, "Farmacia": 0.5, "Hotel": 2.5, "Restaurante": 0.4, "Ropa": 0.9,
              "Servicios públicos": 0.8, "Supermercado": 0.9, "Suscripción digital": 0.15,
              "Tienda online": 1.0, "Transporte": 0.35}
IP_ANONIMAS = ["VPN", "Proxy", "IP desconocida"]
HORA_W = [0.3, 0.2, 0.15, 0.15, 0.2, 0.4, 1, 2, 3, 4, 4.5, 5, 5.5, 5, 4.5, 4.5, 5, 5.5, 6, 6, 5, 4, 2.5, 1]

# Fechas comerciales (Colombia): Día de la Madre = 2.º domingo de mayo; Día del Padre = 3.er domingo de junio;
# Amor y Amistad = 3.er sábado de septiembre; Black Friday = último viernes de noviembre.
FECHAS_PICO = {datetime(2025, 5, 11): 1.6, datetime(2025, 6, 15): 1.5, datetime(2025, 9, 20): 1.6,
               datetime(2025, 11, 28): 2.2, datetime(2025, 12, 1): 1.8,
               datetime(2026, 5, 10): 1.6, datetime(2026, 6, 21): 1.5, datetime(2026, 9, 19): 1.6}


# ----------------------------------------------------------------- tiempo
def _peso_dia(d):
    f = INICIO + timedelta(days=d)
    w = {4: 1.2, 5: 1.2, 6: 1.0}.get(f.weekday(), 0.95)
    if f.day in (1, 2, 15, 16, 30, 31):
        w *= 1.35                                           # quincena y primas
    if f.month == 12 and 15 <= f.day <= 24:
        w *= 1.4                                            # Navidad
    for fecha, m in FECHAS_PICO.items():
        k = (fecha - f).days
        if k == 0:
            w *= m
        elif 1 <= k <= 3:
            w *= 1 + (m - 1) * 0.5
    return w


def _deriva(patron, d):
    """Cómo cambia el fraude con el tiempo (t va de 0 a 1 entre enero de 2025 y noviembre de 2026)."""
    t = d / (DIAS - 1)
    if patron == "prueba_tarjeta":
        return max(0.15, 1.7 - 1.5 * t)                      # pierde fuerza
    if patron == "navegador_antidetect":
        return 0.05 + 2.2 * t * t                            # casi no existe al inicio y crece
    if patron == "granja_dispositivos":
        return 0.2 + 1.8 * math.exp(-((t - 0.55) / 0.18) ** 2)   # ola a mediados del periodo
    if patron == "bot_relleno_tarjetas":
        f = INICIO + timedelta(days=d)
        return 3.0 if datetime(2025, 11, 20) <= f <= datetime(2025, 12, 24) else 1.0
    return 1.0


_CUM = {}


def _acumulado(patron):
    if patron not in _CUM:
        total, acum = 0.0, []
        for d in range(DIAS):
            total += _peso_dia(d) * _deriva(patron, d)
            acum.append(total)
        _CUM[patron] = acum
    return _CUM[patron]


def elegir(d):
    return rng.choices(list(d), weights=list(d.values()))[0]


def hora_normal():
    return rng.choices(range(24), weights=HORA_W)[0]


def ts_en(dia, hora, minuto=None, segundo=None):
    return INICIO + timedelta(days=dia, hours=hora,
                              minutes=rng.randint(0, 59) if minuto is None else minuto,
                              seconds=rng.randint(0, 59) if segundo is None else segundo)


def ts_aleatorio(margen_h=2, hora=None, patron=None):
    cum = _acumulado(patron)
    while True:
        dia = rng.choices(range(DIAS), cum_weights=cum)[0]
        h = hora_normal() if hora is None else hora
        t = ts_en(dia, h)
        if t <= FIN - timedelta(hours=margen_h):
            return t


# --------------------------------------------------------------- clientes
class Cliente:
    __slots__ = ("id", "ciudad", "disp", "metodo", "escala", "canales", "peso", "kyc", "edad0", "tiene_2fa")

    def __init__(self, id_, ciudad, disp, metodo, escala, canales, peso, kyc, edad0, tiene_2fa):
        self.id, self.ciudad, self.disp, self.metodo = id_, ciudad, disp, metodo
        self.escala, self.canales, self.peso = escala, canales, peso
        self.kyc, self.edad0, self.tiene_2fa = kyc, edad0, tiene_2fa


KYC_NIVELES = ["Completo", "Básico", "Sin verificar"]


def crear_clientes(n):
    out = []
    for i in range(1, n + 1):
        out.append(Cliente(f"CLI-{i:06d}", elegir(CIUDADES), rng.sample(DISPOSITIVOS, rng.choice([1, 2, 2])),
                           rng.choice(METODOS), 300_000 * math.exp(rng.gauss(0, 0.55)),
                           {"App móvil": 35, "Web": 30, "POS": 25, "API": 10}, rng.gammavariate(1.5, 1.0),
                           rng.choices(KYC_NIVELES, [55, 35, 10])[0],
                           int(min(2500, 400 * math.exp(rng.gauss(0, 1.0)))), rng.random() < 0.88))
    return out


def crear_comercios(n):
    out = []
    for i in range(1, n + 1):
        out.append(Cliente(f"COM-{i:04d}", elegir(CIUDADES), rng.sample(["Windows PC", "MacBook"], 1),
                           rng.choice(["Visa", "Mastercard", "PSE"]), 150_000 * math.exp(rng.gauss(0, 0.4)),
                           {"API": 60, "POS": 40}, 1.0, rng.choices(KYC_NIVELES, [92, 8, 0])[0],
                           int(min(2500, 700 * math.exp(rng.gauss(0, 0.8)))), False))
    return out


_CONTADOR = defaultdict(int)


def nuevo_cliente(prefijo, edad_max=5, kyc_pesos=(55, 35, 10)):
    """Cliente que se crea en el momento (cuenta nueva)."""
    _CONTADOR[prefijo] += 1
    return Cliente(f"{prefijo}-{_CONTADOR[prefijo]:06d}", elegir(CIUDADES), rng.sample(DISPOSITIVOS, rng.choice([1, 2])),
                   rng.choice(METODOS), 250_000 * math.exp(rng.gauss(0, 0.5)),
                   {"App móvil": 35, "Web": 40, "POS": 15, "API": 10}, 1.0,
                   rng.choices(KYC_NIVELES, kyc_pesos)[0], rng.randint(0, edad_max), rng.random() < 0.8)


CLIENTES = crear_clientes(12_000 * ESC)
COMERCIOS = crear_comercios(500)
_acum, _s = [], 0.0
for _c in CLIENTES:
    _s += _c.peso
    _acum.append(_s)

# Pymes (comercios donde se paga): cada una con su volumen y su nivel de riesgo
N_PYMES = 250
_vol = [math.exp(rng.gauss(0, 1.0)) for _ in range(N_PYMES)]
_riesgo = [math.exp(rng.gauss(0, 0.7)) for _ in range(N_PYMES)]
_cum_legit, _cum_fraude, _a, _b = [], [], 0.0, 0.0
for _v, _r in zip(_vol, _riesgo):
    _a += _v
    _b += _v * _r
    _cum_legit.append(_a)
    _cum_fraude.append(_b)


AFINIDAD = "--afinidad" in sys.argv         # versión 2 (ver arriba)
# Con --movilidad=0.3, el 30 % de las compras LEGÍTIMAS caen en un comercio nuevo para el cliente (como pasa con los clientes nuevos de un comercio)
MOVILIDAD = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--movilidad=")), 0.0)


def pymes_del_cliente(id_cliente):
    """1 a 3 pymes habituales del cliente, calculadas con una huella del código (no gasta números al azar)."""
    h = zlib.crc32(f"{id_cliente}|n".encode()) % 10
    n = 1 if h < 6 else 2 if h < 9 else 3
    return sorted({min(bisect.bisect_left(_cum_legit, zlib.crc32(f"{id_cliente}|{k}".encode()) / 4294967296.0 * _cum_legit[-1]), N_PYMES - 1)
                   for k in range(n)})


def cliente_al_azar():
    return rng.choices(CLIENTES, cum_weights=_acum)[0]


def otra_ciudad(c):
    return rng.choice([x for x in CIUDADES if x != c])


def disp_nuevo(cli):
    return rng.choice([d for d in DISPOSITIVOS if d not in cli.disp])


def ip_normal(canal):
    if canal == "App móvil":
        return rng.choices(["Móvil", "Residencial"], [65, 35])[0]
    if canal == "Web":
        return rng.choices(["Residencial", "Empresarial"], [60, 40])[0]
    if canal == "POS":
        return rng.choices(["Empresarial", "Móvil"], [85, 15])[0]
    return rng.choices(["Empresarial", "Residencial"], [90, 10])[0]


def monto_normal(cli, cat, mult=1.0):
    v = cli.escala * CATEGORIAS[cat] * mult * math.exp(rng.gauss(0, 0.7))
    return int(round(min(max(v, 3_000), 3_000_000)))


def fila(cli, ts, fraude=0, patron="normal", **kw):
    cat = kw.get("cat") or rng.choice(list(CATEGORIAS))
    canal = kw.get("canal") or elegir(cli.canales)
    metodo = kw.get("metodo") or (cli.metodo if rng.random() < 0.8 else rng.choice(METODOS))
    return {
        "ts": ts, "cli": cli, "id_cliente": cli.id, "home": cli.ciudad,
        "dispositivo": kw.get("disp") or rng.choice(cli.disp),
        "ciudad": kw.get("ciudad") or cli.ciudad,
        "monto": kw["monto"] if kw.get("monto") is not None else monto_normal(cli, cat),
        "cat": cat, "metodo": metodo, "canal": canal,
        "ip": kw.get("ip") or ip_normal(canal),
        "estado": kw.get("estado") or "Conocido",
        "fraude": fraude, "patron": patron, "fp": kw.get("fp"),
    }


def llenar(cuota, generador):
    filas = []
    while len(filas) < cuota:
        nuevas = [f for f in generador(cuota - len(filas)) if f["ts"] <= FIN]
        filas.extend(nuevas[: cuota - len(filas)])
    return filas


# ------------------------------------------------------------ casos legítimos
def legit_ip_anonima(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(), patron="ip_anonima_legitima", ip=rng.choices(IP_ANONIMAS, [70, 15, 15])[0])]


def legit_viajero(restante):
    c = cliente_al_azar()
    ciudad = otra_ciudad(c.ciudad)
    base = ts_aleatorio(margen_h=120)
    out = []
    for _ in range(rng.randint(2, 4)):
        cat = rng.choice(["Hotel", "Transporte", "Restaurante", "Tienda online", "Ropa"])
        out.append(fila(c, base + timedelta(hours=rng.uniform(0, 90)), patron="viajero_legitimo", ciudad=ciudad, cat=cat,
                        monto=monto_normal(c, cat, rng.uniform(1.0, 1.8)),
                        estado=rng.choices(["Conocido", "Nuevo"], [85, 15])[0]))
    return out


def legit_celular_nuevo(restante):
    c = cliente_al_azar()
    d = disp_nuevo(c)
    base = ts_aleatorio(margen_h=96)
    return [fila(c, base + timedelta(hours=rng.uniform(0, 70)), patron="celular_nuevo", disp=d, estado="Nuevo")
            for _ in range(rng.randint(1, 3))]


def legit_no_reconocido(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(), patron="dispositivo_no_reconocido_legitimo", disp=disp_nuevo(c), estado="No reconocido")]


def _dias_quincena():
    return [d for d in range(DIAS) if (INICIO + timedelta(days=d)).day in (1, 14, 15, 16, 29, 30, 31)
            and INICIO + timedelta(days=d) < FIN - timedelta(days=1)]


DIAS_QUINCENA = _dias_quincena()


def legit_pico_quincena(restante):
    c = cliente_al_azar()
    dia = rng.choice(DIAS_QUINCENA)
    out = []
    for _ in range(rng.randint(3, 7)):
        cat = rng.choice(list(CATEGORIAS))
        out.append(fila(c, ts_en(dia, rng.randint(9, 20)), patron="pico_quincena", cat=cat, monto=monto_normal(c, cat, 1.3)))
    return out


def legit_comercio_masivo(restante):
    c = rng.choice(COMERCIOS)
    dia = rng.randrange(DIAS - 2)
    out = []
    for _ in range(rng.randint(5, 25)):
        cat = rng.choice(["Tienda online", "Supermercado", "Farmacia", "Ropa", "Restaurante"])
        out.append(fila(c, ts_en(dia, rng.randint(8, 20)), patron="comercio_api_masivo", cat=cat, ip="Empresarial",
                        monto=monto_normal(c, cat)))
    return out


def legit_compra_grande(restante):
    c = cliente_al_azar()
    cat = rng.choice(["Hotel", "Electrónica", "Servicios públicos", "Tienda online", "Ropa"])
    monto = int(math.exp(rng.uniform(math.log(1_500_000), math.log(8_000_000))))
    return [fila(c, ts_aleatorio(hora=rng.randint(9, 20)), patron="compra_grande_legitima", cat=cat, monto=monto)]


def legit_noctambulo(restante):
    c = cliente_al_azar()
    h = rng.choices(range(6), [2, 3, 3, 3, 2, 1])[0]
    cat = rng.choice(["Suscripción digital", "Tienda online", "Restaurante", "Transporte"])
    return [fila(c, ts_aleatorio(hora=h), patron="noctambulo_legitimo", cat=cat)]


def legit_suscripcion(restante):
    c = cliente_al_azar()
    monto = int(round(rng.uniform(9_000, 90_000) / 100) * 100)
    dia_mes, hora = rng.randint(1, 28), rng.randint(0, 23)
    canal, ip = rng.choice(["Web", "App móvil"]), rng.choice(["Residencial", "Móvil"])
    mes0, n = rng.randint(0, 17), rng.randint(4, 8)      # meses contados desde enero de 2025
    out = []
    for k in range(n):
        m = mes0 + k
        t = datetime(2025 + m // 12, m % 12 + 1, dia_mes, hora, rng.randint(0, 59), rng.randint(0, 59))
        if t > FIN:
            break
        out.append(fila(c, t, patron="suscripcion_recurrente", cat="Suscripción digital", canal=canal, ip=ip,
                        monto=monto, metodo=c.metodo))
    return out


def legit_duplicado(restante):
    c = cliente_al_azar()
    cat = rng.choice(list(CATEGORIAS))
    t = ts_aleatorio()
    f1 = fila(c, t, patron="cobro_duplicado", cat=cat)
    return [f1, dict(f1, ts=t + timedelta(seconds=rng.randint(40, 300)))]


def legit_limite(restante):
    c = cliente_al_azar()
    tipo = rng.randrange(5)
    if tipo == 0:
        return [fila(c, ts_aleatorio(), patron="caso_limite", monto=1_000)]
    if tipo == 1:
        return [fila(c, ts_aleatorio(hora=rng.randint(9, 20)), patron="caso_limite", cat=rng.choice(["Hotel", "Electrónica"]),
                     monto=rng.randint(12_000_000, 20_000_000))]
    if tipo == 2:
        return [fila(c, ts_en(rng.randrange(DIAS - 2), 0, 0, 0), patron="caso_limite")]
    if tipo == 3:
        return [fila(c, ts_en(rng.randrange(DIAS - 2), 23, 59, 59), patron="caso_limite")]
    combo = rng.choice([("PayPal", "POS"), ("Nequi", "API"), ("PSE", "POS"), ("Daviplata", "POS")])
    return [fila(c, ts_aleatorio(), patron="caso_limite", metodo=combo[0], canal=combo[1])]


# --- legítimos nuevos (para identidad, geolocalización y huella)
def legit_viajero_exterior(restante):
    c = cliente_al_azar()
    cat = rng.choice(["Hotel", "Transporte", "Restaurante", "Tienda online"])
    return [fila(c, ts_aleatorio(), patron="viajero_exterior_legitimo", cat=cat, monto=monto_normal(c, cat, rng.uniform(1.0, 2.0)),
                 ip=rng.choice(["Residencial", "Móvil"]), estado=rng.choices(["Conocido", "Nuevo"], [80, 20])[0],
                 canal=rng.choice(["Web", "App móvil"]))]


def legit_vpn_corporativa(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(hora=rng.randint(8, 19)), patron="vpn_corporativa_legitima",
                 ip=rng.choices(["VPN", "Empresarial"], [70, 30])[0], canal=rng.choice(["Web", "API"]))]


def legit_tfa_fallido(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(), patron="tfa_fallido_legitimo")]


def legit_cliente_nuevo(restante):
    c = nuevo_cliente("CLN", 5, (70, 25, 5))
    base = ts_aleatorio(margen_h=130)
    out = []
    for k in range(rng.randint(1, 3)):
        cat = rng.choice(list(CATEGORIAS))
        out.append(fila(c, base + timedelta(hours=rng.uniform(0, 100) * (k > 0)), patron="cliente_nuevo_legitimo", cat=cat,
                        monto=monto_normal(c, cat, 0.7), estado="Nuevo" if k == 0 else "Conocido"))
    return out


def legit_cambio_credenciales(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(), patron="cambio_credenciales_legitimo")]


# ------------------------------------------------------------------- fraudes
def f_rafaga(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23), patron="rafaga_velocidad")
    estado = rng.choices(["Nuevo", "No reconocido"], [60, 40])[0]
    d = disp_nuevo(c)
    anon = rng.random() < 0.5
    ciudad = c.ciudad if rng.random() < 0.5 else otra_ciudad(c.ciudad)
    out = []
    for _ in range(min(rng.randint(5, 10), max(restante, 5))):
        t += timedelta(minutes=rng.uniform(1, 6))
        out.append(fila(c, t, 1, "rafaga_velocidad", disp=d, estado=estado, ciudad=ciudad, canal=rng.choice(["Web", "API", "App móvil"]),
                        ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil"]),
                        monto=int(rng.uniform(80_000, 1_200_000))))
    return out


def f_prueba_tarjeta(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23), patron="prueba_tarjeta")
    d = disp_nuevo(c)
    metodo = rng.choice(["Visa", "Mastercard", "PayPal"])
    ip = rng.choice(IP_ANONIMAS + ["Residencial"])
    out = []
    for _ in range(rng.randint(5, 10)):
        t += timedelta(seconds=rng.randint(20, 120))
        out.append(fila(c, t, 1, "prueba_tarjeta", disp=d, estado="Nuevo", metodo=metodo, canal=rng.choice(["Web", "API"]), ip=ip,
                        cat=rng.choice(["Tienda online", "Suscripción digital", "Electrónica"]), monto=rng.randint(1_000, 25_000)))
    t += timedelta(seconds=rng.randint(30, 150))
    out.append(fila(c, t, 1, "prueba_tarjeta", disp=d, estado="Nuevo", metodo=metodo, canal=rng.choice(["Web", "API"]), ip=ip,
                    cat=rng.choice(["Tienda online", "Electrónica"]), monto=rng.randint(600_000, 3_000_000)))
    return out


def f_toma_cuenta(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23), patron="toma_cuenta")
    d = disp_nuevo(c)
    estado = rng.choices(["Nuevo", "No reconocido"], [55, 45])[0]
    ciudad = otra_ciudad(c.ciudad) if rng.random() < 0.85 else c.ciudad
    anon = rng.random() < 0.55
    out = []
    for _ in range(rng.choice([1, 1, 2, 3])):
        t += timedelta(minutes=rng.uniform(2, 30))
        monto = int(min(max(c.escala * rng.uniform(3, 9), 300_000), 6_000_000))
        out.append(fila(c, t, 1, "toma_cuenta", disp=d, estado=estado, ciudad=ciudad,
                        cat=rng.choice(["Electrónica", "Tienda online", "Ropa", "Hotel"]),
                        ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil"]), monto=monto))
    return out


ANCLAS = []


def f_viaje_imposible(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(patron="viaje_imposible")
    ANCLAS.append(fila(c, t, 0, "normal", estado="Conocido"))       # compra legítima en su ciudad
    t2 = t + timedelta(minutes=rng.uniform(5, 45))
    return [fila(c, t2, 1, "viaje_imposible", ciudad=otra_ciudad(c.ciudad), disp=disp_nuevo(c), estado="Nuevo",
                 ip=rng.choice(["Residencial", "Móvil", "IP desconocida"]),
                 monto=int(min(max(c.escala * rng.uniform(1, 4), 100_000), 4_000_000)))]


def f_madrugada(restante):
    c = cliente_al_azar()
    estado = rng.choices(["Nuevo", "No reconocido", "Conocido"], [60, 20, 20])[0]
    anon = rng.random() < 0.4
    monto = int(math.exp(rng.uniform(math.log(1_500_000), math.log(6_000_000))))
    return [fila(c, ts_aleatorio(hora=rng.randint(0, 5), patron="madrugada_monto_alto"), 1, "madrugada_monto_alto", monto=monto,
                 disp=disp_nuevo(c) if estado != "Conocido" else None, estado=estado, canal=rng.choice(["Web", "API"]),
                 cat=rng.choice(["Electrónica", "Tienda online", "Hotel", "Ropa"]),
                 ciudad=c.ciudad if rng.random() < 0.6 else otra_ciudad(c.ciudad),
                 ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil", "Empresarial"]))]


def f_ip_anonima(restante):
    c = cliente_al_azar()
    estado = rng.choices(["Conocido", "Nuevo", "No reconocido"], [40, 50, 10])[0]
    cat = rng.choice(list(CATEGORIAS))
    return [fila(c, ts_aleatorio(patron="ip_anonima"), 1, "ip_anonima", ip=rng.choice(IP_ANONIMAS),
                 disp=None if estado == "Conocido" else disp_nuevo(c), estado=estado, cat=cat,
                 ciudad=c.ciudad if rng.random() < 0.7 else otra_ciudad(c.ciudad), monto=max(monto_normal(c, cat, 1.5), 100_000))]


def f_disimulado(restante):
    c = cliente_al_azar()
    cat = rng.choices(list(CATEGORIAS), [4 if k in ("Tienda online", "Suscripción digital", "Electrónica") else 1 for k in CATEGORIAS])[0]
    estado = rng.choices(["Conocido", "Nuevo"], [60, 40])[0]
    return [fila(c, ts_aleatorio(patron="fraude_disimulado"), 1, "fraude_disimulado", cat=cat, estado=estado,
                 disp=None if estado == "Conocido" else disp_nuevo(c), canal=rng.choices(["Web", "API", "App móvil"], [45, 25, 30])[0],
                 metodo=rng.choice(["Visa", "Mastercard", "PayPal"]) if rng.random() < 0.7 else None,
                 ip=rng.choice(["Residencial", "Móvil"]), monto=monto_normal(c, cat, rng.uniform(0.8, 1.5)))]


# --- fraudes nuevos
def f_granja(restante):
    """Una misma huella de dispositivo usada contra muchos clientes distintos en pocos días."""
    fp = f"FP-{rng.getrandbits(32):08x}"
    base = ts_aleatorio(margen_h=90, patron="granja_dispositivos")
    out = []
    for _ in range(rng.randint(6, 15)):
        c = cliente_al_azar()
        for _ in range(rng.choice([1, 1, 2])):
            out.append(fila(c, base + timedelta(hours=rng.uniform(0, 70)), 1, "granja_dispositivos", disp=disp_nuevo(c),
                            estado=rng.choice(["Nuevo", "No reconocido"]), fp=fp,
                            ciudad=c.ciudad if rng.random() < 0.6 else otra_ciudad(c.ciudad),
                            ip=rng.choice(["Residencial", "Móvil"] + IP_ANONIMAS), canal=rng.choice(["Web", "App móvil", "API"]),
                            monto=int(rng.uniform(150_000, 1_500_000))))
    return out


def f_antidetect(restante):
    """Navegador anti-detección: la huella cambia en cada compra y los datos del equipo no cuadran."""
    c = cliente_al_azar()
    t = ts_aleatorio(patron="navegador_antidetect")
    out = []
    for _ in range(rng.randint(1, 3)):
        t += timedelta(minutes=rng.uniform(1, 30))
        out.append(fila(c, t, 1, "navegador_antidetect", disp=disp_nuevo(c), estado=rng.choice(["Nuevo", "No reconocido"]),
                        ip=rng.choice(IP_ANONIMAS) if rng.random() < 0.3 else rng.choice(["Residencial", "Móvil"]),
                        canal="Web", ciudad=c.ciudad if rng.random() < 0.5 else otra_ciudad(c.ciudad),
                        monto=int(min(max(c.escala * rng.uniform(1.5, 6), 100_000), 5_000_000))))
    return out


def f_bot(restante):
    """Bot que prueba números de tarjeta de muchos clientes con montos pequeños."""
    t = ts_aleatorio(patron="bot_relleno_tarjetas")
    ip = rng.choice(IP_ANONIMAS + ["Residencial"])
    out = []
    for _ in range(rng.randint(4, 9)):
        c = cliente_al_azar()
        t += timedelta(seconds=rng.randint(5, 60))
        out.append(fila(c, t, 1, "bot_relleno_tarjetas", disp=disp_nuevo(c), estado=rng.choice(["Nuevo", "No reconocido"]),
                        canal=rng.choice(["Web", "API"]), ip=ip, monto=rng.randint(5_000, 60_000),
                        cat=rng.choice(["Tienda online", "Suscripción digital"])))
    return out


def f_cuenta_nueva(restante):
    c = nuevo_cliente("CLF", 3, (5, 25, 70))              # cuenta creada por el defraudador
    t = ts_aleatorio(margen_h=60, patron="cuenta_nueva_2fa_fallida")
    out = []
    for _ in range(rng.randint(1, 3)):
        t += timedelta(hours=rng.uniform(0.1, 20))
        out.append(fila(c, t, 1, "cuenta_nueva_2fa_fallida", estado="Nuevo", ip=rng.choice(["Residencial", "Móvil"] + IP_ANONIMAS),
                        cat=rng.choice(["Electrónica", "Tienda online", "Ropa"]), monto=int(rng.uniform(300_000, 2_500_000)),
                        canal=rng.choice(["Web", "App móvil"])))
    return out


def f_amistoso(restante):
    """Fraude amistoso: el cliente compra y luego desconoce el cobro. Se ve como una compra normal."""
    c = cliente_al_azar()
    cat = rng.choices(["Electrónica", "Ropa", "Tienda online", "Hotel"], [3, 3, 3, 1])[0]
    return [fila(c, ts_aleatorio(patron="fraude_amistoso"), 1, "fraude_amistoso", cat=cat, monto=monto_normal(c, cat, rng.uniform(1.0, 2.5)))]


def f_normal(restante):
    c = cliente_al_azar()
    t = ts_aleatorio()
    estado = rng.choices(["Conocido", "Nuevo"], [95, 5])[0]
    out = [fila(c, t, patron="normal", estado=estado, disp=disp_nuevo(c) if estado == "Nuevo" else None)]
    if rng.random() < 0.22:
        for _ in range(rng.choice([1, 1, 2])):
            out.append(fila(c, t + timedelta(hours=rng.uniform(0.3, 20)), patron="normal"))
    return out


CUOTAS_LEGIT = [
    (legit_ip_anonima, 2000 * ESC), (legit_viajero, 1500 * ESC), (legit_celular_nuevo, 2500 * ESC),
    (legit_no_reconocido, 200 * ESC), (legit_pico_quincena, 4000 * ESC), (legit_comercio_masivo, 3000 * ESC),
    (legit_compra_grande, 3000 * ESC), (legit_noctambulo, 2500 * ESC), (legit_suscripcion, 4000 * ESC),
    (legit_duplicado, 800 * ESC), (legit_limite, 300 * ESC),
    (legit_viajero_exterior, 1500 * ESC), (legit_vpn_corporativa, 1000 * ESC), (legit_tfa_fallido, 600 * ESC),
    (legit_cliente_nuevo, 2000 * ESC), (legit_cambio_credenciales, 500 * ESC),
]
CUOTAS_FRAUDE = [
    (f_rafaga, 500 * ESC), (f_prueba_tarjeta, 400 * ESC), (f_toma_cuenta, 500 * ESC), (f_viaje_imposible, 300 * ESC),
    (f_madrugada, 350 * ESC), (f_ip_anonima, 250 * ESC), (f_disimulado, 500 * ESC),
    (f_granja, 300 * ESC), (f_antidetect, 350 * ESC), (f_bot, 200 * ESC), (f_cuenta_nueva, 150 * ESC), (f_amistoso, 200 * ESC),
]
assert sum(q for _, q in CUOTAS_FRAUDE) == FRAUDES, sum(q for _, q in CUOTAS_FRAUDE)


# ------------------------------------------------------- columnas nuevas
P_BASE = {"emulador": 0.003, "ip_ciudad_distinta": 0.12, "rep_delta": 0, "kyc": None,
          "tfa": {"No exigida": 70, "Exigida-aprobada": 17, "Exigida-fallida": 1, "No disponible": 12},
          "fallidos": 0.05, "fallidos_min": 0, "cred": 0.004, "t_form": (45, 0.6), "tecleo": (180, 40),
          "pego": 0.04, "mouse": (8, 2), "tz_mismatch": 0.01, "fp_modo": "estable", "exterior": False,
          "datacenter_forzado": False, "ua_inconsistente": 0.0}
_TFA_MALO = {"No exigida": 35, "Exigida-aprobada": 15, "Exigida-fallida": 15, "No disponible": 35}
PERFILES = {
    # fraudes
    "rafaga_velocidad": dict(emulador=.15, ip_ciudad_distinta=.5, rep_delta=-20, kyc=(30, 40, 30), fallidos=1.5, cred=.05,
                             t_form=(12, .5), tecleo=(90, 30), pego=.4, mouse=(3, 4), tz_mismatch=.15,
                             tfa={"No exigida": 40, "Exigida-aprobada": 10, "Exigida-fallida": 25, "No disponible": 25}),
    "prueba_tarjeta": dict(emulador=.25, ip_ciudad_distinta=.5, rep_delta=-25, kyc=(20, 40, 40), fallidos=.5,
                           t_form=(6, .5), tecleo=(40, 15), pego=.85, mouse=(2, 6), tz_mismatch=.2,
                           tfa={"No exigida": 25, "Exigida-fallida": 10, "No disponible": 65}),
    "toma_cuenta": dict(emulador=.1, ip_ciudad_distinta=.6, rep_delta=-15, fallidos=2.0, cred=.6, t_form=(30, .5),
                        tecleo=(150, 40), pego=.3, mouse=(5, 3), tz_mismatch=.2,
                        tfa={"No exigida": 15, "Exigida-aprobada": 25, "Exigida-fallida": 20, "No disponible": 40}),
    "viaje_imposible": dict(emulador=.05, ip_ciudad_distinta=.8, rep_delta=-10, fallidos=.4, cred=.05, pego=.15, mouse=(6, 3),
                            tz_mismatch=.1, tfa={"No exigida": 45, "Exigida-aprobada": 20, "Exigida-fallida": 10, "No disponible": 25}),
    "madrugada_monto_alto": dict(emulador=.08, ip_ciudad_distinta=.4, rep_delta=-12, fallidos=.6, cred=.05, t_form=(25, .5),
                                 pego=.2, mouse=(5, 3), tz_mismatch=.12, tfa=_TFA_MALO),
    "ip_anonima": dict(emulador=.08, rep_delta=-10, fallidos=.3, pego=.15, mouse=(6, 3), tz_mismatch=.15, tfa=_TFA_MALO),
    "fraude_disimulado": dict(emulador=.01, ip_ciudad_distinta=.2, rep_delta=-3, fallidos=.1, cred=.02, pego=.08,
                              mouse=(7, 2.5), tz_mismatch=.03),
    "granja_dispositivos": dict(emulador=.6, ip_ciudad_distinta=.4, rep_delta=-20, kyc=(15, 35, 50), fallidos=.8, t_form=(10, .5),
                                tecleo=(60, 20), pego=.5, mouse=(3, 5), tz_mismatch=.3,
                                tfa={"No exigida": 30, "Exigida-fallida": 10, "No disponible": 60}),
    "navegador_antidetect": dict(emulador=.3, ip_ciudad_distinta=.5, rep_delta=-15, kyc=(25, 45, 30), fallidos=.4, t_form=(20, .5),
                                 tecleo=(110, 30), pego=.35, mouse=(4, 4), tz_mismatch=.7, fp_modo="cambiante",
                                 ua_inconsistente=.7, tfa=_TFA_MALO),
    "bot_relleno_tarjetas": dict(emulador=.5, ip_ciudad_distinta=.6, rep_delta=-30, kyc=(10, 30, 60), fallidos=3.0, t_form=(2.5, .4),
                                 tecleo=(10, 4), pego=.95, mouse=(1, 9), tz_mismatch=.4, ua_inconsistente=.5,
                                 tfa={"No exigida": 15, "Exigida-fallida": 15, "No disponible": 70}),
    "cuenta_nueva_2fa_fallida": dict(emulador=.1, ip_ciudad_distinta=.5, rep_delta=-12, kyc=(5, 25, 70), fallidos=3.0, cred=.05,
                                     t_form=(20, .5), pego=.3, mouse=(5, 3), tz_mismatch=.2,
                                     tfa={"Exigida-fallida": 80, "No disponible": 20}),
    "fraude_amistoso": {},
    # legítimos que se parecen al fraude
    "ip_anonima_legitima": dict(rep_delta=-8),
    "viajero_exterior_legitimo": dict(exterior=True, tz_mismatch=0.0,
                                      tfa={"No exigida": 10, "Exigida-aprobada": 75, "Exigida-fallida": 2, "No disponible": 13}),
    "vpn_corporativa_legitima": dict(datacenter_forzado=True, rep_delta=-5),
    "tfa_fallido_legitimo": dict(tfa={"Exigida-aprobada": 100}, fallidos=1.8, fallidos_min=1),
    "cliente_nuevo_legitimo": dict(kyc=(70, 25, 5), tfa={"Exigida-aprobada": 55, "No exigida": 35, "No disponible": 10}),
    "cambio_credenciales_legitimo": dict(cred=1.0, tfa={"Exigida-aprobada": 80, "No exigida": 10, "No disponible": 10}),
}


def perfil_de(patron):
    return dict(P_BASE, **PERFILES.get(patron, {}))


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def poisson(lam, minimo=0):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            break
        k += 1
    return min(max(k, minimo), 12)


def navegador_de(so, canal):
    if canal in ("API", "POS"):
        return "No aplica"
    if canal == "App móvil":
        return "App nativa"
    pesos = {"iOS": {"Safari": 85, "Chrome": 15}, "Android": {"Chrome": 80, "Samsung Internet": 15, "Firefox": 5},
             "Windows": {"Chrome": 60, "Edge": 25, "Firefox": 12, "Opera": 3},
             "macOS": {"Safari": 55, "Chrome": 40, "Firefox": 5}, "Linux": {"Chrome": 70, "Firefox": 30}}
    return elegir(pesos[so])


def geo_ip(f, p):
    """(país, ciudad, distancia_km o None, es_datacenter, reputación) de la IP."""
    ip, ciudad = f["ip"], f["ciudad"]
    punto = COORD[ciudad]
    if p["exterior"]:
        pais = rng.choice(list(EXTERIOR_LEGITIMO))
        c, la, lo, tz = rng.choice(EXTERIOR_LEGITIMO[pais])
        return pais, c, round(haversine(punto, (la, lo))), 0, (pais, tz)
    if ip in ("VPN", "Proxy"):
        if rng.random() < 0.25:
            dest = ciudad if rng.random() < 0.4 else otra_ciudad(ciudad)
            return "Colombia", dest, round(haversine(punto, COORD[dest])) if dest != ciudad else rng.randint(0, 15), \
                int(rng.random() < 0.9 or p["datacenter_forzado"]), ("Colombia", None)
        pais = rng.choice(list(EXTERIOR_ANONIMO))
        c, la, lo, tz = EXTERIOR_ANONIMO[pais]
        return pais, c, round(haversine(punto, (la, lo))), int(rng.random() < 0.9 or p["datacenter_forzado"]), (pais, tz)
    if ip == "IP desconocida":
        return "Desconocido", "Desconocida", None, int(rng.random() < 0.3), ("Desconocido", None)
    dest = otra_ciudad(ciudad) if rng.random() < p["ip_ciudad_distinta"] else ciudad
    km = rng.randint(0, 18) if dest == ciudad else round(haversine(punto, COORD[dest]) * rng.uniform(0.95, 1.05))
    return "Colombia", dest, km, int(p["datacenter_forzado"] or (ip == "Empresarial" and rng.random() < 0.1)), ("Colombia", None)


def enriquecer(f, primera):
    """Valores de las columnas nuevas para una fila (en el orden de COLUMNAS_EXTRA)."""
    p, cli, canal, ts = perfil_de(f["patron"]), f["cli"], f["canal"], f["ts"]
    es_comercio = cli.id.startswith("COM")
    web, movil = canal == "Web", canal == "App móvil"

    # --- dispositivo
    disp = f["dispositivo"]
    so = SO[disp]
    inconsistente = p["ua_inconsistente"] and rng.random() < p["ua_inconsistente"]
    if inconsistente:
        so = rng.choice(["Linux", "Windows", "Android"])
    navegador = "Chrome headless" if (inconsistente and web) else navegador_de(so, canal)
    resolucion = rng.choice(RESOLUCION_RARA) if inconsistente else rng.choice(RESOLUCION[disp])
    if f["fp"]:
        huella = f["fp"]
    elif p["fp_modo"] == "cambiante":
        huella = f"FP-{rng.getrandbits(32):08x}"
    else:
        huella = f"FP-{zlib.crc32((cli.id + '|' + disp).encode()):08x}"
    emulador = int(rng.random() < p["emulador"])

    # --- IP
    pais, ciudad_ip, km, datacenter, (pais_tz, tz_ip) = geo_ip(f, p)
    base_rep = {"Residencial": 88, "Móvil": 88, "Empresarial": 88, "VPN": 60, "Proxy": 40, "IP desconocida": 30}[f["ip"]]
    rep = int(min(100, max(0, rng.gauss(base_rep, 10 if base_rep > 80 else 15) + p["rep_delta"] - (15 if datacenter else 0))))
    if tz_ip and p["exterior"]:
        tz = tz_ip
    elif rng.random() < p["tz_mismatch"]:
        tz = rng.choice(TZ_RAROS)
    else:
        tz = "America/Bogota"

    # --- identidad
    kyc = KYC_NIVELES[rng.choices(range(3), p["kyc"])[0]] if p["kyc"] else cli.kyc
    edad = cli.edad0 + (ts - primera[cli.id]).days
    if es_comercio:
        tfa = "No aplica"
    elif not cli.tiene_2fa and p["tfa"] is P_BASE["tfa"]:
        tfa = "No disponible"
    else:
        tfa = elegir(p["tfa"])
    fallidos = poisson(p["fallidos"], p["fallidos_min"])
    cred = int(rng.random() < p["cred"])

    # --- comportamiento (solo se mide en web y app)
    if web or movil:
        t_form = round(max(0.5, math.exp(rng.gauss(math.log(p["t_form"][0]), p["t_form"][1]))), 1)
        tecleo = round(max(5.0, rng.gauss(*p["tecleo"])), 1)
        pego = int(rng.random() < p["pego"])
    else:
        t_form = tecleo = pego = None
    mouse = round(rng.betavariate(*p["mouse"]), 2) if web else None
    return [huella, so, navegador, resolucion, tz, emulador, pais, ciudad_ip, km, datacenter, rep,
            kyc, edad, tfa, fallidos, cred, t_form, tecleo, pego, mouse]


def post_pago(f):
    """Lo que se sabe DESPUÉS: revisión manual, contracargo y fecha de confirmación."""
    ts, fraude, nivel = f["ts"], f["fraude"], f["nivel"]
    p_rev = {"Crítico": 0.9, "Alto": 0.6, "Medio": 0.06, "Bajo": 0.002}[nivel]
    revision = rng.random() < p_rev and f["patron"] != "fraude_amistoso"
    resultado = minutos = None
    if revision:
        acierta = rng.random() < (0.92 if fraude else 0.96)
        resultado = ("Confirmado fraude" if acierta else "Falso positivo") if fraude else \
                    ("Falso positivo" if acierta else "Confirmado fraude")
        minutos = round(max(1.0, math.exp(rng.gauss(math.log(14), 0.8))), 1)
    contracargo, dias, confirmacion = 0, None, None
    if fraude:
        if resultado == "Confirmado fraude":
            confirmacion = ts + timedelta(minutes=minutos)       # se bloquea: no hay contracargo
        else:
            reclama = f["patron"] == "fraude_amistoso" or rng.random() < 0.88
            if reclama:
                contracargo = 1
                dias = rng.randint(15, 110) if f["patron"] == "fraude_amistoso" else rng.randint(10, 90)
                confirmacion = ts + timedelta(days=dias)
            # si no reclama, el fraude nunca se confirma: queda sin etiqueta conocida
    return [int(revision), resultado, minutos, contracargo, dias,
            confirmacion.strftime("%Y-%m-%d %H:%M:%S") if confirmacion else None]


def nivel_riesgo(f, n24):
    s = 0.0
    s += 2 if f["ip"] in IP_ANONIMAS else 0
    s += {"Conocido": 0, "Nuevo": 1, "No reconocido": 3}[f["estado"]]
    s += 2 if n24 >= 5 else 0
    s += 1 if n24 >= 8 else 0
    s += 1 if f["monto"] > 1_500_000 else 0
    s += 1 if f["monto"] > 3_000_000 else 0
    s += 1 if f["ts"].hour < 6 else 0
    s += 1 if f["ciudad"] != f["home"] else 0
    s += rng.gauss(0, 0.8)
    return "Bajo" if s < 1.5 else "Medio" if s < 3 else "Alto" if s < 5 else "Crítico"


COLUMNAS_BASE = ["numero_transaccion", "fecha_pago", "dispositivo", "localizacion", "monto_cop", "detalle_pago",
                 "metodo_pago", "canal_pago", "tipo_ip", "estado_dispositivo", "transacciones_ultimas_24h",
                 "nivel_riesgo", "es_fraude", "id_cliente", "patron", "id_comercio"]
COLUMNAS_EXTRA = ["huella_dispositivo", "sistema_operativo", "navegador", "resolucion_pantalla", "zona_horaria_dispositivo",
                  "es_emulador", "ip_pais", "ip_ciudad_estimada", "distancia_ip_ciudad_km", "ip_es_datacenter", "ip_reputacion",
                  "kyc_nivel", "edad_cuenta_dias", "autenticacion_2fa", "intentos_fallidos_24h", "cambio_credenciales_7d",
                  "tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural"]
COLUMNAS_POST = ["revision_manual", "resultado_revision", "minutos_revision", "contracargo", "dias_hasta_contracargo",
                 "fecha_confirmacion_fraude"]
COLUMNAS = COLUMNAS_BASE + COLUMNAS_EXTRA + COLUMNAS_POST


def construir():
    filas = []
    for gen, q in CUOTAS_FRAUDE:
        filas += llenar(q, gen)
    for gen, q in CUOTAS_LEGIT:
        filas += llenar(q, gen)
    filas += ANCLAS
    faltan = TOTAL - len(filas)
    assert faltan > 0, "las cuotas superan el total"
    filas += llenar(faltan, f_normal)
    assert len(filas) == TOTAL, len(filas)
    filas.sort(key=lambda f: (f["ts"], f["id_cliente"]))

    por_cli = defaultdict(list)
    primera = {}
    for i, f in enumerate(filas):
        por_cli[f["id_cliente"]].append(i)
        primera.setdefault(f["id_cliente"], f["ts"])
    n24 = [0] * len(filas)
    for idx in por_cli.values():
        j = 0
        for k, i in enumerate(idx):
            while filas[idx[j]]["ts"] <= filas[i]["ts"] - timedelta(hours=24):
                j += 1
            n24[i] = k - j + 1
    ids = list(range(1, TOTAL + 1))
    rng.shuffle(ids)
    for i, f in enumerate(filas):
        f["n24"] = n24[i]
        f["nivel"] = nivel_riesgo(f, n24[i])
        f["id"] = f"TXN-{ids[i]:07d}"
    return filas, primera


def formato(v):
    return "" if v is None else v


def main():
    argumentos = [a for a in sys.argv[1:] if a != "--afinidad" and not a.startswith("--movilidad=")]
    salida = argumentos[0] if argumentos else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    filas, primera = construir()
    ruta = os.path.join(salida, "dataset_antifraude_1000000_transacciones" + ("_v2" if AFINIDAD else "")
                        + (f"_mov{int(round(MOVILIDAD * 100))}" if MOVILIDAD > 0 else "") + ".csv.gz")
    with open(ruta, "wb") as crudo:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=crudo, mtime=0, compresslevel=6)   # mtime=0: archivo reproducible
        texto = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        w = csv.writer(texto)
        w.writerow(COLUMNAS)
        for f in filas:
            if AFINIDAD:
                propias = pymes_del_cliente(f["id_cliente"])
                if f["fraude"] and rng.random() < 0.5:      # el fraude también cae en un comercio al azar (donde el cliente no ha comprado)
                    fraude_pyme = rng.choices(range(N_PYMES), cum_weights=_cum_fraude)[0]
                elif MOVILIDAD > 0 and rng.random() < MOVILIDAD:
                    fraude_pyme = rng.choices(range(N_PYMES), cum_weights=_cum_legit)[0]
                else:
                    fraude_pyme = rng.choice(propias)
            else:
                fraude_pyme = rng.choices(range(N_PYMES), cum_weights=_cum_fraude if f["fraude"] else _cum_legit)[0]
            extra = enriquecer(f, primera)
            post = post_pago(f)
            w.writerow([f["id"], f["ts"].strftime("%Y-%m-%d %H:%M:%S"), f["dispositivo"], f"{f['ciudad']}, Colombia", f["monto"],
                        f"{f['cat']} - compra realizada por {f['canal']}", f["metodo"], f["canal"], f["ip"], f["estado"],
                        f["n24"], f["nivel"], f["fraude"], f["id_cliente"], f["patron"], f"PYME-{fraude_pyme + 1:03d}"]
                       + [formato(v) for v in extra] + [formato(v) for v in post])
        texto.flush()
        texto.detach()
        gz.close()
    print("Escrito:", ruta, "| filas:", len(filas), "| fraudes:", sum(f["fraude"] for f in filas))


if __name__ == "__main__":
    main()
