"""Traducción entre los códigos del contrato de la API (ASCII, en mayúsculas) y los nombres que usa el modelo."""
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")

CANAL = {"WEB": "Web", "APP_MOVIL": "App móvil", "POS": "POS", "API": "API"}
METODO_PAGO = {"VISA": "Visa", "MASTERCARD": "Mastercard", "PSE": "PSE", "NEQUI": "Nequi",
               "DAVIPLATA": "Daviplata", "PAYPAL": "PayPal"}
CATEGORIA = {"ELECTRONICA": "Electrónica", "FARMACIA": "Farmacia", "HOTEL": "Hotel", "RESTAURANTE": "Restaurante",
             "ROPA": "Ropa", "SERVICIOS_PUBLICOS": "Servicios públicos", "SUPERMERCADO": "Supermercado",
             "SUSCRIPCION_DIGITAL": "Suscripción digital", "TIENDA_ONLINE": "Tienda online",
             "TRANSPORTE": "Transporte", "OTRA": "Otra"}
TIPO_IP = {"RESIDENCIAL": "Residencial", "MOVIL": "Móvil", "EMPRESARIAL": "Empresarial", "VPN": "VPN",
           "PROXY": "Proxy", "DESCONOCIDA": "IP desconocida"}
ESTADO_DISPOSITIVO = {"CONOCIDO": "Conocido", "NUEVO": "Nuevo", "NO_RECONOCIDO": "No reconocido"}
TIPO_DISPOSITIVO = {"IPHONE": "iPhone", "ANDROID": "Android", "TABLET_ANDROID": "Tablet Android",
                    "WINDOWS_PC": "Windows PC", "MACBOOK": "MacBook"}
NIVEL_KYC = {"COMPLETO": "Completo", "BASICO": "Básico", "SIN_VERIFICAR": "Sin verificar"}
AUTENTICACION_2FA = {"NO_EXIGIDA": "No exigida", "EXIGIDA_APROBADA": "Exigida-aprobada",
                     "EXIGIDA_FALLIDA": "Exigida-fallida", "NO_DISPONIBLE": "No disponible", "NO_APLICA": "No aplica"}

# Decisión del modelo (interna) -> decisión de la API
DECISION_API = {"APROBAR": "APROBADA", "REVISAR": "EN_REVISION", "BLOQUEAR": "RECHAZADA"}


def a_hora_bogota(fecha: Optional[datetime], ahora: datetime) -> datetime:
    """Devuelve la fecha como hora local de Bogotá sin zona (como se entrenó el modelo)."""
    if fecha is None:
        return ahora
    if fecha.tzinfo is not None:
        return fecha.astimezone(BOGOTA).replace(tzinfo=None)
    return fecha


def _v(x, tabla=None):
    if x is None:
        return None
    x = x.value if hasattr(x, "value") else x
    return tabla[x] if tabla else x


def red_de(sol) -> Dict:
    """Los datos de red tal como los mandó el comercio (sin la IP)."""
    r = sol.red
    return {"tipo_ip": r.tipo_ip, "pais": r.pais, "ciudad_estimada": r.ciudad_estimada, "distancia_a_ciudad_km": r.distancia_a_ciudad_km,
            "es_datacenter": r.es_datacenter, "reputacion": r.reputacion}


def fila_cruda(sol, fecha: datetime, red: Optional[Dict] = None) -> Dict:
    """Pasa la solicitud a las columnas con las que se entrenó el modelo (None = no informado).

    `red`: datos de red ya completados por la API (si el comercio mandó la IP); por defecto, los que mandó el comercio."""
    d, i, c = sol.dispositivo, sol.identidad, sol.comportamiento
    r = red if red is not None else red_de(sol)
    b = lambda x: None if x is None else int(bool(x))
    return {
        "id_cliente": sol.id_cliente, "fecha": fecha.isoformat(), "mes": fecha.month, "hora": fecha.hour,
        "localizacion": f"{sol.ciudad}, Colombia", "monto_cop": int(sol.monto_cop),
        "dispositivo": _v(d.tipo, TIPO_DISPOSITIVO), "estado_dispositivo": _v(d.estado, ESTADO_DISPOSITIVO),
        "tipo_ip": _v(r["tipo_ip"], TIPO_IP), "canal_pago": _v(sol.canal_pago, CANAL),
        "metodo_pago": _v(sol.metodo_pago, METODO_PAGO), "categoria": _v(sol.categoria, CATEGORIA),
        "huella_dispositivo": d.huella, "sistema_operativo": d.sistema_operativo, "navegador": d.navegador,
        "resolucion_pantalla": d.resolucion_pantalla, "zona_horaria_dispositivo": d.zona_horaria,
        "es_emulador": b(d.es_emulador),
        "ip_pais": r["pais"], "ip_ciudad_estimada": r["ciudad_estimada"], "distancia_ip_ciudad_km": r["distancia_a_ciudad_km"],
        "ip_es_datacenter": b(r["es_datacenter"]), "ip_reputacion": r["reputacion"],
        "kyc_nivel": _v(i.kyc_nivel, NIVEL_KYC) if i else None,
        "edad_cuenta_dias": i.edad_cuenta_dias if i else None,
        "autenticacion_2fa": _v(i.autenticacion_2fa, AUTENTICACION_2FA) if i else None,
        "intentos_fallidos_24h": i.intentos_fallidos_24h if i else None,
        "cambio_credenciales_7d": b(i.cambio_credenciales_7d) if i else None,
        "tiempo_formulario_seg": c.tiempo_formulario_seg if c else None,
        "velocidad_tecleo_ms": c.velocidad_tecleo_ms if c else None,
        "pego_numero_tarjeta": b(c.pego_numero_tarjeta) if c else None,
        "mouse_natural": c.mouse_natural if c else None,
    }


# Valores neutros para guardar un pago al que le faltan campos opcionales (así el historial no se rompe).
# Solo se usan para lo que se guarda: si el modelo en uso necesita un campo que no llegó, el pago NO se
# puntúa con el modelo (queda en revisión) y estos valores nunca influyen en su decisión.
NEUTROS = {"huella_dispositivo": None, "sistema_operativo": "Desconocido", "navegador": "Desconocido",
           "resolucion_pantalla": "Desconocida", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0,
           "ip_pais": "Desconocido", "ip_ciudad_estimada": "Desconocida", "tipo_ip": "IP desconocida", "estado_dispositivo": "Conocido", "ip_es_datacenter": 0,
           "ip_reputacion": 50, "kyc_nivel": "Sin verificar", "edad_cuenta_dias": 365,
           "autenticacion_2fa": "No disponible", "intentos_fallidos_24h": 0, "cambio_credenciales_7d": 0}


def con_neutros(fila: Dict, id_unico: str) -> Dict:
    f = dict(fila)
    por_defecto = dict(NEUTROS, huella_dispositivo=f"SIN-{id_unico}")
    for k, v in por_defecto.items():
        if f.get(k) is None:
            f[k] = v
    return f
