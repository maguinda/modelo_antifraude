"""El contrato OpenAPI con los errores documentados tal como se devuelven: application/problem+json."""

from typing import Dict

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .metadatos import MEDIA_PROBLEMA, RESPUESTAS_ERROR


def instalar_contrato(app: FastAPI) -> None:
    def contrato() -> Dict:
        """OpenAPI con los errores documentados tal como se devuelven: application/problem+json."""
        if app.openapi_schema:
            return app.openapi_schema
        esquema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            contact=app.contact,
        )
        problema = {"$ref": "#/components/schemas/Problema"}
        for ruta in esquema["paths"].values():
            for operacion in ruta.values():
                respuestas = operacion.get("responses", {})
                if "422" in respuestas:
                    respuestas["422"] = {
                        "description": RESPUESTAS_ERROR[422]["description"],
                        "content": {MEDIA_PROBLEMA: {"schema": problema}},
                    }
                for codigo, r in respuestas.items():
                    if codigo.isdigit() and int(codigo) >= 400 and "application/json" in r.get("content", {}):
                        r["content"] = {MEDIA_PROBLEMA: r["content"]["application/json"]}
        for sobra in ("HTTPValidationError", "ValidationError"):
            esquema.get("components", {}).get("schemas", {}).pop(sobra, None)
        app.openapi_schema = esquema
        return esquema

    app.openapi = contrato  # type: ignore[method-assign]
