"""Dividir los datos por fecha: entrenamiento, validación y prueba."""
from typing import Dict

import pandas as pd

from . import config


def dividir(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Devuelve máscaras (verdadero/falso) para cada conjunto, según las fechas de corte.

    Se divide por fecha y no al azar: en un comercio real el modelo siempre
    decide sobre compras posteriores a las que aprendió.
    """
    fin_entrenamiento = pd.Timestamp(config.FIN_ENTRENAMIENTO) + pd.Timedelta(days=1)   # incluye ese día
    fin_validacion = pd.Timestamp(config.FIN_VALIDACION) + pd.Timedelta(days=1)
    if not fin_entrenamiento < fin_validacion:
        raise ValueError("Los cortes de fecha están en desorden: FIN_ENTRENAMIENTO debe ser anterior a FIN_VALIDACION.")
    fecha = df["fecha"]
    mascaras = {
        "entrenamiento": fecha < fin_entrenamiento,
        "validacion": (fecha >= fin_entrenamiento) & (fecha < fin_validacion),
        "prueba": fecha >= fin_validacion,
    }
    for nombre, mascara in mascaras.items():
        if mascara.sum() == 0:
            raise ValueError(f"El conjunto '{nombre}' quedó vacío.")
        if df.loc[mascara, config.COLUMNA_OBJETIVO].sum() == 0:
            raise ValueError(f"El conjunto '{nombre}' no tiene fraudes.")
    return mascaras
