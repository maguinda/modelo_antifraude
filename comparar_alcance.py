"""¿Cuánto vale compartir historial entre comercios? Entrena el MISMO modelo con el historial por 'comercio' y con la 'plataforma' junta.

El modelo y los datos son los mismos; solo cambia con qué compras se arma el historial de cada cliente y de cada huella.
Úsalo con un dataset donde cada cliente compre sobre todo en sus comercios habituales (el de versión 2).

Uso:  python -m modelo_ia.comparar_alcance --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones_v2.csv.gz
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Sequence

import pandas as pd

from . import config, entrenar


def ejecutar(ruta: Optional[Path] = None, modelos: Sequence[str] = ("gradient_boosting",), conjunto: str = "completo",
             etiquetas_conocidas: bool = True, registrar: bool = True) -> Dict:
    filas, patrones = [], {}
    for alcance in config.ALCANCES_HISTORIAL:
        df, mascaras = entrenar.preparar_datos(ruta, conjunto, alcance)
        for modelo in modelos:
            r = entrenar.ajustar_y_evaluar(df, mascaras, modelo, conjunto, etiquetas_conocidas)
            r["alcance_historial"] = alcance
            m = r["metricas_prueba"]
            filas.append({"alcance": alcance, "modelo": modelo, "recall": m["recall"], "precision": m["precision"], "ranking_ap": m["ranking_ap"],
                          "falsas_alarmas": m["falsas_alarmas"], "fraudes_que_escapan": m["fraudes_que_escapan"],
                          "costo_millones": m["costo_cop"] / 1e6, "umbral": m["umbral"]})
            patrones[f"{modelo}|{alcance}"] = r["recall_por_patron_prueba"]
            if registrar:
                entrenar.registrar_experimento(r, f"alcance del historial: {alcance}")
    tabla = pd.DataFrame(filas)
    salida = {"datos_sinteticos": True, "tabla": tabla.round(4).to_dict(orient="records"), "recall_por_patron": patrones}
    if registrar:
        ruta_json = config.RAIZ / "resultados" / "alcance_resultado.json"
        ruta_json.parent.mkdir(parents=True, exist_ok=True)
        ruta_json.write_text(json.dumps(salida, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    return {"tabla": tabla, "patrones": patrones, "salida": salida}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compara historial por comercio frente a plataforma.")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--modelos", nargs="+", default=["gradient_boosting"])
    ap.add_argument("--sin-guardar", action="store_true")
    a = ap.parse_args(argv)
    r = ejecutar(a.datos, a.modelos, registrar=not a.sin_guardar)
    pd.set_option("display.width", 200)
    print(r["tabla"].round(3).to_string(index=False))
    print("\nRecall por tipo de fraude:")
    print(pd.DataFrame(r["patrones"]).round(2).to_string())


if __name__ == "__main__":
    main()
