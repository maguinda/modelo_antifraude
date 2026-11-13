"""Genera el contrato OpenAPI en un archivo (modelo_ia/api/openapi.json) para entregárselo a los comercios.

Uso:  python -m modelo_ia.api.exportar_openapi
"""
import json
import tempfile
from pathlib import Path

from .. import config
from .ajustes import Ajustes
from .app import crear_app

RUTA = Path(__file__).resolve().parent / "openapi.json"


def generar() -> dict:
    """El contrato no depende del modelo ni de la base: se genera con valores de mentira."""
    with tempfile.TemporaryDirectory() as carpeta:
        ajustes = Ajustes(ruta_bd=Path(carpeta) / "contrato.sqlite3")
        artefacto = {"meta": {"modelo": "ejemplo", "version": config.VERSION, "conjunto": "historial",
                              "numericas": [], "categoricas": []}, "pipeline": None}
        return crear_app(ajustes, artefacto).openapi()


def main() -> None:
    RUTA.write_text(json.dumps(generar(), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print("Contrato escrito en", RUTA)


if __name__ == "__main__":
    main()
