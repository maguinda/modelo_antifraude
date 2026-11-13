"""¿Qué pasa si el estado del dispositivo no lo manda el comercio sino que lo deduce la API del historial?

Mismos datos y mismo modelo; solo cambia de dónde sale la variable `estado_dispositivo`:
  original      lo que trae el dataset (lo «sabe» el sistema de cuentas del comercio)
  derivado      deducido del historial (Conocido / Nuevo), reentrenando el modelo con eso
  sin_variable  la variable no existe (se fija en un valor)
  sin_reentrenar el modelo entrenado con `original`, alimentado con el estado derivado
OJO: en los datos sintéticos `original` lo asignó el generador según el tipo de fraude, no según el historial: por eso puede tener
más información que cualquier derivación. Con datos reales la comparación podría salir distinta.

Uso:  python -m modelo_ia.comparar_estado --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones_v2_mov30.csv.gz
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from . import config, entrenar, evaluacion, modelos, variables


def ejecutar(ruta: Optional[Path] = None, modelo: str = "gradient_boosting", conjunto: str = "completo", alcance: str = "comercio",
             registrar: bool = False) -> Dict:
    df, mascaras = entrenar.preparar_datos(ruta, conjunto, alcance)
    original = df["estado_dispositivo"].copy()
    filas, patrones = [], {}

    def medir(nombre, columna, entrenado_con=None):
        d = df.copy()
        d["estado_dispositivo"] = columna
        if entrenado_con is None:
            r = entrenar.ajustar_y_evaluar(d, mascaras, modelo, conjunto, etiquetas_conocidas=True)
        else:                                                           # modelo ya entrenado con otra versión de la variable
            numericas, categoricas = variables.columnas_de(conjunto)
            cols = numericas + categoricas
            pv = modelos.puntajes(entrenado_con["pipeline"], d.loc[mascaras["validacion"], cols])
            pp = modelos.puntajes(entrenado_con["pipeline"], d.loc[mascaras["prueba"], cols])
            y = d[config.COLUMNA_OBJETIVO]
            umbral, _ = evaluacion.elegir_umbral(pv, y[mascaras["validacion"]].values, d.loc[mascaras["validacion"], "monto_cop"].values)
            r = {"metricas_prueba": evaluacion.metricas(pp, y[mascaras["prueba"]].values, d.loc[mascaras["prueba"], "monto_cop"].values, umbral),
                 "recall_por_patron_prueba": evaluacion.recall_por_patron(d.loc[mascaras["prueba"]], pp, umbral).to_dict()}
        m = r["metricas_prueba"]
        filas.append({"estado": nombre, "recall": m["recall"], "precision": m["precision"], "ranking_ap": m["ranking_ap"],
                      "falsas_alarmas": m["falsas_alarmas"], "fraudes_que_escapan": m["fraudes_que_escapan"], "costo_millones": m["costo_cop"] / 1e6})
        patrones[nombre] = r["recall_por_patron_prueba"]
        return r

    base = medir("original", original)
    derivado_nuevo = variables.derivar_estado_dispositivo(df, alcance, "Nuevo")
    derivado_conocido = variables.derivar_estado_dispositivo(df, alcance, "Conocido")
    medir("derivado (primer pago = Nuevo)", derivado_nuevo)
    medir("derivado (primer pago = Conocido)", derivado_conocido)
    medir("sin la variable", pd.Series("Conocido", index=df.index))
    medir("sin reentrenar: modelo original + derivado", derivado_nuevo, entrenado_con=base)
    tabla = pd.DataFrame(filas)
    reparto = pd.DataFrame({"original": original.value_counts(normalize=True), "derivado (primer pago = Nuevo)": derivado_nuevo.value_counts(normalize=True)}).fillna(0)
    return {"tabla": tabla, "patrones": patrones, "reparto": reparto,
            "salida": {"datos_sinteticos": True, "tabla": tabla.round(4).to_dict(orient="records"), "reparto": reparto.round(4).to_dict()}}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Estado del dispositivo: enviado por el comercio frente a derivado del historial.")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--alcance", default="comercio", choices=list(config.ALCANCES_HISTORIAL))
    a = ap.parse_args(argv)
    r = ejecutar(a.datos, alcance=a.alcance)
    pd.set_option("display.width", 200)
    print(r["tabla"].round(3).to_string(index=False))
    print("\nReparto del estado (parte de las compras):")
    print((r["reparto"] * 100).round(1).to_string())
    print("\nRecall por tipo de fraude:")
    print(pd.DataFrame(r["patrones"]).round(2).to_string())
    ruta_json = config.RAIZ / "resultados" / "estado_resultado.json"
    ruta_json.parent.mkdir(parents=True, exist_ok=True)
    ruta_json.write_text(json.dumps(r["salida"], indent=2, ensure_ascii=False, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
