"""¿Sirve reentrenar? Compara un modelo que se entrena UNA vez contra uno que se reentrena cada mes.

El fraude cambia (aparecen patrones nuevos). Para cada mes de la prueba:
  - estático: el modelo entrenado en la fecha de corte original y dejado igual.
  - reentrenado: un modelo entrenado con todo lo anterior a ese mes, usando solo las etiquetas que ya
    se conocían ese día (los contracargos llegan tarde).
La métrica de operación fija una tasa de falsos positivos (por defecto 3 %) y mira cuánto fraude se detecta.

Uso:  python -m modelo_ia.reentrenamiento --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz
"""
import argparse
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from . import config, entrenar, etiquetas, modelos, variables


def recall_a_fpr(puntaje, reales, fpr: float = 0.03) -> float:
    """Fraude detectado si se deja que `fpr` de las compras legítimas sean falsas alarmas."""
    p, y = np.asarray(puntaje), np.asarray(reales).astype(bool)
    if y.sum() == 0 or (~y).sum() == 0:
        return float("nan")
    umbral = np.quantile(p[~y], 1 - fpr)
    return float((p[y] > umbral).mean())


def simular(df: pd.DataFrame, mascaras: Dict[str, pd.Series], modelo: str = "gradient_boosting",
            conjunto: str = "completo", fpr: float = 0.03) -> pd.DataFrame:
    numericas, categoricas = variables.columnas_de(conjunto)
    columnas = numericas + categoricas
    y = df[config.COLUMNA_OBJETIVO]
    corte0 = pd.Timestamp(config.FIN_ENTRENAMIENTO) + pd.Timedelta(days=1)
    estatico = modelos.crear_modelo(modelo, numericas, categoricas).fit(
        df.loc[mascaras["entrenamiento"], columnas], etiquetas.etiqueta_conocida(df.loc[mascaras["entrenamiento"]], corte0))
    mes_de = df["fecha"].dt.to_period("M")
    filas = []
    for mes in sorted(mes_de[mascaras["prueba"]].unique()):
        inicio = mes.start_time
        en_mes = mascaras["prueba"] & (mes_de == mes)
        if y[en_mes].sum() == 0:
            continue
        antes = df["fecha"] < inicio
        reentrenado = modelos.crear_modelo(modelo, numericas, categoricas).fit(
            df.loc[antes, columnas], etiquetas.etiqueta_conocida(df.loc[antes], inicio))
        fila = {"mes": str(mes), "fraudes": int(y[en_mes].sum())}
        for nombre, m in (("estatico", estatico), ("reentrenado", reentrenado)):
            p = modelos.puntajes(m, df.loc[en_mes, columnas])
            fila[f"ap_{nombre}"] = average_precision_score(y[en_mes], p)
            fila[f"recall_a_{int(fpr * 100)}pct_{nombre}"] = recall_a_fpr(p, y[en_mes], fpr)
        filas.append(fila)
    return pd.DataFrame(filas)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compara modelo estático frente a reentrenado cada mes.")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--modelo", default="gradient_boosting", choices=list(modelos.REGISTRO))
    ap.add_argument("--conjunto", default="completo", choices=list(config.CONJUNTOS_GRUPOS))
    ap.add_argument("--fpr", type=float, default=0.03)
    a = ap.parse_args(argv)
    df, mascaras = entrenar.preparar_datos(a.datos, a.conjunto)
    tabla = simular(df, mascaras, a.modelo, a.conjunto, a.fpr)
    pd.set_option("display.width", 200)
    print(tabla.round(3).to_string(index=False))
    print("\nPromedio:", tabla.drop(columns=["mes", "fraudes"]).mean().round(3).to_dict())


if __name__ == "__main__":
    main()
