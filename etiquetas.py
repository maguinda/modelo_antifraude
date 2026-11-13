"""Etiquetas tardías: en la vida real un fraude se confirma días o semanas después del pago.

Al entrenar con corte en una fecha, solo se puede usar lo que YA se sabía ese día:
un fraude que se confirma después del corte (o que nunca se confirma) se ve como compra legítima.
"""
import pandas as pd

from . import config

COLUMNA_CONFIRMACION = "fecha_confirmacion_fraude"


def etiqueta_conocida(df: pd.DataFrame, fecha_corte) -> pd.Series:
    """1 si la transacción es fraude Y su confirmación llegó a más tardar en la fecha de corte.

    Si el archivo no trae fecha de confirmación (datasets antiguos), se usa la etiqueta completa.
    """
    if COLUMNA_CONFIRMACION not in df.columns:
        return df[config.COLUMNA_OBJETIVO].astype(int)
    confirmacion = pd.to_datetime(df[COLUMNA_CONFIRMACION])
    corte = pd.Timestamp(fecha_corte)
    return ((df[config.COLUMNA_OBJETIVO] == 1) & confirmacion.notna() & (confirmacion <= corte)).astype(int)
