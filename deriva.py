"""Deriva del modelo: ¿los puntajes de los pagos de hoy se parecen a los de los datos con que se validó el modelo?

Se compara la distribución de los puntajes (10 cubetas de 0,1) de una ventana reciente con la de REFERENCIA (los puntajes de validación guardados al entrenar)
usando el PSI (índice de estabilidad de la población):  PSI = Σ (observada − referencia) · ln(observada / referencia).
Lectura habitual: menos de 0,10 estable · de 0,10 a 0,25 atención · más de 0,25 alerta (la población cambió bastante).

Límites que hay que tener presentes: mide si los PUNTAJES cambiaron, no si el modelo acierta (eso necesita las etiquetas, que llegan tarde); con pocos pagos el
PSI es muy ruidoso, por eso se exige una muestra mínima antes de dar un estado; y con datos sintéticos solo demuestra el mecanismo.
"""

import math
from typing import Iterable, List, Optional, Sequence

CUBETAS = 10
UMBRAL_ATENCION = 0.10
UMBRAL_ALERTA = 0.25
MINIMO_MUESTRA = 500  # pagos mínimos en la ventana para dar un estado (con menos, el PSI es ruido)
PISO = 1e-4  # una cubeta vacía no puede valer 0 (el logaritmo no existe): se le pone este mínimo


def cubeta_de(puntaje: float) -> int:
    """0 para [0; 0,1), 1 para [0,1; 0,2) … 9 para [0,9; 1]. Fuera de [0, 1] se pega al extremo."""
    return min(max(int(puntaje * CUBETAS), 0), CUBETAS - 1)


def histograma(puntajes: Iterable[float]) -> List[int]:
    """Cuántos puntajes caen en cada cubeta. Ignora los vacíos y los no numéricos (NaN)."""
    cuentas = [0] * CUBETAS
    for p in puntajes:
        if p is None or (isinstance(p, float) and math.isnan(p)):
            continue
        cuentas[cubeta_de(float(p))] += 1
    return cuentas


def proporciones(cuentas: Sequence[int]) -> List[float]:
    total = sum(cuentas)
    if total <= 0:
        raise ValueError("No hay puntajes: no se pueden calcular proporciones.")
    return [c / total for c in cuentas]


def psi(referencia: Sequence[float], observada: Sequence[float]) -> float:
    """PSI entre dos distribuciones del mismo número de cubetas (cada una suma 1). Una cubeta vacía cuenta como `PISO`."""
    if len(referencia) != len(observada) or not referencia:
        raise ValueError("Las dos distribuciones deben tener las mismas cubetas.")
    total = 0.0
    for e, o in zip(referencia, observada):
        e, o = max(e, PISO), max(o, PISO)
        total += (o - e) * math.log(o / e)
    return total


def estado(valor: Optional[float], pagos: int, minimo: int = MINIMO_MUESTRA) -> str:
    """'sin_referencia' · 'sin_pagos' · 'muestra_insuficiente' · 'estable' · 'atencion' · 'alerta'."""
    if valor is None:
        return "sin_pagos" if pagos <= 0 else "sin_referencia"
    if pagos < minimo:
        return "muestra_insuficiente"
    if valor >= UMBRAL_ALERTA:
        return "alerta"
    if valor >= UMBRAL_ATENCION:
        return "atencion"
    return "estable"
