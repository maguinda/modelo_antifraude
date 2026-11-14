"""Todo lo que usa el secreto del servicio (el «pepper»): hash de las claves y huellas irreversibles de IP y dispositivo."""

import hashlib
import hmac
import ipaddress
from typing import Optional


class Seudonimizador:
    def __init__(self, pepper: bytes):
        self.pepper = pepper

    def hash_clave(self, clave: str) -> str:
        return hmac.new(self.pepper, clave.encode("utf-8"), hashlib.sha256).hexdigest()

    def huella_ip(self, ip: str) -> str:
        """Huella IRREVERSIBLE de una IP (HMAC con el secreto del servicio): sirve para reconocer repeticiones sin guardar la IP."""
        return hmac.new(
            self.pepper, ("ip|" + str(ipaddress.ip_address(ip))).encode("utf-8"), hashlib.sha256
        ).hexdigest()[:16]

    def codigo_ip(self, ip_huella: Optional[str]) -> Optional[str]:
        """Código corto (8 caracteres) de la huella de una IP, para agrupar y mostrar sin revelar la IP."""
        return str(ip_huella)[:8].upper() if ip_huella else None

    def codigo_dispositivo(self, huella: str) -> str:
        """Código corto e irreversible de la huella del dispositivo (la huella real no se muestra)."""
        return hmac.new(self.pepper, ("disp|" + str(huella)).encode("utf-8"), hashlib.sha256).hexdigest()[:8].upper()
