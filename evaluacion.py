"""Métricas, costo para la pyme y elección del umbral."""
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_score, recall_score

from . import config


def costo_total(puntaje, reales, montos, umbral: float,
                costo_falsa_alarma: float = config.COSTO_FALSA_ALARMA) -> float:
    """Costo = monto de los fraudes que se escapan + costo de cada falsa alarma."""
    puntaje, reales, montos = np.asarray(puntaje), np.asarray(reales), np.asarray(montos)
    alerta = puntaje >= umbral
    escapan = (~alerta) & (reales == 1)
    falsas = alerta & (reales == 0)
    return float(montos[escapan].sum() + falsas.sum() * costo_falsa_alarma)


def elegir_umbral(puntaje, reales, montos,
                  umbrales: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    """Umbral con menor costo. Devuelve (umbral, costo). Se llama con datos de VALIDACIÓN."""
    umbrales = list(umbrales) if umbrales is not None else config.UMBRALES_A_PROBAR
    costos = [costo_total(puntaje, reales, montos, u) for u in umbrales]
    mejor = int(np.argmin(costos))
    return umbrales[mejor], costos[mejor]


def metricas(puntaje, reales, montos, umbral: float) -> Dict[str, float]:
    puntaje, reales = np.asarray(puntaje), np.asarray(reales)
    alerta = puntaje >= umbral
    return {
        "umbral": float(umbral),
        "recall": float(recall_score(reales, alerta, zero_division=0)),
        "precision": float(precision_score(reales, alerta, zero_division=0)),
        "ranking_ap": float(average_precision_score(reales, puntaje)),
        "fraudes_que_escapan": int(((~alerta) & (reales == 1)).sum()),
        "falsas_alarmas": int((alerta & (reales == 0)).sum()),
        "costo_cop": costo_total(puntaje, reales, montos, umbral),
    }


def recall_por_patron(df: pd.DataFrame, puntaje, umbral: float) -> pd.Series:
    """Qué parte de cada tipo de fraude se detecta (solo si el archivo trae 'patron')."""
    if "patron" not in df.columns:
        return pd.Series(dtype=float)
    t = df.assign(_alerta=np.asarray(puntaje) >= umbral)
    t = t[t[config.COLUMNA_OBJETIVO] == 1]
    return t.groupby("patron")["_alerta"].mean().round(3)
