"""Ajustes del servicio. Todo lo sensible viene de variables de entorno, nunca del código."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .. import config

PEPPER_DE_DESARROLLO = "pepper-de-desarrollo-cambiar-en-produccion"
PEPPER_LARGO_MINIMO = 32
ENTORNO_PRODUCCION = "produccion"


def _leer_secreto_de_archivo(ruta: str) -> str:
    """Lee un secreto de un archivo (p. ej. un «secret» de Docker), para que no viaje en una variable de entorno que `docker inspect` mostraría."""
    try:
        valor = Path(ruta).read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError(f"No se pudo leer el archivo del secreto (MODELO_IA_PEPPER_ARCHIVO): {Path(ruta).name}")
    if not valor:
        raise RuntimeError("El archivo del secreto (MODELO_IA_PEPPER_ARCHIVO) está vacío.")
    return valor


@dataclass
class Ajustes:
    ruta_bd: Path = config.RAIZ / "api" / "datos_api.sqlite3"
    pepper: str = PEPPER_DE_DESARROLLO                 # secreto que protege las claves guardadas (MODELO_IA_PEPPER)
    ruta_modelo: Optional[Path] = None                 # por defecto, el modelo actual (MODELO_IA_MODELO)
    limite_peticiones_por_minuto: int = 600            # por clave de API
    tamano_maximo_bytes: int = 64 * 1024               # tamaño máximo de una petición
    max_historial_cliente: int = 500                   # compras previas del cliente que se leen
    max_historial_huella: int = 1000                   # compras previas de una huella que se leen
    dias_pasado_maximo: int = 30                       # no se aceptan pagos más viejos que esto
    minutos_futuro_maximo: int = 5                     # ni más adelantados que esto
    monto_maximo_cop: int = 200_000_000
    durabilidad: str = "NORMAL"                        # NORMAL (rápida) o FULL (fuerza cada guardado a disco; más lenta)
    hsts_segundos: int = 0                             # HSTS solo por HTTPS; 0 = apagado (en localhost el navegador lo recordaría meses)
    proveedor_ip: str = "simulado"                     # quién ubica y evalúa las IP (ver proveedores_ip.py); el simulado es solo de demostración
    csp_modo: str = "report-only"                     # política de contenido de /docs: 'report-only' (observa) o 'enforce' (bloquea)

    @classmethod
    def desde_entorno(cls) -> "Ajustes":
        a = cls()
        a.ruta_bd = Path(os.environ.get("MODELO_IA_BD", a.ruta_bd))
        a.pepper = os.environ.get("MODELO_IA_PEPPER", a.pepper)
        if os.environ.get("MODELO_IA_PEPPER_ARCHIVO"):                       # manda sobre la variable directa
            a.pepper = _leer_secreto_de_archivo(os.environ["MODELO_IA_PEPPER_ARCHIVO"])
        if os.environ.get("MODELO_IA_MODELO"):
            a.ruta_modelo = Path(os.environ["MODELO_IA_MODELO"])
        a.limite_peticiones_por_minuto = int(os.environ.get("MODELO_IA_LIMITE", a.limite_peticiones_por_minuto))
        a.csp_modo = os.environ.get("MODELO_IA_CSP", a.csp_modo)
        a.proveedor_ip = os.environ.get("MODELO_IA_PROVEEDOR_IP", a.proveedor_ip)
        a.hsts_segundos = int(os.environ.get("MODELO_IA_HSTS", a.hsts_segundos))
        a.durabilidad = os.environ.get("MODELO_IA_DURABILIDAD", a.durabilidad)
        return a

    def problemas_de_produccion(self) -> dict:
        """Lo que está mal para un servidor real. «bloqueantes» impiden arrancar; «avisos» se muestran pero se puede seguir."""
        bloqueantes, avisos = [], []
        if self.pepper_inseguro:
            bloqueantes.append("Se está usando el secreto de desarrollo. Define MODELO_IA_PEPPER_ARCHIVO (o MODELO_IA_PEPPER) con un secreto propio.")
        elif len(self.pepper) < PEPPER_LARGO_MINIMO:
            bloqueantes.append(f"El secreto es demasiado corto (mínimo {PEPPER_LARGO_MINIMO} caracteres).")
        if self.durabilidad != "FULL":
            avisos.append("MODELO_IA_DURABILIDAD no es FULL: si se va la luz puede perderse lo último que se guardó. En producción conviene FULL.")
        if self.proveedor_ip == "simulado":
            avisos.append("El proveedor de IP es SIMULADO: no ubica IP reales. Configura uno real antes de evaluar pagos de verdad.")
        if self.csp_modo != "enforce":
            avisos.append("La política de contenido está en modo «solo observar» (MODELO_IA_CSP). Pásala a enforce cuando lleve unos días sin avisos.")
        return {"bloqueantes": bloqueantes, "avisos": avisos}

    @property
    def pepper_inseguro(self) -> bool:
        return self.pepper == PEPPER_DE_DESARROLLO
