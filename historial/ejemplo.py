"""Un archivo de pagos INVENTADO de una tienda imaginaria, con sus propios nombres de columna y formatos (como los traería un comercio real).

Sirve para probar el adaptador de punta a punta sin datos de nadie. Tiene fraude plantado (VPN, cuentas nuevas, montos altos) con ruido para que no sea
perfecto, algunas filas con problemas a propósito (ciudad vacía, un número de tarjeta metido en un campo) y las IP son de los rangos de documentación
que el proveedor simulado sí reconoce.
"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

COLUMNAS = ["pedido", "cliente", "fecha_hora", "valor", "medio_pago", "canal", "categoria_producto", "ciudad_entrega", "ip_cliente", "equipo", "so", "navegador",
            "cliente_desde_dias", "verificado", "contracargo"]
MEDIOS = [("Tarjeta crédito Visa", 38), ("Tarjeta crédito Mastercard", 22), ("PSE", 20), ("Nequi", 15), ("Daviplata", 5)]
CATEGORIAS = [("Ropa", 30), ("Electrónica", 20), ("Farmacia", 15), ("Hogar y tienda", 25), ("Viajes", 10)]
CIUDADES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga"]
EQUIPOS = [("iPhone", "iOS", "Safari"), ("Android", "Android", "Chrome"), ("PC Windows", "Windows", "Chrome"), ("Mac", "macOS", "Safari")]


def mapeo_del_ejemplo() -> Dict:
    return {
        "descripcion": "Mapeo del archivo de ejemplo (tienda inventada).",
        "formato": {"separador": ";", "codificacion": "utf-8-sig", "hoja": None, "decimal": ",", "zona_horaria_origen": "America/Bogota", "formato_fecha": "%d/%m/%Y %H:%M", "escala_monto": 1},
        "columnas": {"id_transaccion": "pedido", "id_cliente": "cliente", "fecha_pago": "fecha_hora", "monto_cop": "valor", "metodo_pago": "medio_pago", "canal_pago": "canal",
                     "categoria": "categoria_producto", "ciudad": "ciudad_entrega", "red.ip": "ip_cliente", "dispositivo.tipo": "equipo", "dispositivo.sistema_operativo": "so",
                     "dispositivo.navegador": "navegador", "identidad.edad_cuenta_dias": "cliente_desde_dias", "identidad.kyc_nivel": "verificado"},
        "valores": {"metodo_pago": {"tarjeta crédito visa": "VISA", "tarjeta crédito mastercard": "MASTERCARD", "pse": "PSE", "nequi": "NEQUI", "daviplata": "DAVIPLATA"},
                    "canal_pago": {"sitio web": "WEB", "app": "APP_MOVIL"},
                    "categoria": {"ropa": "ROPA", "electrónica": "ELECTRONICA", "farmacia": "FARMACIA", "hogar y tienda": "TIENDA_ONLINE", "viajes": "TRANSPORTE"},
                    "dispositivo.tipo": {"iphone": "IPHONE", "android": "ANDROID", "pc windows": "WINDOWS_PC", "mac": "MACBOOK"},
                    "identidad.kyc_nivel": {"sí": "COMPLETO", "no": "BASICO"}},
        "por_defecto": {}, "resultado": {"columna": "contracargo", "fraude": ["1"], "legitima": ["0"], "fecha_columna": ""}}


def _elegir(rng, pares):
    return rng.choices([p[0] for p in pares], weights=[p[1] for p in pares])[0]


def generar(carpeta: Path, n: int = 3000, semilla: int = 11, dias: int = 200, fin: datetime = datetime(2026, 10, 31, 18, 0)) -> Dict[str, Path]:
    rng = random.Random(semilla)
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
    clientes = [f"CLI-{i:05d}" for i in range(max(200, n // 6))]
    antig = {c: rng.choice([5, 30, 90, 200, 400, 800, 1500]) for c in clientes}
    ip_de = {c: f"{rng.choice(['192.0.2', '198.51.100'])}.{rng.randint(1, 250)}" for c in clientes}
    equipo_de = {c: rng.choice(EQUIPOS) for c in clientes}
    filas = []
    for i in range(n):
        t = fin - timedelta(seconds=rng.randint(0, dias * 86400))
        fraude = rng.random() < 0.035
        c = rng.choice(clientes)
        monto = int(min(rng.lognormvariate(11.2, 0.8), 2_500_000))
        ip, eq, edad = ip_de[c], equipo_de[c], antig[c]
        if fraude:
            monto = int(min(monto * rng.uniform(1.5, 4), 4_800_000))
            if rng.random() < 0.7:
                c = f"CLI-N{rng.randint(1, 400):04d}"; edad = rng.choice([0, 1, 2, 3]); eq = rng.choice(EQUIPOS)
            ip = rng.choice([f"203.0.113.{rng.randint(64, 127)}", f"203.0.113.{rng.randint(128, 191)}", f"203.0.113.{rng.randint(192, 250)}", ip_de[rng.choice(clientes)]]) if rng.random() < 0.8 else ip
            t = t.replace(hour=rng.choice([1, 2, 3, 4, 23, 12, 15]))
        elif rng.random() < 0.06:
            ip = f"203.0.113.{rng.randint(1, 63)}"               # legítimo desde una oficina o una VPN de empresa: ruido
        filas.append([f"PED-{100000 + i}", c, t.strftime("%d/%m/%Y %H:%M"), f"$ {monto:,}".replace(",", "."), _elegir(rng, MEDIOS), rng.choice(["Sitio web", "Sitio web", "App"]),
                      _elegir(rng, CATEGORIAS), rng.choice(CIUDADES), ip, eq[0], eq[1], eq[2], edad, "sí" if edad > 60 and not fraude else "no", "1" if fraude else "0"])
    filas[3][7] = ""                                            # a propósito: una ciudad vacía
    filas[7][7] = "Número 4111 1111 1111 1111"                    # a propósito: un número de tarjeta (de prueba) metido donde no debe estar
    filas[11][7] = "4242 4242 4242 4242"
    archivo, mapeo = carpeta / "pagos_tienda_ejemplo.csv", carpeta / "mapeo_ejemplo.json"
    with open(archivo, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(COLUMNAS); w.writerows(filas)
    from .mapeo import guardar
    guardar(mapeo_del_ejemplo(), mapeo)
    return {"archivo": archivo, "mapeo": mapeo}
