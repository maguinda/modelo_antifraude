"""Reproduce los pagos del historial, en orden de fecha, por el MISMO servicio que usa la API.

No toca tu base real: usa una carpeta temporal con una base nueva que se borra al terminar. El reloj del servicio se mueve a la fecha de cada pago,
de modo que las señales de historial (compras en 24 h, cliente nuevo, viaje imposible...) se calculan como se habrían calculado ese día.
"""
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .. import predecir
from ..api import esquemas as es
from ..api.ajustes import Ajustes
from ..api.almacen import Almacen, Principal
from ..api.proveedores_ip import crear_proveedor
from ..api.servicio import ErrorAPI, ServicioEvaluacion
from .conversion import Fila


@dataclass
class Resultado:
    numero: int
    fecha: datetime
    monto: int
    fraude: Optional[bool]
    decision: Optional[str] = None
    puntaje: Optional[float] = None
    modo: Optional[str] = None
    reglas: List[str] = field(default_factory=list)
    red_origen: Optional[str] = None
    estado_origen: Optional[str] = None
    error: Optional[str] = None


def reproducir(filas: List[Fila], artefacto: Optional[Dict] = None, art_sin_estado: Optional[Dict] = None, proveedor: str = "simulado",
               conservar_bd: bool = False, avance: Optional[Callable[[int, int], None]] = None) -> Dict:
    artefacto = artefacto or predecir.cargar_artefacto()
    art_sin_estado = art_sin_estado if art_sin_estado is not None else predecir.cargar_artefacto_sin_estado()
    carpeta = Path(tempfile.mkdtemp(prefix="historial_"))
    try:
        pepper = secrets.token_hex(16)
        ajustes = Ajustes(ruta_bd=carpeta / "historial.sqlite3", pepper=pepper, dias_pasado_maximo=36500, minutos_futuro_maximo=5, proveedor_ip=proveedor)
        alm = Almacen(ajustes.ruta_bd, pepper)
        alm.inicializar()
        principal = Principal(0, alm.crear_comercio("Historial"), "comercio", "historial")
        reloj = {"t": datetime(2000, 1, 1)}
        servicio = ServicioEvaluacion(alm, artefacto, ajustes, lambda: reloj["t"], crear_proveedor(proveedor), art_sin_estado)
        orden = sorted((f for f in filas if f.solicitud is not None), key=lambda f: (f.fecha, f.numero))
        vistos, resultados = set(), []
        for i, f in enumerate(orden, 1):
            r = Resultado(f.numero, f.fecha, f.monto, f.fraude)
            idt = f.solicitud["id_transaccion"]
            if idt in vistos:
                r.error = "pedido repetido en el archivo"; resultados.append(r); continue
            vistos.add(idt)
            reloj["t"] = f.fecha + timedelta(seconds=1)
            try:
                resp = servicio.evaluar(principal, es.SolicitudEvaluacion.model_validate(f.solicitud))
                r.decision, r.puntaje, r.modo = resp["decision"], resp["puntaje_riesgo"], resp["modo"]
                r.reglas = [x["regla"] for x in resp["reglas_activadas"]]
                r.red_origen, r.estado_origen = resp["red_origen"], resp["estado_origen"]
            except ErrorAPI as e:
                r.error = e.titulo if hasattr(e, "titulo") else "rechazado por el servicio"
            resultados.append(r)
            if avance and (i % 500 == 0 or i == len(orden)):
                avance(i, len(orden))
        return {"resultados": resultados, "modelo": servicio.meta, "proveedor_ip": proveedor, "carpeta_bd": str(carpeta) if conservar_bd else None}
    finally:
        if not conservar_bd:
            shutil.rmtree(carpeta, ignore_errors=True)
