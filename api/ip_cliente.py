"""La IP real de quien llama, para el límite por IP de claves inválidas.

Detrás de un proxy (Caddy) la conexión llega desde el proxy y la IP del cliente viaja en `X-Forwarded-For`. Esa cabecera SOLO se cree si la conexión
viene de un proxy que se declaró confiable (`MODELO_IA_PROXY_CONFIABLE`): si no, cualquiera podría escribir la IP que quisiera. Con varios proxies se
toma la primera dirección, de derecha a izquierda, que NO sea de un proxy confiable (la de más a la izquierda la puede escribir el cliente).

La IP solo vive en memoria para contar intentos: no se guarda ni se escribe en ningún registro.
"""

import ipaddress
from typing import Optional, Sequence, Tuple, Union

Red = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
DESCONOCIDA = "desconocida"


def redes_confiables(texto: str) -> Tuple[Red, ...]:
    """«10.0.0.0/8, 172.16.0.0/12, 203.0.113.5» → redes. Un valor inválido lanza ValueError (que el servicio no arranque mal configurado)."""
    redes = []
    for trozo in (texto or "").split(","):
        trozo = trozo.strip()
        if trozo:
            try:
                redes.append(ipaddress.ip_network(trozo, strict=False))
            except ValueError:
                raise ValueError(
                    f"MODELO_IA_PROXY_CONFIABLE tiene un valor que no es una IP ni una red: «{trozo[:40]}»"
                )
    return tuple(redes)


def _ip(texto: Optional[str]) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    try:
        ip = ipaddress.ip_address((texto or "").strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def _agrupar(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> str:
    """Una IPv6 se agrupa por su red /64 (quien tiene una IPv6 tiene millones de direcciones: contarlas una a una no limitaría nada)."""
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _es_confiable(ip, confiables: Sequence[Red]) -> bool:
    return any(ip.version == red.version and ip in red for red in confiables)


def ip_del_cliente(par: Optional[str], reenviada: Optional[str], confiables: Sequence[Red]) -> str:
    """La IP (agrupada) a la que se le cuentan los intentos, o «desconocida» si no hay forma de saberla (en ese caso no se limita)."""
    origen = _ip(par)
    if origen is None:
        return DESCONOCIDA
    if reenviada and confiables and _es_confiable(origen, confiables):
        for trozo in reversed([t for t in reenviada.split(",")]):
            candidata = _ip(trozo)
            if candidata is None:  # una entrada que no es una IP: no se confía en nada de la cabecera
                break
            if not _es_confiable(candidata, confiables):
                return _agrupar(candidata)
    return _agrupar(origen)
