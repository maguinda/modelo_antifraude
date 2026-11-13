"""Prueba de la METODOLOGÍA con un conjunto público y real (pagos con tarjeta de comerciantes europeos, 2013; 284.807 pagos, 492 fraudes).

Qué valida: el corte por orden de tiempo, la elección del umbral por costo en validación y la comparación logística frente a
Gradient Boosting, sobre fraude REAL y no inventado por nosotros.
Qué NO valida: nuestras 47 variables (dispositivo, IP, comportamiento): este conjunto solo trae 28 columnas anónimas (V1…V28) y el monto.

Supuesto: la copia de OpenML no trae la hora; se usa el orden de las filas. El orden se verifica: la variable V3 baja con el tiempo en el archivo
original (correlación cercana a -0,4) y aquí `ordenado_cronologicamente` lo comprueba contra un orden mezclado.

Uso:  python -m modelo_ia.validacion_publica                (descarga de OpenML, id 1597, a la carpeta temporal del sistema)
      python -m modelo_ia.validacion_publica --csv ruta.csv (un CSV con columnas V1..V28, Amount, Class)
"""
import argparse
import json
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from . import config, modelos

COLUMNAS_V = [f"V{i}" for i in range(1, 29)]
COSTOS_FALSA_ALARMA_EUR = (1.0, 5.0, 25.0)       # no hay dato real del costo de revisar un pago: se prueban tres
UMBRALES = list(np.round(np.arange(0.02, 0.99, 0.02), 2))


def cargar(ruta_csv: Optional[Path] = None) -> pd.DataFrame:
    if ruta_csv is not None:
        df = pd.read_csv(ruta_csv)
    else:
        from sklearn.datasets import fetch_openml
        df = fetch_openml(data_id=1597, as_frame=True, data_home=tempfile.gettempdir(), parser="auto").frame
    df = df.rename(columns={"Class": "es_fraude", "Amount": "monto"})
    df["es_fraude"] = df["es_fraude"].astype(int)
    for c in COLUMNAS_V + ["monto"]:
        df[c] = df[c].astype(float)
    return df.reset_index(drop=True)


def ordenado_cronologicamente(df: pd.DataFrame, semilla: int = 0) -> Dict[str, float]:
    """Evidencia de que el orden de filas es el orden del tiempo: V3 correlaciona con la fila y no con una fila mezclada."""
    i = np.arange(len(df))
    mezclado = np.random.default_rng(semilla).permutation(i)
    r = float(np.corrcoef(i, df["V3"])[0, 1])
    r0 = float(np.corrcoef(mezclado, df["V3"])[0, 1])
    return {"corr_V3_orden_real": round(r, 3), "corr_V3_orden_mezclado": round(r0, 3), "plausible": bool(abs(r) > 0.3 and abs(r0) < 0.05)}


def partir(n: int, fracciones=(0.6, 0.2, 0.2)) -> Dict[str, slice]:
    a, b = int(n * fracciones[0]), int(n * (fracciones[0] + fracciones[1]))
    return {"entrenamiento": slice(0, a), "validacion": slice(a, b), "prueba": slice(b, n)}


def costo(puntaje, y, monto, umbral: float, falsa_alarma: float) -> float:
    alerta = puntaje >= umbral
    return float(monto[(~alerta) & (y == 1)].sum() + ((alerta) & (y == 0)).sum() * falsa_alarma)


def elegir_umbral(puntaje, y, monto, falsa_alarma: float) -> float:
    return float(UMBRALES[int(np.argmin([costo(puntaje, y, monto, u, falsa_alarma) for u in UMBRALES]))])


def evaluar(df: pd.DataFrame, modelo: str, particiones: Dict[str, np.ndarray]) -> Dict:
    """Entrena con la partición 'entrenamiento'; umbral con 'validacion'; mide en 'prueba'."""
    X, y, monto = df[COLUMNAS_V + ["monto"]], df["es_fraude"].values, df["monto"].values
    tr, va, te = particiones["entrenamiento"], particiones["validacion"], particiones["prueba"]
    m = modelos.crear_modelo(modelo, COLUMNAS_V + ["monto"], [])
    m.fit(X.iloc[tr], y[tr])
    pv, pt = modelos.puntajes(m, X.iloc[va]), modelos.puntajes(m, X.iloc[te])
    sin_modelo = float(monto[te][y[te] == 1].sum())
    filas = {}
    for fa in COSTOS_FALSA_ALARMA_EUR:
        u = elegir_umbral(pv, y[va], monto[va], fa)
        alerta = pt >= u
        filas[str(fa)] = {"umbral": u, "recall": float((alerta & (y[te] == 1)).sum() / max(1, (y[te] == 1).sum())),
                          "precision": float((alerta & (y[te] == 1)).sum() / max(1, alerta.sum())),
                          "falsas_alarmas": int((alerta & (y[te] == 0)).sum()), "fraudes_que_escapan": int(((~alerta) & (y[te] == 1)).sum()),
                          "costo_eur": costo(pt, y[te], monto[te], u, fa), "costo_sin_modelo_eur": sin_modelo}
    return {"modelo": modelo, "ranking_ap": float(average_precision_score(y[te], pt)), "por_costo_de_falsa_alarma_eur": filas}


def ejecutar(ruta_csv: Optional[Path] = None, lista_modelos: Optional[List[str]] = None) -> Dict:
    df = cargar(ruta_csv)
    lista_modelos = lista_modelos or ["logistica", "gradient_boosting"]
    p = partir(len(df))
    idx = np.arange(len(df))
    por_tiempo = {k: idx[s] for k, s in p.items()}
    mezclado = np.random.default_rng(config.SEMILLA).permutation(idx)
    al_azar = {k: mezclado[s] for k, s in p.items()}                       # el atajo que da resultados demasiado bonitos
    return {"filas": len(df), "fraudes": int(df["es_fraude"].sum()), "verificacion_del_orden": ordenado_cronologicamente(df),
            "corte_por_tiempo": [evaluar(df, m, por_tiempo) for m in lista_modelos],
            "corte_al_azar_comparacion": [evaluar(df, m, al_azar) for m in lista_modelos],
            "fraudes_en_prueba_por_tiempo": int(df["es_fraude"].values[por_tiempo["prueba"]].sum())}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Valida la metodología con un conjunto público y real.")
    ap.add_argument("--csv", type=Path, default=None)
    a = ap.parse_args(argv)
    r = ejecutar(a.csv)
    print(f"Filas {r['filas']:,} | fraudes {r['fraudes']} | orden verificado: {r['verificacion_del_orden']}")
    for titulo, clave in (("CORTE POR TIEMPO (el honesto)", "corte_por_tiempo"), ("CORTE AL AZAR (atajo, para comparar)", "corte_al_azar_comparacion")):
        print(f"\n{titulo}")
        for e in r[clave]:
            print(f"  {e['modelo']:18s} AP={e['ranking_ap']:.3f}")
            for fa, f in e["por_costo_de_falsa_alarma_eur"].items():
                print(f"     falsa alarma={fa:>4} EUR  recall={f['recall']:.2f} precisión={f['precision']:.2f} falsas={f['falsas_alarmas']:>4} "
                      f"escapan={f['fraudes_que_escapan']:>3}  costo={f['costo_eur']:>8.0f} (sin modelo {f['costo_sin_modelo_eur']:.0f})")
    ruta = config.RAIZ / "resultados" / "validacion_publica.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
