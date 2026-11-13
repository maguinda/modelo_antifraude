"""Entrenar, evaluar y guardar el modelo.

Uso (desde la carpeta IA/):
    python -m modelo_ia.entrenar                       # regresión logística, variables con historial
    python -m modelo_ia.entrenar --modelo gradient_boosting --conjunto historial
    python -m modelo_ia.entrenar --como-actual         # además lo marca como el modelo en uso
"""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import joblib
import pandas as pd
import sklearn

from . import config, datos, etiquetas, evaluacion, experimentos, modelos, particion, variables


def preparar_datos(ruta: Optional[Path], conjunto: str, alcance: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, pd.Series]]:
    """Carga, agrega variables y divide por fecha. Se hace una vez y se reutiliza."""
    df = datos.cargar(ruta)
    df = variables.construir(df, conjunto, alcance)
    return df, particion.dividir(df)


def definir_umbrales(umbral_revisar: float) -> Tuple[float, float]:
    """(umbral para revisar, umbral para bloquear). Siempre bloquear > revisar."""
    bloquear = max(config.UMBRAL_BLOQUEAR_MINIMO, round(umbral_revisar + 0.20, 2))
    return umbral_revisar, min(bloquear, 0.99)


ESTADO_RELLENO = "Conocido"       # valor fijo que ocupa el lugar de `estado_dispositivo` en un modelo «sin estado» (no lo usa para nada)


def ajustar_y_evaluar(df: pd.DataFrame, mascaras: Dict[str, pd.Series],
                      modelo: str, conjunto: str, etiquetas_conocidas: bool = False, sin_estado: bool = False) -> Dict:
    """Entrena con el pasado, elige el umbral en validación y evalúa en prueba.

    Con etiquetas_conocidas=True, el entrenamiento solo ve los fraudes ya confirmados en la fecha de
    corte (como en la vida real). La validación y la prueba siempre se miden con la verdad completa.
    """
    numericas, categoricas = variables.columnas_de(conjunto)
    columnas = numericas + categoricas
    if sin_estado:                                  # el modelo no puede ver el estado del dispositivo: la columna queda fija
        df = df.assign(estado_dispositivo=ESTADO_RELLENO)
    y = df[config.COLUMNA_OBJETIVO]

    y_entreno = y[mascaras["entrenamiento"]]
    if etiquetas_conocidas:
        corte = pd.Timestamp(config.FIN_ENTRENAMIENTO) + pd.Timedelta(days=1)
        y_entreno = etiquetas.etiqueta_conocida(df.loc[mascaras["entrenamiento"]], corte)
    pipeline = modelos.crear_modelo(modelo, numericas, categoricas)
    inicio = time.time()
    pipeline.fit(df.loc[mascaras["entrenamiento"], columnas], y_entreno)
    segundos = time.time() - inicio

    p_valid = modelos.puntajes(pipeline, df.loc[mascaras["validacion"], columnas])
    p_prueba = modelos.puntajes(pipeline, df.loc[mascaras["prueba"], columnas])
    montos_v = df.loc[mascaras["validacion"], "monto_cop"].values
    montos_p = df.loc[mascaras["prueba"], "monto_cop"].values
    y_v, y_p = y[mascaras["validacion"]].values, y[mascaras["prueba"]].values

    umbral, _ = evaluacion.elegir_umbral(p_valid, y_v, montos_v)     # elegido SIN mirar la prueba
    umbral_revisar, umbral_bloquear = definir_umbrales(umbral)
    return {
        "pipeline": pipeline, "modelo": modelo, "conjunto": conjunto,
        "etiquetas_conocidas": etiquetas_conocidas, "positivos_entrenamiento": int(y_entreno.sum()), "sin_estado": sin_estado,
        "numericas": numericas, "categoricas": categoricas,
        "umbral_revisar": umbral_revisar, "umbral_bloquear": umbral_bloquear,
        "segundos_entrenamiento": round(segundos, 2),
        "filas": {k: int(v.sum()) for k, v in mascaras.items()},
        "fraudes_validacion": int(y_v.sum()), "fraudes_prueba": int(y_p.sum()),
        "metricas_validacion": evaluacion.metricas(p_valid, y_v, montos_v, umbral_revisar),
        "metricas_prueba": evaluacion.metricas(p_prueba, y_p, montos_p, umbral_revisar),
        "metricas_prueba_umbral_050": evaluacion.metricas(p_prueba, y_p, montos_p, 0.5),
        "costo_prueba_sin_modelo_cop": float(montos_p[y_p == 1].sum()),
        "recall_por_patron_prueba": evaluacion.recall_por_patron(
            df.loc[mascaras["prueba"]], p_prueba, umbral_revisar).to_dict(),
    }


def _huella(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()[:16]


def guardar(resultado: Dict, ruta_datos: Path, como_actual: bool = False, como_actual_sin_estado: bool = False) -> Path:
    """Guarda el modelo (.joblib) y sus datos de trazabilidad (.json)."""
    config.CARPETA_MODELOS.mkdir(parents=True, exist_ok=True)
    sufijo = "_sinestado" if resultado.get("sin_estado") else ""
    nombre = f"{resultado['modelo']}_{resultado['conjunto']}{sufijo}_v{config.VERSION}"
    ruta_modelo = config.CARPETA_MODELOS / f"{nombre}.joblib"
    meta = {
        "version": config.VERSION, "modelo": resultado["modelo"], "conjunto": resultado["conjunto"],
        "numericas": resultado["numericas"], "categoricas": resultado["categoricas"],
        "umbral_revisar": resultado["umbral_revisar"], "umbral_bloquear": resultado["umbral_bloquear"],
        "filas": resultado["filas"], "huella_datos": _huella(ruta_datos),
        "scikit_learn": sklearn.__version__, "datos_sinteticos": True,
        "etiquetas_conocidas": resultado["etiquetas_conocidas"], "sin_estado": bool(resultado.get("sin_estado")), "alcance_historial": resultado.get("alcance_historial", "plataforma"),
        "metricas_validacion": resultado["metricas_validacion"],
        "metricas_prueba": resultado["metricas_prueba"],
    }
    joblib.dump({"pipeline": resultado["pipeline"], "meta": meta}, ruta_modelo)
    ruta_modelo.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    if como_actual:
        shutil.copyfile(ruta_modelo, config.CARPETA_MODELOS / "modelo_actual.joblib")
    if como_actual_sin_estado:
        if not resultado.get("sin_estado"):
            raise ValueError("Solo un modelo entrenado con --sin-estado puede ser el modelo para pagos sin estado del dispositivo.")
        shutil.copyfile(ruta_modelo, config.CARPETA_MODELOS / "modelo_actual_sin_estado.joblib")
    return ruta_modelo


def registrar_experimento(resultado: Dict, nota: str = "") -> None:
    m = resultado["metricas_prueba"]
    experimentos.registrar({
        "version": config.VERSION, "modelo": resultado["modelo"], "variables": resultado["conjunto"],
        "umbral": m["umbral"], "recall_prueba": round(m["recall"], 3),
        "precision_prueba": round(m["precision"], 3), "ranking_ap_prueba": round(m["ranking_ap"], 3),
        "falsas_alarmas": m["falsas_alarmas"], "fraudes_que_escapan": m["fraudes_que_escapan"],
        "costo_prueba_millones": round(m["costo_cop"] / 1e6, 1),
        "segundos": resultado["segundos_entrenamiento"],
        "etiquetas_conocidas": resultado["etiquetas_conocidas"], "alcance": resultado.get("alcance_historial", "plataforma"), "nota": nota,
    })


def resumen_texto(r: Dict) -> str:
    v, p = r["metricas_validacion"], r["metricas_prueba"]
    return (f"Modelo: {r['modelo']} | variables: {r['conjunto']} | entrenado en {r['segundos_entrenamiento']} s"
            f"{' | etiquetas conocidas a la fecha de corte' if r['etiquetas_conocidas'] else ''}"
            f" | historial por {r.get('alcance_historial', 'plataforma')}{' | SIN estado del dispositivo' if r.get('sin_estado') else ''}\n"
            f"Filas: {r['filas']}\n"
            f"Umbrales: revisar desde {r['umbral_revisar']} | bloquear desde {r['umbral_bloquear']}\n"
            f"Validación: recall {v['recall']:.3f} | precisión {v['precision']:.3f} | ranking (AP) {v['ranking_ap']:.3f}\n"
            f"Prueba:     recall {p['recall']:.3f} | precisión {p['precision']:.3f} | ranking (AP) {p['ranking_ap']:.3f}\n"
            f"Costo en prueba: {p['costo_cop'] / 1e6:,.1f} M COP (sin modelo: "
            f"{r['costo_prueba_sin_modelo_cop'] / 1e6:,.1f} M) | falsas alarmas {p['falsas_alarmas']:,} | "
            f"fraudes que escapan {p['fraudes_que_escapan']:,}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Entrena el modelo antifraude.")
    ap.add_argument("--datos", type=Path, default=None, help="CSV de transacciones")
    ap.add_argument("--modelo", default=modelos.MODELO_POR_DEFECTO, choices=list(modelos.REGISTRO))
    ap.add_argument("--conjunto", default=config.CONJUNTO_POR_DEFECTO, choices=list(config.CONJUNTOS_VARIABLES))
    ap.add_argument("--etiquetas-conocidas", action="store_true",
                    help="entrenar solo con los fraudes ya confirmados en la fecha de corte")
    ap.add_argument("--alcance", default=config.ALCANCE_HISTORIAL, choices=list(config.ALCANCES_HISTORIAL),
                    help="historial por 'comercio' (como lo calcula la API) o con toda la 'plataforma' junta")
    ap.add_argument("--sin-estado", action="store_true", help="entrenar SIN la variable estado del dispositivo (para pagos donde el comercio no la manda)")
    ap.add_argument("--como-actual-sin-estado", action="store_true", help="dejarlo como el modelo que usa la API cuando el pago no trae estado")
    ap.add_argument("--como-actual", action="store_true", help="marcar este modelo como el que usa predecir.py")
    ap.add_argument("--sin-guardar", action="store_true", help="solo evaluar, sin escribir archivos")
    ap.add_argument("--nota", default="", help="texto corto para la bitácora de experimentos")
    a = ap.parse_args(argv)

    ruta = a.datos or config.DATOS_POR_DEFECTO
    df, mascaras = preparar_datos(ruta, a.conjunto, a.alcance)
    r = ajustar_y_evaluar(df, mascaras, a.modelo, a.conjunto, a.etiquetas_conocidas, a.sin_estado)
    r["alcance_historial"] = a.alcance
    print(resumen_texto(r))
    if not a.sin_guardar:
        destino = guardar(r, Path(ruta), a.como_actual, a.como_actual_sin_estado)
        registrar_experimento(r, a.nota)
        print(f"Modelo guardado en {destino}")
        print(f"Experimento anotado en {config.ARCHIVO_EXPERIMENTOS}")


if __name__ == "__main__":
    main()
