"""Explicar por qué el modelo dio esa probabilidad (solo modelos lineales por ahora)."""
from typing import List

import numpy as np
import pandas as pd

NOMBRES_LEGIBLES = {
    "monto_cop": "monto de la compra", "transacciones_ultimas_24h": "compras en las últimas 24 h",
    "hora": "hora del pago", "es_madrugada": "pago de madrugada", "es_fin_semana": "fin de semana",
    "minutos_desde_anterior": "minutos desde la compra anterior",
    "ciudad_distinta_anterior": "cambio de ciudad frente a la compra anterior",
    "n_compras_previas": "compras previas del cliente", "monto_vs_promedio": "monto frente a su promedio",
    "dispositivo_nuevo_cliente": "dispositivo nuevo para el cliente", "tipo_ip": "tipo de IP",
    "estado_dispositivo": "estado del dispositivo", "metodo_pago": "método de pago",
    "canal_pago": "canal", "localizacion": "ciudad", "categoria": "categoría",
    "dispositivo": "tipo de dispositivo",
}
APORTE_MINIMO = 0.5      # solo se muestran motivos que empujan de verdad hacia fraude


def explicar(pipeline, fila: pd.DataFrame, n_motivos: int = 3) -> List[str]:
    """Los motivos que más empujaron la transacción hacia 'fraude'."""
    clasificador = pipeline.named_steps["clasificador"]
    if not hasattr(clasificador, "coef_"):
        return []            # árboles y boosting: la explicación llega en una etapa posterior
    prep = pipeline.named_steps["preparacion"]
    nombres = prep.get_feature_names_out()
    valores = prep.transform(fila)
    valores = valores.toarray()[0] if hasattr(valores, "toarray") else valores[0]
    aportes = valores * np.ravel(clasificador.coef_)
    motivos = []
    for i in np.argsort(aportes)[::-1][:n_motivos]:
        if aportes[i] < APORTE_MINIMO:
            break
        grupo, variable = nombres[i].split("__", 1)
        if grupo == "numeros":
            motivos.append(NOMBRES_LEGIBLES.get(variable, variable))
        else:
            for original in sorted(NOMBRES_LEGIBLES, key=len, reverse=True):
                if variable.startswith(original + "_"):
                    motivos.append(f"{NOMBRES_LEGIBLES[original]}: {variable[len(original) + 1:]}")
                    break
    return motivos
