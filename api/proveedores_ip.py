"""Inteligencia de IP: de una dirección IP a «dónde está, qué tipo de conexión es y qué reputación tiene».

El comercio solo manda `red.ip` (la IP que ve su servidor). La API la ubica con un PROVEEDOR y completa los datos de red que el modelo
necesita. La IP cruda se usa en memoria y NO se guarda, ni se devuelve, ni se escribe en los registros (solo queda una huella irreversible).

Para usar un proveedor real, se escribe una clase con `nombre` y `consultar(ip) -> InfoIP`, se agrega a `PROVEEDORES` y se elige con
MODELO_IA_PROVEEDOR_IP. El que viene aquí (`simulado`) es de DEMOSTRACIÓN: reconoce unos rangos de IP reservados para documentación y
no sabe nada de IPs reales de internet.
"""
import ipaddress
import math
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

TIPOS = ("RESIDENCIAL", "MOVIL", "EMPRESARIAL", "VPN", "PROXY", "DESCONOCIDA")


@dataclass(frozen=True)
class InfoIP:
    tipo_ip: str                         # uno de TIPOS
    pais: str
    ciudad_estimada: Optional[str]
    es_datacenter: bool
    reputacion: int                      # 0 a 100 (100 = muy buena)
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class ProveedorIP(Protocol):
    nombre: str

    def consultar(self, ip: str) -> InfoIP:
        ...


# Coordenadas para calcular la distancia entre la ciudad declarada por el comprador y la ciudad donde se ubica su IP
CIUDADES: Dict[str, Tuple[float, float]] = {
    "bogota": (4.711, -74.072), "medellin": (6.244, -75.581), "cali": (3.451, -76.532), "barranquilla": (10.968, -74.781),
    "cartagena": (10.391, -75.479), "bucaramanga": (7.119, -73.122), "pereira": (4.814, -75.696), "manizales": (5.070, -75.514),
    "santa marta": (11.241, -74.199), "ibague": (4.438, -75.232),
}


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.strip().lower()) if unicodedata.category(c) != "Mn")


def _haversine(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def distancia_km(ciudad_declarada: str, info: InfoIP) -> Optional[int]:
    """Distancia entre la ciudad que declara el comprador y la ciudad de su IP (None si no se puede calcular)."""
    origen = CIUDADES.get(_norm(ciudad_declarada))
    if origen is None or info.latitud is None or info.longitud is None:
        return None
    return int(round(_haversine(origen, (info.latitud, info.longitud))))


class ProveedorSimulado:
    """DEMOSTRACIÓN. Reconoce solo los rangos de IP reservados para documentación (RFC 5737) y las redes privadas."""
    nombre = "simulado"

    def consultar(self, ip: str) -> InfoIP:
        a = ipaddress.ip_address(ip)
        if a.version == 4:
            ultimo = int(str(a).split(".")[-1])
            if a in ipaddress.ip_network("192.0.2.0/24"):
                return InfoIP("RESIDENCIAL", "Colombia", "Bogotá", False, 88, *CIUDADES["bogota"])
            if a in ipaddress.ip_network("198.51.100.0/24"):
                return InfoIP("MOVIL", "Colombia", "Medellín", False, 88, *CIUDADES["medellin"])
            if a in ipaddress.ip_network("203.0.113.0/24"):
                if ultimo < 64:
                    return InfoIP("EMPRESARIAL", "Colombia", "Bogotá", False, 85, *CIUDADES["bogota"])
                if ultimo < 128:
                    return InfoIP("VPN", "Estados Unidos", "Ashburn", True, 55, 39.04, -77.49)
                if ultimo < 192:
                    return InfoIP("PROXY", "Países Bajos", "Ámsterdam", True, 40, 52.37, 4.90)
                return InfoIP("VPN", "Rusia", "Moscú", True, 35, 55.76, 37.62)
        if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_unspecified:
            return InfoIP("DESCONOCIDA", "Desconocido", None, False, 30)
        return InfoIP("DESCONOCIDA", "Desconocido", None, False, 30)          # IP pública que el simulador no conoce


PROVEEDORES = {"simulado": ProveedorSimulado}


def crear_proveedor(nombre: str) -> ProveedorIP:
    if nombre not in PROVEEDORES:
        raise ValueError(f"Proveedor de IP desconocido '{nombre}'. Opciones: {list(PROVEEDORES)}")
    return PROVEEDORES[nombre]()
