"""Motor de reglas: reglas escritas a mano del tipo 'si pasa esto, entonces haz aquello'.

A diferencia del modelo (que aprende), aquí las reglas se le dicen. Sirven para:
  1. Línea base de la hipótesis H1 (reglas fijas de validación estándar).
  2. Red de seguridad en producción (reglas duras que mandan sobre el modelo).
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

SEVERIDAD = {"APROBAR": 0, "REVISAR": 1, "BLOQUEAR": 2}
NOMBRE_SEVERIDAD = {v: k for k, v in SEVERIDAD.items()}


@dataclass(frozen=True)
class Regla:
    nombre: str
    requiere: Tuple[str, ...]                      # columnas que necesita; si faltan, la regla no se aplica
    condicion: Callable[[pd.DataFrame], pd.Series]  # recibe una tabla y devuelve verdadero/falso por fila
    accion: str                                    # REVISAR o BLOQUEAR
    motivo: str                                    # frase que se muestra al analista

    def __post_init__(self):
        if self.accion not in ("REVISAR", "BLOQUEAR"):
            raise ValueError(f"Acción inválida en la regla '{self.nombre}': {self.accion}")


class MotorReglas:
    def __init__(self, reglas: Sequence[Regla]):
        nombres = [r.nombre for r in reglas]
        if len(nombres) != len(set(nombres)):
            raise ValueError("Hay dos reglas con el mismo nombre.")
        self.reglas: List[Regla] = list(reglas)

    def activadas(self, df: pd.DataFrame) -> pd.DataFrame:
        """Tabla de verdadero/falso: qué regla se activó en cada fila."""
        salida = pd.DataFrame(index=df.index)
        for r in self.reglas:
            if all(c in df.columns for c in r.requiere):
                salida[r.nombre] = r.condicion(df).fillna(False).astype(bool)
            else:
                salida[r.nombre] = False
        return salida

    def severidad(self, df: pd.DataFrame) -> np.ndarray:
        """0 = aprobar, 1 = revisar, 2 = bloquear (gana la regla más severa de la fila)."""
        activadas = self.activadas(df)
        sev = np.zeros(len(df), dtype=int)
        for r in self.reglas:
            sev = np.maximum(sev, np.where(activadas[r.nombre].values, SEVERIDAD[r.accion], 0))
        return sev

    def decidir(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series([NOMBRE_SEVERIDAD[s] for s in self.severidad(df)], index=df.index)

    def explicar(self, pago: Dict) -> Dict:
        """Para un solo pago: decisión y las reglas que se activaron."""
        fila = pd.DataFrame([pago])
        activadas = self.activadas(fila).iloc[0]
        lista = [{"regla": r.nombre, "accion": r.accion, "motivo": r.motivo}
                 for r in self.reglas if bool(activadas[r.nombre])]
        sev = max([SEVERIDAD[x["accion"]] for x in lista], default=0)
        return {"decision": NOMBRE_SEVERIDAD[sev], "reglas_activadas": lista}
