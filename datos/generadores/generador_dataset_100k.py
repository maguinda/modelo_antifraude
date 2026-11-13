#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador del conjunto de datos SINTÉTICO de 100.000 transacciones
(detección de fraude en pagos de pymes de comercio electrónico).

- Todo es inventado: no hay datos de personas reales.
- Semilla fija (42): al correrlo otra vez sale exactamente el mismo archivo.
- Mismas 13 columnas del ejemplo de 500 transacciones + 2 al final:
  id_cliente (permite ver el historial de un mismo cliente) y patron
  (qué tipo de fraude o de caso legítimo es; NO es una entrada del modelo).
- nivel_riesgo se calcula SOLO con las señales de la transacción más ruido,
  nunca con es_fraude. Aun así es una alerta derivada: si el modelo la usa como
  entrada hay que decirlo en la exposición.
- Las reglas del fraude las define este script: el modelo aprenderá estas
  reglas, no el fraude real.

Uso:  python3 generador_dataset_100k.py [carpeta_de_salida]
"""
import csv
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta

SEMILLA = 42
TOTAL = 100_000
FRAUDES = 4_000          # 4 % (el estudio citado habla de 4,1 %)
INICIO = datetime(2026, 1, 1)
FIN = datetime(2026, 10, 31, 23, 59, 59)

rng = random.Random(SEMILLA)

CIUDADES = {"Bogotá": 30, "Medellín": 15, "Cali": 12, "Barranquilla": 8,
            "Cartagena": 6, "Bucaramanga": 6, "Pereira": 5, "Manizales": 4,
            "Santa Marta": 5, "Ibagué": 5}
DISPOSITIVOS = ["MacBook", "Tablet Android", "Android", "iPhone", "Windows PC"]
METODOS = ["PayPal", "Visa", "Daviplata", "PSE", "Mastercard", "Nequi"]
CANALES = ["App móvil", "API", "POS", "Web"]
# categoría -> multiplicador del monto típico
CATEGORIAS = {"Electrónica": 2.2, "Farmacia": 0.5, "Hotel": 2.5,
              "Restaurante": 0.4, "Ropa": 0.9, "Servicios públicos": 0.8,
              "Supermercado": 0.9, "Suscripción digital": 0.15,
              "Tienda online": 1.0, "Transporte": 0.35}
IP_ANONIMAS = ["VPN", "Proxy", "IP desconocida"]
# peso de cada hora del día para las compras normales
HORA_W = [0.3, 0.2, 0.15, 0.15, 0.2, 0.4, 1, 2, 3, 4, 4.5, 5, 5.5, 5, 4.5,
          4.5, 5, 5.5, 6, 6, 5, 4, 2.5, 1]
DIAS = (FIN - INICIO).days + 1


# ----------------------------------------------------------------- utilidades
def elegir(d):
    return rng.choices(list(d), weights=list(d.values()))[0]


def hora_normal():
    return rng.choices(range(24), weights=HORA_W)[0]


def ts_en(dia, hora, minuto=None, segundo=None):
    return INICIO + timedelta(days=dia, hours=hora,
                              minutes=rng.randint(0, 59) if minuto is None else minuto,
                              seconds=rng.randint(0, 59) if segundo is None else segundo)


def ts_aleatorio(margen_h=36, hora=None):
    """Fecha y hora al azar, dejando un margen antes del final del periodo."""
    while True:
        dia = rng.randrange(DIAS)
        h = hora_normal() if hora is None else hora
        t = ts_en(dia, h)
        if t <= FIN - timedelta(hours=margen_h):
            return t


class Cliente:
    __slots__ = ("id", "ciudad", "disp", "metodo", "escala", "canales", "peso")

    def __init__(self, id_, ciudad, disp, metodo, escala, canales, peso):
        self.id, self.ciudad, self.disp = id_, ciudad, disp
        self.metodo, self.escala, self.canales, self.peso = metodo, escala, canales, peso


def crear_clientes(n):
    out = []
    for i in range(1, n + 1):
        disp = rng.sample(DISPOSITIVOS, rng.choice([1, 2, 2]))
        canales = {"App móvil": 35, "Web": 30, "POS": 25, "API": 10}
        out.append(Cliente(f"CLI-{i:05d}", elegir(CIUDADES), disp,
                           rng.choice(METODOS),
                           300_000 * math.exp(rng.gauss(0, 0.55)),
                           canales, rng.gammavariate(1.5, 1.0)))
    return out


def crear_comercios(n):
    out = []
    for i in range(1, n + 1):
        out.append(Cliente(f"COM-{i:04d}", elegir(CIUDADES),
                           rng.sample(["Windows PC", "MacBook"], 1),
                           rng.choice(["Visa", "Mastercard", "PSE"]),
                           150_000 * math.exp(rng.gauss(0, 0.4)),
                           {"API": 60, "POS": 40}, 1.0))
    return out


CLIENTES = crear_clientes(12_000)
COMERCIOS = crear_comercios(150)
_acum, _s = [], 0.0
for _c in CLIENTES:
    _s += _c.peso
    _acum.append(_s)


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
        "ts": ts, "id_cliente": cli.id, "home": cli.ciudad,
        "dispositivo": kw.get("disp") or rng.choice(cli.disp),
        "ciudad": kw.get("ciudad") or cli.ciudad,
        "monto": kw["monto"] if kw.get("monto") is not None else monto_normal(cli, cat),
        "cat": cat, "metodo": metodo, "canal": canal,
        "ip": kw.get("ip") or ip_normal(canal),
        "estado": kw.get("estado") or "Conocido",
        "fraude": fraude, "patron": patron,
    }


def llenar(cuota, generador):
    """Llama al generador (que devuelve una lista de filas) hasta completar la cuota exacta."""
    filas = []
    while len(filas) < cuota:
        filas.extend(generador(cuota - len(filas))[: cuota - len(filas)])
    return filas


# ------------------------------------------------------------ casos legítimos
def legit_ip_anonima(restante):
    c = cliente_al_azar()
    ip = rng.choices(IP_ANONIMAS, [70, 15, 15])[0]
    return [fila(c, ts_aleatorio(), patron="ip_anonima_legitima", ip=ip)]


def legit_viajero(restante):
    c = cliente_al_azar()
    ciudad = otra_ciudad(c.ciudad)
    base = ts_aleatorio(margen_h=120)
    out = []
    for _ in range(rng.randint(2, 4)):
        t = base + timedelta(hours=rng.uniform(0, 90))
        cat = rng.choice(["Hotel", "Transporte", "Restaurante", "Tienda online", "Ropa"])
        out.append(fila(c, t, patron="viajero_legitimo", ciudad=ciudad, cat=cat,
                        monto=monto_normal(c, cat, rng.uniform(1.0, 1.8)),
                        estado=rng.choices(["Conocido", "Nuevo"], [85, 15])[0]))
    return out


def legit_celular_nuevo(restante):
    c = cliente_al_azar()
    d = disp_nuevo(c)
    base = ts_aleatorio(margen_h=96)
    return [fila(c, base + timedelta(hours=rng.uniform(0, 70)), patron="celular_nuevo",
                 disp=d, estado="Nuevo") for _ in range(rng.randint(1, 3))]


def legit_no_reconocido(restante):
    c = cliente_al_azar()
    return [fila(c, ts_aleatorio(), patron="dispositivo_no_reconocido_legitimo",
                 disp=disp_nuevo(c), estado="No reconocido")]


def _dias_quincena():
    out = []
    for d in range(DIAS):
        f = INICIO + timedelta(days=d)
        if f.day in (1, 14, 15, 16, 29, 30, 31) and f < FIN - timedelta(days=1):
            out.append(d)
    return out


DIAS_QUINCENA = _dias_quincena()


def legit_pico_quincena(restante):
    c = cliente_al_azar()
    dia = rng.choice(DIAS_QUINCENA)
    out = []
    for _ in range(rng.randint(3, 7)):
        t = ts_en(dia, rng.randint(9, 20))
        cat = rng.choice(list(CATEGORIAS))
        out.append(fila(c, t, patron="pico_quincena", cat=cat,
                        monto=monto_normal(c, cat, 1.3)))
    return out


def legit_comercio_masivo(restante):
    c = rng.choice(COMERCIOS)
    dia = rng.randrange(DIAS - 2)
    out = []
    for _ in range(rng.randint(5, 25)):
        cat = rng.choice(["Tienda online", "Supermercado", "Farmacia", "Ropa", "Restaurante"])
        out.append(fila(c, ts_en(dia, rng.randint(8, 20)), patron="comercio_api_masivo",
                        cat=cat, ip="Empresarial",
                        monto=monto_normal(c, cat)))
    return out


def legit_compra_grande(restante):
    c = cliente_al_azar()
    cat = rng.choice(["Hotel", "Electrónica", "Servicios públicos", "Tienda online", "Ropa"])
    monto = int(math.exp(rng.uniform(math.log(1_500_000), math.log(8_000_000))))
    return [fila(c, ts_aleatorio(hora=rng.randint(9, 20)), patron="compra_grande_legitima",
                 cat=cat, monto=monto)]


def legit_noctambulo(restante):
    c = cliente_al_azar()
    h = rng.choices(range(6), [2, 3, 3, 3, 2, 1])[0]
    cat = rng.choice(["Suscripción digital", "Tienda online", "Restaurante", "Transporte"])
    return [fila(c, ts_aleatorio(hora=h), patron="noctambulo_legitimo", cat=cat)]


def legit_suscripcion(restante):
    c = cliente_al_azar()
    monto = int(round(rng.uniform(9_000, 90_000) / 100) * 100)
    dia_mes, hora = rng.randint(1, 28), rng.randint(0, 23)
    canal = rng.choice(["Web", "App móvil"])
    ip = rng.choice(["Residencial", "Móvil"])
    mes0, n = rng.randint(1, 4), rng.randint(4, 8)
    out = []
    for k in range(n):
        mes = mes0 + k
        if mes > 10:
            break
        t = datetime(2026, mes, dia_mes, hora, rng.randint(0, 59), rng.randint(0, 59))
        out.append(fila(c, t, patron="suscripcion_recurrente", cat="Suscripción digital",
                        canal=canal, ip=ip, monto=monto, metodo=c.metodo))
    return out


def legit_duplicado(restante):
    c = cliente_al_azar()
    cat = rng.choice(list(CATEGORIAS))
    t = ts_aleatorio()
    f1 = fila(c, t, patron="cobro_duplicado", cat=cat)
    f2 = dict(f1, ts=t + timedelta(seconds=rng.randint(40, 300)))
    return [f1, f2]


def legit_limite(restante):
    c = cliente_al_azar()
    tipo = rng.randrange(5)
    if tipo == 0:
        return [fila(c, ts_aleatorio(), patron="caso_limite", monto=1_000)]
    if tipo == 1:
        return [fila(c, ts_aleatorio(hora=rng.randint(9, 20)), patron="caso_limite",
                     cat=rng.choice(["Hotel", "Electrónica"]),
                     monto=rng.randint(12_000_000, 20_000_000))]
    if tipo == 2:
        return [fila(c, ts_en(rng.randrange(DIAS - 2), 0, 0, 0), patron="caso_limite")]
    if tipo == 3:
        return [fila(c, ts_en(rng.randrange(DIAS - 2), 23, 59, 59), patron="caso_limite")]
    combo = rng.choice([("PayPal", "POS"), ("Nequi", "API"), ("PSE", "POS"), ("Daviplata", "POS")])
    return [fila(c, ts_aleatorio(), patron="caso_limite", metodo=combo[0], canal=combo[1])]


# ------------------------------------------------------------------- fraudes
def f_rafaga(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23))
    estado = rng.choices(["Nuevo", "No reconocido"], [60, 40])[0]
    d = disp_nuevo(c)
    anon = rng.random() < 0.5
    ciudad = c.ciudad if rng.random() < 0.5 else otra_ciudad(c.ciudad)
    out = []
    for _ in range(min(rng.randint(5, 10), max(restante, 5))):
        t += timedelta(minutes=rng.uniform(1, 6))
        out.append(fila(c, t, 1, "rafaga_velocidad", disp=d, estado=estado, ciudad=ciudad,
                        canal=rng.choice(["Web", "API", "App móvil"]),
                        ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil"]),
                        monto=int(rng.uniform(80_000, 1_200_000))))
    return out


def f_prueba_tarjeta(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23))
    d = disp_nuevo(c)
    metodo = rng.choice(["Visa", "Mastercard", "PayPal"])
    ip = rng.choice(IP_ANONIMAS + ["Residencial"])
    out = []
    for _ in range(rng.randint(5, 10)):
        t += timedelta(seconds=rng.randint(20, 120))
        out.append(fila(c, t, 1, "prueba_tarjeta", disp=d, estado="Nuevo", metodo=metodo,
                        canal=rng.choice(["Web", "API"]), ip=ip,
                        cat=rng.choice(["Tienda online", "Suscripción digital", "Electrónica"]),
                        monto=rng.randint(1_000, 25_000)))
    t += timedelta(seconds=rng.randint(30, 150))
    out.append(fila(c, t, 1, "prueba_tarjeta", disp=d, estado="Nuevo", metodo=metodo,
                    canal=rng.choice(["Web", "API"]), ip=ip,
                    cat=rng.choice(["Tienda online", "Electrónica"]),
                    monto=rng.randint(600_000, 3_000_000)))
    return out


def f_toma_cuenta(restante):
    c = cliente_al_azar()
    t = ts_aleatorio(hora=rng.randint(0, 23))
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
                        ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil"]),
                        monto=monto))
    return out


ANCLAS = []


def f_viaje_imposible(restante):
    c = cliente_al_azar()
    t = ts_aleatorio()
    ancla = fila(c, t, 0, "normal", estado="Conocido")      # compra legítima en su ciudad
    ANCLAS.append(ancla)
    t2 = t + timedelta(minutes=rng.uniform(5, 45))
    return [fila(c, t2, 1, "viaje_imposible", ciudad=otra_ciudad(c.ciudad), disp=disp_nuevo(c),
                 estado="Nuevo", ip=rng.choice(["Residencial", "Móvil", "IP desconocida"]),
                 monto=int(min(max(c.escala * rng.uniform(1, 4), 100_000), 4_000_000)))]


def f_madrugada(restante):
    c = cliente_al_azar()
    h = rng.randint(0, 5)
    estado = rng.choices(["Nuevo", "No reconocido", "Conocido"], [60, 20, 20])[0]
    anon = rng.random() < 0.4
    monto = int(math.exp(rng.uniform(math.log(1_500_000), math.log(6_000_000))))
    return [fila(c, ts_aleatorio(hora=h), 1, "madrugada_monto_alto", monto=monto,
                 disp=disp_nuevo(c) if estado != "Conocido" else None, estado=estado,
                 canal=rng.choice(["Web", "API"]),
                 cat=rng.choice(["Electrónica", "Tienda online", "Hotel", "Ropa"]),
                 ciudad=c.ciudad if rng.random() < 0.6 else otra_ciudad(c.ciudad),
                 ip=rng.choice(IP_ANONIMAS) if anon else rng.choice(["Residencial", "Móvil", "Empresarial"]))]


def f_ip_anonima(restante):
    c = cliente_al_azar()
    estado = rng.choices(["Conocido", "Nuevo", "No reconocido"], [40, 50, 10])[0]
    cat = rng.choice(list(CATEGORIAS))
    d = None if estado == "Conocido" else disp_nuevo(c)
    return [fila(c, ts_aleatorio(), 1, "ip_anonima", ip=rng.choice(IP_ANONIMAS), disp=d,
                 estado=estado, cat=cat,
                 ciudad=c.ciudad if rng.random() < 0.7 else otra_ciudad(c.ciudad),
                 monto=max(monto_normal(c, cat, 1.5), 100_000))]


def f_disimulado(restante):
    c = cliente_al_azar()
    cat = rng.choices(list(CATEGORIAS),
                      [4 if k in ("Tienda online", "Suscripción digital", "Electrónica") else 1
                       for k in CATEGORIAS])[0]
    estado = rng.choices(["Conocido", "Nuevo"], [60, 40])[0]
    return [fila(c, ts_aleatorio(), 1, "fraude_disimulado", cat=cat, estado=estado,
                 disp=None if estado == "Conocido" else disp_nuevo(c),
                 canal=rng.choices(["Web", "API", "App móvil"], [45, 25, 30])[0],
                 metodo=rng.choice(["Visa", "Mastercard", "PayPal"]) if rng.random() < 0.7 else None,
                 ip=rng.choice(["Residencial", "Móvil"]),
                 monto=monto_normal(c, cat, rng.uniform(0.8, 1.5)))]


def f_normal(restante):
    """Compra normal; a veces con 1 o 2 compras más en las siguientes 24 h."""
    c = cliente_al_azar()
    t = ts_aleatorio()
    estado = rng.choices(["Conocido", "Nuevo"], [95, 5])[0]
    out = [fila(c, t, patron="normal", estado=estado,
                disp=disp_nuevo(c) if estado == "Nuevo" else None)]
    if rng.random() < 0.22:
        for _ in range(rng.choice([1, 1, 2])):
            out.append(fila(c, t + timedelta(hours=rng.uniform(0.3, 20)), patron="normal"))
    return out


# ------------------------------------------------------------------ armado
CUOTAS_LEGIT = [
    (legit_ip_anonima, 2000), (legit_viajero, 1500), (legit_celular_nuevo, 2500),
    (legit_no_reconocido, 200), (legit_pico_quincena, 4000), (legit_comercio_masivo, 3000),
    (legit_compra_grande, 3000), (legit_noctambulo, 2500), (legit_suscripcion, 4000),
    (legit_duplicado, 800), (legit_limite, 300),
]
CUOTAS_FRAUDE = [
    (f_rafaga, 700), (f_prueba_tarjeta, 500), (f_toma_cuenta, 800), (f_viaje_imposible, 400),
    (f_madrugada, 500), (f_ip_anonima, 400), (f_disimulado, 700),
]
assert sum(q for _, q in CUOTAS_FRAUDE) == FRAUDES


def nivel_riesgo(f, n24):
    """Alerta derivada de señales de la transacción + ruido. Nunca usa es_fraude."""
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


def construir():
    filas = []
    for gen, q in CUOTAS_FRAUDE:
        filas += llenar(q, gen)
    for gen, q in CUOTAS_LEGIT:
        filas += llenar(q, gen)
    filas += ANCLAS                      # compras legítimas previas al viaje imposible
    faltan = TOTAL - len(filas)
    assert faltan > 0, "las cuotas superan el total"
    filas += llenar(faltan, f_normal)
    assert len(filas) == TOTAL, len(filas)

    filas.sort(key=lambda f: (f["ts"], f["id_cliente"]))
    # transacciones del mismo cliente en las últimas 24 h (incluye la actual)
    por_cli = defaultdict(list)
    for i, f in enumerate(filas):
        por_cli[f["id_cliente"]].append(i)
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
        f["id"] = f"TXN-{ids[i]:06d}"
    return filas


COLUMNAS = ["numero_transaccion", "fecha_pago", "dispositivo", "localizacion", "monto_cop",
            "detalle_pago", "metodo_pago", "canal_pago", "tipo_ip", "estado_dispositivo",
            "transacciones_ultimas_24h", "nivel_riesgo", "es_fraude", "id_cliente", "patron"]


def main():
    salida = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    filas = construir()
    ruta = os.path.join(salida, "dataset_antifraude_100000_transacciones.csv")
    with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNAS)
        for f in filas:
            w.writerow([f["id"], f["ts"].strftime("%Y-%m-%d %H:%M:%S"), f["dispositivo"],
                        f"{f['ciudad']}, Colombia", f["monto"],
                        f"{f['cat']} - compra realizada por {f['canal']}", f["metodo"],
                        f["canal"], f["ip"], f["estado"], f["n24"], f["nivel"],
                        f["fraude"], f["id_cliente"], f["patron"]])
    print("Escrito:", ruta, "| filas:", len(filas), "| fraudes:", sum(f["fraude"] for f in filas))


if __name__ == "__main__":
    main()
