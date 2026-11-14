"""Traduce los filtros del panel (un objeto ya VALIDADO y sin SQL: `consultas.Filtros`) a condiciones SQL con parámetros.

Es lo que antes hacía el propio `Filtros`. Vive en los repositorios porque el SQL es de la base de datos: el resto del programa solo conoce los filtros.
Nunca pone texto del usuario dentro del SQL: todo valor viaja como parámetro.
"""

from datetime import timedelta
from typing import Any, List, Tuple


def condiciones_de(f: Any) -> Tuple[List[str], tuple]:
    """Las condiciones (con alias `e` para evaluaciones) y sus parámetros, en el orden de siempre."""
    cond: List[str] = []
    par: List[Any] = []

    def agregar(sql: str, *valores: Any) -> None:
        cond.append(sql)
        par.extend(valores)

    if f.comercio_id is not None:
        agregar("e.comercio_id = ?", f.comercio_id)
    if f.desde:
        agregar("e.fecha >= ?", f.desde.isoformat())
    if f.hasta:
        agregar("e.fecha < ?", (f.hasta + timedelta(days=1)).isoformat())
    if f.decision:
        agregar("e.decision = ?", f.decision)
    if f.modo:
        agregar("e.modo = ?", f.modo)
    if f.monto_min is not None:
        agregar("e.monto_cop >= ?", f.monto_min)
    if f.monto_max is not None:
        agregar("e.monto_cop <= ?", f.monto_max)
    if f.id_cliente:
        agregar("e.id_cliente = ?", f.id_cliente)
    if f.id_transaccion:
        agregar("e.id_transaccion = ?", f.id_transaccion)
    if f.id_seguimiento:
        agregar("e.id_seguimiento = ?", f.id_seguimiento.upper())
    if f.regla:
        agregar("EXISTS (SELECT 1 FROM json_each(e.reglas_json) j WHERE json_extract(j.value, '$.regla') = ?)", f.regla)
    if f.riesgo_min is not None:
        agregar(
            "e.probabilidad >= ?", f.riesgo_min
        )  # los pagos sin puntaje (modo degradado) no entran en un filtro de riesgo
    if f.riesgo_max is not None:
        agregar("e.probabilidad <= ?", f.riesgo_max)
    if f.q:
        agregar("(e.id_seguimiento = ? OR e.id_transaccion = ? OR e.id_cliente = ?)", f.q.upper(), f.q, f.q)
    if f.ip:
        agregar("e.ip_codigo = ?", f.ip.upper())
    if f.dispositivo:
        agregar("e.disp_codigo = ?", f.dispositivo.upper())
    return cond, tuple(par)


def donde_de(f: Any) -> Tuple[str, tuple]:
    """El `WHERE ...` completo (o vacío si no hay filtros) y sus parámetros."""
    cond, par = condiciones_de(f)
    return (("WHERE " + " AND ".join(cond)) if cond else ""), par
