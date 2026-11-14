"""Comercios, su suspensión y su acuerdo de datos."""
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .auditoria import auditar
from .base import Base
from .comunes import PLATAFORMA, ahora_iso, texto_limpio


class ComercioNoAutorizado(Exception):
    """Un comercio no puede operar ahora: está suspendido (`comercio_suspendido`) o no tiene acuerdo de datos vigente (`sin_acuerdo`)."""
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


class ComercioInexistente(Exception):
    """El id de comercio no existe (o es el de la plataforma, que no se administra como un comercio)."""


class OperacionNoPermitida(Exception):
    """Algo que no se debe hacer nunca, ni siendo administrador (p. ej. suspender la plataforma y dejar sin acceso a todo el equipo)."""


DIAS_AVISO_VENCIMIENTO = 30


def estado_acuerdo(ultimo: Optional[Any], hoy: date) -> Dict[str, Any]:
    """El estado del acuerdo de datos de un comercio a partir de su último registro: sin_acuerdo, vigente, vence_pronto (en 30 días o menos) o vencido."""
    if ultimo is None:
        return {"estado": "sin_acuerdo", "version": None, "firmado_en": None, "vigente_hasta": None, "dias_para_vencer": None}
    hasta = date.fromisoformat(ultimo["vigente_hasta"]) if ultimo["vigente_hasta"] else None
    dias = (hasta - hoy).days if hasta else None
    estado = "vigente" if dias is None or dias > DIAS_AVISO_VENCIMIENTO else "vence_pronto" if dias >= 0 else "vencido"
    return {"estado": estado, "version": ultimo["version"], "firmado_en": ultimo["firmado_en"], "vigente_hasta": ultimo["vigente_hasta"], "dias_para_vencer": dias}


class ComerciosRepositorio:
    def __init__(self, base: Base):
        self.base = base

    def crear_comercio(self, nombre: str, razon_social: Optional[str] = None, nit: Optional[str] = None, por: Optional[int] = None) -> int:
        razon_social = texto_limpio(razon_social, 2, 120, "La razón social") if razon_social else None
        if nit is not None and not re.fullmatch(r"[0-9][0-9.\-]{3,18}[0-9]", str(nit)):
            raise ValueError("El NIT debe llevar solo números, puntos o guion (de 5 a 20 caracteres).")
        with self.base.transaccion() as con:
            cur = con.execute("INSERT INTO comercios (nombre, creado, razon_social, nit) VALUES (?, ?, ?, ?)", (nombre, ahora_iso(), razon_social, nit))
            auditar(con, None, por, "comercio_creado", None, {"nombre": nombre, "id": cur.lastrowid, "con_nit": nit is not None})   # el NIT nunca va a la auditoría
            return int(cur.lastrowid)

    def verificar_comercio(self, con: sqlite3.Connection, comercio_id: int, exigir_acuerdo: bool = False, hoy: Optional[date] = None) -> None:
        """Se llama DENTRO del bloqueo de cada escritura de un comercio: así, ningún pago se guarda después de que se suspendió (se relee el estado
        aquí, no antes). La plataforma (analistas y administradores) nunca se bloquea."""
        if comercio_id == PLATAFORMA:
            return
        c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
        if c is None or not c["activo"]:
            raise ComercioNoAutorizado("comercio_suspendido", "Este comercio está suspendido. Contacta al administrador del servicio.")
        if exigir_acuerdo:
            ult = con.execute("SELECT * FROM acuerdos WHERE comercio_id=? ORDER BY id DESC LIMIT 1", (comercio_id,)).fetchone()
            if estado_acuerdo(ult, hoy or date.today())["estado"] not in ("vigente", "vence_pronto"):
                raise ComercioNoAutorizado("sin_acuerdo", "Este comercio no tiene un acuerdo de datos vigente. Contacta al administrador del servicio.")

    def suspender_comercio(self, comercio_id: int, motivo: str, por: Optional[int] = None) -> bool:
        """Devuelve True si se suspendió ahora, False si ya estaba suspendido (repetirlo no hace nada ni ensucia la auditoría)."""
        motivo = texto_limpio(motivo, 3, 200, "El motivo")
        if comercio_id == PLATAFORMA:
            raise OperacionNoPermitida("El comercio interno de la plataforma no se puede suspender: dejaría sin acceso a todo el equipo.")
        with self.base.transaccion() as con:
            c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
            if c is None:
                raise ComercioInexistente("No existe ese comercio.")
            if not c["activo"]:
                return False
            con.execute("UPDATE comercios SET activo=0, suspendido_en=?, motivo_suspension=? WHERE id=?", (ahora_iso(), motivo, comercio_id))
            auditar(con, comercio_id, por, "comercio_suspendido", None, {"motivo": motivo})
            return True

    def reactivar_comercio(self, comercio_id: int, por: Optional[int] = None) -> bool:
        with self.base.transaccion() as con:
            c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
            if c is None:
                raise ComercioInexistente("No existe ese comercio.")
            if c["activo"]:
                return False
            con.execute("UPDATE comercios SET activo=1, suspendido_en=NULL, motivo_suspension=NULL WHERE id=?", (comercio_id,))
            auditar(con, comercio_id, por, "comercio_reactivado", None, {})
            return True

    def registrar_acuerdo(self, comercio_id: int, version: str, firmado_en: date, vigente_hasta: Optional[date], referencia: str,
                          por: Optional[int] = None, hoy: Optional[date] = None) -> int:
        """Guarda los DATOS de un acuerdo de datos (no el documento). El historial no se edita: uno nuevo reemplaza al anterior como vigente."""
        hoy = hoy or date.today()
        if not re.fullmatch(r"[\w .\-]{1,40}", str(version or "")):
            raise ValueError("La versión del acuerdo debe tener de 1 a 40 caracteres (letras, números, espacio, punto o guion).")
        if not re.fullmatch(r"[\w .,:#()\-]{1,120}", str(referencia or "")):
            raise ValueError("La referencia del archivo firmado debe tener de 1 a 120 caracteres, sin rutas ni signos raros (por ejemplo su nombre o su huella SHA-256).")
        if not isinstance(firmado_en, date) or isinstance(firmado_en, datetime) or (vigente_hasta is not None and (not isinstance(vigente_hasta, date) or isinstance(vigente_hasta, datetime))):
            raise ValueError("Las fechas del acuerdo deben ser fechas (AAAA-MM-DD).")
        if (firmado_en - hoy).days > 1:
            raise ValueError("La fecha de firma no puede ser futura.")
        if vigente_hasta is not None and vigente_hasta < firmado_en:
            raise ValueError("El acuerdo no puede vencer antes de su firma.")
        if comercio_id == PLATAFORMA:
            raise OperacionNoPermitida("La plataforma no es un comercio: no lleva acuerdo de datos.")
        with self.base.transaccion() as con:
            if con.execute("SELECT 1 FROM comercios WHERE id=?", (comercio_id,)).fetchone() is None:
                raise ComercioInexistente("No existe ese comercio.")
            cur = con.execute("INSERT INTO acuerdos (comercio_id, version, firmado_en, vigente_hasta, referencia, registrado, registrado_por) VALUES (?,?,?,?,?,?,?)",
                              (comercio_id, version, firmado_en.isoformat(), vigente_hasta.isoformat() if vigente_hasta else None, referencia, ahora_iso(), por))
            auditar(con, comercio_id, por, "acuerdo_registrado", None, {"version": version, "firmado_en": firmado_en.isoformat(),
                                                                                "vigente_hasta": vigente_hasta.isoformat() if vigente_hasta else None, "referencia": referencia})
            return int(cur.lastrowid)

    def historial_acuerdos(self, comercio_id: int, hoy: Optional[date] = None) -> Optional[List[Dict[str, Any]]]:
        """None si el comercio no existe. Del más reciente al más viejo; el primero es el que vale."""
        hoy = hoy or date.today()
        with self.base.lectura() as con:
            if comercio_id == PLATAFORMA or con.execute("SELECT 1 FROM comercios WHERE id=?", (comercio_id,)).fetchone() is None:
                return None
            filas = con.execute("SELECT * FROM acuerdos WHERE comercio_id=? ORDER BY id DESC", (comercio_id,)).fetchall()
        return [dict(estado_acuerdo(f, hoy) if i == 0 else {"estado": "reemplazado", "version": f["version"], "firmado_en": f["firmado_en"], "vigente_hasta": f["vigente_hasta"],
                                                          "dias_para_vencer": None}, referencia=f["referencia"], registrado=f["registrado"]) for i, f in enumerate(filas)]

    def listar_comercios(self, hoy: Optional[date] = None, con_nit: bool = False) -> List[Dict[str, Any]]:
        """Para el personal. El NIT (que en una persona natural es su cédula) solo sale si `con_nit`."""
        hoy = hoy or date.today()
        with self.base.lectura(instantanea=True) as con:
            filas = con.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM claves k WHERE k.comercio_id=c.id AND k.activa=1) AS claves_activas, "
                "(SELECT MAX(k.ultimo_uso) FROM claves k WHERE k.comercio_id=c.id) AS ultimo_uso, "
                "(SELECT COUNT(*) FROM evaluaciones e WHERE e.comercio_id=c.id) AS pagos FROM comercios c WHERE c.id != ? ORDER BY c.activo DESC, c.nombre", (PLATAFORMA,)).fetchall()
            ultimos = {a["comercio_id"]: a for a in con.execute("SELECT * FROM acuerdos a WHERE a.id = (SELECT MAX(id) FROM acuerdos WHERE comercio_id = a.comercio_id)")}
        return [{"id": f["id"], "nombre": f["nombre"], "razon_social": f["razon_social"], "nit": f["nit"] if con_nit else None,
                 "estado": "activo" if f["activo"] else "suspendido", "suspendido_en": f["suspendido_en"], "motivo_suspension": f["motivo_suspension"],
                 "creado": f["creado"], "claves_activas": f["claves_activas"], "ultimo_uso": f["ultimo_uso"], "pagos": f["pagos"],
                 "acuerdo": estado_acuerdo(ultimos.get(f["id"]), hoy)} for f in filas]
