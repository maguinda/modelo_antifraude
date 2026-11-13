"""Mide cuánto aporta cada grupo de variables nuevas (huella, geolocalización, identidad, comportamiento, perfil).

Dos lecturas:
  - SUMAR: el conjunto 'historial' (referencia) más un grupo.
  - QUITAR: el conjunto 'completo' sin un grupo (si empeora mucho, ese grupo importa).
Todo se mide en la PRUEBA con el umbral elegido en la validación.

Uso:  python -m modelo_ia.comparar_variables --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Sequence

import pandas as pd

from . import config, entrenar, variables

GRUPOS_NUEVOS = ["perfil", "huella", "geo_ip", "identidad", "comportamiento"]


def _conjuntos() -> Dict[str, str]:
    """nombre del conjunto -> cómo leerlo."""
    for g in GRUPOS_NUEVOS:
        variables.registrar_conjunto(f"completo_sin_{g}", [x for x in config.CONJUNTOS_GRUPOS["completo"] if x != g])
    lista = {"base": "referencia", "historial": "referencia"}
    lista.update({g: "suma" for g in GRUPOS_NUEVOS})
    lista["completo"] = "todo"
    lista.update({f"completo_sin_{g}": "quita" for g in GRUPOS_NUEVOS})
    return lista


def ejecutar(ruta: Optional[Path] = None, modelos: Sequence[str] = ("logistica", "gradient_boosting"),
             etiquetas_conocidas: bool = False, registrar: bool = True) -> Dict:
    df, mascaras = entrenar.preparar_datos(ruta, "completo")
    filas, patrones = [], {}
    for nombre_modelo in modelos:
        for conjunto, lectura in _conjuntos().items():
            r = entrenar.ajustar_y_evaluar(df, mascaras, nombre_modelo, conjunto, etiquetas_conocidas)
            m = r["metricas_prueba"]
            filas.append({"modelo": nombre_modelo, "conjunto": conjunto, "lectura": lectura,
                          "n_variables": len(r["numericas"]) + len(r["categoricas"]),
                          "recall": m["recall"], "precision": m["precision"], "ranking_ap": m["ranking_ap"],
                          "falsas_alarmas": m["falsas_alarmas"], "fraudes_que_escapan": m["fraudes_que_escapan"],
                          "costo_millones": m["costo_cop"] / 1e6, "umbral": m["umbral"]})
            if conjunto in ("historial", "completo"):
                patrones[(nombre_modelo, conjunto)] = r["recall_por_patron_prueba"]
            if registrar:
                entrenar.registrar_experimento(r, f"aporte de variables ({lectura})")
    tabla = pd.DataFrame(filas)
    ref = tabla[tabla["conjunto"] == "historial"].set_index("modelo")
    comp = tabla[tabla["conjunto"] == "completo"].set_index("modelo")
    tabla["dif_ap"] = [row.ranking_ap - (ref.loc[row.modelo, "ranking_ap"] if row.lectura in ("suma", "todo")
                                          else comp.loc[row.modelo, "ranking_ap"] if row.lectura == "quita" else row.ranking_ap)
                       for row in tabla.itertuples()]
    tabla["dif_costo_millones"] = [row.costo_millones - (ref.loc[row.modelo, "costo_millones"] if row.lectura in ("suma", "todo")
                                                         else comp.loc[row.modelo, "costo_millones"] if row.lectura == "quita" else row.costo_millones)
                                   for row in tabla.itertuples()]
    por_patron = {f"{m}|{c}": v for (m, c), v in patrones.items()}
    salida = {"datos_sinteticos": True, "etiquetas_conocidas": etiquetas_conocidas,
              "tabla": tabla.round(4).to_dict(orient="records"), "recall_por_patron": por_patron}
    if registrar:
        ruta_json = config.RAIZ / "resultados" / "variables_resultado.json"
        ruta_json.parent.mkdir(parents=True, exist_ok=True)
        ruta_json.write_text(json.dumps(salida, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    return {"tabla": tabla, "patrones": patrones, "salida": salida}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Mide el aporte de cada grupo de variables.")
    ap.add_argument("--datos", type=Path, default=None)
    ap.add_argument("--modelos", nargs="+", default=["logistica", "gradient_boosting"])
    ap.add_argument("--etiquetas-conocidas", action="store_true")
    ap.add_argument("--sin-guardar", action="store_true")
    a = ap.parse_args(argv)
    r = ejecutar(a.datos, a.modelos, a.etiquetas_conocidas, registrar=not a.sin_guardar)
    pd.set_option("display.width", 220)
    cols = ["modelo", "conjunto", "n_variables", "recall", "precision", "ranking_ap", "dif_ap", "costo_millones",
            "dif_costo_millones", "falsas_alarmas"]
    print(r["tabla"][cols].round(3).to_string(index=False))
    for (m, c), v in r["patrones"].items():
        print(f"\nRecall por tipo de fraude: {m} / {c}")
        print(pd.Series(v).to_string())


if __name__ == "__main__":
    main()
