"""Proveedor de IP «propio»: ubica y clasifica una IP con bases públicas descargadas (nada sale del servidor, la IP no viaja a ningún tercero).

Qué sabe hacer, y con qué confianza:
  - País y operador (ASN): bases DB-IP Lite (CC BY 4.0: exige crédito; se da en el panel y la documentación).  Confianza: buena para país, regular para operador.
  - Ciudad: solo se usa para mostrarla. Las bases gratuitas ubican mal las IP de celular colombianas (casi todo cae en la capital), así que dentro de
    Colombia NO se entregan coordenadas y la distancia a la ciudad declarada no se calcula (el modelo recibe 0). Fuera de Colombia sí, porque ahí lo que
    importa es el país.
  - Centro de datos: rangos publicados por AWS y Google Cloud, más una lista corta de operadores de alojamiento por el nombre de su ASN (heurística).
  - Salidas de Tor: lista pública del Proyecto Tor.  Se tratan como PROXY.
  - NO sabe: si una conexión es móvil o residencial (la base de ASN solo da el nombre del operador: todo operador normal sale RESIDENCIAL), ni detecta VPN
    comerciales (salvo que estén en rangos de nube o en alojamiento conocido), ni tiene reputación global.
  - La «reputación» es una ESTIMACIÓN FIJA por categoría (normal 80, centro de datos 45, Tor 20, sin datos 50), no una reputación real. Se reemplazará por la
    experiencia propia (intentos fallidos y clientes distintos por IP) y/o un proveedor de pago.
Las bases se bajan con:  python -m modelo_ia.api.ip_datos descargar
"""

import ipaddress
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple, Union

from .ip_datos import CARPETA, FUENTES, _manifiesto
from .proveedores_ip import InfoIP

REPUTACION = {"normal": 80, "centro_de_datos": 45, "tor": 20, "sin_datos": 50}
# Operadores de alojamiento/nube conocidos (se busca en el nombre de la organización del ASN). Heurística: una IP de estos NO es de una persona en su casa.
ALOJAMIENTO = (
    "AMAZON",
    "GOOGLE",
    "MICROSOFT",
    "DIGITALOCEAN",
    "OVH",
    "HETZNER",
    "LINODE",
    "AKAMAI CONNECTED CLOUD",
    "VULTR",
    "CHOOPA",
    "M247",
    "LEASEWEB",
    "CONTABO",
    "ORACLE",
    "ALIBABA",
    "TENCENT",
    "SCALEWAY",
    "CLOUDFLARE",
    "DATACAMP",
    "PACKET",
    "EQUINIX",
    "COLOCROSSING",
    "HOSTINGER",
    "GODADDY",
    "NAMECHEAP",
    "IONOS",
    "RACKSPACE",
    "FASTLY",
    "HOSTING",
    "DATACENTER",
    "DATA CENTER",
    "SERVERS",
    "VPS",
)
LIMITE_ANTIGUEDAD_DIAS = 60
# Nombres cortos de uso corriente (los de la base son los formales de la ONU: «Estados Unidos de América (los)»). Los demás se limpian quitando el paréntesis.
NOMBRES_PAIS = {
    "CO": "Colombia",
    "US": "Estados Unidos",
    "RU": "Rusia",
    "NL": "Países Bajos",
    "GB": "Reino Unido",
    "KR": "Corea del Sur",
    "VE": "Venezuela",
    "BO": "Bolivia",
    "CN": "China",
    "DE": "Alemania",
    "FR": "Francia",
    "ES": "España",
    "MX": "México",
    "BR": "Brasil",
    "AR": "Argentina",
    "PE": "Perú",
    "EC": "Ecuador",
    "CL": "Chile",
    "PA": "Panamá",
    "IN": "India",
    "NG": "Nigeria",
    "UA": "Ucrania",
    "TR": "Turquía",
    "VN": "Vietnam",
    "ID": "Indonesia",
}


def _registro(dato: Any) -> Dict[str, Any]:
    """Un registro de la base como diccionario; si la base trae algo inesperado (un archivo dañado), se trata como vacío en vez de fallar."""
    return dato if isinstance(dato, dict) else {}


def _nombre_pais(iso: Optional[str], nombres: Dict[str, str]) -> str:
    if iso in NOMBRES_PAIS:
        return NOMBRES_PAIS[iso]
    base = nombres.get("es") or nombres.get("en")
    return re.sub(r"\s*\((el|la|los|las)\)$", "", base) if base else "Desconocido"


def _red_a_clave(red: ipaddress._BaseNetwork) -> Tuple[int, int, int]:
    return red.version, red.prefixlen, int(red.network_address)


class ProveedorPropio:
    nombre = "propio"

    def __init__(self, carpeta: Optional[Path] = None):
        import maxminddb

        self.carpeta = Path(carpeta or CARPETA)
        falta = [f["archivo"] for f in FUENTES.values() if not (self.carpeta / f["archivo"]).exists()]
        if falta:
            raise ValueError(
                f"Faltan las bases de IP ({', '.join(falta)}). Bájalas con: python -m modelo_ia.api.ip_datos descargar"
            )
        self._ciudad = maxminddb.open_database(str(self.carpeta / FUENTES["ciudad"]["archivo"]))
        self._asn = maxminddb.open_database(str(self.carpeta / FUENTES["asn"]["archivo"]))
        self._nube: Dict[Tuple[int, int], Set[int]] = {}
        for archivo, campos in (
            (FUENTES["aws"]["archivo"], ("ip_prefix", "ipv6_prefix")),
            (FUENTES["google"]["archivo"], ("ipv4Prefix", "ipv6Prefix")),
        ):
            doc = json.loads((self.carpeta / archivo).read_text(encoding="utf-8"))
            for p in doc.get("prefixes", []) + doc.get("ipv6_prefixes", []):
                texto = next((p[c] for c in campos if c in p), None)
                if texto:
                    v, largo, base = _red_a_clave(ipaddress.ip_network(texto, strict=False))
                    self._nube.setdefault((v, largo), set()).add(base)
        self._tor = {
            str(ipaddress.ip_address(l.strip()))
            for l in (self.carpeta / FUENTES["tor"]["archivo"]).read_text(encoding="utf-8").splitlines()
            if l.strip()
        }
        man = _manifiesto(self.carpeta)
        fechas = [date.fromisoformat(m["descargado"][:10]) for m in man.values() if "descargado" in m]
        self.antiguedad_dias = (date.today() - min(fechas)).days if fechas else None

    def _en_nube(self, a: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
        entero, bits = int(a), a.max_prefixlen
        for (version, largo), bases in self._nube.items():
            if version == a.version and ((entero >> (bits - largo)) << (bits - largo)) in bases:
                return True
        return False

    def consultar(self, ip: str) -> InfoIP:
        a = ipaddress.ip_address(ip)
        if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_unspecified or a.is_multicast:
            return InfoIP("DESCONOCIDA", "Desconocido", None, False, 30)
        ciudad, asn = _registro(self._ciudad.get(str(a))), _registro(self._asn.get(str(a)))
        iso = _registro(ciudad.get("country")).get("iso_code")
        nombres = _registro(_registro(ciudad.get("country")).get("names"))
        pais = _nombre_pais(iso, nombres)
        nombres_ciudad = _registro(_registro(ciudad.get("city")).get("names"))
        nombre_ciudad = nombres_ciudad.get("es") or nombres_ciudad.get("en")
        loc = _registro(ciudad.get("location"))
        lat, lon = (
            (loc.get("latitude"), loc.get("longitude")) if (iso != "CO" and iso is not None) else (None, None)
        )  # ciudad poco fiable dentro de Colombia
        org = str(asn.get("autonomous_system_organization", "")).upper()
        con_asn = bool(
            asn.get("autonomous_system_number") or asn.get("autonomous_system_organization")
        )  # un registro sin número ni nombre de operador no cuenta
        if str(a) in self._tor:
            return InfoIP("PROXY", pais, nombre_ciudad, True, REPUTACION["tor"], lat, lon)
        if self._en_nube(a) or any(k in org for k in ALOJAMIENTO):
            return InfoIP("PROXY", pais, nombre_ciudad, True, REPUTACION["centro_de_datos"], lat, lon)
        if not con_asn and iso is None:
            return InfoIP("DESCONOCIDA", "Desconocido", None, False, REPUTACION["sin_datos"])
        return InfoIP(
            "RESIDENCIAL" if con_asn else "DESCONOCIDA",
            pais,
            nombre_ciudad,
            False,
            REPUTACION["normal"] if con_asn else REPUTACION["sin_datos"],
            lat,
            lon,
        )
