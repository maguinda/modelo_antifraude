"""Reglas fijas de la línea base para la hipótesis H1.

Hay dos versiones de las MISMAS reglas:
  - ingenuas: umbrales redondos, como los pondría una pasarela.
  - ajustadas: umbrales elegidos con la VALIDACIÓN (nunca con la prueba), o sea, las mejores
    reglas fijas que se pueden armar con estas señales. Es la comparación difícil para el modelo.

OJO: el conjunto de datos no trae 3D Secure, AVS ni CVV. Estas son reglas fijas de monto,
velocidad, ubicación y dispositivo, no la validación completa de una pasarela.
Fuente de las reglas de monto, cambio de ciudad y cliente nuevo: entrevista a Giovany Pernet (VISA).
"""
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import config, evaluacion
from .motor import MotorReglas, Regla

ANONIMAS = ("VPN", "Proxy", "IP desconocida")

PARAMS_INGENUOS: Dict = {
    "monto_alto": 1_500_000,
    "compras_24h": 5,
    "minutos_cambio_ciudad": 60,
    "monto_cliente_nuevo": 1_000_000,
    "monto_madrugada": 1_000_000,
    "accion_ip_anonima": "REVISAR",
    "accion_dispositivo_no_reconocido": "BLOQUEAR",
}

# Valores que el ajuste puede probar. None = esa regla se apaga.
GRILLAS: Dict = {
    "monto_alto": [500_000, 750_000, 1_000_000, 1_500_000, 2_000_000, 3_000_000, 4_000_000, None],
    "compras_24h": [3, 4, 5, 6, 8, 10, None],
    "minutos_cambio_ciudad": [10, 20, 30, 60, 120, 240, None],
    "monto_cliente_nuevo": [500_000, 1_000_000, 2_000_000, 3_000_000, None],
    "monto_madrugada": [500_000, 1_000_000, 1_500_000, 2_000_000, 3_000_000, None],
    "accion_ip_anonima": ["NADA", "REVISAR", "BLOQUEAR"],
    "accion_dispositivo_no_reconocido": ["NADA", "REVISAR", "BLOQUEAR"],
}


def construir_reglas(params: Dict) -> List[Regla]:
    """Arma las reglas con los umbrales dados. Una regla con valor None o 'NADA' se omite."""
    reglas: List[Regla] = []
    p = params
    if p.get("monto_alto") is not None:
        v = p["monto_alto"]
        reglas.append(Regla("monto_alto", ("monto_cop",), lambda d, v=v: d["monto_cop"] >= v,
                            "REVISAR", f"Compra de {v:,} COP o más".replace(",", ".")))
    if p.get("compras_24h") is not None:
        v = p["compras_24h"]
        reglas.append(Regla("muchas_compras_24h", ("transacciones_ultimas_24h",),
                            lambda d, v=v: d["transacciones_ultimas_24h"] >= v,
                            "REVISAR", f"{v} o más compras en 24 horas"))
    if p.get("minutos_cambio_ciudad") is not None:
        v = p["minutos_cambio_ciudad"]
        reglas.append(Regla("cambio_de_ciudad_rapido", ("ciudad_distinta_anterior", "minutos_desde_anterior"),
                            lambda d, v=v: (d["ciudad_distinta_anterior"] == 1) & (d["minutos_desde_anterior"] <= v),
                            "BLOQUEAR", f"Otra ciudad a los {v} minutos o menos de la compra anterior"))
    if p.get("monto_cliente_nuevo") is not None:
        v = p["monto_cliente_nuevo"]
        reglas.append(Regla("cliente_nuevo_monto_alto", ("n_compras_previas", "monto_cop"),
                            lambda d, v=v: (d["n_compras_previas"] == 0) & (d["monto_cop"] >= v),
                            "REVISAR", f"Cliente sin historial con compra de {v:,} COP o más".replace(",", ".")))
    if p.get("monto_madrugada") is not None:
        v = p["monto_madrugada"]
        reglas.append(Regla("madrugada_monto_alto", ("hora", "monto_cop"),
                            lambda d, v=v: (d["hora"] <= 5) & (d["monto_cop"] >= v),
                            "REVISAR", f"Compra de madrugada de {v:,} COP o más".replace(",", ".")))
    if p.get("accion_ip_anonima", "NADA") != "NADA":
        reglas.append(Regla("ip_anonima", ("tipo_ip",), lambda d: d["tipo_ip"].isin(ANONIMAS),
                            p["accion_ip_anonima"], "IP anónima (VPN, proxy o desconocida)"))
    if p.get("accion_dispositivo_no_reconocido", "NADA") != "NADA":
        reglas.append(Regla("dispositivo_no_reconocido", ("estado_dispositivo",),
                            lambda d: d["estado_dispositivo"] == "No reconocido",
                            p["accion_dispositivo_no_reconocido"], "Dispositivo no reconocido"))
    return reglas


def motor_ingenuo() -> MotorReglas:
    return MotorReglas(construir_reglas(PARAMS_INGENUOS))


def _costo(params: Dict, df: pd.DataFrame, reales, montos) -> float:
    sev = MotorReglas(construir_reglas(params)).severidad(df)
    return evaluacion.costo_total(sev, reales, montos, 1)       # alerta = revisar o bloquear


def ajustar(df_validacion: pd.DataFrame, rondas: int = 2,
            inicio: Optional[Dict] = None) -> Tuple[Dict, float, float]:
    """Busca los umbrales con menor costo EN LA VALIDACIÓN (un parámetro a la vez, 2 rondas).

    Empieza en los umbrales ingenuos, así que nunca queda peor que ellos en la validación.
    Devuelve (parámetros, costo_inicial, costo_final).
    """
    reales = df_validacion[config.COLUMNA_OBJETIVO].values
    montos = df_validacion["monto_cop"].values
    params = dict(inicio or PARAMS_INGENUOS)
    costo_inicial = costo = _costo(params, df_validacion, reales, montos)
    for _ in range(rondas):
        for clave, candidatos in GRILLAS.items():
            for valor in candidatos:
                if valor == params[clave]:
                    continue
                prueba = dict(params, **{clave: valor})
                c = _costo(prueba, df_validacion, reales, montos)
                if c < costo:
                    params, costo = prueba, c
    return params, costo_inicial, costo
