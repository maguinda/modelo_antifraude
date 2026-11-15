"""Ajustes del servicio. Todo lo sensible viene de variables de entorno, nunca del código."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import config
from .repositorios.motor_pg import es_direccion_pg

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
    bd_url: Optional[str] = field(
        default=None, repr=False
    )  # dirección de PostgreSQL (MODELO_IA_BD_URL o MODELO_IA_BD_URL_ARCHIVO); si está, manda sobre ruta_bd
    pepper: str = PEPPER_DE_DESARROLLO  # secreto que protege las claves guardadas (MODELO_IA_PEPPER)
    ruta_modelo: Optional[Path] = None  # por defecto, el modelo actual (MODELO_IA_MODELO)
    limite_peticiones_por_minuto: int = 600  # por clave de API
    tamano_maximo_bytes: int = 64 * 1024  # tamaño máximo de una petición
    max_historial_cliente: int = 500  # compras previas del cliente que se leen
    max_historial_huella: int = 1000  # compras previas de una huella que se leen
    dias_pasado_maximo: int = 30  # no se aceptan pagos más viejos que esto
    minutos_futuro_maximo: int = 5  # ni más adelantados que esto
    monto_maximo_cop: int = 200_000_000
    durabilidad: str = "NORMAL"  # NORMAL (rápida) o FULL (fuerza cada guardado a disco; más lenta)
    hsts_segundos: int = 0  # HSTS solo por HTTPS; 0 = apagado (en localhost el navegador lo recordaría meses)
    exigir_acuerdo: bool = (
        False  # si es True, un comercio sin acuerdo de datos vigente no puede evaluar pagos (MODELO_IA_EXIGIR_ACUERDO)
    )
    proveedor_ip: str = (
        "simulado"  # quién ubica y evalúa las IP (ver proveedores_ip.py); el simulado es solo de demostración
    )
    servir_pantallas: bool = True  # si es True, esta API también entrega las pantallas de la carpeta frontend/ (MODELO_IA_PANTALLAS=0 las deja a otro servidor web)
    csp_modo: str = "report-only"  # política de contenido de /docs: 'report-only' (observa) o 'enforce' (bloquea)
    exigir_mfa: bool = (
        False  # si es True, las acciones de administrador que cambian algo piden el código TOTP (MODELO_IA_EXIGIR_MFA)
    )
    limite_claves_invalidas: int = (
        20  # claves inventadas por minuto y por IP antes de responder 429 (MODELO_IA_LIMITE_INVALIDAS)
    )
    proxies_confiables: str = (
        ""  # IP o redes (separadas por coma) cuyo X-Forwarded-For se acepta (MODELO_IA_PROXY_CONFIABLE)
    )
    exigir_registro_modelos: bool = False  # si es True, solo arranca con un modelo EN USO del registro y con la huella verificada (MODELO_IA_EXIGIR_REGISTRO)
    deriva_min_muestra: int = (
        500  # pagos mínimos en la ventana para dar un estado de deriva (MODELO_IA_DERIVA_MIN_MUESTRA)
    )
    log_formato: str = "auto"  # registros: 'json' (una línea JSON por registro), 'texto' o 'auto' (json en producción, texto en desarrollo)

    @property
    def destino_bd(self) -> Any:
        """Lo que se le pasa al Almacen: la dirección de PostgreSQL si hay, o la ruta del archivo de SQLite."""
        return self.bd_url if self.bd_url else self.ruta_bd

    @classmethod
    def desde_entorno(cls) -> "Ajustes":
        a = cls()
        a.ruta_bd = Path(os.environ.get("MODELO_IA_BD", a.ruta_bd))
        if os.environ.get("MODELO_IA_BD_URL_ARCHIVO"):  # la dirección lleva la contraseña: por archivo, como el pepper
            a.bd_url = _leer_secreto_de_archivo(os.environ["MODELO_IA_BD_URL_ARCHIVO"])
        elif os.environ.get("MODELO_IA_BD_URL"):
            a.bd_url = os.environ["MODELO_IA_BD_URL"].strip()
        if a.bd_url is not None and not es_direccion_pg(a.bd_url):
            raise ValueError(
                "MODELO_IA_BD_URL debe empezar por postgresql:// (la base de SQLite se elige con MODELO_IA_BD)."
            )
        if a.bd_url is not None and os.environ.get("MODELO_IA_BD"):
            raise ValueError("Elige UNA base: MODELO_IA_BD (SQLite) o MODELO_IA_BD_URL (PostgreSQL), no las dos.")
        a.pepper = os.environ.get("MODELO_IA_PEPPER", a.pepper)
        if os.environ.get("MODELO_IA_PEPPER_ARCHIVO"):  # manda sobre la variable directa
            a.pepper = _leer_secreto_de_archivo(os.environ["MODELO_IA_PEPPER_ARCHIVO"])
        if os.environ.get("MODELO_IA_MODELO"):
            a.ruta_modelo = Path(os.environ["MODELO_IA_MODELO"])
        a.limite_peticiones_por_minuto = int(os.environ.get("MODELO_IA_LIMITE", a.limite_peticiones_por_minuto))
        a.csp_modo = os.environ.get("MODELO_IA_CSP", a.csp_modo)
        if os.environ.get("MODELO_IA_PANTALLAS", "").strip().lower() in ("0", "false", "no", "off"):
            a.servir_pantallas = False
        a.log_formato = os.environ.get("MODELO_IA_LOG_FORMATO", a.log_formato).strip().lower()
        a.exigir_registro_modelos = os.environ.get("MODELO_IA_EXIGIR_REGISTRO", "").strip().lower() in (
            "1",
            "true",
            "si",
            "sí",
            "yes",
        )
        a.deriva_min_muestra = int(os.environ.get("MODELO_IA_DERIVA_MIN_MUESTRA", a.deriva_min_muestra))
        a.exigir_mfa = os.environ.get("MODELO_IA_EXIGIR_MFA", "").strip().lower() in ("1", "true", "si", "sí", "yes")
        a.limite_claves_invalidas = int(os.environ.get("MODELO_IA_LIMITE_INVALIDAS", a.limite_claves_invalidas))
        a.proxies_confiables = os.environ.get("MODELO_IA_PROXY_CONFIABLE", a.proxies_confiables).strip()
        a.proveedor_ip = os.environ.get("MODELO_IA_PROVEEDOR_IP", a.proveedor_ip)
        a.exigir_acuerdo = os.environ.get("MODELO_IA_EXIGIR_ACUERDO", "").strip().lower() in (
            "1",
            "true",
            "si",
            "sí",
            "yes",
        )
        a.hsts_segundos = int(os.environ.get("MODELO_IA_HSTS", a.hsts_segundos))
        a.durabilidad = os.environ.get("MODELO_IA_DURABILIDAD", a.durabilidad)
        return a

    def redes_proxy(self) -> tuple:
        """Las redes de proxies confiables ya interpretadas. Un valor mal escrito es un error de configuración: el servicio no debe arrancar."""
        from .ip_cliente import redes_confiables

        return redes_confiables(self.proxies_confiables)

    def problemas_de_produccion(self) -> dict:
        """Lo que está mal para un servidor real. «bloqueantes» impiden arrancar; «avisos» se muestran pero se puede seguir."""
        bloqueantes, avisos = [], []
        if self.pepper_inseguro:
            bloqueantes.append(
                "Se está usando el secreto de desarrollo. Define MODELO_IA_PEPPER_ARCHIVO (o MODELO_IA_PEPPER) con un secreto propio."
            )
        elif len(self.pepper) < PEPPER_LARGO_MINIMO:
            bloqueantes.append(f"El secreto es demasiado corto (mínimo {PEPPER_LARGO_MINIMO} caracteres).")
        if (
            self.bd_url
            and "sslmode=" not in self.bd_url
            and not re.search(r"@(localhost|127\.0\.0\.1|\[::1\])[:/]", self.bd_url)
        ):
            avisos.append(
                "La dirección de PostgreSQL no lleva sslmode: la conexión puede ir sin cifrar. Fuera de tu propia máquina usa ?sslmode=verify-full (con sslrootcert)."
            )
        if self.durabilidad != "FULL":
            avisos.append(
                "MODELO_IA_DURABILIDAD no es FULL: si se va la luz puede perderse lo último que se guardó. En producción conviene FULL."
            )
        if not self.exigir_acuerdo:
            avisos.append(
                "MODELO_IA_EXIGIR_ACUERDO no está activo: se aceptan pagos de comercios sin acuerdo de datos vigente. Actívalo cuando empiece el piloto."
            )
        if self.proveedor_ip == "simulado":
            avisos.append(
                "El proveedor de IP es SIMULADO: no ubica IP reales. Configura uno real antes de evaluar pagos de verdad."
            )
        if not self.exigir_mfa:
            avisos.append(
                "MODELO_IA_EXIGIR_MFA no está activo: las acciones de administrador (crear o suspender comercios, acuerdos) piden solo la clave. "
                "Activa el segundo factor de cada administrador («admin activar-mfa») y luego esta opción."
            )
        if not self.exigir_registro_modelos:
            avisos.append(
                "MODELO_IA_EXIGIR_REGISTRO no está activo: si no hay un modelo registrado, se usa modelo_actual.joblib SIN verificar su huella (un .joblib se carga ejecutando código). "
                "Registra los modelos («admin modelo-adoptar») y activa esta opción."
            )
        if not self.proxies_confiables:
            avisos.append(
                "MODELO_IA_PROXY_CONFIABLE está vacío: detrás de un proxy (Caddy) todas las peticiones parecen venir de la misma IP, y el límite por IP "
                "de claves inválidas no distingue a nadie. Indica la red del proxy."
            )
        if self.csp_modo != "enforce":
            avisos.append(
                "La política de contenido está en modo «solo observar» (MODELO_IA_CSP). Pásala a enforce cuando lleve unos días sin avisos."
            )
        return {"bloqueantes": bloqueantes, "avisos": avisos}

    @property
    def pepper_inseguro(self) -> bool:
        return self.pepper == PEPPER_DE_DESARROLLO
