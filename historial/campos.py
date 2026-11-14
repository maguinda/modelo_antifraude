"""Catálogo de los campos que entiende la API y cómo suelen llamarse en los archivos de los comercios.

`CAMPOS` se compara en una prueba con el contrato de la API: si la API gana un campo y aquí no se agrega, esa prueba falla.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..api import esquemas as es


def normalizar(texto) -> str:
    """Minúsculas, sin tildes y con todo lo que no sea letra o número convertido en un espacio: «Tarjeta_Crédito» → «tarjeta credito»."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


@dataclass(frozen=True)
class Campo:
    ruta: str                                   # como en la API: «dispositivo.tipo»
    tipo: str                                   # texto | fecha | monto | entero | decimal | bool | enum | ip
    requerido: bool
    ayuda: str
    opciones: Tuple[str, ...] = ()
    sinonimos: Tuple[str, ...] = ()


def _op(enum) -> Tuple[str, ...]:
    return tuple(e.value for e in enum)


CAMPOS: List[Campo] = [
    Campo("id_transaccion", "texto", True, "Identificador del pago en el sistema del comercio (número de pedido).", sinonimos=("pedido", "orden", "order", "order id", "id pedido", "transaccion", "transaction", "transaction id", "referencia", "numero pedido")),
    Campo("id_cliente", "texto", True, "Código interno del cliente (no documento, correo ni teléfono).", sinonimos=("cliente", "customer", "customer id", "id cliente", "usuario", "user id", "codigo cliente")),
    Campo("fecha_pago", "fecha", True, "Fecha y hora del pago. Para el historial es obligatoria: se usa para reproducir los pagos en orden.", sinonimos=("fecha", "fecha hora", "fecha pago", "created at", "date", "datetime", "timestamp", "fecha creacion")),
    Campo("monto_cop", "monto", True, "Monto en pesos colombianos.", sinonimos=("monto", "valor", "amount", "total", "importe", "valor total", "precio")),
    Campo("metodo_pago", "enum", True, "Cómo pagó.", _op(es.MetodoPago), ("metodo pago", "medio pago", "medio de pago", "payment method", "forma pago", "metodo")),
    Campo("canal_pago", "enum", True, "Desde dónde pagó.", _op(es.Canal), ("canal", "channel", "origen venta", "plataforma")),
    Campo("categoria", "enum", True, "Rubro de la compra.", _op(es.Categoria), ("categoria", "category", "rubro", "categoria producto", "tipo producto")),
    Campo("ciudad", "texto", True, "Ciudad declarada del comprador.", sinonimos=("ciudad", "city", "ciudad entrega", "ciudad envio", "municipio")),
    Campo("dispositivo.tipo", "enum", True, "Tipo de equipo desde el que pagó.", _op(es.TipoDispositivo), ("dispositivo", "device", "device type", "tipo dispositivo", "equipo")),
    Campo("dispositivo.estado", "enum", False, "Si el cliente ya había usado ese equipo.", _op(es.EstadoDispositivo), ("estado dispositivo", "device status")),
    Campo("dispositivo.huella", "texto", False, "Identificador del equipo (se convierte en un código irreversible).", sinonimos=("huella", "fingerprint", "device id", "id dispositivo", "huella dispositivo")),
    Campo("dispositivo.sistema_operativo", "texto", False, "Sistema operativo.", sinonimos=("sistema operativo", "os", "so")),
    Campo("dispositivo.navegador", "texto", False, "Navegador.", sinonimos=("navegador", "browser")),
    Campo("dispositivo.resolucion_pantalla", "texto", False, "Resolución como 390x844.", sinonimos=("resolucion", "pantalla", "screen", "screen resolution")),
    Campo("dispositivo.zona_horaria", "texto", False, "Zona horaria del equipo, como America/Bogota.", sinonimos=("zona horaria", "timezone", "tz")),
    Campo("dispositivo.es_emulador", "bool", False, "Si es un emulador.", sinonimos=("emulador", "emulator", "es emulador")),
    Campo("red.ip", "ip", False, "IP del comprador. No se guarda: solo una huella irreversible.", sinonimos=("ip", "ip cliente", "ip address", "direccion ip", "client ip", "ip comprador")),
    Campo("red.tipo_ip", "enum", False, "Clasificación de la IP, si el comercio ya la tiene.", _op(es.TipoIp), ("tipo ip", "ip type", "tipo conexion")),
    Campo("red.pais", "texto", False, "País donde se ubica la IP, si el comercio ya lo tiene.", sinonimos=("pais ip", "ip country", "pais")),
    Campo("red.ciudad_estimada", "texto", False, "Ciudad donde se ubica la IP, si el comercio ya la tiene.", sinonimos=("ciudad ip", "ip city")),
    Campo("red.distancia_a_ciudad_km", "decimal", False, "Distancia entre la IP y la ciudad declarada.", sinonimos=("distancia ip ciudad", "distancia km")),
    Campo("red.es_datacenter", "bool", False, "Si la IP es de un centro de datos.", sinonimos=("datacenter", "es datacenter", "centro de datos")),
    Campo("red.reputacion", "entero", False, "Reputación de la IP de 0 a 100.", sinonimos=("reputacion ip", "ip reputation", "ip score")),
    Campo("identidad.kyc_nivel", "enum", False, "Nivel de verificación del cliente.", _op(es.NivelKyc), ("kyc", "nivel kyc", "verificacion", "verificado", "nivel verificacion")),
    Campo("identidad.edad_cuenta_dias", "entero", False, "Antigüedad de la cuenta en días.", sinonimos=("edad cuenta", "antiguedad", "dias cuenta", "cliente desde dias", "account age")),
    Campo("identidad.autenticacion_2fa", "enum", False, "Resultado del doble factor.", _op(es.Autenticacion2fa), ("2fa", "autenticacion", "doble factor", "mfa")),
    Campo("identidad.intentos_fallidos_24h", "entero", False, "Intentos fallidos de ingreso en 24 h.", sinonimos=("intentos fallidos", "failed attempts", "intentos")),
    Campo("identidad.cambio_credenciales_7d", "bool", False, "Si cambió su contraseña o correo esta semana.", sinonimos=("cambio credenciales", "cambio clave", "password changed")),
    Campo("comportamiento.tiempo_formulario_seg", "decimal", False, "Segundos que tardó en llenar el formulario.", sinonimos=("tiempo formulario", "form time", "segundos formulario")),
    Campo("comportamiento.velocidad_tecleo_ms", "decimal", False, "Milisegundos promedio entre teclas.", sinonimos=("velocidad tecleo", "typing speed")),
    Campo("comportamiento.pego_numero_tarjeta", "bool", False, "Si pegó (no tecleó) el número de tarjeta.", sinonimos=("pego tarjeta", "paste card", "pegado")),
    Campo("comportamiento.mouse_natural", "decimal", False, "0 = movimiento de bot, 1 = humano.", sinonimos=("mouse natural", "mouse score", "human score")),
]
POR_RUTA: Dict[str, Campo] = {c.ruta: c for c in CAMPOS}
REQUERIDOS = [c.ruta for c in CAMPOS if c.requerido]


def rutas_de_la_api() -> List[str]:
    """Todas las rutas «hoja» del contrato de la API (para comprobar que este catálogo no quedó desactualizado)."""
    salida = []
    for nombre, f in es.SolicitudEvaluacion.model_fields.items():
        sub = f.annotation
        sub = getattr(sub, "__args__", (sub,))[0] if getattr(sub, "__args__", None) else sub
        if hasattr(sub, "model_fields"):
            salida += [f"{nombre}.{n}" for n in sub.model_fields]
        else:
            salida.append(nombre)
    return salida
