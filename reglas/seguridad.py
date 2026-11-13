"""Red de seguridad para producción: reglas duras que mandan sobre el modelo.

No se ajustan con datos: son POLÍTICA de la empresa (qué nunca se debe aprobar sin mirar).
Si el modelo dice APROBAR pero una de estas reglas dice BLOQUEAR o REVISAR, gana la más severa.
Los valores son conservadores y están aquí para que se puedan cambiar con una sola línea.
"""
from typing import Dict, Iterable, List

import pandas as pd

from .motor import Regla
from .reglas_base import ANONIMAS

PARAMS_SEGURIDAD: Dict = {
    "monto_revision_obligatoria": 10_000_000,   # desde aquí nunca se aprueba sin revisar
    "compras_24h_bloqueo": 15,                  # ráfaga extrema
    "minutos_viaje_imposible": 30,              # otra ciudad en menos de 30 minutos
}


def construir_reglas_seguridad(lista_negra: Iterable[str] = (), params: Dict = None) -> List[Regla]:
    p = dict(PARAMS_SEGURIDAD, **(params or {}))
    negra = frozenset(lista_negra)
    return [
        Regla("lista_negra_cliente", ("id_cliente",),
              lambda d: d["id_cliente"].isin(negra), "BLOQUEAR", "Cliente en la lista negra"),
        Regla("dispositivo_no_reconocido_con_ip_anonima", ("estado_dispositivo", "tipo_ip"),
              lambda d: (d["estado_dispositivo"] == "No reconocido") & d["tipo_ip"].isin(ANONIMAS),
              "BLOQUEAR", "Dispositivo no reconocido y desde una IP anónima"),
        Regla("rafaga_extrema", ("transacciones_ultimas_24h",),
              lambda d: d["transacciones_ultimas_24h"] >= p["compras_24h_bloqueo"],
              "BLOQUEAR", f"{p['compras_24h_bloqueo']} o más compras en 24 horas"),
        Regla("viaje_imposible", ("ciudad_distinta_anterior", "minutos_desde_anterior"),
              lambda d: (d["ciudad_distinta_anterior"] == 1)
              & (d["minutos_desde_anterior"] <= p["minutos_viaje_imposible"]),
              "BLOQUEAR", "Cambio de ciudad imposible en tan pocos minutos"),
        Regla("monto_muy_alto", ("monto_cop",),
              lambda d: d["monto_cop"] >= p["monto_revision_obligatoria"],
              "REVISAR", "Monto muy alto: revisión obligatoria"),
    ]
