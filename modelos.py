"""Fábrica de modelos. Para probar un algoritmo nuevo solo se agrega una línea al REGISTRO."""
from typing import Callable, Dict, List

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier

from . import config

MODELO_POR_DEFECTO = "logistica"

REGISTRO: Dict[str, Callable[[], object]] = {
    "logistica": lambda: LogisticRegression(class_weight="balanced", max_iter=1000),
    "arbol": lambda: DecisionTreeClassifier(max_depth=6, min_samples_leaf=50,
                                            class_weight="balanced", random_state=config.SEMILLA),
    "random_forest": lambda: RandomForestClassifier(n_estimators=100, min_samples_leaf=10,
                                                    max_samples=0.25, class_weight="balanced",
                                                    n_jobs=-1, random_state=config.SEMILLA),
    "gradient_boosting": lambda: HistGradientBoostingClassifier(class_weight="balanced",
                                                                random_state=config.SEMILLA),
    "regresion_lineal": lambda: LinearRegression(),   # solo de comparación: no es un clasificador
}


def crear_modelo(nombre: str, numericas: List[str], categoricas: List[str]) -> Pipeline:
    """Pipeline completo: escalar números + convertir categorías a 0/1 + clasificador."""
    if nombre not in REGISTRO:
        raise ValueError(f"Modelo desconocido '{nombre}'. Opciones: {list(REGISTRO)}")
    preparacion = ColumnTransformer([
        ("numeros", StandardScaler(), numericas),
        ("textos", OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float32),
         categoricas),
    ])
    return Pipeline([("preparacion", preparacion), ("clasificador", REGISTRO[nombre]())])


def puntajes(modelo: Pipeline, X) -> np.ndarray:
    """Probabilidad de fraude (o puntaje, en la regresión lineal)."""
    if hasattr(modelo, "predict_proba"):
        return modelo.predict_proba(X)[:, 1]
    return modelo.predict(X)
