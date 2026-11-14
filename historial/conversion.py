"""Convierte una fila del archivo del comercio en una solicitud de la API, y dice por qué se rechaza si no se puede.

Regla de privacidad: los motivos de rechazo hablan de CAMPOS y de tipos de error, nunca repiten el valor de un cliente.
Los identificadores del cliente, del pedido y del equipo se convierten por defecto en códigos irreversibles antes de entrar al sistema.
"""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..api import esquemas as es
from .campos import CAMPOS, POR_RUTA, normalizar

FORMATOS_FECHA = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y")
BOGOTA = ZoneInfo("America/Bogota")
VERDADERO = {"1", "si", "s", "true", "t", "yes", "y", "verdadero", "x"}
FALSO = {"0", "no", "n", "false", "f", "falso"}


@dataclass
class Fila:
    numero: int
    solicitud: Optional[Dict[str, Any]] = None
    motivo: Optional[str] = None
    fraude: Optional[bool] = None                     # None = sin etiqueta
    fecha: Optional[datetime] = None
    monto: int = 0
    avisos: List[str] = field(default_factory=list)   # campos que se descartaron pero no impidieron usar la fila


class Convertidor:
    def __init__(self, mapeo: Dict, seudonimizar: bool = True):
        self.m, self.seud = mapeo, seudonimizar
        self.clave = os.urandom(32)                   # solo vive durante esta ejecución: los códigos no se pueden reconstruir después
        self.f = mapeo["formato"]
        self.valores_sin_traducir: Dict[str, Dict[str, int]] = {}
        self.res = mapeo["resultado"]
        self._fraude = {normalizar(x) for x in self.res.get("fraude", [])}
        self._legit = {normalizar(x) for x in self.res.get("legitima", [])}

    # ---------------------------------------------------------------- valores sueltos
    def _codigo(self, valor: str, prefijo: str, largo: int = 18) -> str:
        if not self.seud:
            return re.sub(r"[^A-Za-z0-9_\-:.]", "_", valor)[:64]
        # Solo letras: un código con dígitos podría, por casualidad, parecer un número de tarjeta y la API lo rechazaría.
        n = int.from_bytes(hmac.new(self.clave, valor.encode("utf-8"), hashlib.sha256).digest(), "big")
        letras = ""
        for _ in range(largo):
            n, r = divmod(n, 26); letras += chr(65 + r)
        return prefijo + letras

    def _fecha(self, t: str) -> Optional[datetime]:
        t = t.strip()
        if not t:
            return None
        candidatos = ([self.f["formato_fecha"]] if self.f.get("formato_fecha") else []) + list(FORMATOS_FECHA)
        d = None
        for fmt in candidatos:
            try:
                d = datetime.strptime(t, fmt); break
            except ValueError:
                continue
        if d is None:
            try:
                d = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                return None
        if d.tzinfo is None:
            origen = self.f.get("zona_horaria_origen") or "America/Bogota"
            d = d.replace(tzinfo=timezone.utc if origen.upper() == "UTC" else ZoneInfo(origen))
        return d.astimezone(BOGOTA).replace(tzinfo=None)

    def _numero(self, t: str) -> Optional[float]:
        t = re.sub(r"[^\d,.\-]", "", t.strip())
        if not t:
            return None
        dec = self.f.get("decimal", ".")
        miles = "," if dec == "." else "."
        t = t.replace(miles, "")
        if dec == ",":
            t = t.replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return None

    @staticmethod
    def _booleano(t: str) -> Optional[bool]:
        n = normalizar(t)
        return True if n in VERDADERO else False if n in FALSO else None

    def _enum(self, ruta: str, t: str) -> Optional[str]:
        n = normalizar(t)
        directo = {normalizar(o): o for o in POR_RUTA[ruta].opciones}
        tabla = self.m["valores"].get(ruta, {})
        if n in tabla:
            return tabla[n]
        if n in directo:
            return directo[n]
        d = self.valores_sin_traducir.setdefault(ruta, {})
        if len(d) < 30 or n in d:
            d[n] = d.get(n, 0) + 1
        return None

    # ---------------------------------------------------------------- una fila
    def _leer(self, fila: Dict[str, str], ruta: str) -> Optional[str]:
        col = self.m["columnas"].get(ruta)
        v = fila.get(col, "").strip() if col else ""
        if v == "" and ruta in self.m["por_defecto"]:
            return str(self.m["por_defecto"][ruta])
        return v if v != "" else None

    def convertir(self, numero: int, fila: Dict[str, str]) -> Fila:
        out = Fila(numero)
        s: Dict[str, Any] = {"moneda": "COP", "dispositivo": {}, "red": {}, "identidad": {}, "comportamiento": {}}

        def poner(ruta: str, valor):
            if "." in ruta:
                g, k = ruta.split(".", 1); s[g][k] = valor
            else:
                s[ruta] = valor
        for campo in CAMPOS:
            t = self._leer(fila, campo.ruta)
            if t is None:
                if campo.requerido and campo.ruta != "red.ip":
                    out.motivo = f"falta {campo.ruta}"; return out
                continue
            if campo.tipo == "texto":
                if campo.ruta == "id_transaccion":
                    t = self._codigo(t, "T")
                elif campo.ruta == "id_cliente":
                    t = self._codigo(t, "C")
                elif campo.ruta == "dispositivo.huella":
                    t = self._codigo(t, "H", 16)
                poner(campo.ruta, t)
            elif campo.tipo == "fecha":
                d = self._fecha(t)
                if d is None:
                    out.motivo = "fecha no válida"; return out
                s["fecha_pago"] = d.isoformat(); out.fecha = d
            elif campo.tipo == "monto":
                x = self._numero(t)
                if x is None or x * float(self.f.get("escala_monto", 1)) < 1:
                    out.motivo = "monto no válido"; return out
                out.monto = int(x * float(self.f.get("escala_monto", 1)) + 0.5); s["monto_cop"] = out.monto             # redondeo normal (Python redondea 0,5 hacia el par)
            elif campo.tipo == "enum":
                v = self._enum(campo.ruta, t)
                if v is None:
                    if campo.requerido:
                        out.motivo = f"valor no reconocido en {campo.ruta}"; return out
                    out.avisos.append(campo.ruta); continue
                poner(campo.ruta, v)
            elif campo.tipo == "bool":
                b = self._booleano(t)
                if b is None:
                    out.avisos.append(campo.ruta); continue
                poner(campo.ruta, b)
            elif campo.tipo in ("entero", "decimal"):
                x = self._numero(t)
                if x is None:
                    out.avisos.append(campo.ruta); continue
                poner(campo.ruta, int(round(x)) if campo.tipo == "entero" else x)
            elif campo.tipo == "ip":
                poner(campo.ruta, t)
        for g in ("identidad", "comportamiento"):
            if not s[g]:
                s.pop(g)
        # etiqueta del resultado (no entra a la solicitud: solo sirve para medir)
        col = self.res.get("columna")
        if col:
            n = normalizar(fila.get(col, ""))
            out.fraude = True if n in self._fraude else False if n in self._legit else None
        # la API decide si la fila es válida (incluido el rechazo de números de tarjeta)
        try:
            es.SolicitudEvaluacion.model_validate(s)
        except ValidationError as e:
            rutas = sorted({".".join(str(x) for x in err["loc"] if x != "__root__") for err in e.errors()})
            if any(es.MENSAJE_TARJETA[:30] in str(err.get("msg", "")) for err in e.errors()):
                out.motivo = "contiene un número de tarjeta"
                return out
            if "red.ip" in rutas and (s["red"].get("tipo_ip") or s["red"].get("pais")):         # IP inválida pero hay otra información de red: se descarta la IP
                s["red"].pop("ip", None); out.avisos.append("red.ip")
                try:
                    es.SolicitudEvaluacion.model_validate(s); out.solicitud = s; return out
                except ValidationError:
                    pass
            if "red.ip" in rutas or "red" in rutas:
                s["red"].pop("ip", None); out.avisos.append("red.ip")
                out.motivo = "sin datos de red utilizables"
                return out
            out.motivo = "datos inválidos en: " + ", ".join(rutas)[:120]
            return out
        out.solicitud = s
        return out
