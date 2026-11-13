"""Comparación para la hipótesis H1: modelo de aprendizaje automático frente a reglas fijas.

H1: sobre las mismas transacciones de prueba, el modelo detecta más fraudes (recall) que las
reglas fijas, SIN subir la tasa de falsos positivos.

Para que la comparación sea justa:
  - Las reglas se ajustan con la validación (no con la prueba), igual que el modelo.
  - El modelo se evalúa de dos maneras: con su umbral de menor costo y con un umbral que iguala
    la tasa de falsos positivos de las reglas ajustadas (así se ve cuánto más detecta con el mismo
    nivel de molestias a clientes buenos).
  - La diferencia lleva intervalo de confianza del 95 % por remuestreo (bootstrap) pareado.

Uso:  python -m modelo_ia.comparar_h1 [--datos ruta.csv] [--repeticiones 1000]
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import config, entrenar, evaluacion, experimentos, modelos
from .reglas import MotorReglas
from .reglas.reglas_base import ajustar, construir_reglas, motor_ingenuo
from .reglas.seguridad import construir_reglas_seguridad


def tasas(alerta, reales) -> Dict[str, float]:
    a, y = np.asarray(alerta, bool), np.asarray(reales).astype(bool)
    return {"recall": float((a & y).sum() / y.sum()), "fpr": float((a & ~y).sum() / (~y).sum())}


def umbral_igual_fpr(puntaje_validacion, reales_validacion, fpr_objetivo: float) -> float:
    """El umbral más bajo cuya tasa de falsos positivos en validación no supera la de las reglas."""
    p, y = np.asarray(puntaje_validacion), np.asarray(reales_validacion).astype(bool)
    negativos = (~y).sum()
    for u in np.round(np.arange(0.01, 1.0, 0.01), 2):
        if ((p >= u) & ~y).sum() / negativos <= fpr_objetivo:
            return float(u)
    return 0.99


def bootstrap_diferencias(alerta_a, alerta_b, reales, repeticiones: int = 1000,
                          semilla: int = config.SEMILLA) -> Dict:
    """Diferencia (A menos B) en recall y en tasa de falsos positivos, con IC del 95 %.

    Remuestreo pareado: en cada repetición se eligen las mismas transacciones para A y para B.
    """
    a, b, y = (np.asarray(alerta_a, bool), np.asarray(alerta_b, bool), np.asarray(reales).astype(bool))
    tp_a, tp_b = (a & y).astype(np.int32), (b & y).astype(np.int32)
    fp_a, fp_b = (a & ~y).astype(np.int32), (b & ~y).astype(np.int32)
    yi = y.astype(np.int32)
    n = len(y)
    rng = np.random.default_rng(semilla)
    d_rec, d_fpr = np.empty(repeticiones), np.empty(repeticiones)
    for i in range(repeticiones):
        idx = rng.integers(0, n, n)
        pos = yi[idx].sum()
        d_rec[i] = (tp_a[idx].sum() - tp_b[idx].sum()) / pos
        d_fpr[i] = (fp_a[idx].sum() - fp_b[idx].sum()) / (n - pos)
    ic = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    ta, tb = tasas(a, y), tasas(b, y)
    return {"dif_recall": ta["recall"] - tb["recall"], "ic95_recall": ic(d_rec),
            "dif_fpr": ta["fpr"] - tb["fpr"], "ic95_fpr": ic(d_fpr), "repeticiones": repeticiones}


def veredicto(b: Dict) -> str:
    """Lectura de H1 a partir de los intervalos (no es una prueba de significancia formal)."""
    mas_recall = b["ic95_recall"][0] > 0
    sin_subir_fpr = b["ic95_fpr"][1] <= 0
    if mas_recall and sin_subir_fpr:
        return "H1 apoyada: el modelo detecta más fraude y no sube los falsos positivos."
    if mas_recall:
        return "El modelo detecta más fraude, pero NO se puede afirmar que no suba los falsos positivos."
    if b["ic95_recall"][1] < 0:
        return "H1 no apoyada: el modelo detecta menos fraude que las reglas."
    return "Sin diferencia clara en recall: H1 no se puede afirmar."


def _fila(nombre: str, severidad, reales, montos) -> Dict:
    m = evaluacion.metricas(np.asarray(severidad), reales, montos, 1)     # alerta = severidad >= 1
    t = tasas(np.asarray(severidad) >= 1, reales)
    return {"metodo": nombre, "recall": m["recall"], "precision": m["precision"], "fpr": t["fpr"],
            "falsas_alarmas": m["falsas_alarmas"], "fraudes_que_escapan": m["fraudes_que_escapan"],
            "costo_millones": m["costo_cop"] / 1e6}


def ejecutar(ruta: Optional[Path] = None, repeticiones: int = 1000, registrar: bool = True) -> Dict:
    df, mascaras = entrenar.preparar_datos(ruta, "historial")
    y = df[config.COLUMNA_OBJETIVO]
    v, p = mascaras["validacion"], mascaras["prueba"]
    dv, dp = df.loc[v], df.loc[p]
    yv, yp = y[v].values, y[p].values
    mv, mp = dv["monto_cop"].values, dp["monto_cop"].values

    # --- modelo (regresión logística) ---------------------------------------
    r = entrenar.ajustar_y_evaluar(df, mascaras, modelos.MODELO_POR_DEFECTO, "historial")
    cols = r["numericas"] + r["categoricas"]
    pv, pp = modelos.puntajes(r["pipeline"], dv[cols]), modelos.puntajes(r["pipeline"], dp[cols])
    sev_modelo = np.where(pp >= r["umbral_bloquear"], 2, np.where(pp >= r["umbral_revisar"], 1, 0))

    # --- reglas ---------------------------------------------------------------
    ingenuas = motor_ingenuo()
    params, costo_ini_v, costo_fin_v = ajustar(dv)                       # solo con validación
    ajustadas = MotorReglas(construir_reglas(params))
    sev_ingenuas, sev_ajustadas = ingenuas.severidad(dp), ajustadas.severidad(dp)

    # --- modelo con la misma tasa de falsos positivos que las reglas ajustadas -
    fpr_reglas_valid = tasas(ajustadas.severidad(dv) >= 1, yv)["fpr"]
    u_igual = umbral_igual_fpr(pv, yv, fpr_reglas_valid)
    sev_modelo_igual = (pp >= u_igual).astype(int)
    # y lo mismo para la comparación con las reglas ingenuas (cada una con SU propia tasa de falsos positivos)
    fpr_ingenuas_valid = tasas(ingenuas.severidad(dv) >= 1, yv)["fpr"]
    u_igual_ing = umbral_igual_fpr(pv, yv, fpr_ingenuas_valid)
    sev_modelo_igual_ing = (pp >= u_igual_ing).astype(int)

    # --- modelo + red de seguridad -----------------------------------------------
    seguridad = MotorReglas(construir_reglas_seguridad())
    sev_hibrido = np.maximum(sev_modelo, seguridad.severidad(dp))

    filas = [
        _fila("Reglas ingenuas", sev_ingenuas, yp, mp),
        _fila("Reglas ajustadas (validación)", sev_ajustadas, yp, mp),
        _fila(f"Modelo, umbral de menor costo ({r['umbral_revisar']})", sev_modelo, yp, mp),
        _fila(f"Modelo, igualando falsos positivos de las reglas ajustadas ({u_igual})", sev_modelo_igual, yp, mp),
        _fila("Modelo + red de seguridad", sev_hibrido, yp, mp),
    ]
    tabla = pd.DataFrame(filas).set_index("metodo")

    comparaciones = {
        "modelo_igual_fpr_vs_reglas_ajustadas": bootstrap_diferencias(sev_modelo_igual >= 1, sev_ajustadas >= 1, yp, repeticiones),
        "modelo_igual_fpr_vs_reglas_ingenuas": bootstrap_diferencias(sev_modelo_igual_ing >= 1, sev_ingenuas >= 1, yp, repeticiones),
        "modelo_costo_vs_reglas_ajustadas": bootstrap_diferencias(sev_modelo >= 1, sev_ajustadas >= 1, yp, repeticiones),
    }
    for c in comparaciones.values():
        c["veredicto"] = veredicto(c)

    salida = {
        "datos_sinteticos": True, "filas_prueba": int(p.sum()), "fraudes_prueba": int(yp.sum()),
        "parametros_reglas_ajustadas": params,
        "umbral_modelo_igual_fpr": {"vs_reglas_ajustadas": u_igual, "vs_reglas_ingenuas": u_igual_ing},
        "costo_validacion_reglas_ingenuas_millones": costo_ini_v / 1e6,
        "costo_validacion_reglas_ajustadas_millones": costo_fin_v / 1e6,
        "tabla_prueba": tabla.round(4).reset_index().to_dict(orient="records"),
        "comparaciones": comparaciones,
    }
    if registrar:
        config.ARCHIVO_H1.parent.mkdir(parents=True, exist_ok=True)
        config.ARCHIVO_H1.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")
        for nombre, sev in [("reglas_ingenuas", sev_ingenuas), ("reglas_ajustadas", sev_ajustadas),
                            ("modelo_mas_red_seguridad", sev_hibrido)]:
            f = _fila(nombre, sev, yp, mp)
            experimentos.registrar({
                "version": config.VERSION, "modelo": nombre, "variables": "historial", "umbral": 1,
                "recall_prueba": round(f["recall"], 3), "precision_prueba": round(f["precision"], 3),
                "falsas_alarmas": f["falsas_alarmas"], "fraudes_que_escapan": f["fraudes_que_escapan"],
                "costo_prueba_millones": round(f["costo_millones"], 1), "nota": "comparación H1"})
    return {"tabla": tabla, "salida": salida}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compara el modelo con reglas fijas (hipótesis H1).")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--repeticiones", type=int, default=1000)
    ap.add_argument("--sin-guardar", action="store_true")
    a = ap.parse_args(argv)
    r = ejecutar(a.datos, a.repeticiones, registrar=not a.sin_guardar)
    pd.set_option("display.width", 200)
    print("Prueba (septiembre en adelante). DATOS SINTÉTICOS: ver advertencia en el README.\n")
    print(r["tabla"].round(3).to_string())
    print("\nParámetros de las reglas ajustadas:", r["salida"]["parametros_reglas_ajustadas"])
    for nombre, c in r["salida"]["comparaciones"].items():
        print(f"\n{nombre}")
        print(f"  diferencia en recall: {c['dif_recall']:+.3f}  IC95 % [{c['ic95_recall'][0]:+.3f}, {c['ic95_recall'][1]:+.3f}]")
        print(f"  diferencia en falsos positivos (tasa): {c['dif_fpr']:+.4f}  IC95 % [{c['ic95_fpr'][0]:+.4f}, {c['ic95_fpr'][1]:+.4f}]")
        print(f"  {c['veredicto']}")


if __name__ == "__main__":
    main()
