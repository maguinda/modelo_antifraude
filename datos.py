"""Cargar y validar el archivo de transacciones."""
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from . import config


def validar_esquema(df: pd.DataFrame) -> None:
    """Falla con un mensaje claro si el archivo no sirve para entrenar."""
    faltan = [c for c in config.COLUMNAS_OBLIGATORIAS if c not in df.columns]
    if faltan:
        raise ValueError(f"Faltan columnas en los datos: {faltan}")
    if df[config.COLUMNA_OBJETIVO].isna().any():
        raise ValueError("es_fraude tiene valores vacíos.")
    if not set(df[config.COLUMNA_OBJETIVO].unique()) <= {0, 1}:
        raise ValueError("es_fraude solo puede valer 0 o 1.")
    if df[config.COLUMNA_OBJETIVO].sum() == 0:
        raise ValueError("No hay ningún fraude en los datos: no se puede entrenar.")
    if (df["monto_cop"] <= 0).any():
        raise ValueError("Hay montos menores o iguales a cero.")


def agregar_columnas_basicas(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas derivadas de la fecha y del detalle (no usan nada del futuro)."""
    d = df.copy()
    d["fecha"] = pd.to_datetime(d["fecha_pago"])
    d["mes"] = d["fecha"].dt.month
    d["hora"] = d["fecha"].dt.hour
    d["categoria"] = d["detalle_pago"].str.split(" - ").str[0]
    return d


def cargar(ruta: Optional[Union[str, Path]] = None) -> pd.DataFrame:
    """Lee el CSV, lo valida y lo deja en orden de fecha."""
    ruta = Path(ruta) if ruta else config.DATOS_POR_DEFECTO
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro el archivo de datos: {ruta}")
    df = pd.read_csv(ruta, encoding="utf-8-sig")
    validar_esquema(df)
    df = df.sort_values("fecha_pago", kind="stable").reset_index(drop=True)
    return agregar_columnas_basicas(df)
