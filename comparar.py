"""Compara varios modelos con los mismos datos y deja todo anotado en la bitácora.

Uso:  python -m modelo_ia.comparar
      python -m modelo_ia.comparar --modelos logistica gradient_boosting --conjunto base
"""
import argparse
from pathlib import Path

import pandas as pd

from . import config, entrenar, modelos


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compara modelos y conjuntos de variables.")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--modelos", nargs="+", default=list(modelos.REGISTRO), choices=list(modelos.REGISTRO))
    ap.add_argument("--conjunto", default=config.CONJUNTO_POR_DEFECTO, choices=list(config.CONJUNTOS_VARIABLES))
    ap.add_argument("--nota", default="comparación")
    a = ap.parse_args(argv)

    df, mascaras = entrenar.preparar_datos(a.datos or config.DATOS_POR_DEFECTO, a.conjunto)
    filas = {}
    for nombre in a.modelos:
        r = entrenar.ajustar_y_evaluar(df, mascaras, nombre, a.conjunto)
        entrenar.registrar_experimento(r, a.nota)
        m = r["metricas_prueba"]
        filas[nombre] = {"recall": round(m["recall"], 3), "precision": round(m["precision"], 3),
                         "ranking_ap": round(m["ranking_ap"], 3), "umbral": m["umbral"],
                         "costo_millones": round(m["costo_cop"] / 1e6, 1),
                         "falsas_alarmas": m["falsas_alarmas"], "segundos": r["segundos_entrenamiento"]}
    print(f"Prueba (sep–dic), variables: {a.conjunto}")
    print(pd.DataFrame(filas).T.to_string())


if __name__ == "__main__":
    main()
