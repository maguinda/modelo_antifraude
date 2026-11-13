"""Bitácora de experimentos: cada entrenamiento deja una fila para ver cómo mejora el modelo."""
from datetime import datetime
from typing import Dict

import pandas as pd

from . import config


def registrar(fila: Dict) -> None:
    """Agrega una fila a resultados/experimentos.csv (lo crea si no existe)."""
    ruta = config.ARCHIVO_EXPERIMENTOS
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fila = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **fila}
    nueva = pd.DataFrame([fila])
    if ruta.exists():
        previa = pd.read_csv(ruta)
        nueva = pd.concat([previa, nueva], ignore_index=True)
    nueva.to_csv(ruta, index=False)


def leer() -> pd.DataFrame:
    ruta = config.ARCHIVO_EXPERIMENTOS
    return pd.read_csv(ruta) if ruta.exists() else pd.DataFrame()
